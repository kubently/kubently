#!/usr/bin/env python3
"""
LangSmith Experiment Runner for Kubently
Tests different prompts, tools, and configurations using LangSmith experiments
"""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict
from enum import Enum

from langsmith import Client, RunTree
from langsmith.evaluation import evaluate, EvaluationResult
from langsmith.schemas import Run, Example
from langchain_core.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
import httpx


@dataclass
class ExperimentConfig:
    """Configuration for an experiment run."""
    prompt_template: str
    model_name: str
    model_provider: str  # "gemini", "anthropic", "openai"
    temperature: float = 0.7
    max_tokens: int = 4096
    tool_config: Dict[str, Any] = None
    system_prompt: Optional[str] = None
    metadata: Dict[str, Any] = None


class KubentlyEvaluator:
    """Evaluator for Kubently responses."""

    def __init__(self, api_url: str = "http://localhost:8080", api_key: str = "test-api-key"):
        self.api_url = api_url
        self.api_key = api_key

    async def evaluate_response(
        self,
        run: Run,
        example: Example
    ) -> EvaluationResult:
        """Evaluate a single response against expected outputs."""
        scores = {}
        feedback = []

        # Get the actual response
        response = run.outputs.get("response", "")
        expected_fix = example.outputs.get("expected_fix", "")
        required_tools = example.outputs.get("required_tools", [])

        # Score: Root cause identification
        root_cause_score = self._score_root_cause_identification(response, expected_fix)
        scores["root_cause_identification"] = root_cause_score
        if root_cause_score < 0.8:
            feedback.append(f"Root cause not clearly identified. Expected: {expected_fix[:100]}...")

        # Score: Tool usage
        tools_used = run.outputs.get("tools_used", [])
        tool_score = self._score_tool_usage(tools_used, required_tools)
        scores["tool_usage"] = tool_score
        if tool_score < 1.0:
            missing_tools = set(required_tools) - set(tools_used)
            if missing_tools:
                feedback.append(f"Missing required tools: {', '.join(missing_tools)}")

        # Score: Fix accuracy
        fix_score = self._score_fix_accuracy(response, expected_fix)
        scores["fix_accuracy"] = fix_score
        if fix_score < 0.8:
            feedback.append("Proposed fix doesn't match expected solution")

        # Score: Response quality
        quality_score = self._score_response_quality(response)
        scores["response_quality"] = quality_score
        if quality_score < 0.7:
            feedback.append("Response lacks clarity or completeness")

        # Calculate overall score
        overall_score = (
            root_cause_score * 0.3 +
            tool_score * 0.2 +
            fix_score * 0.3 +
            quality_score * 0.2
        )

        return EvaluationResult(
            key="kubently_evaluation",
            score=overall_score,
            value={
                "scores": scores,
                "feedback": feedback,
                "success": overall_score >= 0.8
            },
            comment="; ".join(feedback) if feedback else "All checks passed"
        )

    def _score_root_cause_identification(self, response: str, expected_fix: str) -> float:
        """Score how well the root cause was identified."""
        response_lower = response.lower()
        expected_lower = expected_fix.lower()

        # Extract key terms from expected fix
        key_terms = []
        if "image" in expected_lower:
            key_terms.extend(["image", "pull", "registry"])
        if "crash" in expected_lower:
            key_terms.extend(["crash", "exit", "error"])
        if "service" in expected_lower:
            key_terms.extend(["service", "selector", "port"])
        if "rbac" in expected_lower or "permission" in expected_lower:
            key_terms.extend(["rbac", "permission", "role", "authorization"])
        if "configmap" in expected_lower:
            key_terms.extend(["configmap", "configuration", "missing"])
        if "secret" in expected_lower:
            key_terms.extend(["secret", "credentials", "missing"])
        if "resource" in expected_lower or "oom" in expected_lower:
            key_terms.extend(["memory", "resource", "limit", "oom"])

        # Count matching key terms
        matches = sum(1 for term in key_terms if term in response_lower)
        if key_terms:
            return min(1.0, matches / len(key_terms))
        else:
            # If no specific key terms, do a simple substring check
            return 1.0 if any(word in response_lower for word in expected_lower.split()[:3]) else 0.5

    def _score_tool_usage(self, tools_used: List[str], required_tools: List[str]) -> float:
        """Score how well the required tools were used."""
        if not required_tools:
            return 1.0

        used_set = set(tools_used)
        required_set = set(required_tools)

        # Check if all required tools were used
        intersection = used_set & required_set
        if not required_set:
            return 1.0

        return len(intersection) / len(required_set)

    def _score_fix_accuracy(self, response: str, expected_fix: str) -> float:
        """Score the accuracy of the proposed fix."""
        response_lower = response.lower()
        expected_lower = expected_fix.lower()

        # Look for kubectl commands
        if "kubectl" in expected_lower:
            # Extract kubectl commands from expected
            import re
            expected_cmds = re.findall(r'kubectl\s+\w+\s+\w+', expected_lower)
            response_cmds = re.findall(r'kubectl\s+\w+\s+\w+', response_lower)

            if expected_cmds:
                matching = sum(1 for cmd in expected_cmds if cmd in response_lower)
                return min(1.0, matching / len(expected_cmds))

        # Fallback to keyword matching
        fix_keywords = ["fix", "solution", "resolve", "patch", "update", "change", "modify"]
        has_fix = any(keyword in response_lower for keyword in fix_keywords)

        return 0.8 if has_fix else 0.4

    def _score_response_quality(self, response: str) -> float:
        """Score the overall quality of the response."""
        score = 1.0

        # Check response length
        if len(response) < 50:
            score -= 0.3
        elif len(response) > 5000:
            score -= 0.1

        # Check for structure
        if "\n" in response:
            score += 0.1  # Has paragraphs/structure

        # Check for technical content
        tech_terms = ["pod", "container", "namespace", "kubectl", "kubernetes", "service"]
        tech_count = sum(1 for term in tech_terms if term in response.lower())
        if tech_count < 2:
            score -= 0.2

        return max(0.0, min(1.0, score))


