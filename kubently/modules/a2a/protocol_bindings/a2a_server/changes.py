"""Change-correlation logic for the get_recent_changes tool.

"What changed before this incident?" is the first question of every
investigation. This module turns raw evidence — workload metadata, ReplicaSet
revision annotations, rollout history, Kubernetes events, helm release
history, ArgoCD sync history — into one compact, chronologically sorted
changes timeline the model can correlate against first-error timestamps.

Deliberately import-light (stdlib only) so unit tests can exercise the
parsing/formatting logic without pulling the langchain/a2a stack that
agent.py requires. All I/O (HTTP calls to the Kubently API) stays in
agent.py; everything here is pure input -> output.

Availability contract: kubectl- and helm-sourced changes are always
attempted (helm degrades gracefully when the executor has it disabled).
ArgoCD queries are attempted ONLY when ARGOCD_URL is set in the A2A server's
environment — on the control plane the variable only switches the source on;
the executor's own ARGOCD_URL is what actually gets dialed.
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

ARGOCD_URL_ENV = "ARGOCD_URL"

# Kubernetes annotations/labels that tie a resource to its change sources.
HELM_RELEASE_ANNOTATION = "meta.helm.sh/release-name"
HELM_NAMESPACE_ANNOTATION = "meta.helm.sh/release-namespace"
ARGOCD_TRACKING_ANNOTATION = "argocd.argoproj.io/tracking-id"
INSTANCE_LABEL = "app.kubernetes.io/instance"
MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
CHANGE_CAUSE_ANNOTATION = "kubernetes.io/change-cause"

_WINDOW_PATTERN = re.compile(r"^(\d+)([mhd])$")
_WINDOW_UNITS = {"m": "minutes", "h": "hours", "d": "days"}

DEFAULT_WINDOW = "24h"
MAX_TIMELINE_ENTRIES = 60
MAX_MESSAGE_CHARS = 160


def argocd_enabled() -> bool:
    """Whether ArgoCD should be queried as a change source."""
    return bool(os.getenv(ARGOCD_URL_ENV, "").strip())


def parse_window(window: str | None) -> timedelta:
    """Parse a '30m' / '6h' / '2d' window; fall back to the default."""
    match = _WINDOW_PATTERN.match((window or DEFAULT_WINDOW).strip().lower())
    if not match:
        match = _WINDOW_PATTERN.match(DEFAULT_WINDOW)
    value, unit = int(match.group(1)), match.group(2)
    return timedelta(**{_WINDOW_UNITS[unit]: value})


def parse_timestamp(value) -> datetime | None:
    """Parse RFC3339-ish timestamps as produced by kubectl/helm/ArgoCD."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    # Helm emits fractional seconds + a numeric offset; kubectl emits 'Z'.
    text = re.sub(r"(\.\d+)", "", text).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass
class ChangeEntry:
    """One row of the timeline."""

    timestamp: datetime | None
    source: str  # rollout | helm | argocd | event
    summary: str
    # Sort tiebreaker so undated entries keep insertion order.
    order: int = 0


@dataclass
class WorkloadInfo:
    """Change-relevant metadata extracted from one workload object."""

    kind: str = ""
    name: str = ""
    helm_release: str | None = None
    helm_namespace: str | None = None
    argocd_app: str | None = None
    revision: str | None = None
    images: list = field(default_factory=list)


def extract_workload_info(obj: dict) -> WorkloadInfo:
    """Pull helm/argocd ownership and revision info from a workload object."""
    metadata = obj.get("metadata") or {}
    annotations = metadata.get("annotations") or {}
    labels = metadata.get("labels") or {}

    info = WorkloadInfo(
        kind=obj.get("kind") or "",
        name=metadata.get("name") or "",
        helm_release=annotations.get(HELM_RELEASE_ANNOTATION),
        helm_namespace=annotations.get(HELM_NAMESPACE_ANNOTATION),
        revision=annotations.get(REVISION_ANNOTATION),
    )

    # ArgoCD tracking annotation is authoritative: "<app>:<group>/<kind>:<ns>/<name>".
    tracking = annotations.get(ARGOCD_TRACKING_ANNOTATION)
    if tracking and ":" in tracking:
        info.argocd_app = tracking.split(":", 1)[0]
    elif labels.get(MANAGED_BY_LABEL) != "Helm" and labels.get(INSTANCE_LABEL):
        # The default ArgoCD tracking label; ambiguous (Helm sets it too), so
        # only trust it when the resource is not Helm-managed.
        info.argocd_app = labels.get(INSTANCE_LABEL)

    containers = ((obj.get("spec") or {}).get("template") or {}).get("spec", {}).get(
        "containers"
    ) or []
    info.images = [c.get("image") for c in containers if c.get("image")]
    return info


