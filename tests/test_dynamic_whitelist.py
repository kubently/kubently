#!/usr/bin/env python3
"""
Tests for Dynamic Command Whitelist System.
"""

import os

# Import the modules to test
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.executor.command_analyzer import CommandAnalyzer, CommandCategory, RiskLevel
from kubently.modules.executor.dynamic_whitelist import (
    DynamicCommandWhitelist,
    SecurityMode,
    WhitelistConfig,
)
from kubently.modules.executor.learning_engine import LearningEngine, LearningSuggestion, Pattern


class TestDynamicCommandWhitelist:
    """Test DynamicCommandWhitelist class."""

    def setup_method(self):
        """Setup test fixtures."""
        # Create temporary config file
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "whitelist.yaml")

    def teardown_method(self):
        """Cleanup test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_config(self, config_dict):
        """Helper to write config to file."""
        with open(self.config_path, "w") as f:
            yaml.safe_dump(config_dict, f)

    def test_load_default_config(self):
        """Test loading default configuration when file doesn't exist."""
        whitelist = DynamicCommandWhitelist(config_path="/nonexistent/path")

        assert whitelist.current_config is not None
        assert whitelist.current_config.mode == SecurityMode.READ_ONLY
        assert "get" in whitelist.current_config.allowed_verbs
        assert "delete" not in whitelist.current_config.allowed_verbs

    def test_load_custom_config(self):
        """Test loading custom configuration from file."""
        config = {
            "mode": "extendedReadOnly",
            "commands": {
                "allowedVerbs": ["get", "describe", "port-forward"],
                "allowedFlags": ["--namespace", "--port"],
                "forbiddenPatterns": ["--dangerous"],
            },
            "limits": {
                "maxArguments": 30,
                "timeoutSeconds": 60,
            },
        }
        self._write_config(config)

        whitelist = DynamicCommandWhitelist(config_path=self.config_path)

        assert whitelist.current_config.mode == SecurityMode.EXTENDED_READ_ONLY
        assert "port-forward" in whitelist.current_config.allowed_verbs
        assert "--port" in whitelist.current_config.allowed_flags
        assert whitelist.current_config.max_arguments == 30
        assert whitelist.current_config.timeout_seconds == 60

    def test_validate_command_allowed(self):
        """Test validation of allowed commands."""
        config = {
            "mode": "readOnly",
            "commands": {
                "allowedVerbs": ["get", "describe"],
            },
        }
        self._write_config(config)

        whitelist = DynamicCommandWhitelist(config_path=self.config_path)

        # Test allowed command
        is_valid, reason = whitelist.validate_command(["get", "pods"])
        assert is_valid is True
        assert reason is None

        # Test with namespace flag
        is_valid, reason = whitelist.validate_command(["get", "pods", "-n", "default"])
        assert is_valid is True

    def test_validate_command_blocked_verb(self):
        """Test validation blocks unauthorized verbs."""
        config = {
            "mode": "readOnly",
            "commands": {
                "allowedVerbs": ["get", "describe"],
            },
        }
        self._write_config(config)

        whitelist = DynamicCommandWhitelist(config_path=self.config_path)

        # Test blocked verb
        is_valid, reason = whitelist.validate_command(["delete", "pod", "test"])
        assert is_valid is False
        assert "not allowed" in reason

    def test_validate_command_forbidden_pattern(self):
        """Test validation blocks forbidden patterns."""
        config = {
            "mode": "readOnly",
            "commands": {
                "allowedVerbs": ["get"],
            },
        }
        self._write_config(config)

        whitelist = DynamicCommandWhitelist(config_path=self.config_path)

        # Test immutable forbidden pattern
        is_valid, reason = whitelist.validate_command(["get", "pods", "--token=secret"])
        assert is_valid is False
        assert "Forbidden pattern" in reason

    def test_validate_command_restricted_resources(self):
        """Test validation blocks restricted resources."""
        config = {
            "mode": "readOnly",
            "commands": {
                "allowedVerbs": ["get"],
                "restrictedResources": ["secrets", "configmaps"],
            },
        }
        self._write_config(config)

        whitelist = DynamicCommandWhitelist(config_path=self.config_path)

        # Test restricted resource
        is_valid, reason = whitelist.validate_command(["get", "secrets"])
        assert is_valid is False
        assert "restricted" in reason

    def test_security_modes(self):
        """Test different security modes have correct defaults."""
        # Test READ_ONLY mode
        config = {"mode": "readOnly"}
        self._write_config(config)
        whitelist = DynamicCommandWhitelist(config_path=self.config_path)

        assert "get" in whitelist.current_config.allowed_verbs
        assert "exec" not in whitelist.current_config.allowed_verbs
        assert "port-forward" not in whitelist.current_config.allowed_verbs

        # Test EXTENDED_READ_ONLY mode
        config = {"mode": "extendedReadOnly"}
        self._write_config(config)
        whitelist = DynamicCommandWhitelist(config_path=self.config_path)

        assert "get" in whitelist.current_config.allowed_verbs
        assert "exec" in whitelist.current_config.allowed_verbs
        assert "port-forward" in whitelist.current_config.allowed_verbs

        # Test FULL_ACCESS mode
        config = {"mode": "fullAccess"}
        self._write_config(config)
        whitelist = DynamicCommandWhitelist(config_path=self.config_path)

        assert "get" in whitelist.current_config.allowed_verbs
        assert "exec" in whitelist.current_config.allowed_verbs
        assert "cp" in whitelist.current_config.allowed_verbs
        assert "proxy" in whitelist.current_config.allowed_verbs

    def test_config_validation_rejects_dangerous_verbs(self):
        """Test config validation rejects dangerous verbs in safe modes."""
        config = {
            "mode": "readOnly",
            "commands": {
                "allowedVerbs": ["get", "delete"],  # delete should be rejected
            },
        }
        self._write_config(config)

        whitelist = DynamicCommandWhitelist(config_path=self.config_path)

        # Should fall back to safe defaults
        assert "delete" not in whitelist.current_config.allowed_verbs
        assert "get" in whitelist.current_config.allowed_verbs


