"""Tests for the change-correlation aggregation/formatting logic (changes.py)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from kubently.modules.a2a.protocol_bindings.a2a_server import changes

NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


def ts(minutes_ago: int) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestParseWindow:
    def test_units(self):
        assert changes.parse_window("30m") == timedelta(minutes=30)
        assert changes.parse_window("6h") == timedelta(hours=6)
        assert changes.parse_window("2d") == timedelta(days=2)

    def test_invalid_falls_back_to_default(self):
        assert changes.parse_window("soon") == timedelta(hours=24)
        assert changes.parse_window(None) == timedelta(hours=24)
        assert changes.parse_window("") == timedelta(hours=24)


class TestParseTimestamp:
    def test_kubectl_z_suffix(self):
        parsed = changes.parse_timestamp("2026-08-17T10:30:00Z")
        assert parsed == datetime(2026, 8, 17, 10, 30, tzinfo=timezone.utc)

    def test_helm_fractional_seconds_with_offset(self):
        parsed = changes.parse_timestamp("2026-08-17T06:30:00.123456789-04:00")
        assert parsed == datetime(2026, 8, 17, 10, 30, tzinfo=timezone.utc)

    def test_invalid(self):
        assert changes.parse_timestamp("not-a-date") is None
        assert changes.parse_timestamp(None) is None
        assert changes.parse_timestamp(12345) is None


class TestExtractWorkloads:
    def _workload(self, name, annotations=None, labels=None):
        return {
            "kind": "Deployment",
            "metadata": {"name": name, "annotations": annotations or {}, "labels": labels or {}},
            "spec": {"template": {"spec": {"containers": [{"image": "nginx:1.25"}]}}},
        }

    def test_helm_annotations(self):
        body = {"items": [self._workload("api", annotations={
            "meta.helm.sh/release-name": "api-release",
            "meta.helm.sh/release-namespace": "prod",
            "deployment.kubernetes.io/revision": "7",
        })]}
        [info] = changes.extract_workloads(json.dumps(body))
        assert info.helm_release == "api-release"
        assert info.helm_namespace == "prod"
        assert info.revision == "7"
        assert info.images == ["nginx:1.25"]

    def test_argocd_tracking_annotation_wins(self):
        body = {"items": [self._workload("api", annotations={
            "argocd.argoproj.io/tracking-id": "shop-app:apps/Deployment:prod/api",
        }, labels={"app.kubernetes.io/instance": "other-name"})]}
        [info] = changes.extract_workloads(json.dumps(body))
        assert info.argocd_app == "shop-app"

    def test_instance_label_ignored_when_helm_managed(self):
        body = {"items": [self._workload("api", labels={
            "app.kubernetes.io/instance": "api-release",
            "app.kubernetes.io/managed-by": "Helm",
        })]}
        [info] = changes.extract_workloads(json.dumps(body))
        assert info.argocd_app is None

    def test_instance_label_used_when_not_helm(self):
        body = {"items": [self._workload("api", labels={"app.kubernetes.io/instance": "shop-app"})]}
        [info] = changes.extract_workloads(json.dumps(body))
        assert info.argocd_app == "shop-app"

    def test_single_object_and_garbage(self):
        [info] = changes.extract_workloads(json.dumps(self._workload("solo")))
        assert info.name == "solo"
        assert changes.extract_workloads("not json") == []
        assert changes.extract_workloads(None) == []


class TestReplicasetChanges:
    def _rs(self, name, owner, revision, created, replicas=1, cause=None):
        annotations = {"deployment.kubernetes.io/revision": revision}
        if cause:
            annotations["kubernetes.io/change-cause"] = cause
        return {
            "metadata": {
                "name": name,
                "annotations": annotations,
                "creationTimestamp": created,
                "ownerReferences": [{"kind": "Deployment", "name": owner}],
            },
            "spec": {
                "replicas": replicas,
                "template": {"spec": {"containers": [{"image": f"app:{revision}"}]}},
            },
        }

    def test_basic_and_owner_filter(self):
        body = {"items": [
            self._rs("api-abc", "api", "2", ts(30)),
            self._rs("web-def", "web", "5", ts(10)),
        ]}
        entries = changes.replicaset_changes(json.dumps(body))
        assert len(entries) == 2
        scoped = changes.replicaset_changes(json.dumps(body), deployment="api")
        assert len(scoped) == 1
        assert "revision 2" in scoped[0].summary
        assert "app:2" in scoped[0].summary
        assert scoped[0].timestamp is not None

    def test_scaled_to_zero_and_change_cause(self):
        body = {"items": [self._rs("api-old", "api", "1", ts(120), replicas=0, cause="kubectl set image")]}
        [entry] = changes.replicaset_changes(json.dumps(body))
        assert "scaled to 0" in entry.summary
        assert "kubectl set image" in entry.summary

    def test_rs_without_revision_annotation_skipped(self):
        body = {"items": [{"metadata": {"name": "bare", "annotations": {}}}]}
        assert changes.replicaset_changes(json.dumps(body)) == []


class TestParseRolloutHistory:
    OUTPUT = """deployment.apps/api
