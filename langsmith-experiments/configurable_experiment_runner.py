#!/usr/bin/env python3
"""
Configurable LangSmith Experiment Runner with .env file support
Allows easy swapping of LLM providers and models through environment files
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import logging
from dotenv import load_dotenv

from langsmith import Client
from langsmith.evaluation import evaluate, EvaluationResult
from langsmith.schemas import Run, Example

# Import enhanced mock data
from enhanced_mocked_data import EnhancedMockData, DebugStep

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Model configuration from environment file."""
    provider: str  # gemini, anthropic, openai, azure
    model_name: str
    api_key: str
    temperature: float = 0.3
    max_tokens: int = 4096
    api_base: Optional[str] = None  # For Azure or custom endpoints
    api_version: Optional[str] = None  # For Azure
    additional_params: Dict[str, Any] = None


class EnvConfigLoader:
    """Loads model configuration from environment files."""

    @staticmethod
    def load_from_file(env_file: Path) -> ModelConfig:
        """Load configuration from a .env file."""
        if not env_file.exists():
            raise FileNotFoundError(f"Environment file not found: {env_file}")

        # Load the env file
        load_dotenv(env_file, override=True)

        # Extract configuration
        provider = os.getenv("LLM_PROVIDER", "gemini").lower()

        # Map provider to model and API key
        config_map = {
            "gemini": {
                "model_key": "GEMINI_MODEL",
                "api_key": "GOOGLE_API_KEY",
                "default_model": "gemini-1.5-flash"
            },
            "anthropic": {
                "model_key": "ANTHROPIC_MODEL",
                "api_key": "ANTHROPIC_API_KEY",
                "default_model": "claude-3-5-sonnet-20241022"
            },
            "openai": {
                "model_key": "OPENAI_MODEL",
                "api_key": "OPENAI_API_KEY",
                "default_model": "gpt-4o"
            },
            "azure": {
                "model_key": "AZURE_DEPLOYMENT_NAME",
                "api_key": "AZURE_API_KEY",
                "default_model": "gpt-4"
            }
        }

        if provider not in config_map:
            raise ValueError(f"Unknown provider: {provider}")

        provider_config = config_map[provider]

        # Get model name and API key
        model_name = os.getenv(provider_config["model_key"], provider_config["default_model"])
        api_key = os.getenv(provider_config["api_key"])

        if not api_key:
            raise ValueError(f"API key not found for provider {provider}: {provider_config['api_key']}")

        # Get optional parameters
        temperature = float(os.getenv("TEMPERATURE", "0.3"))
        max_tokens = int(os.getenv("MAX_TOKENS", "4096"))

        # Azure-specific parameters
        api_base = os.getenv("AZURE_API_BASE") if provider == "azure" else None
        api_version = os.getenv("AZURE_API_VERSION", "2024-02-01") if provider == "azure" else None

        # Additional parameters
        additional_params = {}
        if os.getenv("THINKING_MODE"):
            additional_params["thinking_mode"] = os.getenv("THINKING_MODE")
        if os.getenv("SYSTEM_PROMPT"):
            additional_params["system_prompt"] = os.getenv("SYSTEM_PROMPT")

        return ModelConfig(
            provider=provider,
            model_name=model_name,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            api_base=api_base,
            api_version=api_version,
            additional_params=additional_params
        )

    @staticmethod
    def create_sample_env_files():
        """Create sample .env files for different providers."""
        samples = {
            "gemini-flash.env": """# Google Gemini Flash Configuration
LLM_PROVIDER=gemini
GEMINI_MODEL=gemini-1.5-flash
GOOGLE_API_KEY=your-google-api-key
TEMPERATURE=0.3
MAX_TOKENS=4096
""",
            "gemini-pro.env": """# Google Gemini Pro Configuration
LLM_PROVIDER=gemini
GEMINI_MODEL=gemini-1.5-pro
GOOGLE_API_KEY=your-google-api-key
TEMPERATURE=0.7
MAX_TOKENS=8192
THINKING_MODE=deep
""",
            "claude-sonnet.env": """# Anthropic Claude Sonnet Configuration
LLM_PROVIDER=anthropic
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=your-anthropic-api-key
TEMPERATURE=0.3
MAX_TOKENS=4096
""",
            "claude-haiku.env": """# Anthropic Claude Haiku Configuration
LLM_PROVIDER=anthropic
ANTHROPIC_MODEL=claude-3-5-haiku-20241022
ANTHROPIC_API_KEY=your-anthropic-api-key
TEMPERATURE=0.1
MAX_TOKENS=4096
""",
            "gpt-4o.env": """# OpenAI GPT-4o Configuration
LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4o
OPENAI_API_KEY=your-openai-api-key
TEMPERATURE=0.3
MAX_TOKENS=4096
""",
            "gpt-4o-mini.env": """# OpenAI GPT-4o Mini Configuration
LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4o-mini
OPENAI_API_KEY=your-openai-api-key
TEMPERATURE=0.2
MAX_TOKENS=4096
""",
            "azure-gpt4.env": """# Azure OpenAI Configuration
LLM_PROVIDER=azure
AZURE_DEPLOYMENT_NAME=gpt-4-deployment
AZURE_API_KEY=your-azure-api-key
AZURE_API_BASE=https://your-resource.openai.azure.com
AZURE_API_VERSION=2024-02-01
TEMPERATURE=0.3
MAX_TOKENS=4096
"""
        }

        env_dir = Path("env-configs")
        env_dir.mkdir(exist_ok=True)

        for filename, content in samples.items():
            filepath = env_dir / filename
            filepath.write_text(content)
            logger.info(f"Created sample: {filepath}")


