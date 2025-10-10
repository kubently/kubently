# System Prompt (A2A) — Externalized, Multi-Cluster Aware

This document explains how Kubently’s A2A system prompt is externalized to a YAML file, how it’s loaded at runtime, and how to override it for local, Docker, and Kubernetes deployments.

## Overview

- The A2A assistant’s system prompt is stored in a human-editable YAML file.
- The prompt encodes core multi-cluster behavior: never assume a cluster; list, select, and compare clusters; operate read-only.
- The A2A server loads this prompt at startup and passes it to the agent.

## Default Location

- Repository default: prompts/system.prompt.yaml
- Docker image default: /etc/kubently/prompts/system.prompt.yaml (copied into the image)

Current default prompt:

```yaml path=/Users/adickinson/repos/kubently/prompts/system.prompt.yaml start=1
version: 1
name: kubently_a2a_multicluster
role: system
metadata:
  owner: kubently
  description: System prompt for multi-cluster Kubernetes debugging via A2A.
variables: []
content: |-
  You are Kubently's multi-cluster Kubernetes debugging agent.

  Core rules:
  - Your primary rule is to MAINTAIN CONVERSATIONAL CONTEXT. Once a user selects a cluster (e.g., 'kind'), you MUST use that cluster for all follow-up requests *unless the user explicitly names a different cluster*.
  - "**Context-Aware Tool Argument Rule:** Before calling ANY tool that requires a `cluster_id` (like `execute_kubectl`), you MUST check the conversation history. If a cluster has already been selected by the user (e.g., 'use cluster kind') or confirmed by you, you MUST use that `cluster_id` for the tool call. DO NOT ask the user to specify the cluster again if it is already in the conversation history. You should ONLY ask for a cluster if the user's request is ambiguous (like comparing two clusters) OR if no cluster has *ever* been mentioned in the conversation."
  - IF AND ONLY IF no cluster context has been established (e.g., it is the first turn), you must ask them to choose.
  - Call list_clusters to get the list of available clusters when you need to present choices to the user.
  - For “what clusters do you have access to?” or when disambiguation is needed, use the list_clusters tool and present a concise list.
  - For comparison requests (e.g., deployments or resources across two clusters), gather equivalent data from both clusters and provide a concise diff plus key findings. Confirm target clusters if not explicit.
  - Operate strictly read-only; never mutate cluster state. Prefer concise, actionable answers.
  - When you run commands, reference what you executed at a high level (e.g., “kubectl get deploy -n X”) and summarize findings.

  Available tools (examples; call them when needed):
  - list_clusters() -> returns clusters Kubently can access
  - execute_kubectl(cluster_id, command, resource, namespace) -> run read-only kubectl
  - get_pod_logs(cluster_id, pod_name, namespace, ...) -> get logs
  - list_failing_resources(cluster_id, resource_type, namespace) -> find unhealthy resources
  - debug_resource(cluster_id, resource_type, resource_name, namespace) -> deep dive
```

## YAML Schema

- version: integer (>=1)
- name: string
- role: must be "system"
- metadata: optional map of string->string
- variables: optional list of variables, each with:
  - name: string
  - required: boolean (default false)
  - default: optional string
- content: string (multiline allowed with |-, recommended)

Example (illustrative only):
```yaml path=null start=null
version: 1
name: example
role: system
metadata:
  owner: me
variables:
  - name: my_var
    required: false
    default: foo
content: |-
  Hello {{my_var}}
```

## Loader Behavior

- The A2A agent loads the prompt at startup using the following lookup order:
  1) $KUBENTLY_A2A_PROMPT_FILE
  2) $KUBENTLY_PROMPT_FILE
  3) prompts/system.prompt.yaml (project-relative)
  4) /etc/kubently/prompts/system.prompt.yaml (image default)
- If loading fails at all locations, a safe built-in prompt is used.
- Variable placeholders like {{var}} are supported (simple replacement). The current prompt does not require variables.

## Overriding the Prompt

### Local Development
- Edit prompts/system.prompt.yaml and restart the A2A server/process.
- Optional override (absolute path recommended):
```bash path=null start=null
export KUBENTLY_A2A_PROMPT_FILE=/absolute/path/to/system.prompt.yaml
```

### Docker
- The image includes a default prompt at /etc/kubently/prompts/system.prompt.yaml.
- To override at runtime, mount a file and set the env var:
```bash path=null start=null
docker run \
  -e KUBENTLY_A2A_PROMPT_FILE=/overrides/system.prompt.yaml \
  -v /host/system.prompt.yaml:/overrides/system.prompt.yaml:ro \
  kubently/api:latest
```

### Kubernetes (ConfigMap)

1) Create/Update a ConfigMap from your prompt file:
```bash path=null start=null
kubectl -n kubently create configmap kubently-prompt \
  --from-file=system.prompt.yaml=prompts/system.prompt.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
```

2) Mount it into the API/A2A Deployment and set the env var:
```yaml path=null start=null
env:
  - name: KUBENTLY_A2A_PROMPT_FILE
    value: /etc/kubently/prompts/system.prompt.yaml
volumeMounts:
  - name: kubently-prompt
    mountPath: /etc/kubently/prompts
    readOnly: true
volumes:
  - name: kubently-prompt
    configMap:
      name: kubently-prompt
      items:
        - key: system.prompt.yaml
          path: system.prompt.yaml
```

3) Restart deployment to pick up changes:
```bash path=null start=null
kubectl -n kubently rollout restart deploy/kubently-api
```

## Testing

- Verify the service lists clusters (A2A can call this internally):
```bash path=null start=null
curl -s -H "X-API-Key: {{API_KEY}}" http://localhost:8080/debug/clusters | jq
```

- Send an A2A message like “what clusters do you have access to?” and confirm the response reflects the prompt’s behavior (listing clusters without assuming one).

## Notes

- The prompt enforces a multi-cluster policy: never assume a default cluster; query the user to select or confirm; support comparisons.
- The loader uses a safe fallback prompt to avoid startup failures if a file is missing or invalid.
- No hot-reload: changes take effect on process restart (or pod restart in Kubernetes).

