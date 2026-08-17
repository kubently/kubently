"""Tests for the executor-side change-correlation runners (helm, argocd)
and the read-only rollout subcommand whitelist rule."""

import json
from unittest.mock import MagicMock, patch

import pytest

from kubently.modules.executor.argocd import ArgoCDRunner
from kubently.modules.executor.dynamic_whitelist import DynamicCommandWhitelist
from kubently.modules.executor.helm import HelmRunner


class TestHelmRunnerAvailability:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("HELM_HISTORY_ENABLED", raising=False)
        runner = HelmRunner(helm_path="/usr/local/bin/helm")
        assert runner.available is False
        result = runner.run({"subcommand": "history", "release_name": "x"})
        assert result["status"] == "UNAVAILABLE"
        assert "HELM_HISTORY_ENABLED" in result["error"]

    def test_enabled_but_no_binary(self):
        runner = HelmRunner(enabled=True, helm_path=None)
        assert runner.available is False

    def test_env_enable(self, monkeypatch):
        monkeypatch.setenv("HELM_HISTORY_ENABLED", "true")
        assert HelmRunner(helm_path="/bin/helm").available is True


class TestHelmRunnerValidation:
    def setup_method(self):
        self.runner = HelmRunner(enabled=True, helm_path="/bin/helm")

    def test_unknown_subcommand_rejected(self):
        for subcommand in ("get", "install", "uninstall", "rollback", None, "history; rm -rf /"):
            result = self.runner.run({"subcommand": subcommand})
            assert result["success"] is False, subcommand
            assert result["status"] == "FAILED"

    def test_history_requires_valid_release(self):
        assert "release_name" in self.runner.run({"subcommand": "history"})["error"]
        for bad in ("UPPER", "has space", "semi;colon", "$(cmd)", "-leading"):
            result = self.runner.run({"subcommand": "history", "release_name": bad})
            assert result["success"] is False, bad

    def test_bad_namespace_rejected(self):
        result = self.runner.run(
            {"subcommand": "history", "release_name": "ok", "namespace": "bad ns"}
        )
        assert result["success"] is False

    def test_history_argv(self):
        argv, error = self.runner._build_argv(
            "history", {"release_name": "api", "namespace": "prod", "max": 5}
        )
        assert error is None
        assert argv == ["/bin/helm", "history", "api", "-o", "json", "--max", "5", "-n", "prod"]

    def test_list_argv_all_namespaces_when_unscoped(self):
        argv, error = self.runner._build_argv("list", {})
        assert error is None
        assert "--all-namespaces" in argv
        argv, _ = self.runner._build_argv("list", {"namespace": "prod"})
        assert "--all-namespaces" not in argv
        assert argv[-2:] == ["-n", "prod"]

    def test_max_is_bounded(self):
        argv, _ = self.runner._build_argv("history", {"release_name": "a", "max": 9999})
        assert "50" in argv
        argv, _ = self.runner._build_argv("history", {"release_name": "a", "max": "junk"})
        assert "10" in argv

    @patch("kubently.modules.executor.helm.subprocess.run")
    def test_successful_run_caps_output(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="x" * 100, stderr="")
        runner = HelmRunner(enabled=True, helm_path="/bin/helm", max_output_chars=50)
        result = runner.run({"subcommand": "history", "release_name": "api"})
        assert result["success"] is True
        assert "[truncated at 50 chars" in result["output"]

    @patch("kubently.modules.executor.helm.subprocess.run")
    def test_helm_error_passthrough(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error: release: not found"
        )
        result = self.runner.run({"subcommand": "history", "release_name": "ghost"})
        assert result["success"] is False
        assert "not found" in result["error"]


