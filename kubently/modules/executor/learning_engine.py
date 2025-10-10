#!/usr/bin/env python3
"""
Learning Engine for Kubently Agent.

Learns from command patterns and provides intelligent suggestions for whitelist updates.
"""

import hashlib
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("kubently-agent.learning-engine")


@dataclass
class Pattern:
    """Represents a command pattern."""

    template: str
    verb: str
    resource_type: Optional[str]
    namespace_pattern: Optional[str]
    flags: Set[str]
    occurrences: int
    first_seen: datetime
    last_seen: datetime
    always_safe: bool
    confidence: float


@dataclass
class LearningSuggestion:
    """Suggestion for whitelist modification."""

    action: str  # "add_verb", "add_flag", "add_pattern", "increase_limit"
    target: str  # What to add/modify
    reason: str  # Why this suggestion
    confidence: float  # Confidence level (0-1)
    supporting_data: Dict  # Evidence for suggestion


class LearningEngine:
    """Learns from command patterns to suggest whitelist improvements."""

    # Pattern templates for generalization
    GENERALIZATION_RULES = [
        # Resource name patterns
        (re.compile(r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b"), "<UUID>"),
        (re.compile(r"\b\d{10,}\b"), "<TIMESTAMP>"),
        (re.compile(r"\bipv4:\d+\.\d+\.\d+\.\d+\b"), "ipv4:<IP>"),
        (re.compile(r"\b[a-z0-9]{10,}-[a-z0-9]{5}\b"), "<POD_NAME>"),
        (re.compile(r"\b[a-z]+-\d{5,}\b"), "<GENERATED_NAME>"),
        # Common Kubernetes patterns
        (re.compile(r"deployment\.apps/[\w-]+"), "deployment.apps/<NAME>"),
        (re.compile(r"pod/[\w-]+"), "pod/<NAME>"),
        (re.compile(r"service/[\w-]+"), "service/<NAME>"),
        (re.compile(r"configmap/[\w-]+"), "configmap/<NAME>"),
        # Namespace patterns
        (re.compile(r"-n\s+[\w-]+"), "-n <NAMESPACE>"),
        (re.compile(r"--namespace=[\w-]+"), "--namespace=<NAMESPACE>"),
    ]

    def __init__(self, store=None):
        """
        Initialize learning engine.

        Args:
            store: WhitelistStore instance for persistence
        """
        self.store = store
        self.patterns: Dict[str, Pattern] = {}
        self.verb_frequency = Counter()
        self.flag_frequency = Counter()
        self.resource_frequency = Counter()
        self.rejection_patterns = defaultdict(list)

    def learn_from_command(
        self,
        args: List[str],
        allowed: bool,
        rejection_reason: Optional[str] = None,
        analysis: Optional[Dict] = None,
    ) -> None:
        """
        Learn from a command execution attempt.

        Args:
            args: Command arguments
            allowed: Whether command was allowed
            rejection_reason: Reason if rejected
            analysis: Command analysis results
        """
        if not args:
            return

        verb = args[0]
        pattern_str = self._generalize_command(args)
        pattern_hash = hashlib.md5(pattern_str.encode()).hexdigest()

        # Update verb frequency
        self.verb_frequency[verb] += 1

        # Extract and count flags
        flags = self._extract_flags(args)
        for flag in flags:
            self.flag_frequency[flag] += 1

        # Extract resources
        resources = self._extract_resources(args)
        for resource in resources:
            self.resource_frequency[resource] += 1

        # Track rejection patterns
        if not allowed and rejection_reason:
            self.rejection_patterns[rejection_reason].append(
                {"verb": verb, "pattern": pattern_str, "timestamp": datetime.now()}
            )

        # Update or create pattern
        if pattern_hash in self.patterns:
            pattern = self.patterns[pattern_hash]
            pattern.occurrences += 1
            pattern.last_seen = datetime.now()
            pattern.always_safe = pattern.always_safe and allowed
        else:
            self.patterns[pattern_hash] = Pattern(
                template=pattern_str,
                verb=verb,
                resource_type=resources[0] if resources else None,
                namespace_pattern=self._extract_namespace_pattern(args),
                flags=flags,
                occurrences=1,
                first_seen=datetime.now(),
                last_seen=datetime.now(),
                always_safe=allowed,
                confidence=0.0,
            )

        # Persist to store if available
        if self.store:
            self.store.record_pattern(
                pattern=pattern_str,
                verb=verb,
                allowed=allowed,
                risk_assessment=analysis.get("risk_level") if analysis else None,
            )

    def _generalize_command(self, args: List[str]) -> str:
        """
        Generalize command to pattern template.

        Args:
            args: Command arguments

        Returns:
            Generalized pattern string
        """
        command_str = " ".join(args)

        # Apply generalization rules
        for pattern, replacement in self.GENERALIZATION_RULES:
            command_str = pattern.sub(replacement, command_str)

        return command_str

    def _extract_flags(self, args: List[str]) -> Set[str]:
        """Extract flags from command arguments."""
        flags = set()

        for arg in args[1:]:  # Skip verb
            if arg.startswith("-"):
                # Extract flag name without value
                if "=" in arg:
                    flag = arg.split("=")[0]
                else:
                    flag = arg
                flags.add(flag)

        return flags

    def _extract_resources(self, args: List[str]) -> List[str]:
        """Extract resource types from command."""
        resources = []

        # Common Kubernetes resource types
        resource_types = {
            "pod",
            "pods",
            "po",
            "service",
            "services",
            "svc",
            "deployment",
            "deployments",
            "deploy",
            "replicaset",
            "replicasets",
            "rs",
            "statefulset",
            "statefulsets",
            "sts",
            "daemonset",
            "daemonsets",
            "ds",
            "job",
            "jobs",
            "cronjob",
            "cronjobs",
            "cj",
            "configmap",
            "configmaps",
            "cm",
            "secret",
            "secrets",
            "ingress",
            "ingresses",
            "ing",
            "namespace",
            "namespaces",
            "ns",
            "node",
            "nodes",
            "no",
            "persistentvolume",
            "persistentvolumes",
            "pv",
            "persistentvolumeclaim",
            "persistentvolumeclaims",
            "pvc",
            "storageclass",
            "storageclasses",
            "sc",
            "serviceaccount",
            "serviceaccounts",
            "sa",
            "role",
            "roles",
            "rolebinding",
            "rolebindings",
            "clusterrole",
            "clusterroles",
            "clusterrolebinding",
            "clusterrolebindings",
        }

        for arg in args[1:]:  # Skip verb
            if arg.startswith("-"):
                continue

            # Check direct resource type
            if arg.lower() in resource_types:
                resources.append(arg.lower())

            # Check resource/name format
            if "/" in arg:
                resource_part = arg.split("/")[0].lower()
                if resource_part in resource_types or resource_part.endswith(".apps"):
                    resources.append(resource_part)

        return resources

    def _extract_namespace_pattern(self, args: List[str]) -> Optional[str]:
        """Extract namespace pattern from command."""
        for i, arg in enumerate(args):
            if arg in ["-n", "--namespace"] and i + 1 < len(args):
                return "<namespace>"
            elif arg.startswith("--namespace="):
                return "<namespace>"
            elif arg == "--all-namespaces":
                return "*"

        return None

    def get_suggestions(
        self, min_confidence: float = 0.7, min_occurrences: int = 5
    ) -> List[LearningSuggestion]:
        """
        Generate suggestions for whitelist improvements.

        Args:
            min_confidence: Minimum confidence threshold
            min_occurrences: Minimum pattern occurrences

        Returns:
            List of suggestions
        """
        suggestions = []

        # Analyze frequently rejected safe commands
        suggestions.extend(self._suggest_new_verbs(min_occurrences))
        suggestions.extend(self._suggest_new_flags(min_occurrences))
        suggestions.extend(self._suggest_limit_adjustments())
        suggestions.extend(self._suggest_mode_changes())

        # Filter by confidence
        suggestions = [s for s in suggestions if s.confidence >= min_confidence]

        # Sort by confidence
        suggestions.sort(key=lambda s: s.confidence, reverse=True)

        return suggestions

    def _suggest_new_verbs(self, min_occurrences: int) -> List[LearningSuggestion]:
        """Suggest new verbs to add to whitelist."""
        suggestions = []

        # Analyze rejection patterns for verb-related rejections
        verb_rejections = self.rejection_patterns.get("Verb not allowed", [])

        if verb_rejections:
            verb_counts = Counter(r["verb"] for r in verb_rejections)

            for verb, count in verb_counts.items():
                if count >= min_occurrences:
                    # Check if verb is consistently safe in patterns
                    safe_patterns = sum(
                        1 for p in self.patterns.values() if p.verb == verb and p.always_safe
                    )

                    if safe_patterns > 0:
                        confidence = min(safe_patterns / (safe_patterns + count), 0.95)

                        suggestions.append(
                            LearningSuggestion(
                                action="add_verb",
                                target=verb,
                                reason=f"Verb '{verb}' rejected {count} times but appears safe",
                                confidence=confidence,
                                supporting_data={
                                    "rejection_count": count,
                                    "safe_pattern_count": safe_patterns,
                                    "first_seen": min(
                                        r["timestamp"] for r in verb_rejections if r["verb"] == verb
                                    ),
                                },
                            )
                        )

        return suggestions

    def _suggest_new_flags(self, min_occurrences: int) -> List[LearningSuggestion]:
        """Suggest new flags to add to whitelist."""
        suggestions = []

        # Analyze frequently used flags that might be missing
        for flag, count in self.flag_frequency.most_common(20):
            if count >= min_occurrences:
                # Check if flag appears in safe patterns
                safe_uses = sum(
                    1 for p in self.patterns.values() if flag in p.flags and p.always_safe
                )

                if safe_uses > count * 0.8:  # 80% safe usage
                    confidence = min(safe_uses / count, 0.9)

                    suggestions.append(
                        LearningSuggestion(
                            action="add_flag",
                            target=flag,
                            reason=f"Flag '{flag}' used {count} times with {safe_uses} safe uses",
                            confidence=confidence,
                            supporting_data={
                                "total_uses": count,
                                "safe_uses": safe_uses,
                            },
                        )
                    )

        return suggestions

    def _suggest_limit_adjustments(self) -> List[LearningSuggestion]:
        """Suggest adjustments to limits (timeout, max args)."""
        suggestions = []

        # Analyze patterns for commands that might need longer timeouts
        long_running_verbs = {"logs", "exec", "port-forward", "proxy"}

        for pattern in self.patterns.values():
            if pattern.verb in long_running_verbs and pattern.occurrences > 10:
                suggestions.append(
                    LearningSuggestion(
                        action="increase_limit",
                        target="timeoutSeconds",
                        reason=f"Verb '{pattern.verb}' frequently used and may need longer timeout",
                        confidence=0.75,
                        supporting_data={
                            "verb": pattern.verb,
                            "occurrences": pattern.occurrences,
                        },
                    )
                )
                break  # Only suggest once

        return suggestions

    def _suggest_mode_changes(self) -> List[LearningSuggestion]:
        """Suggest security mode changes based on usage patterns."""
        suggestions = []

        # Check if extended commands are frequently attempted
        extended_verbs = {"port-forward", "exec"}
        extended_attempts = sum(self.verb_frequency.get(verb, 0) for verb in extended_verbs)

        if extended_attempts > 20:
            suggestions.append(
                LearningSuggestion(
                    action="change_mode",
                    target="extendedReadOnly",
                    reason=f"Extended commands attempted {extended_attempts} times",
                    confidence=0.8,
                    supporting_data={
                        "extended_attempts": extended_attempts,
                        "verbs": list(extended_verbs),
                    },
                )
            )

        return suggestions

    def calculate_pattern_confidence(self, pattern: Pattern) -> float:
        """
        Calculate confidence score for a pattern.

        Args:
            pattern: Pattern to evaluate

        Returns:
            Confidence score (0-1)
        """
        factors = []

        # Factor 1: Occurrence frequency
        occurrence_score = min(pattern.occurrences / 100, 1.0)
        factors.append(occurrence_score * 0.3)

        # Factor 2: Time consistency
        days_active = (pattern.last_seen - pattern.first_seen).days
        time_score = min(days_active / 30, 1.0)
        factors.append(time_score * 0.2)

        # Factor 3: Safety record
        safety_score = 1.0 if pattern.always_safe else 0.5
        factors.append(safety_score * 0.3)

        # Factor 4: Verb safety
        safe_verbs = {"get", "describe", "logs", "list", "explain"}
        verb_score = 1.0 if pattern.verb in safe_verbs else 0.7
        factors.append(verb_score * 0.2)

        return sum(factors)

    def export_learning_data(self) -> Dict:
        """
        Export learning data for analysis.

        Returns:
            Dictionary with learning statistics
        """
        return {
            "total_patterns": len(self.patterns),
            "safe_patterns": sum(1 for p in self.patterns.values() if p.always_safe),
            "top_verbs": dict(self.verb_frequency.most_common(10)),
            "top_flags": dict(self.flag_frequency.most_common(10)),
            "top_resources": dict(self.resource_frequency.most_common(10)),
            "rejection_reasons": {
                reason: len(patterns) for reason, patterns in self.rejection_patterns.items()
            },
            "suggestions": [
                {
                    "action": s.action,
                    "target": s.target,
                    "reason": s.reason,
                    "confidence": s.confidence,
                }
                for s in self.get_suggestions()
            ],
        }