REVISION  CHANGE-CAUSE
1         <none>
2         kubectl apply --filename=deploy.yaml
3         image bumped to v2
"""

    def test_skips_none_causes(self):
        entries = changes.parse_rollout_history(self.OUTPUT, "deployment/api")
        assert len(entries) == 2
        assert entries[0].summary == "deployment/api revision 2 change-cause: kubectl apply --filename=deploy.yaml"
        assert entries[0].timestamp is None

    def test_empty(self):
        assert changes.parse_rollout_history("", "deployment/api") == []


class TestEventChanges:
    def _event(self, name, kind="Pod", reason="Started", etype="Normal", when=None, count=1, message="msg"):
        return {
            "type": etype,
            "reason": reason,
            "message": message,
            "count": count,
            "lastTimestamp": when or ts(5),
            "involvedObject": {"kind": kind, "name": name},
            "metadata": {"creationTimestamp": when or ts(5)},
        }

    def test_prefix_scoping_covers_ownership_chain(self):
        body = {"items": [
            self._event("api-7d9f8-x2v", kind="Pod"),
            self._event("api-7d9f8", kind="ReplicaSet", reason="SuccessfulCreate"),
            self._event("api", kind="Deployment", reason="ScalingReplicaSet"),
            self._event("api-gateway", kind="Deployment"),  # 'api-' prefix: included by design
            self._event("web-5c6d7-abc", kind="Pod"),
        ]}
        entries = changes.event_changes(json.dumps(body), name_prefixes=["api"])
        names = "\n".join(e.summary for e in entries)
        assert "web-5c6d7" not in names
        assert len(entries) == 4

    def test_unscoped_includes_all(self):
        body = {"items": [self._event("a"), self._event("b")]}
        assert len(changes.event_changes(json.dumps(body))) == 2

    def test_count_and_message_trim(self):
        body = {"items": [self._event("api", count=17, message="x" * 500)]}
        [entry] = changes.event_changes(json.dumps(body))
        assert "(x17)" in entry.summary
        assert "x" * 161 not in entry.summary

    def test_event_time_fallback(self):
        event = self._event("api")
        del event["lastTimestamp"]
        event["eventTime"] = ts(3)
        [entry] = changes.event_changes(json.dumps({"items": [event]}))
        assert entry.timestamp is not None


class TestHelmHistory:
    HISTORY = json.dumps([
        {"revision": 41, "updated": "2026-08-17T09:00:00.123-04:00", "status": "superseded",
         "chart": "api-1.2.2", "description": "Upgrade complete"},
        {"revision": 42, "updated": "2026-08-17T11:58:00.9-04:00", "status": "deployed",
         "chart": "api-1.2.3", "description": "Upgrade complete"},
    ])

    def test_entries(self):
        entries = changes.helm_history_changes(self.HISTORY, "api-release")
        assert len(entries) == 2
        assert "helm release api-release revision 42 [deployed] chart api-1.2.3" in entries[1].summary
        assert entries[1].timestamp.tzinfo is not None

    def test_garbage(self):
        assert changes.helm_history_changes("not json", "r") == []
        assert changes.helm_history_changes(json.dumps({"a": 1}), "r") == []

    def test_parse_releases(self):
        listing = json.dumps([
            {"name": "api", "namespace": "prod", "updated": ts(10)},
            {"name": "web", "namespace": "prod", "updated": ts(20)},
            {"namespace": "prod"},  # nameless: skipped
        ])
        assert changes.parse_helm_releases(listing) == [
            ("api", "prod", ts(10)), ("web", "prod", ts(20)),
        ]


class TestArgoCDChanges:
    def test_history_and_drift(self):
        app = json.dumps({
            "name": "shop-app",
            "syncStatus": "OutOfSync",
            "healthStatus": "Degraded",
            "lastOperation": {"finishedAt": ts(2)},
            "history": [
                {"id": 7, "revision": "abc123def456789", "deployedAt": ts(60),
                 "source": {"targetRevision": "main"}},
            ],
        })
        entries = changes.argocd_changes(app)
        assert len(entries) == 2
        assert "sync #7 deployed revision abc123def456" in entries[0].summary
        assert "OutOfSync" in entries[1].summary

    def test_synced_app_adds_no_drift_entry(self):
        app = json.dumps({"name": "ok-app", "syncStatus": "Synced", "history": []})
        assert changes.argocd_changes(app) == []

    def test_garbage(self):
        assert changes.argocd_changes("not json") == []


class TestBuildTimeline:
    def _entry(self, minutes_ago, source="event", summary="something"):
        return changes.ChangeEntry(
            timestamp=NOW - timedelta(minutes=minutes_ago), source=source, summary=summary
        )

    def test_sorted_oldest_first_within_window(self):
        timeline = changes.build_timeline(
            [self._entry(5, summary="newest"), self._entry(90, summary="oldest")],
            timedelta(hours=2), "ns prod", now=NOW,
        )
        assert timeline.index("oldest") < timeline.index("newest")
        assert "Changes timeline: ns prod (last 2h" in timeline

    def test_window_filtering_notes_older_entries(self):
        timeline = changes.build_timeline(
            [self._entry(5), self._entry(600, summary="ancient")],
            timedelta(hours=1), "x", now=NOW,
        )
        assert "ancient" not in timeline
        assert "1 entries predate the window" in timeline

    def test_undated_section(self):
        undated = changes.ChangeEntry(timestamp=None, source="rollout", summary="rev 3 cause: x")
        timeline = changes.build_timeline([undated], timedelta(hours=1), "x", now=NOW)
        assert "Known changes without timestamps" in timeline
        assert "rev 3 cause: x" in timeline

    def test_truncation_keeps_most_recent(self):
        entries = [self._entry(i, summary=f"e{i}") for i in range(80)]
        timeline = changes.build_timeline(entries, timedelta(hours=2), "x", now=NOW)
        assert "e0" in timeline  # most recent kept
        assert "e79" not in timeline  # oldest dropped
        assert "20 older in-window entries omitted" in timeline

    def test_empty(self):
        timeline = changes.build_timeline([], timedelta(hours=1), "x", now=NOW)
        assert "No changes found in this window" in timeline

    def test_unavailable_sources_and_correlation_instruction(self):
        timeline = changes.build_timeline(
            [], timedelta(hours=1), "x",
            sources_unavailable={"helm history": "HELM_HISTORY_ENABLED is not 'true'"},
            now=NOW,
        )
        assert "helm history unavailable" in timeline
        assert "first-error" in timeline
        assert "events expire" in timeline


class TestArgoCDEnabled:
    def test_env_gate(self, monkeypatch):
        monkeypatch.delenv("ARGOCD_URL", raising=False)
        assert changes.argocd_enabled() is False
        monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.com")
        assert changes.argocd_enabled() is True
        monkeypatch.setenv("ARGOCD_URL", "   ")
        assert changes.argocd_enabled() is False