class ExperimentRunner:
    """Runs experiments with different configurations."""

    def __init__(
        self,
        api_url: str = "http://localhost:8080",
        api_key: str = "test-api-key",
        langsmith_api_key: Optional[str] = None
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.langsmith_api_key = langsmith_api_key or os.getenv("LANGSMITH_API_KEY")

        if not self.langsmith_api_key:
            raise ValueError("LANGSMITH_API_KEY environment variable is required")

        self.client = Client(api_key=self.langsmith_api_key)
        self.evaluator = KubentlyEvaluator(api_url, api_key)

        # A2A client for actual API calls
        self.http_client = httpx.AsyncClient(
            base_url=f"{api_url}/a2a/",
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )

    def create_model(self, config: ExperimentConfig):
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

    async def run_single_test(
        self,
        config: ExperimentConfig,
        inputs: Dict[str, Any],
        run_tree: Optional[RunTree] = None
    ) -> Dict[str, Any]:
        """Run a single test with the given configuration."""
        # Build the prompt
        prompt = PromptTemplate.from_template(config.prompt_template)
        formatted_prompt = prompt.format(**inputs)

        if config.system_prompt:
            formatted_prompt = f"{config.system_prompt}\n\n{formatted_prompt}"

        # Make the actual API call to Kubently
        response_text = ""
        tools_used = []

        try:
            # Send to actual Kubently API via A2A protocol
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/stream",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "user",
                        "parts": [{"partId": "p1", "text": inputs["query"]}]
                    }
                }
            }

            async with self.http_client.stream("POST", "/", json=request) as response:
                if response.status_code == 200:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                result = data.get("result", {})

                                # Extract response text
                                if result.get("kind") == "artifact-update":
                                    artifact = result.get("artifact", {})
                                    for part in artifact.get("parts", []):
                                        if part.get("kind") == "text":
                                            response_text = part.get("text", "")

                                # Extract tool calls
                                if result.get("kind") == "status-update":
                                    message = result.get("status", {}).get("message", {})
                                    if message and "parts" in message:
                                        for part in message["parts"]:
                                            if part.get("kind") == "text":
                                                text = part.get("text", "")
                                                if "🔧 Tool Call:" in text:
                                                    import re
                                                    tool_match = re.search(r'🔧 Tool Call: (\w+)', text)
                                                    if tool_match:
                                                        tools_used.append(tool_match.group(1))

                            except json.JSONDecodeError:
                                pass

        except Exception as e:
            response_text = f"Error calling API: {str(e)}"

        # Return results
        return {
            "response": response_text,
            "tools_used": tools_used,
            "prompt_used": formatted_prompt,
            "config": asdict(config) if hasattr(config, '__dataclass_fields__') else config,
            "timestamp": datetime.now().isoformat()
        }

    def create_test_function(self, config: ExperimentConfig) -> Callable:
        """Create a test function for LangSmith evaluation."""

        async def test_fn(inputs: Dict[str, Any]) -> Dict[str, Any]:
            """Test function that will be called by LangSmith."""
            return await self.run_single_test(config, inputs)

        return test_fn

    async def run_experiment(
        self,
        dataset_name: str,
        configs: List[ExperimentConfig],
        experiment_prefix: str = "kubently-experiment",
        max_concurrency: int = 5
    ) -> List[Dict[str, Any]]:
        """Run an experiment with multiple configurations."""
        results = []

        # Get the dataset
        datasets = list(self.client.list_datasets(dataset_name=dataset_name))
        if not datasets:
            raise ValueError(f"Dataset '{dataset_name}' not found")
        dataset = datasets[0]

        print(f"Running experiment on dataset: {dataset_name}")
        print(f"Testing {len(configs)} configurations")

        for i, config in enumerate(configs, 1):
            experiment_name = f"{experiment_prefix}-{i}-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            print(f"\n[{i}/{len(configs)}] Running configuration: {config.model_name}")

            # Create test function for this config
            test_fn = self.create_test_function(config)

            # Run evaluation
            eval_results = await evaluate(
                test_fn,
                data=dataset_name,
                evaluators=[self.evaluator.evaluate_response],
                experiment=experiment_name,
                max_concurrency=max_concurrency,
                metadata={
                    "config": asdict(config) if hasattr(config, '__dataclass_fields__') else config,
                    "timestamp": datetime.now().isoformat()
                }
            )

            # Collect results
            result_summary = {
                "experiment_name": experiment_name,
                "config": config,
                "results": eval_results,
                "timestamp": datetime.now().isoformat()
            }
            results.append(result_summary)

            print(f"  Completed. View at: https://smith.langchain.com/experiments/{experiment_name}")

        return results

    async def close(self):
        """Close HTTP client."""
        await self.http_client.aclose()


