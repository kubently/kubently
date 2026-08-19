"""Structured multi-pod log search for the Kubently executor.

`kubectl logs` reads one container at a time and ships everything it prints.
When the question is "which pods logged X since the deploy?", that means
either N round trips or flooding the channel with unfiltered logs. This
runner answers the question where the data lives: it resolves pods from a
selector, fetches each container's recent logs, filters them locally, and
returns only the matching lines (with optional context) — so raw logs never
transit Redis, the API, or the model's context.

Security model:

- Every kubectl invocation goes through the SAME runner the executor uses for
  ordinary commands (injected as `kubectl_runner`), so the dynamic whitelist
  and read-only enforcement apply unchanged. Only `get pods` and `logs` are
  ever composed here.
- User-supplied values (namespace, selector, container) are passed in
  `--flag=value` form and the pod name is validated as a DNS name, so a value
  can never be parsed as an extra kubectl flag.
- No network access of its own; no shell — argv lists only.

Results are capped at several levels (pods scanned, matches per container,
total matches, line length, total characters) and every cap that fires is
announced in the output, so the model always knows when it is looking at a
partial result.

Deliberately import-light (stdlib only) to match sse_executor.py.
"""

import json
import os
import re
import time

DEFAULT_MAX_PODS = 20
DEFAULT_MAX_MATCHES_PER_CONTAINER = 50
DEFAULT_MAX_TOTAL_MATCHES = 200
DEFAULT_MAX_LINE_CHARS = 500
DEFAULT_MAX_OUTPUT_CHARS = 20000
DEFAULT_TAIL_LINES = 2000
MAX_TAIL_LINES = 10000
MAX_QUERY_CHARS = 512
DEFAULT_TIME_BUDGET_SECONDS = 50

_DNS_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")


def build_matcher(query: str, use_regex: bool = False, case_sensitive: bool = False):
    """Return a `line -> bool` predicate for the query.

    Raises ValueError for an empty/oversized query or an invalid regex, with a
    message meant for the model to read and correct.
    """
    if not query or not isinstance(query, str):
        raise ValueError("Missing log search 'query' string.")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"Query too long ({len(query)} chars, max {MAX_QUERY_CHARS}).")
    if use_regex:
        try:
            pattern = re.compile(query, 0 if case_sensitive else re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"Invalid regex '{query}': {e}") from e
        return lambda line: pattern.search(line) is not None
    if case_sensitive:
        return lambda line: query in line
    needle = query.lower()
    return lambda line: needle in line.lower()


def filter_lines(
    lines: list,
    matcher,
    context_lines: int = 0,
    max_matches: int | None = None,
    max_line_chars: int = DEFAULT_MAX_LINE_CHARS,
) -> tuple[list, int, bool]:
    """Filter lines through `matcher`, keeping `context_lines` around each match.

    Returns (output_lines, total_match_count, capped). `total_match_count` is
    the number of matches in the input regardless of the cap, so callers can
    report "showing N of M". Context blocks are merged when they overlap and
    gaps are marked with "..." so line adjacency is never misrepresented.
    """
    match_indices = [i for i, line in enumerate(lines) if matcher(line)]
    total = len(match_indices)
    capped = max_matches is not None and total > max_matches
    if capped:
        match_indices = match_indices[:max_matches]

    if not match_indices:
        return [], total, capped

    keep: set[int] = set()
    for i in match_indices:
        keep.update(range(max(0, i - context_lines), min(len(lines), i + context_lines + 1)))

    output: list[str] = []
    previous = None
    for i in sorted(keep):
        if previous is not None and i > previous + 1:
            output.append("...")
        line = lines[i]
        if len(line) > max_line_chars:
            line = line[:max_line_chars] + " [line truncated]"
        output.append(line)
        previous = i
    return output, total, capped


def _positive_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