class TestCommandAnalyzer:
    """Test CommandAnalyzer class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.analyzer = CommandAnalyzer()

    def test_analyze_safe_command(self):
        """Test analysis of safe read-only command."""
        args = ["get", "pods", "-n", "default"]
        analysis = self.analyzer.analyze(args)

        assert analysis.verb == "get"
        assert analysis.category == CommandCategory.READ
        assert analysis.risk_level == RiskLevel.SAFE
        assert "default" in analysis.namespaces
        assert "-n" in analysis.flags

    def test_analyze_debug_command(self):
        """Test analysis of debug command."""
        args = ["logs", "pod/test", "--follow", "--tail=100"]
        analysis = self.analyzer.analyze(args)

        assert analysis.verb == "logs"
        assert analysis.category == CommandCategory.DEBUG
        assert analysis.risk_level == RiskLevel.LOW
        assert "--follow" in analysis.flags
        assert "--tail" in analysis.flags

    def test_analyze_high_risk_command(self):
        """Test analysis of high-risk command."""
        args = ["exec", "-it", "pod/test", "--", "/bin/bash"]
        analysis = self.analyzer.analyze(args)

        assert analysis.verb == "exec"
        assert analysis.category == CommandCategory.EXEC
        assert analysis.risk_level == RiskLevel.HIGH
        assert len(analysis.warnings) > 0

    def test_analyze_critical_command(self):
        """Test analysis of critical command."""
        args = ["delete", "deployment", "critical-app"]
        analysis = self.analyzer.analyze(args)

        assert analysis.verb == "delete"
        assert analysis.category == CommandCategory.DELETE
        assert analysis.risk_level == RiskLevel.CRITICAL
        assert len(analysis.warnings) > 0

    def test_detect_suspicious_patterns(self):
        """Test detection of suspicious patterns."""
        args = ["get", "pods", "--token=secret123"]
        analysis = self.analyzer.analyze(args)

        assert len(analysis.patterns) > 0
        assert any("token" in pattern.lower() for pattern in analysis.patterns)

    def test_extract_resources(self):
        """Test resource extraction from commands."""
        args = ["get", "deployment.apps/nginx", "-n", "production"]
        analysis = self.analyzer.analyze(args)

        assert len(analysis.resources) > 0
        assert "production" in analysis.namespaces

    def test_is_safe_for_mode(self):
        """Test safety check for different modes."""
        # Safe command for all modes
        args = ["get", "pods"]
        is_safe, reason = self.analyzer.is_safe_for_mode(args, "readOnly")
        assert is_safe is True

        # Exec not safe for readOnly
        args = ["exec", "pod/test", "ls"]
        is_safe, reason = self.analyzer.is_safe_for_mode(args, "readOnly")
        assert is_safe is False
        assert "not allowed in read-only mode" in reason

        # Exec safe for extendedReadOnly
        is_safe, reason = self.analyzer.is_safe_for_mode(args, "extendedReadOnly")
        assert is_safe is True

        # Delete not safe for extendedReadOnly
        args = ["delete", "pod", "test"]
        is_safe, reason = self.analyzer.is_safe_for_mode(args, "readOnly")
        assert is_safe is False


class TestLearningEngine:
    """Test LearningEngine class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.engine = LearningEngine()

    def test_learn_from_command(self):
        """Test learning from command execution."""
        # Learn from multiple similar commands
        for i in range(10):
            self.engine.learn_from_command(
                args=["get", f"pod/test-{i}", "-n", "default"], allowed=True
            )

        assert self.engine.verb_frequency["get"] == 10
        assert "-n" in self.engine.flag_frequency

    def test_pattern_generalization(self):
        """Test command pattern generalization."""
        args = ["get", "pod/nginx-7d9f8b6d5-xk2lp", "-n", "production"]
        pattern_str = self.engine._generalize_command(args)

        # Should generalize pod name
        assert "<" in pattern_str and ">" in pattern_str

    def test_learn_rejection_patterns(self):
        """Test learning from rejected commands."""
        # Learn from rejected commands
        for i in range(5):
            self.engine.learn_from_command(
                args=["delete", f"pod/test-{i}"], allowed=False, rejection_reason="Verb not allowed"
            )

        assert "Verb not allowed" in self.engine.rejection_patterns
        assert len(self.engine.rejection_patterns["Verb not allowed"]) == 5

    def test_generate_suggestions(self):
        """Test generation of whitelist suggestions."""
        # Simulate learning from commands
        for i in range(20):
            self.engine.learn_from_command(
                args=["port-forward", f"pod/test-{i}", "8080:80"],
                allowed=False,
                rejection_reason="Verb not allowed",
            )

        # Add some safe patterns
        for i in range(10):
            pattern_hash = f"pattern-{i}"
            self.engine.patterns[pattern_hash] = Pattern(
                template="port-forward pod/<NAME> 8080:80",
                verb="port-forward",
                resource_type="pod",
                namespace_pattern=None,
                flags=set(),
                occurrences=10,
                first_seen=datetime.now() - timedelta(days=10),
                last_seen=datetime.now(),
                always_safe=True,
                confidence=0.8,
            )

        suggestions = self.engine.get_suggestions(min_confidence=0.5, min_occurrences=5)

        # Should suggest adding port-forward verb
        verb_suggestions = [s for s in suggestions if s.action == "add_verb"]
        assert len(verb_suggestions) > 0

    def test_calculate_pattern_confidence(self):
        """Test pattern confidence calculation."""
        pattern = Pattern(
            template="get pods",
            verb="get",
            resource_type="pods",
            namespace_pattern=None,
            flags=set(),
            occurrences=50,
            first_seen=datetime.now() - timedelta(days=30),
            last_seen=datetime.now(),
            always_safe=True,
            confidence=0.0,
        )

        confidence = self.engine.calculate_pattern_confidence(pattern)

        # Should have high confidence for safe, frequent pattern
        assert confidence > 0.7

    def test_export_learning_data(self):
        """Test export of learning statistics."""
        # Add some test data
        self.engine.verb_frequency["get"] = 100
        self.engine.verb_frequency["describe"] = 50
        self.engine.flag_frequency["-n"] = 75

        data = self.engine.export_learning_data()

        assert "top_verbs" in data
        assert "get" in data["top_verbs"]
        assert data["top_verbs"]["get"] == 100
        assert "top_flags" in data
        assert "-n" in data["top_flags"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
