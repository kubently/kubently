#!/usr/bin/env python3
"""
Command Analyzer for Kubently Agent.

Analyzes kubectl commands for safety, risk assessment, and pattern detection.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("kubently-agent.command-analyzer")


class RiskLevel(Enum):
    """Risk levels for commands."""

    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CommandCategory(Enum):
    """Categories of kubectl commands."""

    READ = "read"
    DEBUG = "debug"
    NETWORK = "network"
    EXEC = "exec"
    WRITE = "write"
    DELETE = "delete"
    AUTH = "auth"
    UNKNOWN = "unknown"


@dataclass
class CommandAnalysis:
    """Results of command analysis."""

    verb: str
    category: CommandCategory
    risk_level: RiskLevel
    resources: List[str]
    namespaces: List[str]
    flags: Dict[str, str]
    patterns: Set[str]
    warnings: List[str]
    suggestions: List[str]


class CommandAnalyzer:
    """Analyzes kubectl commands for safety and patterns."""

    # Command categorization
    COMMAND_CATEGORIES = {
        CommandCategory.READ: {
            "get",
            "describe",
            "list",
            "api-resources",
            "api-versions",
            "explain",
            "version",
            "cluster-info",
        },
        CommandCategory.DEBUG: {"logs", "top", "events", "debug"},
        CommandCategory.NETWORK: {"port-forward", "proxy"},
        CommandCategory.EXEC: {"exec", "attach", "cp", "run"},
        CommandCategory.WRITE: {
            "apply",
            "create",
            "patch",
            "label",
            "annotate",
            "set",
            "expose",
            "autoscale",
            "rollout",
        },
        CommandCategory.DELETE: {"delete", "drain", "cordon", "uncordon"},
        CommandCategory.AUTH: {"auth", "certificate", "config"},
    }

    # Risk assessment rules
    RISK_RULES = {
        RiskLevel.SAFE: {
            "verbs": {
                "get",
                "describe",
                "list",
                "api-resources",
                "api-versions",
                "explain",
                "version",
            },
            "exclude_resources": {"secrets", "configmaps"},
            "exclude_flags": set(),
        },
        RiskLevel.LOW: {
            "verbs": {"logs", "top", "events"},
            "exclude_resources": {"secrets"},
            "exclude_flags": {"--previous", "--since"},
        },
        RiskLevel.MEDIUM: {
            "verbs": {"port-forward", "cp"},
            "exclude_resources": set(),
            "exclude_flags": {"--address"},
        },
        RiskLevel.HIGH: {
            "verbs": {"exec", "attach", "run"},
            "exclude_resources": set(),
            "exclude_flags": set(),
        },
        RiskLevel.CRITICAL: {
            "verbs": {"delete", "apply", "create", "patch", "replace", "edit", "scale"},
            "exclude_resources": set(),
            "exclude_flags": set(),
        },
    }

    # Resource type patterns
    RESOURCE_PATTERNS = {
        "workload": re.compile(
            r"^(deployment|replicaset|statefulset|daemonset|job|cronjob)s?$", re.I
        ),
        "network": re.compile(r"^(service|endpoint|ingress|networkpolicy)s?$", re.I),
        "storage": re.compile(r"^(persistentvolume|persistentvolumeclaim|storageclass)s?$", re.I),
        "config": re.compile(r"^(configmap|secret)s?$", re.I),
        "auth": re.compile(
            r"^(serviceaccount|role|clusterrole|rolebinding|clusterrolebinding)s?$", re.I
        ),
        "core": re.compile(r"^(pod|node|namespace)s?$", re.I),
    }

    # Suspicious patterns
    SUSPICIOUS_PATTERNS = [
        (re.compile(r"--token[=\s]"), "Direct token usage"),
        (re.compile(r"--kubeconfig[=\s]"), "Custom kubeconfig"),
        (re.compile(r"--insecure"), "Insecure connection"),
        (re.compile(r"bash|sh|/bin/"), "Shell execution"),
        (re.compile(r"curl|wget"), "Network tools"),
        (re.compile(r"\.\.\/"), "Path traversal"),
        (re.compile(r"\$\(|\${|`"), "Command substitution"),
        (re.compile(r"&&|\|\||;"), "Command chaining"),
        (re.compile(r"sudo|su\s"), "Privilege escalation"),
        (re.compile(r"rm\s+-rf|rm\s+-fr"), "Dangerous deletion"),
    ]

    def __init__(self):
        """Initialize command analyzer."""
        self._verb_to_category = {}
        self._build_verb_map()

    def _build_verb_map(self) -> None:
        """Build reverse mapping from verb to category."""
        for category, verbs in self.COMMAND_CATEGORIES.items():
            for verb in verbs:
                self._verb_to_category[verb] = category

    def analyze(self, args: List[str]) -> CommandAnalysis:
        """
        Analyze kubectl command arguments.

        Args:
            args: kubectl command arguments

        Returns:
            CommandAnalysis object with analysis results
        """
        if not args:
            return self._empty_analysis()

        verb = args[0]
        category = self._categorize_command(verb)
        risk_level = self._assess_risk(verb, args)
        resources = self._extract_resources(args)
        namespaces = self._extract_namespaces(args)
        flags = self._extract_flags(args)
        patterns = self._detect_patterns(args)
        warnings = self._generate_warnings(verb, args, patterns)
        suggestions = self._generate_suggestions(verb, args)

        return CommandAnalysis(
            verb=verb,
            category=category,
            risk_level=risk_level,
            resources=resources,
            namespaces=namespaces,
            flags=flags,
            patterns=patterns,
            warnings=warnings,
            suggestions=suggestions,
        )

    def _empty_analysis(self) -> CommandAnalysis:
        """Return empty analysis for invalid commands."""
        return CommandAnalysis(
            verb="",
            category=CommandCategory.UNKNOWN,
            risk_level=RiskLevel.CRITICAL,
            resources=[],
            namespaces=[],
            flags={},
            patterns=set(),
            warnings=["Empty command"],
            suggestions=[],
        )

    def _categorize_command(self, verb: str) -> CommandCategory:
        """Categorize command by verb."""
        return self._verb_to_category.get(verb, CommandCategory.UNKNOWN)

    def _assess_risk(self, verb: str, args: List[str]) -> RiskLevel:
        """
        Assess risk level of command.

        Args:
            verb: kubectl verb
            args: Full command arguments

        Returns:
            Risk level
        """
        # Check each risk level from safest to most critical
        for level in [
            RiskLevel.SAFE,
            RiskLevel.LOW,
            RiskLevel.MEDIUM,
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        ]:
            rules = self.RISK_RULES[level]

            if verb in rules["verbs"]:
                # Check for excluded resources
                if rules["exclude_resources"]:
                    for arg in args[1:]:
                        if any(resource in arg.lower() for resource in rules["exclude_resources"]):
                            # Bump up risk level if accessing restricted resources
                            if level == RiskLevel.SAFE:
                                return RiskLevel.LOW
                            elif level == RiskLevel.LOW:
                                return RiskLevel.MEDIUM
                            else:
                                return level

                return level

        # Unknown commands are high risk by default
        return RiskLevel.HIGH

    def _extract_resources(self, args: List[str]) -> List[str]:
        """Extract resource types from command."""
        resources = []

        # Skip verb
        for i, arg in enumerate(args[1:], 1):
            # Skip flags
            if arg.startswith("-"):
                continue

            # Check if it matches resource patterns
            for resource_type, pattern in self.RESOURCE_PATTERNS.items():
                if pattern.match(arg):
                    resources.append(arg)
                    break
            else:
                # Could be a resource name or type
                if i == 1 and not "/" in arg:
                    # Likely a resource type
                    resources.append(arg)

        return resources

    def _extract_namespaces(self, args: List[str]) -> List[str]:
        """Extract namespaces from command."""
        namespaces = []

        for i, arg in enumerate(args):
            if arg in ["-n", "--namespace"] and i + 1 < len(args):
                namespaces.append(args[i + 1])
            elif arg.startswith("--namespace="):
                namespaces.append(arg.split("=", 1)[1])
            elif arg == "--all-namespaces":
                namespaces.append("*")

        return namespaces

    def _extract_flags(self, args: List[str]) -> Dict[str, str]:
        """Extract flags and their values."""
        flags = {}

        i = 1  # Skip verb
        while i < len(args):
            arg = args[i]

            if arg.startswith("--"):
                if "=" in arg:
                    # --flag=value format
                    key, value = arg.split("=", 1)
                    flags[key] = value
                else:
                    # --flag value format
                    if i + 1 < len(args) and not args[i + 1].startswith("-"):
                        flags[arg] = args[i + 1]
                        i += 1
                    else:
                        flags[arg] = "true"

            elif arg.startswith("-") and len(arg) == 2:
                # Short flag
                if i + 1 < len(args) and not args[i + 1].startswith("-"):
                    flags[arg] = args[i + 1]
                    i += 1
                else:
                    flags[arg] = "true"

            i += 1

        return flags

    def _detect_patterns(self, args: List[str]) -> Set[str]:
        """Detect suspicious patterns in command."""
        detected = set()

        command_str = " ".join(args)

        for pattern, description in self.SUSPICIOUS_PATTERNS:
            if pattern.search(command_str):
                detected.add(description)

        return detected

    def _generate_warnings(self, verb: str, args: List[str], patterns: Set[str]) -> List[str]:
        """Generate warnings based on analysis."""
        warnings = []

        # Warn about suspicious patterns
        if patterns:
            warnings.append(f"Suspicious patterns detected: {', '.join(patterns)}")

        # Warn about high-risk verbs
        if verb in self.RISK_RULES[RiskLevel.CRITICAL]["verbs"]:
            warnings.append(f"Critical operation '{verb}' requested")

        # Warn about sensitive resources
        sensitive_resources = {"secrets", "configmaps", "serviceaccounts"}
        for arg in args:
            if any(resource in arg.lower() for resource in sensitive_resources):
                warnings.append(f"Accessing sensitive resource: {arg}")

        # Warn about wide access
        if "--all-namespaces" in args or "-A" in args:
            warnings.append("Command targets all namespaces")

        # Warn about exec/attach to privileged containers
        if verb in ["exec", "attach"]:
            if any("privileged" in arg.lower() for arg in args):
                warnings.append("Attempting to access privileged container")

        return warnings

    def _generate_suggestions(self, verb: str, args: List[str]) -> List[str]:
        """Generate safety suggestions."""
        suggestions = []

        # Suggest namespace restriction
        if "--all-namespaces" in args or "-A" in args:
            suggestions.append("Consider restricting to specific namespace with -n")

        # Suggest read-only alternatives
        if verb in self.RISK_RULES[RiskLevel.HIGH]["verbs"]:
            suggestions.append(f"Consider using 'describe' or 'logs' instead of '{verb}'")

        # Suggest using labels
        if verb == "get" and len(args) > 2:
            if not any(arg.startswith("-l") or arg.startswith("--selector") for arg in args):
                suggestions.append("Consider using label selectors to limit scope")

        # Suggest timeout for long-running commands
        if verb in ["logs", "exec", "port-forward"]:
            if not any("timeout" in arg for arg in args):
                suggestions.append("Consider adding a timeout to prevent hanging")

        return suggestions

    def is_safe_for_mode(self, args: List[str], mode: str) -> Tuple[bool, Optional[str]]:
        """
        Check if command is safe for given security mode.

        Args:
            args: kubectl command arguments
            mode: Security mode (readOnly, extendedReadOnly, fullAccess)

        Returns:
            Tuple of (is_safe, reason_if_not)
        """
        analysis = self.analyze(args)

        if mode == "readOnly":
            if analysis.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                return (
                    False,
                    f"Command risk too high for read-only mode: {analysis.risk_level.value}",
                )
            if analysis.category in [
                CommandCategory.EXEC,
                CommandCategory.WRITE,
                CommandCategory.DELETE,
                CommandCategory.NETWORK,
            ]:
                return (
                    False,
                    f"Command category '{analysis.category.value}' not allowed in read-only mode",
                )

        elif mode == "extendedReadOnly":
            if analysis.risk_level == RiskLevel.CRITICAL:
                return False, f"Critical risk commands not allowed: {analysis.risk_level.value}"
            if analysis.category in [CommandCategory.WRITE, CommandCategory.DELETE]:
                return False, f"Write/Delete operations not allowed in extended read-only mode"

        # fullAccess allows everything except immutable forbidden patterns
        # (those are checked elsewhere)

        # Check for warnings that should block
        if analysis.warnings:
            for warning in analysis.warnings:
                if "Suspicious patterns detected" in warning:
                    return False, warning

        return True, None
