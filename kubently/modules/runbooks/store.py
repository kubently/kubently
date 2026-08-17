"""Runbook loading, matching, and prompt-context building.

Runbooks are markdown files with lightweight YAML frontmatter:

    ---
    name: Payments CrashLoopBackOff
    match:
      alerts: ["KubePodCrashLooping", "PaymentsPod*"]
      namespaces: ["payments", "payments-*"]
      workloads: ["payment-api*"]
      topics: ["crashloop", "OOMKilled", "payment service down"]
    ---
    1. Check recent deploys of payment-api first ...

Matching is scored against the investigation's query text (a chat question, an
alert-derived query, or an A2A message — all reach the agent as text):

- ``alerts``: case-insensitive glob patterns matched against text tokens.
  Strongest signal — an alert name in the text is near-certain intent.
- ``namespaces`` / ``workloads``: case-insensitive glob patterns matched
  against text tokens (pod names keep their hyphens when tokenized, so
  ``payment-api*`` matches ``payment-api-7f9d8b-x2v``).
- ``topics``: free-text tags; a tag counts when it appears as a substring of
  the lowercased text. Weakest signal, used for chat questions that mention
  no alert or resource by name.

Reloading follows the executor whitelist's model (mtime + content signature,
periodic) but lazily on access instead of a watcher thread — the agent already
runs an event loop and a second config-watcher thread per process is the thing
sse_executor.py deliberately avoids. Kubelet syncs updated ConfigMap volumes
in ~1 minute, so edits go live without a pod restart.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_RUNBOOKS_DIR = "/etc/kubently/runbooks"
DEFAULT_RELOAD_SECONDS = 30.0
# ~2k tokens: enough for one detailed runbook or a few short ones without
# crowding out kubectl output in the context window.
DEFAULT_MAX_CHARS = 8000

# Match-score weights. An alert-name hit should always outrank any pile of
# topic hits (topics are fuzzy; alert names are exact operator intent).
ALERT_WEIGHT = 100
SELECTOR_WEIGHT = 40
TOPIC_WEIGHT = 10

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
# Tokens keep the characters Kubernetes names use (hyphens, dots, underscores)
# so selector globs can match pod/workload names embedded in prose.
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

TRUNCATION_NOTE = "\n\n[runbook truncated to fit the context budget]"

FRAMING = (
    "OPERATOR RUNBOOK CONTEXT: The runbook(s) below are the operator's documented "
    "procedure for this situation. Follow the runbook where it applies; if you "
    "deviate from it or skip steps, note the deviation and why. If a runbook "
    "informed your diagnosis, cite it by name in your final answer / root-cause "
    "summary (e.g. \"Per runbook 'Payments CrashLoopBackOff' ...\")."
)


@dataclass(frozen=True)
class Runbook:
    """One parsed runbook file."""

    name: str
    source: str  # filename, for citation/debugging
    body: str
    alerts: tuple = ()
    namespaces: tuple = ()
    workloads: tuple = ()
    topics: tuple = ()

    def has_match_criteria(self) -> bool:
        return bool(self.alerts or self.namespaces or self.workloads or self.topics)


def _as_str_tuple(value) -> tuple:
    """Frontmatter lists are hand-written; accept a bare string or a list."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return ()


