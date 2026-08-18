# GitOps PR Remediation

Kubently's agent can **propose** manifest fixes as pull requests against your
GitOps manifests repository. A human reviews and merges the PR; your GitOps
controller (ArgoCD, Flux) applies it. This closes the loop from diagnosis to
fix **without giving the agent any write access to your clusters** — the
read-only trust posture is unchanged.

**Default: OFF.** Nothing changes until you configure a remediation target.

## How it works

1. An investigation reaches a high-confidence root cause that maps to a
   concrete manifest change (image tag, resource limit, env var, replica
   count, probe threshold, ...).
2. The agent fetches the current file from your manifests repo with
   `get_manifest_file` — it diffs against reality, never against a
   remembered or imagined manifest.
3. The agent calls `propose_fix_pr`: a branch is created off the configured
   base branch, the proposed content is committed, and a PR is opened. The
   PR body contains the investigation evidence (including the
   change-correlation citation) and is clearly marked
   **machine-proposed, pending human review**.
4. The PR URL is returned into the investigation's RCA. A human reviews,
   merges (or closes), and GitOps applies the merged change.

The agent **cannot merge**: no merge capability exists in its tool surface,
and the PR body says so explicitly. Protect the base branch on the Git host
so review is enforced by the platform, not by convention.

## Where it runs (and why)

PR creation runs on the **API server (control plane)**, never on executors:

- The Git host is an external service, not something inside a monitored
  cluster — there is nothing cluster-local about the manifests repo, so the
  executor channel (which exists to reach in-cluster services like Loki or
  Prometheus) buys nothing here.
- Executors are Kubently's read-only arm. They never hold write-capable
  credentials; giving each remote executor a Git write token would multiply
  the credential blast radius across every customer cluster.
- One API-side token means one secret to scope, one place to rotate, and
  fix PRs still work when a cluster's executor is offline.

## Guardrails

| Guardrail | Mechanism |
|-----------|-----------|
| Propose-only | The tool surface has no merge/approve capability; PR bodies carry a machine-proposed marker and an explicit do-not-merge-without-review note |
| Size caps | Proposals above `maxFiles` (default 5) or `maxLines` changed (default 200, measured by diff — a one-field edit in a 500-line manifest counts 2 lines) are refused before any write reaches the Git host |
| Evidence required | `propose_fix_pr` refuses proposals without an evidence summary; prompt guidance requires a high-confidence RCA, a minimal fix, and a change-correlation citation (`get_recent_changes`) |
| Fetch-before-edit | Prompt guidance requires basing edits on `get_manifest_file` output; identical-content proposals are rejected (catches already-merged fixes and repo/cluster drift) |
| Token isolation | The token is read from the environment inside the provider client. It is never a tool argument, never in tool output, never in interceptor traces, and provider error bodies are redacted before they can reach model context |
| Path hygiene | Repo paths are validated (no traversal, no absolute paths) before any request is made |

## Configuration

### 1. Create a repo-scoped token (security-critical)

**Scope the token to the manifests repository only.** The token can create
branches and PRs in whatever it is granted — grant it exactly one repo:

- **GitHub**: use a *fine-grained personal access token* (or a GitHub App
  installation token) restricted to the single manifests repository, with
  permissions **Contents: Read and write** and **Pull requests: Read and
  write**. Do not use a classic PAT with the broad `repo` scope, and never a
  token that can see other repositories.
- **GitLab**: use a *project access token* on the single manifests project
  with the **Developer** role and the **api** scope. Do not use a personal
  or group token.

Also:
- Protect the base branch (required reviews) so merging always involves a
  human, enforced by the Git host.
- Rotate the token on your normal credential schedule; rotation is one
  secret update + API pod restart.

### 2. Create the secret (never in values files)

```bash
kubectl create secret generic kubently-gitops-token \
  --from-literal=token="<repo-scoped-token>" \
  --namespace kubently
```

### 3. Enable in Helm values

```yaml
gitRemediation:
  enabled: true
  provider: "github"          # or "gitlab"
  repo: "acme/k8s-manifests"  # GitHub: owner/repo; GitLab: full project path
  baseBranch: "main"
  existingSecret: "kubently-gitops-token"
  # existingSecretKey: "token"
  # maxFiles: 5
  # maxLines: 200
  # apiBase: ""               # GitHub Enterprise / self-hosted GitLab API base
```

For GitHub Enterprise set `apiBase: "https://github.example.com/api/v3"`;
for self-hosted GitLab set `apiBase: "https://gitlab.example.com/api/v4"`.

### Environment variables (set by the chart)

| Variable | Description |
|----------|-------------|
| `KUBENTLY_GITOPS_PROVIDER` | `github` or `gitlab` |
| `KUBENTLY_GITOPS_REPO` | Manifests repo (GitHub `owner/repo`, GitLab project path) |
| `KUBENTLY_GITOPS_BASE_BRANCH` | Base branch for proposals (default `main`) |
| `KUBENTLY_GITOPS_TOKEN` | Repo-scoped token, from the secret |
| `KUBENTLY_GITOPS_MAX_FILES` | File cap per proposal (default 5) |
| `KUBENTLY_GITOPS_MAX_LINES` | Changed-line cap per proposal (default 200) |
| `KUBENTLY_GITOPS_API_BASE` | API base override for GHE / self-hosted GitLab |

All three of provider, repo and token must be present or the tools are not
registered (a partial configuration logs a warning and stays off). When off,
the system prompt never mentions the tools.

## Example

```
User: payments pods are OOMKilled since this morning

Agent: (investigates: OOMKilled events, memory metrics at the limit,
        get_recent_changes shows revision 42 halved the memory limit)
       Root cause: revision 42 (deployed 09:14) reduced
       payments/deployment/payments memory limit 512Mi -> 256Mi; working set
       is ~430Mi. Proposed fix PR (pending human review):
       https://github.com/acme/k8s-manifests/pull/87 — restores the 512Mi
       limit. A human must review and merge; ArgoCD will then apply it.
```

## Troubleshooting

- **Tools missing**: check the API logs for "GitOps remediation partially
  configured" — it lists which of provider/repo/token is absent.
- **`REFUSED` size cap**: the fix touches too many files/lines. That is the
  guardrail working; make the change by hand or raise the caps deliberately.
- **HTTP 404 from provider on a repo that exists**: fine-grained tokens
  return 404 for repos they are not granted — re-check the token's
  repository access.
- **Identical-content rejection**: the fix may already be merged, or the
  live cluster has drifted from git — investigate the drift first.
