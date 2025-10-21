#!/usr/bin/env python3
"""
Integrated LangSmith Experiment Runner for Kubently
Manages scenario setup/teardown and runs experiments with real Kubernetes issues
"""

import asyncio
import json
import os
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor
import logging

from langsmith import Client, RunTree
from langsmith.evaluation import evaluate, EvaluationResult
from langsmith.schemas import Run, Example
from langchain_core.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
import httpx

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class ScenarioSetup:
    """Manages setup and teardown of test scenarios."""
    name: str
    script_path: Path
    namespace: str

    def setup(self) -> bool:
        """Set up the scenario in Kubernetes."""
        try:
            logger.info(f"Setting up scenario: {self.name}")
            result = subprocess.run(
                ["bash", str(self.script_path), "setup"],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                logger.error(f"Setup failed for {self.name}: {result.stderr}")
                return False

            # Wait for resources to stabilize
            time.sleep(3)
            logger.info(f"Scenario {self.name} setup complete")
            return True

        except Exception as e:
            logger.error(f"Setup exception for {self.name}: {e}")
            return False

    def cleanup(self) -> bool:
        """Clean up the scenario from Kubernetes."""
        try:
            logger.info(f"Cleaning up scenario: {self.name}")
            result = subprocess.run(
                ["bash", str(self.script_path), "cleanup"],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                logger.error(f"Cleanup failed for {self.name}: {result.stderr}")
                return False

            logger.info(f"Scenario {self.name} cleanup complete")
            return True

        except Exception as e:
            logger.error(f"Cleanup exception for {self.name}: {e}")
            return False

    def verify_setup(self) -> bool:
        """Verify the scenario is properly set up."""
        try:
            # Check if namespace exists
            result = subprocess.run(
                ["kubectl", "get", "namespace", self.namespace],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                logger.error(f"Namespace {self.namespace} not found")
                return False

            # Check for pods in namespace
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", self.namespace],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                logger.error(f"Failed to get pods in namespace {self.namespace}")
                return False

            logger.info(f"Verification passed for {self.name}")
            return True

        except Exception as e:
            logger.error(f"Verification exception for {self.name}: {e}")
            return False


@dataclass
class ExperimentConfig:
    """Configuration for an experiment run."""
    prompt_template: str
    model_name: str
    model_provider: str
    temperature: float = 0.7
    max_tokens: int = 4096
    tool_config: Dict[str, Any] = None
    system_prompt: Optional[str] = None
    metadata: Dict[str, Any] = None


class IntegratedExperimentRunner:
    """Runs experiments with automatic scenario management."""

    def __init__(
        self,
        api_url: str = "http://localhost:8080",
        api_key: str = "test-api-key",
        langsmith_api_key: Optional[str] = None,
        scenarios_dir: Optional[Path] = None
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.langsmith_api_key = langsmith_api_key or os.getenv("LANGSMITH_API_KEY")

        if not self.langsmith_api_key:
            raise ValueError("LANGSMITH_API_KEY environment variable is required")

        self.client = Client(api_key=self.langsmith_api_key)

        # Find scenarios directory
        if scenarios_dir:
            self.scenarios_dir = scenarios_dir
        else:
            self.scenarios_dir = Path(__file__).parent.parent / "test-automation" / "scenarios"

        if not self.scenarios_dir.exists():
            raise ValueError(f"Scenarios directory not found: {self.scenarios_dir}")

        # HTTP client for Kubently API
        self.http_client = httpx.AsyncClient(
            base_url=f"{api_url}/a2a/",
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )

        # Thread pool for parallel scenario setup
        self.executor = ThreadPoolExecutor(max_workers=4)

    def load_scenario_setups(self, scenario_names: Optional[List[str]] = None) -> List[ScenarioSetup]:
        """Load scenario setup configurations."""
        setups = []

        for script_path in sorted(self.scenarios_dir.glob("*.sh")):
            name = script_path.stem

            # Filter by requested scenarios if specified
            if scenario_names and name not in scenario_names:
                continue

            # Extract namespace from script
            content = script_path.read_text()
            namespace = self._extract_namespace(content, name)

            setups.append(ScenarioSetup(
                name=name,
                script_path=script_path,
                namespace=namespace
            ))

        return setups

    def _extract_namespace(self, content: str, name: str) -> str:
        """Extract namespace from scenario script."""
        import re

        # Try to find NAMESPACE variable
        namespace_match = re.search(r'NAMESPACE="?([^"\s]+)"?', content)
        if namespace_match:
            return namespace_match.group(1)

        # Try to find kubectl create namespace command
        namespace_match = re.search(r'create\s+namespace\s+([^\s]+)', content)
        if namespace_match:
            return namespace_match.group(1)

        # Fallback to generic name
        scenario_num = re.search(r'^(\d+)-', name)
        if scenario_num:
            return f"test-ns-{scenario_num.group(1)}"

        return f"test-ns-{name[:8]}"

    async def run_single_test_with_scenario(
        self,
        config: ExperimentConfig,
        scenario: ScenarioSetup,
        inputs: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        """Run a single test with scenario setup and cleanup."""

        # Setup scenario
        setup_success = await asyncio.get_event_loop().run_in_executor(
            self.executor, scenario.setup
        )

        if not setup_success:
            logger.error(f"Failed to setup scenario {scenario.name}")
            return {
                "error": "Scenario setup failed",
                "scenario": scenario.name
            }, False

        # Verify setup
        verify_success = await asyncio.get_event_loop().run_in_executor(
            self.executor, scenario.verify_setup
        )

        if not verify_success:
            logger.warning(f"Scenario verification failed for {scenario.name}")

        try:
            # Run the actual test against Kubently
            logger.info(f"Running test for scenario {scenario.name}")
            response_text = ""
            tools_used = []

            # Build prompt
            prompt = PromptTemplate.from_template(config.prompt_template)
            formatted_prompt = prompt.format(**inputs)

            if config.system_prompt:
                formatted_prompt = f"{config.system_prompt}\n\n{formatted_prompt}"

            # Call Kubently API
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

            result = {
                "response": response_text,
                "tools_used": tools_used,
                "scenario": scenario.name,
                "namespace": scenario.namespace,
                "timestamp": datetime.now().isoformat()
            }

            return result, True

        except Exception as e:
            logger.error(f"Test execution failed for {scenario.name}: {e}")
            return {
                "error": str(e),
                "scenario": scenario.name
            }, False

        finally:
            # Always cleanup
            cleanup_success = await asyncio.get_event_loop().run_in_executor(
                self.executor, scenario.cleanup
            )

            if not cleanup_success:
                logger.warning(f"Cleanup failed for scenario {scenario.name}")

    async def run_experiment_with_scenarios(
        self,
        dataset_name: str,
        config: ExperimentConfig,
        scenario_names: Optional[List[str]] = None,
        max_concurrent_scenarios: int = 2
    ) -> Dict[str, Any]:
        """Run experiment with automatic scenario management."""

        # Get dataset
        datasets = list(self.client.list_datasets(dataset_name=dataset_name))
        if not datasets:
            raise ValueError(f"Dataset '{dataset_name}' not found")
        dataset = datasets[0]

        # Load examples from dataset
        examples = list(self.client.list_examples(dataset_id=dataset.id))
        logger.info(f"Found {len(examples)} examples in dataset")

        # Load scenario setups
        setups = self.load_scenario_setups(scenario_names)
        logger.info(f"Loaded {len(setups)} scenario setups")

        # Match examples to scenarios
        results = []
        semaphore = asyncio.Semaphore(max_concurrent_scenarios)

        async def run_scenario_with_limit(example, scenario):
            async with semaphore:
                return await self.run_single_test_with_scenario(
                    config,
                    scenario,
                    example.inputs
                )

        # Process each example
        tasks = []
        for example in examples:
            # Find matching scenario
            scenario_name = example.inputs.get("metadata", {}).get("scenario_name")
            matching_scenario = next(
                (s for s in setups if s.name == scenario_name),
                None
            )

            if not matching_scenario:
                logger.warning(f"No scenario setup found for {scenario_name}")
                continue

            tasks.append(run_scenario_with_limit(example, matching_scenario))

        # Run all tasks
        if tasks:
            test_results = await asyncio.gather(*tasks)

            # Evaluate results
            for (result, success), example in zip(test_results, examples):
                if success:
                    evaluation = self.evaluate_result(result, example)
                    results.append({
                        "example": example.inputs.get("metadata", {}).get("scenario_name"),
                        "result": result,
                        "evaluation": evaluation,
                        "success": success
                    })
                else:
                    results.append({
                        "example": example.inputs.get("metadata", {}).get("scenario_name"),
                        "result": result,
                        "evaluation": {"score": 0, "error": "Test failed"},
                        "success": success
                    })

        # Aggregate results
        successful = sum(1 for r in results if r["success"])
        average_score = sum(
            r["evaluation"].get("score", 0) for r in results
        ) / max(len(results), 1)

        return {
            "config": asdict(config) if hasattr(config, '__dataclass_fields__') else config,
            "total_scenarios": len(results),
            "successful": successful,
            "average_score": average_score,
            "results": results,
            "timestamp": datetime.now().isoformat()
        }

    def evaluate_result(self, result: Dict[str, Any], example: Example) -> Dict[str, Any]:
        """Evaluate a test result against expected outputs."""
        response = result.get("response", "")
        expected_fix = example.outputs.get("expected_fix", "")
        required_tools = example.outputs.get("required_tools", [])
        tools_used = result.get("tools_used", [])

        # Simple scoring
        scores = {}

        # Check if root cause mentioned
        response_lower = response.lower()
        expected_lower = expected_fix.lower()
        key_terms = expected_lower.split()[:5]  # First 5 words of expected fix

        root_cause_score = sum(
            1 for term in key_terms if term in response_lower
        ) / max(len(key_terms), 1)
        scores["root_cause"] = root_cause_score

        # Check tool usage
        if required_tools:
            tool_score = len(
                set(tools_used) & set(required_tools)
            ) / len(required_tools)
            scores["tools"] = tool_score
        else:
            scores["tools"] = 1.0

        # Overall score
        overall = (scores["root_cause"] * 0.6 + scores["tools"] * 0.4)

        return {
            "score": overall,
            "scores": scores,
            "success": overall >= 0.7
        }

    async def close(self):
        """Cleanup resources."""
        await self.http_client.aclose()
        self.executor.shutdown(wait=True)


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run LangSmith experiments with automatic scenario management"
    )
    parser.add_argument("--dataset", required=True, help="Dataset name")
    parser.add_argument("--api-url", default="http://localhost:8080", help="Kubently API URL")
    parser.add_argument("--api-key", default="test-api-key", help="API key")
    parser.add_argument("--scenarios", nargs="+", help="Specific scenarios to test")
    parser.add_argument("--model", default="gemini-1.5-flash", help="Model to use")
    parser.add_argument("--temperature", type=float, default=0.3, help="Temperature")
    parser.add_argument("--max-concurrent", type=int, default=2,
                        help="Max concurrent scenarios")

    args = parser.parse_args()

    # Create configuration
    config = ExperimentConfig(
        prompt_template="""
You are a Kubernetes debugging assistant. You have access to tools for investigating and fixing issues.

Query: {query}
Namespace: {namespace}
Cluster Type: {cluster_type}

Please investigate the issue and provide:
1. The root cause of the problem
2. A clear fix or solution
3. Verification steps

Be specific and actionable in your response.
""",
        model_name=args.model,
        model_provider="gemini" if "gemini" in args.model else "openai",
        temperature=args.temperature
    )

    # Create runner
    runner = IntegratedExperimentRunner(
        api_url=args.api_url,
        api_key=args.api_key
    )

    try:
        # Check if Kubently is accessible
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{args.api_url}/health", timeout=5.0)
                if response.status_code != 200:
                    logger.error("Kubently API not healthy")
                    return
            except:
                logger.error("Cannot connect to Kubently API. Is it running?")
                return

        # Run experiment
        logger.info("Starting integrated experiment with automatic scenario management")
        results = await runner.run_experiment_with_scenarios(
            dataset_name=args.dataset,
            config=config,
            scenario_names=args.scenarios,
            max_concurrent_scenarios=args.max_concurrent
        )

        # Display results
        print(f"\n{'='*60}")
        print("EXPERIMENT RESULTS")
        print(f"{'='*60}")
        print(f"Total Scenarios: {results['total_scenarios']}")
        print(f"Successful: {results['successful']}")
        print(f"Average Score: {results['average_score']:.2%}")
        print(f"\nDetails:")

        for r in results["results"]:
            status = "✅" if r["success"] else "❌"
            score = r["evaluation"].get("score", 0)
            print(f"  {status} {r['example']}: {score:.2%}")

        # Save results
        output_file = f"integrated_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2, default=str)

        print(f"\nResults saved to: {output_file}")

    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())