class LogSearchRunner:
    """Searches logs across the pods matching a selector, using the executor's
    own (whitelist-enforced) kubectl runner for every fetch."""

    def __init__(
        self,
        kubectl_runner,
        max_pods: int | None = None,
        max_matches_per_container: int | None = None,
        max_total_matches: int | None = None,
        max_line_chars: int | None = None,
        max_output_chars: int | None = None,
        time_budget_seconds: int | None = None,
    ):
        self._kubectl = kubectl_runner
        self.max_pods = max_pods or _positive_env("LOG_SEARCH_MAX_PODS", DEFAULT_MAX_PODS)
        self.max_matches_per_container = max_matches_per_container or _positive_env(
            "LOG_SEARCH_MAX_MATCHES_PER_CONTAINER", DEFAULT_MAX_MATCHES_PER_CONTAINER
        )
        self.max_total_matches = max_total_matches or _positive_env(
            "LOG_SEARCH_MAX_TOTAL_MATCHES", DEFAULT_MAX_TOTAL_MATCHES
        )
        self.max_line_chars = max_line_chars or _positive_env(
            "LOG_SEARCH_MAX_LINE_CHARS", DEFAULT_MAX_LINE_CHARS
        )
        self.max_output_chars = max_output_chars or _positive_env(
            "LOG_SEARCH_MAX_OUTPUT_CHARS", DEFAULT_MAX_OUTPUT_CHARS
        )
        self.time_budget_seconds = time_budget_seconds or _positive_env(
            "LOG_SEARCH_TIME_BUDGET", DEFAULT_TIME_BUDGET_SECONDS
        )

    def run(self, request: dict) -> dict:
        """Run one search request and return the executor result shape:
        {"success", "output", "error", "status", "return_code"}."""
        started = time.monotonic()

        namespace = request.get("namespace")
        if not namespace or not _DNS_NAME.match(str(namespace)):
            return self._error(f"Invalid or missing namespace: {namespace!r}")

        selector = request.get("selector")
        pod_name = request.get("pod_name")
        if bool(selector) == bool(pod_name):
            return self._error("Provide exactly one of 'selector' or 'pod_name'.")
        if pod_name and not _DNS_NAME.match(str(pod_name)):
            return self._error(f"Invalid pod name: {pod_name!r}")

        try:
            matcher = build_matcher(
                request.get("query"),
                use_regex=bool(request.get("use_regex")),
                case_sensitive=bool(request.get("case_sensitive")),
            )
        except ValueError as e:
            return self._error(str(e))

        targets, notes, error = self._resolve_targets(
            namespace, selector, pod_name, request.get("container")
        )
        if error:
            return self._error(error)
        if not targets:
            where = f"selector '{selector}'" if selector else f"pod '{pod_name}'"
            return self._success(
                f"No pods (or matching containers) found for {where} in namespace "
                f"'{namespace}'. Check the selector with: get pods -n {namespace} --show-labels"
            )

        tail = min(int(request.get("tail_lines") or DEFAULT_TAIL_LINES), MAX_TAIL_LINES)
        sections: list[str] = []
        no_match: list[str] = []
        errors: list[str] = []
        total_shown = 0
        total_matches = 0
        containers_searched = 0

        for pod, container in targets:
            if total_shown >= self.max_total_matches:
                notes.append(
                    f"total match cap ({self.max_total_matches}) reached — "
                    f"{len(targets) - containers_searched} container(s) not searched; "
                    "narrow the query, selector or time window"
                )
                break
            if time.monotonic() - started > self.time_budget_seconds:
                notes.append(
                    f"time budget ({self.time_budget_seconds}s) exhausted — "
                    f"{len(targets) - containers_searched} container(s) not searched; "
                    "narrow the selector or lower tail_lines"
                )
                break

            containers_searched += 1
            name = f"{pod}/{container}"
            result = self._kubectl(self._logs_args(request, namespace, pod, container, tail))
            if not result.get("success"):
                detail = (result.get("error") or result.get("output") or "").strip()
                if request.get("previous") and "previous terminated container" in detail:
                    no_match.append(f"{name} (no previous container)")
                else:
                    errors.append(
                        f"{name}: {detail.splitlines()[0] if detail else 'unknown error'}"
                    )
                continue

            lines = (result.get("stdout") or result.get("output") or "").splitlines()
            kept, matches, capped = filter_lines(
                lines,
                matcher,
                context_lines=min(int(request.get("context_lines") or 0), 10),
                max_matches=min(
                    self.max_matches_per_container, self.max_total_matches - total_shown
                ),
                max_line_chars=self.max_line_chars,
            )
            total_matches += matches
            if not matches:
                no_match.append(name)
                continue
            shown = min(
                matches, self.max_matches_per_container, self.max_total_matches - total_shown
            )
            total_shown += shown
            section = [f"=== {name} ===", *kept]
            if capped:
                section.append(
                    f"[showing {shown} of {matches} matches in this container — "
                    "narrow the query or time window]"
                )
            sections.append("\n".join(section))

        output = self._assemble(
            request,
            namespace,
            selector,
            pod_name,
            targets,
            containers_searched,
            total_matches,
            total_shown,
            sections,
            no_match,
            errors,
            notes,
            tail,
        )
        return self._success(output)

    # -- kubectl composition ------------------------------------------------

    def _resolve_targets(
        self, namespace: str, selector: str | None, pod_name: str | None, container: str | None
    ) -> tuple[list, list, str | None]:
        """Return ([(pod, container), ...], notes, error) for the search scope."""
        args = ["get", "pods", f"--namespace={namespace}", "-o", "json"]
        if selector:
            args.append(f"--selector={selector}")
        else:
            args.insert(2, pod_name)
        result = self._kubectl(args)
        if not result.get("success"):
            detail = (result.get("error") or result.get("output") or "").strip()
            return (
                [],
                [],
                f"Could not list pods: {detail.splitlines()[0] if detail else 'unknown error'}",
            )

        try:
            payload = json.loads(result.get("stdout") or result.get("output") or "")
        except ValueError:
            return [], [], "Could not parse pod list (kubectl returned non-JSON)."

        items = payload.get("items") if payload.get("kind") == "PodList" else [payload]
        items = items or []

        notes: list[str] = []
        if len(items) > self.max_pods:
            notes.append(
                f"selector matched {len(items)} pods; searching the first {self.max_pods} — "
                "narrow the selector to cover the rest"
            )
            items = items[: self.max_pods]

        targets: list[tuple[str, str]] = []
        for item in items:
            pod = (item.get("metadata") or {}).get("name")
            if not pod:
                continue
            spec = item.get("spec") or {}
            containers = [c.get("name") for c in (spec.get("containers") or []) if c.get("name")]
            containers += [
                c.get("name") for c in (spec.get("initContainers") or []) if c.get("name")
            ]
            for c in containers:
                if container and c != container:
                    continue
                targets.append((pod, c))
        return targets, notes, None

    @staticmethod
    def _logs_args(request: dict, namespace: str, pod: str, container: str, tail: int) -> list:
        args = [
            "logs",
            pod,
            f"--namespace={namespace}",
            f"--container={container}",
            f"--tail={tail}",
            "--timestamps",
        ]
        if request.get("since_time"):
            args.append(f"--since-time={request['since_time']}")
        elif request.get("since"):
            args.append(f"--since={request['since']}")
        if request.get("previous"):
            args.append("--previous")
        return args

    # -- output assembly ----------------------------------------------------

    def _assemble(
        self,
        request,
        namespace,
        selector,
        pod_name,
        targets,
        containers_searched,
        total_matches,
        total_shown,
        sections,
        no_match,
        errors,
        notes,
        tail,
    ) -> str:
        mode = "regex" if request.get("use_regex") else "substring"
        scope = f"selector '{selector}'" if selector else f"pod '{pod_name}'"
        window = (
            f"since {request['since_time']}"
            if request.get("since_time")
            else f"last {request['since']}"
            if request.get("since")
            else f"tail {tail} lines/container"
        )
        which = "previous (pre-restart) logs" if request.get("previous") else "current logs"
        header = (
            f"Searched {containers_searched} of {len(targets)} container(s) for {scope} in "
            f"namespace '{namespace}' ({mode} \"{request.get('query')}\", {window}, {which}): "
            f"{total_matches} matching line(s)"
        )
        header += f", {total_shown} shown." if total_shown < total_matches else "."

        parts = [header]
        parts.extend(sections)
        if no_match:
            parts.append("No matches: " + ", ".join(no_match))
        if errors:
            parts.append("Errors:\n" + "\n".join(f"  {e}" for e in errors))
        for note in notes:
            parts.append(f"[{note}]")
        if not sections and not errors:
            parts.append(
                "Hint: try a broader query, use_regex for alternatives (e.g. "
                "'error|exception|fail'), a longer since window, or previous=true "
                "for restarted containers."
            )

        output = "\n\n".join(parts)
        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars] + (
                f"\n[truncated at {self.max_output_chars} chars — narrow the query, "
                "selector or time window]"
            )
        return output

    @staticmethod
    def _success(output: str) -> dict:
        return {
            "success": True,
            "output": output,
            "error": None,
            "status": "SUCCESS",
            "return_code": 0,
        }

    @staticmethod
    def _error(message: str, status: str = "FAILED") -> dict:
        return {
            "success": False,
            "output": None,
            "error": message,
            "status": status,
            "return_code": -1,
        }