def extract_workloads(workload_list_json: str) -> list:
    """Parse `kubectl get deploy,sts,ds -o json` output into WorkloadInfo list."""
    try:
        body = json.loads(workload_list_json)
    except (ValueError, TypeError):
        return []
    items = body.get("items") if isinstance(body, dict) else None
    if items is None and isinstance(body, dict) and body.get("kind") != "List":
        items = [body]  # single-object get
    return [extract_workload_info(obj) for obj in (items or [])]


def replicaset_changes(rs_list_json: str, deployment: str | None = None) -> list:
    """Turn ReplicaSet revision annotations into dated rollout entries.

    `kubectl rollout history` has revisions but no dates; the ReplicaSet
    behind each revision has a creationTimestamp. Together they date each
    rollout. Note a rollback re-uses an old ReplicaSet, so its entry keeps
    the original creation date — events cover the rollback time itself.
    """
    try:
        body = json.loads(rs_list_json)
    except (ValueError, TypeError):
        return []

    entries = []
    for rs in body.get("items") or []:
        metadata = rs.get("metadata") or {}
        annotations = metadata.get("annotations") or {}
        revision = annotations.get(REVISION_ANNOTATION)
        if not revision:
            continue

        owner = next(
            (
                ref.get("name")
                for ref in metadata.get("ownerReferences") or []
                if ref.get("kind") == "Deployment"
            ),
            None,
        )
        if deployment and owner != deployment:
            continue

        containers = ((rs.get("spec") or {}).get("template") or {}).get("spec", {}).get(
            "containers"
        ) or []
        images = ", ".join(c.get("image", "?") for c in containers)
        replicas = (rs.get("spec") or {}).get("replicas")
        state = "active" if replicas else "scaled to 0"
        cause = annotations.get(CHANGE_CAUSE_ANNOTATION)

        summary = f"Deployment {owner or '?'} revision {revision} created ({state}) — images: {images or '?'}"
        if cause:
            summary += f" — cause: {cause}"
        entries.append(
            ChangeEntry(
                timestamp=parse_timestamp(metadata.get("creationTimestamp")),
                source="rollout",
                summary=summary,
            )
        )
    return entries


def parse_rollout_history(text: str, workload: str) -> list:
    """Parse `kubectl rollout history` table output into undated entries.

    Only revisions with a real CHANGE-CAUSE add information beyond the
    ReplicaSet entries, so <none> rows are skipped.
    """
    entries = []
    for line in (text or "").splitlines():
        match = re.match(r"^(\d+)\s+(.*\S)\s*$", line.strip())
        if not match:
            continue
        revision, cause = match.group(1), match.group(2)
        if cause == "<none>":
            continue
        entries.append(
            ChangeEntry(
                timestamp=None,
                source="rollout",
                summary=f"{workload} revision {revision} change-cause: {cause}",
            )
        )
    return entries


def event_changes(events_json: str, name_prefixes: list | None = None) -> list:
    """Turn `kubectl get events -o json` output into timeline entries.

    Normal + Warning events both matter: the Normal ones (ScalingReplicaSet,
    Pulled with a new image, Killing) ARE the record of the change; the
    Warning ones (BackOff, Unhealthy, FailedMount) are its consequences.

    name_prefixes scopes to a resource and its children: a Deployment 'api'
    owns ReplicaSet 'api-<hash>' and Pods 'api-<hash>-<id>', so prefix
    matching on 'api' catches the whole ownership chain.
    """
    try:
        body = json.loads(events_json)
    except (ValueError, TypeError):
        return []

    entries = []
    for event in body.get("items") or []:
        involved = event.get("involvedObject") or {}
        obj_name = involved.get("name") or ""
        if name_prefixes is not None and not any(
            obj_name == p or obj_name.startswith(f"{p}-") for p in name_prefixes
        ):
            continue

        timestamp = (
            parse_timestamp(event.get("lastTimestamp"))
            or parse_timestamp(event.get("eventTime"))
            or parse_timestamp((event.get("metadata") or {}).get("creationTimestamp"))
        )
        message = (event.get("message") or "").replace("\n", " ")[:MAX_MESSAGE_CHARS]
        count = event.get("count") or 1
        count_note = f" (x{count})" if count and count > 1 else ""
        entries.append(
            ChangeEntry(
                timestamp=timestamp,
                source="event",
                summary=(
                    f"[{event.get('type', '?')}/{event.get('reason', '?')}] "
                    f"{involved.get('kind', '?')}/{obj_name}: {message}{count_note}"
                ),
            )
        )
    return entries