def parse_runbook(text: str, source: str) -> Optional[Runbook]:
    """Parse one runbook file. Returns None (with a log line) on files that
    can't be used, so one bad file never takes down the rest of the directory.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        logger.warning("Runbook %s has no frontmatter block; skipping", source)
        return None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        logger.warning("Runbook %s has invalid frontmatter YAML (%s); skipping", source, e)
        return None
    if not isinstance(meta, dict):
        logger.warning("Runbook %s frontmatter is not a mapping; skipping", source)
        return None

    body = text[m.end():].strip()
    if not body:
        logger.warning("Runbook %s has an empty body; skipping", source)
        return None

    # Hand-written files: fall back to the filename when name is omitted.
    name = str(meta.get("name") or Path(source).stem).strip()

    match = meta.get("match") or {}
    if not isinstance(match, dict):
        logger.warning("Runbook %s 'match' is not a mapping; skipping", source)
        return None

    runbook = Runbook(
        name=name,
        source=source,
        body=body,
        alerts=_as_str_tuple(match.get("alerts")),
        namespaces=_as_str_tuple(match.get("namespaces")),
        workloads=_as_str_tuple(match.get("workloads")),
        topics=_as_str_tuple(match.get("topics")),
    )
    if not runbook.has_match_criteria():
        logger.warning(
            "Runbook %s ('%s') has no match criteria and will never be injected",
            source,
            name,
        )
    return runbook


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(text.lower()))


def _glob_hits(patterns: tuple, tokens: set) -> int:
    hits = 0
    for pattern in patterns:
        p = pattern.lower()
        if any(fnmatch.fnmatchcase(t, p) for t in tokens):
            hits += 1
    return hits


def score_runbook(runbook: Runbook, text: str) -> int:
    """Relevance of one runbook to the investigation text. 0 = no match."""
    tokens = _tokens(text)
    lowered = text.lower()
    score = 0
    score += ALERT_WEIGHT * _glob_hits(runbook.alerts, tokens)
    score += SELECTOR_WEIGHT * _glob_hits(runbook.namespaces, tokens)
    score += SELECTOR_WEIGHT * _glob_hits(runbook.workloads, tokens)
    score += TOPIC_WEIGHT * sum(1 for t in runbook.topics if t.lower() in lowered)
    return score


def _format_block(runbook: Runbook) -> str:
    return f"--- Runbook: {runbook.name} (from {runbook.source}) ---\n{runbook.body}"


def build_runbook_context(runbooks: list, max_chars: int = DEFAULT_MAX_CHARS) -> Optional[str]:
    """Build the injectable context message from ranked runbooks.

    Best-first packing: the top match always goes in (truncated if it alone
    exceeds the budget); further matches are added only if they fit whole.
    Prefer one complete, best-matching runbook over concatenating fragments
    of everything.
    """
    if not runbooks:
        return None

    parts = [FRAMING]
    used = len(FRAMING)
    included = 0
    for runbook in runbooks:
        block = _format_block(runbook)
        cost = len(block) + 2  # joining "\n\n"
        if included == 0 and used + cost > max_chars:
            # Best match doesn't fit whole: truncate rather than drop.
            keep = max_chars - used - len(TRUNCATION_NOTE) - 2
            if keep <= 0:
                break
            parts.append(block[:keep] + TRUNCATION_NOTE)
            included += 1
            break
        if used + cost > max_chars:
            break
        parts.append(block)
        used += cost
        included += 1

    if included == 0:
        return None
    return "\n\n".join(parts)


class RunbookStore:
    """Directory-backed runbook store with periodic, signature-checked reload."""

    def __init__(
        self,
        directory: Optional[str] = None,
        reload_seconds: Optional[float] = None,
        max_chars: Optional[int] = None,
    ):
        self.directory = Path(
            directory
            or os.getenv("KUBENTLY_RUNBOOKS_DIR")
            or DEFAULT_RUNBOOKS_DIR
        )
        self.reload_seconds = (
            reload_seconds
            if reload_seconds is not None
            else float(os.getenv("KUBENTLY_RUNBOOKS_RELOAD_SECONDS", DEFAULT_RELOAD_SECONDS))
        )
        self.max_chars = (
            max_chars
            if max_chars is not None
            else int(os.getenv("KUBENTLY_RUNBOOKS_MAX_CHARS", DEFAULT_MAX_CHARS))
        )
        self._lock = threading.Lock()
        self._runbooks: list = []
        self._signature: Optional[tuple] = None
        self._last_scan = 0.0
        self._load()

    def _files(self) -> list:
        if not self.directory.is_dir():
            return []
        # ConfigMap volumes hide ..data/..timestamp symlink machinery behind
        # regular-looking files; glob("*.md") only sees the projected keys.
        return sorted(p for p in self.directory.glob("*.md") if p.is_file())

    def _current_signature(self) -> tuple:
        sig = []
        for path in self._files():
            try:
                st = path.stat()
                sig.append((path.name, st.st_mtime_ns, st.st_size))
            except OSError:
                continue
        return tuple(sig)

    def _load(self) -> None:
        signature = self._current_signature()
        runbooks = []
        for path in self._files():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                logger.warning("Cannot read runbook %s: %s", path, e)
                continue
            runbook = parse_runbook(text, path.name)
            if runbook:
                runbooks.append(runbook)
        with self._lock:
            self._runbooks = runbooks
            self._signature = signature
            self._last_scan = time.monotonic()
        if runbooks:
            logger.info(
                "Loaded %d runbook(s) from %s: %s",
                len(runbooks),
                self.directory,
                ", ".join(r.name for r in runbooks),
            )

    def _maybe_reload(self) -> None:
        with self._lock:
            fresh = time.monotonic() - self._last_scan < self.reload_seconds
        if fresh:
            return
        signature = self._current_signature()
        with self._lock:
            unchanged = signature == self._signature
            if unchanged:
                self._last_scan = time.monotonic()
        if not unchanged:
            logger.info("Runbook directory %s changed; reloading", self.directory)
            self._load()

    @property
    def runbooks(self) -> list:
        self._maybe_reload()
        with self._lock:
            return list(self._runbooks)

    def select(self, text: str) -> list:
        """Runbooks relevant to the investigation text, best match first."""
        if not text or not text.strip():
            return []
        scored = [(score_runbook(r, text), r) for r in self.runbooks]
        matched = [(s, r) for s, r in scored if s > 0]
        # Ties break on name so injection order is deterministic.
        matched.sort(key=lambda sr: (-sr[0], sr[1].name))
        return [r for _, r in matched]

    def build_context(self, text: str) -> Optional[str]:
        """One-call convenience: select + format, or None when nothing matches."""
        return build_runbook_context(self.select(text), self.max_chars)
