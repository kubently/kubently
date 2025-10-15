#!/usr/bin/env python3
"""
LangSmith Dataset Builder for Kubently
Converts existing test scenarios into LangSmith datasets for experimentation
"""

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import uuid

from langsmith import Client
from langsmith.schemas import Dataset, Example


@dataclass
class KubentlyScenario:
    """Represents a Kubently test scenario."""
    name: str
    namespace: str
    expected_fix: str
    query: str
    setup_script: str
    validation_checks: List[str]
    metadata: Dict[str, Any]


class DatasetBuilder:
    """Builds LangSmith datasets from Kubently test scenarios."""

    def __init__(self, langsmith_api_key: Optional[str] = None):
        """Initialize the dataset builder."""
        self.langsmith_api_key = langsmith_api_key or os.getenv("LANGSMITH_API_KEY")
        if not self.langsmith_api_key:
            raise ValueError("LANGSMITH_API_KEY environment variable is required")

        self.client = Client(api_key=self.langsmith_api_key)
        self.scenarios_dir = Path(__file__).parent.parent / "test-automation" / "scenarios"

    def parse_scenario_file(self, file_path: Path) -> Optional[KubentlyScenario]:
        """Parse a scenario shell script to extract metadata."""
        try:
            content = file_path.read_text()

            # Extract namespace
            namespace_match = re.search(r'NAMESPACE="?([^"\s]+)"?', content)
            if not namespace_match:
                namespace_match = re.search(r'create\s+namespace\s+([^\s]+)', content)

            if namespace_match:
                namespace = namespace_match.group(1)
            else:
                scenario_num = re.search(r'^(\d+)-', file_path.stem)
                if scenario_num:
                    namespace = f"test-ns-{scenario_num.group(1)}"
                else:
                    namespace = f"test-ns-{file_path.stem[:8]}"

            # Extract expected fix
            expected_fix = "Unknown"
            for line in content.split('\n'):
                if 'Expected fix:' in line or 'expected fix:' in line or 'THE FIX:' in line:
                    expected_fix = line.split(':', 1)[1].strip()
                    break

            # Extract validation checks (commands that verify the fix)
            validation_checks = []
            in_validation = False
            for line in content.split('\n'):
                if 'validation' in line.lower() or 'verify' in line.lower():
                    in_validation = True
                if in_validation and 'kubectl' in line:
                    validation_checks.append(line.strip())

            # Create query based on scenario
            name = file_path.stem
            if "imagepull" in name.lower():
                query = f"In cluster kind, there's an issue with a pod in namespace {namespace}. The pod is showing ImagePullBackOff status. Please investigate and fix the issue."
                scenario_type = "image_pull"
            elif "crash" in name.lower():
                query = f"In cluster kind, a pod in namespace {namespace} is in CrashLoopBackOff. Please diagnose the issue."
                scenario_type = "crash_loop"
            elif "service" in name.lower():
                query = f"In cluster kind, the service in namespace {namespace} doesn't seem to be working. Traffic isn't reaching the pods. Please investigate."
                scenario_type = "service_issue"
            elif "rbac" in name.lower():
                query = f"In cluster kind, a pod in namespace {namespace} is encountering authorization issues. Please diagnose and make recommendations."
                scenario_type = "rbac"
            elif "cross-namespace" in name.lower() or "namespace-a" in content:
                query = "In cluster kind, there are connectivity issues between namespaces. Please investigate and identify the root cause."
                scenario_type = "cross_namespace"
            elif "oom" in name.lower():
                query = f"In cluster kind, a pod in namespace {namespace} keeps getting OOMKilled. Please investigate and fix the issue."
                scenario_type = "memory"
            elif "probe" in name.lower():
                query = f"In cluster kind, a pod in namespace {namespace} is failing its health checks. Please investigate."
                scenario_type = "health_probe"
            elif "network" in name.lower():
                query = f"In cluster kind, there are network connectivity issues in namespace {namespace}. Please investigate."
                scenario_type = "network"
            elif "permission" in name.lower() or "rbac" in name.lower():
                query = f"In cluster kind, a service account in namespace {namespace} is having permission issues. Please investigate."
                scenario_type = "permissions"
            elif "configmap" in name.lower():
                query = f"In cluster kind, a pod in namespace {namespace} is having configuration issues. Please investigate."
                scenario_type = "configuration"
            elif "secret" in name.lower():
                query = f"In cluster kind, a pod in namespace {namespace} is having issues with secrets. Please investigate."
                scenario_type = "secrets"
            elif "volume" in name.lower() or "pvc" in name.lower():
                query = f"In cluster kind, a pod in namespace {namespace} is having volume mount issues. Please investigate."
                scenario_type = "storage"
            elif "resource" in name.lower() or "limit" in name.lower():
                query = f"In cluster kind, a pod in namespace {namespace} is having resource constraint issues. Please investigate."
                scenario_type = "resources"
            elif "dns" in name.lower():
                query = f"In cluster kind, there are DNS resolution issues in namespace {namespace}. Please investigate."
                scenario_type = "dns"
            elif "ingress" in name.lower():
                query = f"In cluster kind, the ingress in namespace {namespace} is not working correctly. Please investigate."
                scenario_type = "ingress"
            else:
                query = f"In cluster kind, there's an issue in namespace {namespace}. Please investigate and identify the root cause."
                scenario_type = "general"

            # Extract metadata
            metadata = {
                "file_path": str(file_path),
                "scenario_type": scenario_type,
                "difficulty": self._estimate_difficulty(name, expected_fix),
                "required_tools": self._extract_required_tools(expected_fix),
                "namespace": namespace
            }

            return KubentlyScenario(
                name=name,
                namespace=namespace,
                expected_fix=expected_fix,
                query=query,
                setup_script=content,
                validation_checks=validation_checks,
                metadata=metadata
            )

        except Exception as e:
            print(f"Failed to parse scenario {file_path}: {e}")
            return None

    def _estimate_difficulty(self, name: str, fix: str) -> str:
        """Estimate scenario difficulty based on name and fix."""
        if any(word in name.lower() for word in ["typo", "simple", "basic"]):
            return "easy"
        elif any(word in name.lower() for word in ["cross", "rbac", "network", "permission"]):
            return "hard"
        elif any(word in fix.lower() for word in ["complex", "multiple", "coordinate"]):
            return "hard"
        else:
            return "medium"

    def _extract_required_tools(self, fix: str) -> List[str]:
        """Extract required tools from the fix description."""
        tools = []

        if "kubectl edit" in fix or "kubectl patch" in fix:
            tools.append("execute_kubectl")
        if "kubectl logs" in fix:
            tools.append("get_pod_logs")
        if "kubectl describe" in fix or "kubectl get" in fix:
            tools.append("debug_resource")

        # If no specific tools found, assume basic debugging is needed
        if not tools:
            tools = ["debug_resource", "get_pod_logs"]

        return tools

    def load_scenarios(self) -> List[KubentlyScenario]:
        """Load all scenarios from the scenarios directory."""
        scenarios = []

        if not self.scenarios_dir.exists():
            raise FileNotFoundError(f"Scenarios directory not found: {self.scenarios_dir}")

        for scenario_file in sorted(self.scenarios_dir.glob("*.sh")):
            scenario = self.parse_scenario_file(scenario_file)
            if scenario:
                scenarios.append(scenario)

        return scenarios

    def create_dataset(self, dataset_name: str, description: Optional[str] = None) -> Dataset:
        """Create a LangSmith dataset."""
        if not description:
            description = (
                "Kubently test scenarios for debugging Kubernetes issues. "
                "Each example contains a query about a Kubernetes problem and the expected fix."
            )

        # Check if dataset already exists
        try:
            datasets = list(self.client.list_datasets(dataset_name=dataset_name))
            if datasets:
                print(f"Dataset '{dataset_name}' already exists. Using existing dataset.")
                return datasets[0]
        except:
            pass

        # Create new dataset
        dataset = self.client.create_dataset(
            dataset_name=dataset_name,
            description=description,
            data_type="kv"  # Key-value pairs for inputs/outputs
        )

        print(f"Created dataset: {dataset_name}")
        return dataset

    def add_examples_to_dataset(self, dataset: Dataset, scenarios: List[KubentlyScenario]):
        """Add scenarios as examples to the dataset."""
        for scenario in scenarios:
            # Create input (what the agent receives)
            inputs = {
                "query": scenario.query,
                "namespace": scenario.namespace,
                "cluster_type": "kind",
                "metadata": {
                    "scenario_name": scenario.name,
                    "scenario_type": scenario.metadata["scenario_type"],
                    "difficulty": scenario.metadata["difficulty"]
                }
            }

            # Create expected output (what we expect the agent to produce)
            outputs = {
                "expected_fix": scenario.expected_fix,
                "required_tools": scenario.metadata["required_tools"],
                "validation_checks": scenario.validation_checks,
                "success_criteria": {
                    "root_cause_identified": True,
                    "fix_proposed": True,
                    "tools_used_correctly": True
                }
            }

            # Create the example
            example = Example(
                inputs=inputs,
                outputs=outputs,
                metadata={
                    "scenario_name": scenario.name,
                    "created_at": datetime.now().isoformat(),
                    **scenario.metadata
                }
            )

            # Add to dataset
            self.client.create_example(
                inputs=inputs,
                outputs=outputs,
                dataset_id=dataset.id
            )

            print(f"  Added example: {scenario.name}")

    def build_full_dataset(self, dataset_name: Optional[str] = None) -> Dataset:
        """Build a complete dataset from all scenarios."""
        if not dataset_name:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dataset_name = f"kubently-scenarios-{timestamp}"

        print("Loading scenarios...")
        scenarios = self.load_scenarios()
        print(f"Found {len(scenarios)} scenarios")

        print(f"\nCreating dataset: {dataset_name}")
        dataset = self.create_dataset(dataset_name)

        print("\nAdding examples to dataset...")
        self.add_examples_to_dataset(dataset, scenarios)

        print(f"\nDataset '{dataset_name}' created with {len(scenarios)} examples")
        print(f"View in LangSmith: https://smith.langchain.com/datasets/{dataset.id}")

        return dataset

    def export_to_json(self, output_file: Optional[Path] = None) -> Path:
        """Export scenarios to JSON format for offline use."""
        if not output_file:
            output_file = Path("kubently_dataset.json")

        scenarios = self.load_scenarios()

        dataset = {
            "name": "kubently-scenarios",
            "description": "Kubently test scenarios for debugging Kubernetes issues",
            "created_at": datetime.now().isoformat(),
            "examples": []
        }

        for scenario in scenarios:
            example = {
                "inputs": {
                    "query": scenario.query,
                    "namespace": scenario.namespace,
                    "cluster_type": "kind",
                    "metadata": scenario.metadata
                },
                "outputs": {
                    "expected_fix": scenario.expected_fix,
                    "required_tools": scenario.metadata["required_tools"],
                    "validation_checks": scenario.validation_checks
                },
                "metadata": {
                    "scenario_name": scenario.name,
                    **scenario.metadata
                }
            }
            dataset["examples"].append(example)

        with open(output_file, "w") as f:
            json.dump(dataset, f, indent=2)

        print(f"Exported dataset to {output_file}")
        return output_file


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Build LangSmith datasets from Kubently scenarios")
    parser.add_argument("--dataset-name", help="Name for the dataset (default: timestamped)")
    parser.add_argument("--export-json", action="store_true", help="Export to JSON file")
    parser.add_argument("--json-file", help="JSON output file path")

    args = parser.parse_args()

    builder = DatasetBuilder()

    if args.export_json:
        output_file = Path(args.json_file) if args.json_file else None
        builder.export_to_json(output_file)
    else:
        dataset = builder.build_full_dataset(args.dataset_name)

        # Also export to JSON for backup
        json_file = Path(f"kubently_dataset_{dataset.id}.json")
        builder.export_to_json(json_file)


if __name__ == "__main__":
    main()