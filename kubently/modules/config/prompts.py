from __future__ import annotations

import os
import re

import yaml
from pydantic import BaseModel, Field, conint, validator


class VariableSpec(BaseModel):
    name: str
    required: bool = False
    default: str | None = None


class PromptSpec(BaseModel):
    version: conint(ge=1)
    name: str
    role: str
    content: str
    variables: list[VariableSpec] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @validator("role")
    def role_must_be_system(cls, v: str) -> str:
        if v != "system":
            raise ValueError("role must be 'system'")
        return v


DEFAULT_PROMPT = (
    "You are Kubently's Kubernetes debugging agent. Operate strictly read-only; "
    "never mutate cluster state. Ask the user which cluster to use if not specified."
)


def _render(content: str, values: dict[str, str]) -> str:
    """Render {{var}} placeholders using a simple replacement."""
    return re.sub(r"\{\{([^}]+)\}\}", lambda m: values.get(m.group(1).strip(), m.group(0)), content)


def _load_spec(path: str) -> PromptSpec:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return PromptSpec(**data)


def _resolve_values(spec: PromptSpec, overrides: dict[str, str] | None) -> dict[str, str]:
    overrides = overrides or {}
    resolved: dict[str, str] = {}
    for v in spec.variables:
        if v.name in overrides:
            resolved[v.name] = overrides[v.name]
        elif v.default is not None:
            resolved[v.name] = v.default
        elif v.required:
            raise ValueError(f"Missing required prompt variable: {v.name}")
    # allow additional overrides not declared in variables
    for k, v in overrides.items():
        resolved.setdefault(k, v)
    return resolved


def _candidate_paths(role: str, filename: str) -> list[str]:
    """Return candidate file paths to search for the prompt file."""
    return [
        os.getenv(f"KUBENTLY_{role.upper()}_PROMPT_FILE"),  # Role-specific env var
        os.getenv("KUBENTLY_PROMPT_FILE"),  # Generic env var
        # Project-relative default based on filename
        os.path.join(os.getcwd(), "prompts", filename),
        # Image default based on filename
        f"/etc/kubently/prompts/{filename}",
    ]


def get_prompt(
    role: str = "a2a",
    default_filename: str = "system.prompt.yaml",
    variables: dict[str, str] | None = None,
) -> str:
    """Load and render the system prompt for a role.

    Lookup order:
    - KUBENTLY_<ROLE>_PROMPT_FILE
    - KUBENTLY_PROMPT_FILE
    - prompts/<default_filename>
    - /etc/kubently/prompts/<default_filename>
    Fallback to a safe built-in prompt on error.
    """
    for path in filter(None, _candidate_paths(role, default_filename)):
        try:
            if not os.path.isfile(path):
                continue
            spec = _load_spec(path)
            values = _resolve_values(spec, variables)
            return _render(spec.content, values)
        except Exception:
            continue
    # Fallback prompt
    return DEFAULT_PROMPT