def create_default_configs() -> List[ExperimentConfig]:
    """Create default experiment configurations."""
    configs = []

    # Base prompt template
    base_prompt = """
You are a Kubernetes debugging assistant. You have access to tools for investigating and fixing issues in a Kubernetes cluster.

Query: {query}
Namespace: {namespace}
Cluster Type: {cluster_type}

Please investigate the issue and provide:
1. The root cause of the problem
2. A clear fix or solution
3. Verification steps

Be specific and actionable in your response.
"""

    # Variation 1: Direct and concise
    concise_prompt = """
Debug this Kubernetes issue:
Query: {query}
Namespace: {namespace}

Identify root cause and provide fix.
"""

    # Variation 2: Detailed with reasoning
    detailed_prompt = """
As a Kubernetes expert, analyze this issue step by step:

Query: {query}
Namespace: {namespace}
Cluster: {cluster_type}

Please:
1. First, gather diagnostic information
2. Analyze the symptoms to identify root cause
3. Propose a specific fix with kubectl commands
4. Explain why this fix addresses the root cause
5. Provide commands to verify the fix worked

Use all available tools to investigate thoroughly.
"""

    # Create configurations with different models and prompts
    configs.extend([
        # Gemini configurations
        ExperimentConfig(
            prompt_template=base_prompt,
            model_name="gemini-1.5-flash",
            model_provider="gemini",
            temperature=0.3,
            metadata={"variant": "base_prompt_low_temp"}
        ),
        ExperimentConfig(
            prompt_template=base_prompt,
            model_name="gemini-1.5-pro",
            model_provider="gemini",
            temperature=0.7,
            metadata={"variant": "base_prompt_pro"}
        ),
        ExperimentConfig(
            prompt_template=concise_prompt,
            model_name="gemini-1.5-flash",
            model_provider="gemini",
            temperature=0.3,
            metadata={"variant": "concise_prompt"}
        ),
        ExperimentConfig(
            prompt_template=detailed_prompt,
            model_name="gemini-1.5-flash",
            model_provider="gemini",
            temperature=0.5,
            metadata={"variant": "detailed_prompt"}
        ),
    ])

    # Add Claude configurations if API key is available
    if os.getenv("ANTHROPIC_API_KEY"):
        configs.extend([
            ExperimentConfig(
                prompt_template=base_prompt,
                model_name="claude-3-5-sonnet-20240620",
                model_provider="anthropic",
                temperature=0.3,
                metadata={"variant": "claude_sonnet"}
            ),
            ExperimentConfig(
                prompt_template=detailed_prompt,
                model_name="claude-3-5-haiku-20241022",
                model_provider="anthropic",
                temperature=0.3,
                metadata={"variant": "claude_haiku"}
            ),
        ])

    # Add OpenAI configurations if API key is available
    if os.getenv("OPENAI_API_KEY"):
        configs.extend([
            ExperimentConfig(
                prompt_template=base_prompt,
                model_name="gpt-4o",
                model_provider="openai",
                temperature=0.3,
                metadata={"variant": "gpt4o"}
            ),
            ExperimentConfig(
                prompt_template=concise_prompt,
                model_name="gpt-4o-mini",
                model_provider="openai",
                temperature=0.3,
                metadata={"variant": "gpt4o_mini"}
            ),
        ])

    return configs


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Run LangSmith experiments for Kubently")
    parser.add_argument("--dataset", required=True, help="Dataset name to use")
    parser.add_argument("--api-url", default="http://localhost:8080", help="Kubently API URL")
    parser.add_argument("--api-key", default="test-api-key", help="API key")
    parser.add_argument("--experiment-prefix", default="kubently-exp", help="Experiment name prefix")
    parser.add_argument("--max-concurrency", type=int, default=5, help="Max concurrent evaluations")
    parser.add_argument("--configs", help="Path to JSON file with configurations")

    args = parser.parse_args()

    runner = ExperimentRunner(args.api_url, args.api_key)

    try:
        # Load or create configurations
        if args.configs:
            with open(args.configs, "r") as f:
                config_data = json.load(f)
                configs = [ExperimentConfig(**cfg) for cfg in config_data]
        else:
            configs = create_default_configs()

        # Run experiments
        results = await runner.run_experiment(
            dataset_name=args.dataset,
            configs=configs,
            experiment_prefix=args.experiment_prefix,
            max_concurrency=args.max_concurrency
        )

        # Save results summary
        output_file = f"experiment_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2, default=str)

        print(f"\nExperiment complete. Results saved to: {output_file}")

    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())