class TestArgoCDRunner:
    def test_unavailable_without_url(self, monkeypatch):
        monkeypatch.delenv("ARGOCD_URL", raising=False)
        monkeypatch.delenv("ARGOCD_TOKEN", raising=False)
        result = ArgoCDRunner().run({"operation": "get_app", "app_name": "x"})
        assert result["status"] == "UNAVAILABLE"
        assert "ARGOCD_URL" in result["error"]

    def test_operation_and_name_validation(self):
        runner = ArgoCDRunner(base_url="https://argocd.local", token="t")
        assert runner.run({"operation": "delete_app"})["success"] is False
        assert runner.run({"operation": "get_app"})["success"] is False
        assert runner.run({"operation": "get_app", "app_name": "Bad Name"})["success"] is False
        assert (
            runner.run({"operation": "revision_metadata", "app_name": "ok"})["success"] is False
        )
        result = runner.run(
            {"operation": "revision_metadata", "app_name": "ok", "revision": "bad rev!"}
        )
        assert result["success"] is False

    @patch("kubently.modules.executor.argocd.requests.get")
    def test_get_app_compacts_and_authenticates(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "metadata": {"name": "shop", "managedFields": ["huge"]},
                "spec": {"project": "default", "destination": {"namespace": "prod"}},
                "status": {
                    "sync": {"status": "Synced", "revision": "abc"},
                    "health": {"status": "Healthy"},
                    "history": [
                        {"id": i, "revision": f"rev{i}", "deployedAt": "2026-08-17T10:00:00Z",
                         "source": {"repoURL": "r", "targetRevision": "main"}}
                        for i in range(15)
                    ],
                },
            },
        )
        runner = ArgoCDRunner(base_url="https://argocd.local", token="secret-token")
        result = runner.run({"operation": "get_app", "app_name": "shop"})
        assert result["success"] is True
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://argocd.local/api/v1/applications/shop"
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer secret-token"
        payload = json.loads(result["output"])
        assert payload["name"] == "shop"
        assert "managedFields" not in result["output"]
        assert len(payload["history"]) == 10  # capped, most recent kept
        assert payload["history"][-1]["id"] == 14

    @patch("kubently.modules.executor.argocd.requests.get")
    def test_list_apps_truncation_note(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"items": [{"metadata": {"name": f"app{i}"}} for i in range(60)]},
        )
        runner = ArgoCDRunner(base_url="https://argocd.local")
        result = runner.run({"operation": "list_apps"})
        payload = json.loads(result["output"])
        assert len(payload["items"]) == 50
        assert "showing 50 of 60" in payload["kubently_truncation"]

    @patch("kubently.modules.executor.argocd.requests.get")
    def test_api_error_surfaced(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=403, json=lambda: {"message": "permission denied"}
        )
        runner = ArgoCDRunner(base_url="https://argocd.local", token="t")
        result = runner.run({"operation": "get_app", "app_name": "shop"})
        assert result["success"] is False
        assert "permission denied" in result["error"]

    def test_bad_selector_rejected(self):
        runner = ArgoCDRunner(base_url="https://argocd.local")
        result = runner.run({"operation": "list_apps", "selector": "a=b; rm"})
        assert result["success"] is False


class TestRolloutWhitelist:
    def setup_method(self):
        self.whitelist = DynamicCommandWhitelist(config_path="/nonexistent-config")

    def teardown_method(self):
        self.whitelist.stop()

    def test_read_only_subcommands_allowed(self):
        assert self.whitelist.validate_command(["rollout", "history", "deployment/api"]) == (True, None)
        allowed, _ = self.whitelist.validate_command(
            ["rollout", "status", "deployment/api", "-n", "prod"]
        )
        assert allowed is True

    def test_mutating_subcommands_blocked(self):
        for subcommand in ("restart", "undo", "pause", "resume"):
            allowed, reason = self.whitelist.validate_command(
                ["rollout", subcommand, "deployment/api"]
            )
            assert allowed is False, subcommand
            assert "not allowed" in reason

    def test_bare_rollout_blocked(self):
        allowed, _ = self.whitelist.validate_command(["rollout"])
        assert allowed is False