def helm_history_changes(history_json: str, release: str) -> list:
    """Turn `helm history -o json` output into timeline entries."""
    try:
        revisions = json.loads(history_json)
    except (ValueError, TypeError):
        return []
    if not isinstance(revisions, list):
        return []

    entries = []
    for rev in revisions:
        description = (rev.get("description") or "").strip()
        summary = (
            f"helm release {release} revision {rev.get('revision', '?')} "
            f"[{rev.get('status', '?')}] chart {rev.get('chart', '?')}"
        )
        if description:
            summary += f" — {description[:MAX_MESSAGE_CHARS]}"
        entries.append(
            ChangeEntry(
                timestamp=parse_timestamp(rev.get("updated")),
                source="helm",
                summary=summary,
            )
        )
    return entries


def parse_helm_releases(list_json: str) -> list:
    """Parse `helm list -o json` into [(release, namespace, updated)] tuples."""
    try:
        releases = json.loads(list_json)
    except (ValueError, TypeError):
        return []
    if not isinstance(releases, list):
        return []
    return [
        (r.get("name"), r.get("namespace"), r.get("updated")) for r in releases if r.get("name")
    ]


def argocd_changes(app_json: str) -> list:
    """Turn a compacted ArgoCD application (executor output) into entries."""
    try:
        app = json.loads(app_json)
    except (ValueError, TypeError):
        return []
    if not isinstance(app, dict):
        return []

    name = app.get("name") or "?"
    entries = []
    for deploy in app.get("history") or []:
        revision = (deploy.get("revision") or "?")[:12]
        source = deploy.get("source") or {}
        target = source.get("targetRevision") or source.get("chart") or ""
        target_note = f" (target {target})" if target else ""
        entries.append(
            ChangeEntry(
                timestamp=parse_timestamp(deploy.get("deployedAt")),
                source="argocd",
                summary=(
                    f"ArgoCD app {name} sync #{deploy.get('id', '?')} "
                    f"deployed revision {revision}{target_note}"
                ),
            )
        )

    sync_status, health = app.get("syncStatus"), app.get("healthStatus")
    if sync_status and sync_status != "Synced":
        last_op = app.get("lastOperation") or {}
        entries.append(
            ChangeEntry(
                timestamp=parse_timestamp(last_op.get("finishedAt")),
                source="argocd",
                summary=(
                    f"ArgoCD app {name} is currently {sync_status} "
                    f"(health: {health or '?'}) — live state has drifted from git"
                ),
            )
        )
    return entries


def build_timeline(
    entries: list,
    window: timedelta,
    scope: str,
    sources_unavailable: dict | None = None,
    now: datetime | None = None,
) -> str:
    """Assemble the final timeline text the model reasons over.

    Dated entries inside the window come first, oldest -> newest (so the
    reading order matches cause -> effect). Undated entries (rollout
    change-causes, revisions predating the window's events) follow in their
    own section — presence without a false timestamp beats omission.
    """
    now = now or datetime.now(UTC)
    cutoff = now - window

    dated = [e for e in entries if e.timestamp and e.timestamp >= cutoff]
    undated = [e for e in entries if not e.timestamp]
    older = len([e for e in entries if e.timestamp and e.timestamp < cutoff])

    dated.sort(key=lambda e: (e.timestamp, e.order))

    truncated = 0
    if len(dated) > MAX_TIMELINE_ENTRIES:
        truncated = len(dated) - MAX_TIMELINE_ENTRIES
        dated = dated[-MAX_TIMELINE_ENTRIES:]  # keep the most recent

    lines = [
        f"=== Changes timeline: {scope} (last {_format_window(window)}, "
        f"until {now.strftime('%Y-%m-%d %H:%M:%S')} UTC) ==="
    ]

    if not dated and not undated:
        lines.append("No changes found in this window from any source.")
    for entry in dated:
        stamp = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"{stamp}  [{entry.source}] {entry.summary}")

    if undated:
        lines.append("--- Known changes without timestamps ---")
        lines.extend(f"           [{e.source}] {e.summary}" for e in undated[:10])

    notes = []
    if truncated:
        notes.append(f"{truncated} older in-window entries omitted (cap {MAX_TIMELINE_ENTRIES})")
    if older:
        notes.append(f"{older} entries predate the window (widen the window to see them)")
    notes.append(
        "Kubernetes events expire (default ~1h TTL); an empty event list does "
        "not mean nothing happened earlier in the window"
    )
    for source, reason in (sources_unavailable or {}).items():
        notes.append(f"{source} unavailable: {reason}")
    lines.append("--- Notes ---")
    lines.extend(f"- {n}" for n in notes)

    lines.append(
        "Correlate: compare each change timestamp above with the first-error "
        "timestamp (events/logs). Name the correlated change explicitly in the RCA."
    )
    return "\n".join(lines)


def _format_window(window: timedelta) -> str:
    total_minutes = int(window.total_seconds() // 60)
    if total_minutes % (60 * 24) == 0:
        return f"{total_minutes // (60 * 24)}d"
    if total_minutes % 60 == 0:
        return f"{total_minutes // 60}h"
    return f"{total_minutes}m"
