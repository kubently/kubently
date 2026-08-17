"""Operator runbook ingestion.

Loads hand-written markdown runbooks (with lightweight YAML frontmatter) from a
directory — typically a ConfigMap mounted at /etc/kubently/runbooks — and
selects the ones relevant to an investigation so the agent can follow the
operator's documented procedure.

Black box interface: RunbookStore is the only entry point. Swap the storage
(directory, ConfigMap, object store) without touching the agent.
"""

from .store import Runbook, RunbookStore, build_runbook_context

__all__ = ["Runbook", "RunbookStore", "build_runbook_context"]