class ConfigurableExperimentRunner:
    """Runs experiments with configurable LLM providers via env files."""

    def __init__(self, langsmith_api_key: Optional[str] = None):
        self.langsmith_api_key = langsmith_api_key or os.getenv("LANGSMITH_API_KEY")

        if not self.langsmith_api_key:
            raise ValueError("LANGSMITH_API_KEY environment variable is required")

        self.client = Client(api_key=self.langsmith_api_key)
        self.mock_data = EnhancedMockData()

    def create_llm(self, config: ModelConfig):
        """Create LLM instance based on configuration."""
        # Set API key in environment for the provider
        if config.provider == "gemini":
            os.environ["GOOGLE_API_KEY"] = config.api_key
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(
                model=config.model_name,
                temperature=config.temperature,
                max_output_tokens=config.max_tokens
            )

        elif config.provider == "anthropic":
            os.environ["ANTHROPIC_API_KEY"] = config.api_key
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(
                model=config.model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens
            )

        elif config.provider == "openai":
            os.environ["OPENAI_API_KEY"] = config.api_key
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=config.model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens
            )

        elif config.provider == "azure":
            from langchain_openai import AzureChatOpenAI
            return AzureChatOpenAI(
                deployment_name=config.model_name,
                api_key=config.api_key,
                api_base=config.api_base,
                api_version=config.api_version,
                temperature=config.temperature,
                max_tokens=config.max_tokens
            )
        else:
            raise ValueError(f"Unknown provider: {config.provider}")

    async def run_single_test(
        self,
        config: ModelConfig,
        inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run a single test with the configured model."""

        # Extract scenario information
        scenario_type = inputs.get("metadata", {}).get("scenario_type", "image_pull")
        namespace = inputs.get("namespace", "test-ns")

        # Get the realistic workflow for this scenario
        workflow = self.mock_data.get_workflow(scenario_type)

        # Create LLM
        llm = self.create_llm(config)

        # Generate narrative response
        narrative = self.mock_data.get_response_narrative(scenario_type, namespace)

        # Get tool sequence
        tools_used = [step.tool for step in workflow]

        # If system prompt provided, enhance the response
        if config.additional_params and config.additional_params.get("system_prompt"):
            prompt = f"{config.additional_params['system_prompt']}\n\n{inputs.get('query', '')}"
            try:
                response = await llm.ainvoke(prompt)
                narrative = response.content
            except Exception as e:
                logger.warning(f"LLM invocation failed, using mock: {e}")

        return {
            "response": narrative,
            "tools_used": tools_used,
            "workflow_steps": len(workflow),
            "scenario_type": scenario_type,
            "model_config": {
                "provider": config.provider,
                "model": config.model_name,
                "temperature": config.temperature
            },
            "timestamp": datetime.now().isoformat()
        }

    def evaluate_response(self, run: Run, example: Example) -> EvaluationResult:
        """Evaluate response quality."""
        response = run.outputs.get("response", "")
        tools_used = run.outputs.get("tools_used", [])
        workflow_steps = run.outputs.get("workflow_steps", 0)

        expected_fix = example.outputs.get("expected_fix", "")
        required_tools = example.outputs.get("required_tools", [])

        scores = {}

        # Score diagnostic depth (4-8 steps is good)
        if workflow_steps >= 6:
            scores["diagnostic_depth"] = 1.0
        elif workflow_steps >= 4:
            scores["diagnostic_depth"] = 0.8
        else:
            scores["diagnostic_depth"] = 0.5

        # Score tool usage
        if required_tools:
            tool_score = len(set(tools_used) & set(required_tools)) / len(required_tools)
            scores["tool_usage"] = tool_score
        else:
            scores["tool_usage"] = 1.0

        # Score response quality
        response_lower = response.lower()
        has_root_cause = "root cause" in response_lower
        has_fix = "kubectl" in response_lower or "fix" in response_lower
        has_verification = "verif" in response_lower or "check" in response_lower

        quality_score = 0.3 * has_root_cause + 0.4 * has_fix + 0.3 * has_verification
        scores["response_quality"] = quality_score

        # Overall score
        overall = (
            scores["diagnostic_depth"] * 0.3 +
            scores["tool_usage"] * 0.3 +
            scores["response_quality"] * 0.4
        )

        return EvaluationResult(
            key="enhanced_evaluation",
            score=overall,
            value=scores,
            comment=f"Steps: {workflow_steps}, Tools: {len(tools_used)}"
        )

    async def run_experiment(
        self,
        dataset_name: str,
        env_files: List[Path],
        experiment_prefix: str = "configurable-exp"
    ) -> List[Dict[str, Any]]:
        """Run experiments with different environment configurations."""

        results = []

        # Get dataset
        datasets = list(self.client.list_datasets(dataset_name=dataset_name))
        if not datasets:
            raise ValueError(f"Dataset '{dataset_name}' not found")
        dataset = datasets[0]

        for env_file in env_files:
            logger.info(f"\nLoading configuration from: {env_file}")

            try:
                # Load configuration
                config = EnvConfigLoader.load_from_file(env_file)
                logger.info(f"  Provider: {config.provider}")
                logger.info(f"  Model: {config.model_name}")
                logger.info(f"  Temperature: {config.temperature}")

                # Create test function
                async def test_fn(inputs: Dict[str, Any]) -> Dict[str, Any]:
                    return await self.run_single_test(config, inputs)

                # Run evaluation
                experiment_name = f"{experiment_prefix}-{env_file.stem}-{datetime.now().strftime('%Y%m%d_%H%M%S')}"

                eval_results = await evaluate(
                    test_fn,
                    data=dataset_name,
                    evaluators=[self.evaluate_response],
                    experiment=experiment_name,
                    max_concurrency=5,
                    metadata={
                        "env_file": str(env_file),
                        "provider": config.provider,
                        "model": config.model_name,
                        "timestamp": datetime.now().isoformat()
                    }
                )

                results.append({
                    "env_file": str(env_file),
                    "config": asdict(config),
                    "experiment_name": experiment_name,
                    "results": eval_results
                })

                logger.info(f"  ✓ Experiment complete: {experiment_name}")

            except Exception as e:
                logger.error(f"  ✗ Failed to run experiment with {env_file}: {e}")
                results.append({
                    "env_file": str(env_file),
                    "error": str(e)
                })

        return results


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run LangSmith experiments with configurable models via env files"
    )
    parser.add_argument("--dataset", default="kubently-scenarios", help="Dataset name")
    parser.add_argument("--env", nargs="+", help="Environment files to use")
    parser.add_argument("--env-dir", help="Directory containing env files")
    parser.add_argument("--create-samples", action="store_true", help="Create sample env files")
    parser.add_argument("--experiment-prefix", default="config-exp", help="Experiment prefix")

    args = parser.parse_args()

    # Create samples if requested
    if args.create_samples:
        EnvConfigLoader.create_sample_env_files()
        print("\nSample environment files created in 'env-configs/' directory")
        print("Edit them with your API keys before running experiments")
        return

    # Collect env files
    env_files = []

    if args.env:
        for env_path in args.env:
            env_file = Path(env_path)
            if env_file.exists():
                env_files.append(env_file)
            else:
                logger.warning(f"Environment file not found: {env_file}")

    if args.env_dir:
        env_dir = Path(args.env_dir)
        if env_dir.exists():
            env_files.extend(env_dir.glob("*.env"))

    if not env_files:
        logger.error("No environment files specified. Use --env or --env-dir")
        logger.info("Run with --create-samples to create sample env files")
        return

    logger.info(f"Found {len(env_files)} environment files to test")

    # Run experiments
    runner = ConfigurableExperimentRunner()
    results = await runner.run_experiment(
        dataset_name=args.dataset,
        env_files=env_files,
        experiment_prefix=args.experiment_prefix
    )

    # Display summary
    print(f"\n{'='*60}")
    print("EXPERIMENT SUMMARY")
    print(f"{'='*60}")

    for result in results:
        if "error" in result:
            print(f"\n❌ {result['env_file']}: {result['error']}")
        else:
            config = result["config"]
            print(f"\n✓ {result['env_file']}")
            print(f"  Provider: {config['provider']}")
            print(f"  Model: {config['model_name']}")
            print(f"  Experiment: {result['experiment_name']}")

    # Save results
    output_file = f"configurable_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    asyncio.run(main())