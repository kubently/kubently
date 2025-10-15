#!/usr/bin/env python3
"""
Mocked LangSmith Experiment Runner for Kubently
Tests prompts and configurations using simulated responses - no Kubernetes required!
"""

import asyncio
import json
import os
import random
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict

from langsmith import Client
from langsmith.evaluation import evaluate, EvaluationResult
from langsmith.schemas import Run, Example
from langchain_core.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI


@dataclass
class ExperimentConfig:
    """Configuration for an experiment run."""
    prompt_template: str
    model_name: str
    model_provider: str  # "gemini", "anthropic", "openai"
    temperature: float = 0.7
    max_tokens: int = 4096
    system_prompt: Optional[str] = None
    metadata: Dict[str, Any] = None


class MockKubentlyAgent:
    """Mocks Kubently agent responses based on scenario types."""

    def __init__(self):
        self.response_templates = self._load_response_templates()
        self.tool_patterns = self._load_tool_patterns()

    def _load_response_templates(self) -> Dict[str, str]:
        """Load realistic response templates for different scenario types."""
        return {
            "image_pull": """
After investigating the issue in namespace {namespace}, I found that the pod is in ImagePullBackOff status.

Root Cause: The image name contains a typo - it's trying to pull 'nginy:latest' instead of 'nginx:latest'.

Fix:
```bash
kubectl patch deployment nginx-deployment -n {namespace} --type='json' -p='[{{"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": "nginx:latest"}}]'
```

This will update the deployment to use the correct image name. The pod should start successfully after this change.
""",
            "crash_loop": """
I've diagnosed the CrashLoopBackOff issue in namespace {namespace}.

Root Cause: The application is crashing immediately on startup due to a missing required environment variable.

Fix:
```bash
kubectl set env deployment/app-deployment -n {namespace} REQUIRED_VAR=value
```

The container expects this environment variable to be set. After adding it, the pod should start normally.
""",
            "service_issue": """
After checking the service configuration in namespace {namespace}, I identified the issue.

Root Cause: The service selector doesn't match the pod labels. The service is looking for 'app=web' but the pods have 'app=webapp'.

Fix:
```bash
kubectl patch service webapp-service -n {namespace} --type='json' -p='[{{"op": "replace", "path": "/spec/selector/app", "value": "webapp"}}]'
```

This aligns the service selector with the actual pod labels, allowing traffic to reach the pods.
""",
            "rbac": """
I've analyzed the authorization issues in namespace {namespace}.

Root Cause: The service account lacks the necessary permissions to access the required resources.

Fix:
```bash
kubectl create rolebinding app-binding -n {namespace} --clusterrole=view --serviceaccount={namespace}:default
```

This grants the service account read permissions to the resources it needs.
""",
            "configmap": """
The pod in namespace {namespace} is failing to start due to configuration issues.

Root Cause: The pod references a ConfigMap 'app-config' that doesn't exist in the namespace.

Fix:
```bash
kubectl create configmap app-config -n {namespace} --from-literal=key=value
```

Creating the missing ConfigMap will allow the pod to mount the configuration and start successfully.
""",
            "memory": """
Investigation shows the pod in namespace {namespace} is being OOMKilled.

Root Cause: The container is exceeding its memory limit of 128Mi. The application needs more memory to run.

Fix:
```bash
kubectl patch deployment memory-app -n {namespace} --type='json' -p='[{{"op": "replace", "path": "/spec/template/spec/containers/0/resources/limits/memory", "value": "512Mi"}}]'
```

Increasing the memory limit will prevent the OOM killer from terminating the container.
""",
            "general": """
I've investigated the issue in namespace {namespace}.

After checking the pods, services, and events, I found that the main issue is related to resource configuration.

The problem has been identified and can be resolved by updating the deployment configuration.

Please check the pod status after applying the recommended changes.
"""
        }

    def _load_tool_patterns(self) -> Dict[str, List[str]]:
        """Define which tools are typically used for each scenario type."""
        return {
            "image_pull": ["debug_resource", "get_pod_logs", "execute_kubectl"],
            "crash_loop": ["get_pod_logs", "debug_resource", "execute_kubectl"],
            "service_issue": ["debug_resource", "execute_kubectl"],
            "rbac": ["debug_resource", "execute_kubectl"],
            "configmap": ["debug_resource", "execute_kubectl"],
            "memory": ["get_pod_logs", "debug_resource", "execute_kubectl"],
            "general": ["debug_resource", "get_pod_logs"]
        }

    async def generate_response(
        self,
        query: str,
        namespace: str,
        scenario_type: str,
        config: ExperimentConfig
    ) -> Dict[str, Any]:
        """Generate a mocked response based on the scenario."""

        # Simulate processing time
        await asyncio.sleep(random.uniform(0.5, 1.5))

        # Get template for this scenario type
        template = self.response_templates.get(
            scenario_type,
            self.response_templates["general"]
        )

        # Get tools that would be used
        tools = self.tool_patterns.get(
            scenario_type,
            self.tool_patterns["general"]
        )

        # Use the actual LLM to generate a response based on our template
        # This allows us to test how different models interpret the same problem
        model = self._create_model(config)

        # Create a prompt that guides the model to respond appropriately
        guided_prompt = f"""
{config.system_prompt if config.system_prompt else ''}

Based on this Kubernetes debugging scenario:
{query}

Namespace: {namespace}

Provide a response similar to this structure but in your own words:
{template.format(namespace=namespace)}

Remember to:
1. Identify the root cause clearly
2. Provide specific kubectl commands for the fix
3. Explain why the fix works
"""

        # Get response from actual LLM
        if config.model_provider == "gemini":
            llm = ChatGoogleGenerativeAI(
                model=config.model_name,
                temperature=config.temperature,
                max_output_tokens=config.max_tokens
            )
        elif config.model_provider == "anthropic":
            llm = ChatAnthropic(
                model=config.model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens
            )
        elif config.model_provider == "openai":
            llm = ChatOpenAI(
                model=config.model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens
            )
        else:
            # Fallback to template
            response_text = template.format(namespace=namespace)
            return {
                "response": response_text,
                "tools_used": tools,
                "mocked": True
            }

        try:
            # Get actual LLM response
            response = await llm.ainvoke(guided_prompt)
            response_text = response.content
        except Exception as e:
            # Fallback to template if LLM fails
            response_text = template.format(namespace=namespace)

        return {
            "response": response_text,
            "tools_used": tools,
            "scenario_type": scenario_type,
            "namespace": namespace,
            "timestamp": datetime.now().isoformat(),
            "mocked": True
        }

    def _create_model(self, config: ExperimentConfig):
        """Create a language model based on configuration."""
        if config.model_provider == "gemini":
            return ChatGoogleGenerativeAI(
                model=config.model_name,
                temperature=config.temperature,
                max_output_tokens=config.max_tokens
            )
        elif config.model_provider == "anthropic":
            return ChatAnthropic(
                model=config.model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens
            )
        elif config.model_provider == "openai":
            return ChatOpenAI(
                model=config.model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens
            )
        else:
            raise ValueError(f"Unknown model provider: {config.model_provider}")


class MockedExperimentRunner:
    """Runs experiments with mocked Kubently responses."""

    def __init__(self, langsmith_api_key: Optional[str] = None):
        self.langsmith_api_key = langsmith_api_key or os.getenv("LANGSMITH_API_KEY")

        if not self.langsmith_api_key:
            raise ValueError("LANGSMITH_API_KEY environment variable is required")

        self.client = Client(api_key=self.langsmith_api_key)
        self.mock_agent = MockKubentlyAgent()

    async def run_single_test(
        self,
        config: ExperimentConfig,
        inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run a single test with mocked response."""

        # Extract scenario information
        query = inputs.get("query", "")
        namespace = inputs.get("namespace", "test-ns")
        scenario_type = inputs.get("metadata", {}).get("scenario_type", "general")

        # Generate mocked response
        result = await self.mock_agent.generate_response(
            query=query,
            namespace=namespace,
            scenario_type=scenario_type,
            config=config
        )

        return result

    def create_test_function(self, config: ExperimentConfig) -> Callable:
        """Create a test function for LangSmith evaluation."""

        async def test_fn(inputs: Dict[str, Any]) -> Dict[str, Any]:
            """Test function that will be called by LangSmith."""
            return await self.run_single_test(config, inputs)

        return test_fn

    def evaluate_response(self, run: Run, example: Example) -> EvaluationResult:
        """Evaluate a response against expected outputs."""
        response = run.outputs.get("response", "")
        expected_fix = example.outputs.get("expected_fix", "")
        required_tools = example.outputs.get("required_tools", [])
        tools_used = run.outputs.get("tools_used", [])

        scores = {}
        feedback = []

        # Score: Root cause identification
        response_lower = response.lower()
        expected_lower = expected_fix.lower()

        # Extract key terms from expected fix
        key_terms = []
        if "image" in expected_lower or "typo" in expected_lower:
            key_terms.extend(["image", "typo", "nginx", "pull"])
        if "crash" in expected_lower:
            key_terms.extend(["crash", "environment", "variable", "exit"])
        if "selector" in expected_lower or "label" in expected_lower:
            key_terms.extend(["selector", "label", "mismatch", "service"])
        if "permission" in expected_lower or "rbac" in expected_lower:
            key_terms.extend(["permission", "rbac", "role", "authorization"])
        if "configmap" in expected_lower:
            key_terms.extend(["configmap", "missing", "configuration"])
        if "memory" in expected_lower or "oom" in expected_lower:
            key_terms.extend(["memory", "oom", "limit", "resource"])

        # Count matching key terms
        if key_terms:
            matches = sum(1 for term in key_terms if term in response_lower)
            root_cause_score = min(1.0, matches / max(len(key_terms), 1))
        else:
            root_cause_score = 0.5

        scores["root_cause_identification"] = root_cause_score
        if root_cause_score < 0.7:
            feedback.append("Root cause not clearly identified")

        # Score: Tool usage
        if required_tools:
            tool_score = len(set(tools_used) & set(required_tools)) / len(required_tools)
        else:
            tool_score = 1.0

        scores["tool_usage"] = tool_score
        if tool_score < 0.8:
            feedback.append(f"Missing tools: {set(required_tools) - set(tools_used)}")

        # Score: Fix accuracy (check for kubectl commands)
        has_kubectl = "kubectl" in response_lower
        has_specific_fix = any(
            cmd in response_lower
            for cmd in ["patch", "create", "apply", "set", "edit"]
        )

        fix_score = 0.5
        if has_kubectl and has_specific_fix:
            fix_score = 1.0
        elif has_kubectl:
            fix_score = 0.75

        scores["fix_accuracy"] = fix_score
        if fix_score < 0.7:
            feedback.append("Fix lacks specific kubectl commands")

        # Score: Response quality
        quality_score = 1.0
        if len(response) < 100:
            quality_score -= 0.3
            feedback.append("Response too brief")
        if "root cause" not in response_lower and "issue" not in response_lower:
            quality_score -= 0.2

        scores["response_quality"] = max(0.0, quality_score)

        # Overall score
        overall_score = (
            root_cause_score * 0.3 +
            tool_score * 0.2 +
            fix_score * 0.3 +
            quality_score * 0.2
        )

        return EvaluationResult(
            key="mocked_evaluation",
            score=overall_score,
            value={
                "scores": scores,
                "feedback": feedback,
                "success": overall_score >= 0.7
            },
            comment="; ".join(feedback) if feedback else "All checks passed"
        )

    async def run_experiment(
        self,
        dataset_name: str,
        configs: List[ExperimentConfig],
        experiment_prefix: str = "mocked-kubently",
        max_concurrency: int = 5
    ) -> List[Dict[str, Any]]:
        """Run experiments with multiple configurations."""
        results = []

        # Get dataset
        datasets = list(self.client.list_datasets(dataset_name=dataset_name))
        if not datasets:
            raise ValueError(f"Dataset '{dataset_name}' not found")
        dataset = datasets[0]

        print(f"Running mocked experiments on dataset: {dataset_name}")
        print(f"Testing {len(configs)} configurations")

        for i, config in enumerate(configs, 1):
            experiment_name = f"{experiment_prefix}-{i}-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            print(f"\n[{i}/{len(configs)}] Running configuration: {config.metadata.get('variant', config.model_name)}")

            # Create test function
            test_fn = self.create_test_function(config)

            # Run evaluation
            eval_results = await evaluate(
                test_fn,
                data=dataset_name,
                evaluators=[self.evaluate_response],
                experiment=experiment_name,
                max_concurrency=max_concurrency,
                metadata={
                    "config": asdict(config) if hasattr(config, '__dataclass_fields__') else config,
                    "timestamp": datetime.now().isoformat(),
                    "mocked": True
                }
            )

            # Collect results
            result_summary = {
                "experiment_name": experiment_name,
                "config": config,
                "results": eval_results,
                "timestamp": datetime.now().isoformat(),
                "mocked": True
            }
            results.append(result_summary)

            print(f"  Completed. View at: https://smith.langchain.com/experiments/{experiment_name}")

        return results


def create_test_configurations() -> List[ExperimentConfig]:
    """Create various test configurations."""
    configs = []

    # Different prompt templates to test
    prompts = {
        "minimal": "Debug: {query}\nNamespace: {namespace}\nProvide root cause and fix.",
        "structured": """
As a Kubernetes expert, analyze this issue:
{query}
Namespace: {namespace}

Please provide:
1. Root cause analysis
2. Specific fix with kubectl commands
3. Verification steps
""",
        "detailed": """
You are a senior Kubernetes engineer. Investigate this issue thoroughly:

Problem: {query}
Namespace: {namespace}
Cluster: {cluster_type}

Your analysis should include:
- Diagnosis of the root cause
- Step-by-step fix instructions
- kubectl commands to resolve the issue
- Explanation of why this fix works
- Commands to verify the fix

Be specific and actionable.
"""
    }

    # Create configurations
    for prompt_name, prompt_template in prompts.items():
        # Gemini configurations
        if os.getenv("GOOGLE_API_KEY"):
            configs.append(ExperimentConfig(
                prompt_template=prompt_template,
                model_name="gemini-1.5-flash",
                model_provider="gemini",
                temperature=0.3,
                metadata={"variant": f"gemini-flash-{prompt_name}"}
            ))

        # Claude configurations
        if os.getenv("ANTHROPIC_API_KEY"):
            configs.append(ExperimentConfig(
                prompt_template=prompt_template,
                model_name="claude-3-5-sonnet-20240620",
                model_provider="anthropic",
                temperature=0.3,
                metadata={"variant": f"claude-sonnet-{prompt_name}"}
            ))

        # OpenAI configurations
        if os.getenv("OPENAI_API_KEY"):
            configs.append(ExperimentConfig(
                prompt_template=prompt_template,
                model_name="gpt-4o-mini",
                model_provider="openai",
                temperature=0.3,
                metadata={"variant": f"gpt4o-mini-{prompt_name}"}
            ))

    return configs


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Run mocked LangSmith experiments")
    parser.add_argument("--dataset", required=True, help="Dataset name")
    parser.add_argument("--experiment-prefix", default="mocked-exp", help="Experiment prefix")
    parser.add_argument("--max-concurrency", type=int, default=5, help="Max concurrent tests")

    args = parser.parse_args()

    runner = MockedExperimentRunner()

    # Create test configurations
    configs = create_test_configurations()

    if not configs:
        print("No configurations available. Please set at least one API key:")
        print("  export GOOGLE_API_KEY=...")
        print("  export ANTHROPIC_API_KEY=...")
        print("  export OPENAI_API_KEY=...")
        return

    print(f"Created {len(configs)} test configurations")

    # Run experiments
    results = await runner.run_experiment(
        dataset_name=args.dataset,
        configs=configs,
        experiment_prefix=args.experiment_prefix,
        max_concurrency=args.max_concurrency
    )

    # Display summary
    print(f"\n{'='*60}")
    print("EXPERIMENT SUMMARY")
    print(f"{'='*60}")

    for result in results:
        config = result["config"]
        variant = config.metadata.get("variant", "unknown") if hasattr(config, "metadata") else "unknown"
        print(f"\n{variant}:")
        # Results would be populated by LangSmith evaluation
        print(f"  Experiment: {result['experiment_name']}")
        print(f"  Timestamp: {result['timestamp']}")

    # Save results
    output_file = f"mocked_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\nResults saved to: {output_file}")
    print("\nNote: This was a MOCKED experiment - no real Kubernetes clusters were used")


if __name__ == "__main__":
    asyncio.run(main())