### 📋 Agent Prompt to Generate K8s Failure Scenarios

You are a Kubernetes Chaos Engineering assistant. Your task is to generate a set of runnable bash scripts that deploy intentionally broken Kubernetes configurations. These scripts will be used as a test harness to evaluate the diagnostic capabilities of another AI troubleshooting agent.

**Instructions:**

1.  Generate a script for **each** of the 20 scenarios listed below.
2.  Each script must be **self-contained**. Use `cat <<EOF | kubectl apply -f -` (heredoc) for all YAML manifests so they can be run directly from the shell.
3.  Each scenario script must also include the necessary setup (e.g., create a namespace) and a final command (e.g., `kubectl get pods -w`) to show the broken state.
4.  Crucially, for each scenario, add a commented-out section (starting with `#`) that clearly explains:
    * `# SCENARIO:` The name of the test.
    * `# SYMPTOM:` What the user will observe (e.g., "Pod stuck in `Pending`" or "curl times out").
    * `# THE FIX:` A brief explanation of the misconfiguration and how to correct it.

---

### Scenario Generation List

Please generate scripts for the following 20 scenarios:

#### Part 1: Pod & Container Failures
1.  **ImagePullBackOff (Typo):** A Deployment that references an invalid image name (e.g., `busyboxx:latest`).
2.  **ImagePullBackOff (Private Registry):** A Deployment that references a valid image from a private registry but provides no `imagePullSecrets`.
3.  **CrashLoopBackOff:** A Pod that runs a simple command which immediately exits with an error code (e.g., `command: ["sh", "-c", "echo 'I am failing' && exit 1"]`).
4.  **RunContainerError (Missing ConfigMap):** A Pod that attempts to mount a `ConfigMap` as an environment variable (`envFrom`) using `configMapRef`, but the referenced ConfigMap does not exist.
5.  **RunContainerError (Missing Secret Key):** A Pod that tries to pull an environment variable from a Secret (`secretKeyRef`), but the specified `key` does not exist within that Secret (even if the Secret itself does).
6.  **OOMKilled:** A Deployment with a container that has a dangerously low memory limit (e.g., `limits: { memory: "15Mi" }`) and a command designed to consume more than that amount.
7.  **Failed Readiness Probe:** A Deployment running `nginx` where the `readinessProbe` is configured to query an HTTP path (`/ready`) that doesn't exist (it will return a 404), so the Pod never gets added to the Service endpoints.
8.  **Failing Liveness Probe:** A Deployment running `nginx` where the `livenessProbe` is configured to hit a non-existent path. This will cause the Kubelet to perpetually restart the container.

#### Part 2: Deployment & Scheduling Failures
9.  **Mismatched Labels (ReplicaSet Failure):** A Deployment where its `spec.selector.matchLabels` (e.g., `app: web`) does not match the labels in its own `spec.template.metadata.labels` (e.g., `app: frontend`). This results in a successful Deployment but 0 ready pods.
10. **Unschedulable (Insufficient Resources):** A Pod that requests an impossible amount of resources, such as `requests: { cpu: "1000", memory: "500Gi" }`, forcing it to be stuck in `Pending`.
11. **Unschedulable (Taint/Toleration):** A Pod that needs to run, but all available nodes have a `NoSchedule` taint (e.g., `app=critical:NoSchedule`) and the Pod does not have a matching `toleration`. (The script should taint a node first, if possible, or just assume one exists).
12. **PVC Unbound (Bad StorageClass):** A `PersistentVolumeClaim` that requests a `storageClassName` which does not exist in the cluster. Any Pod that tries to mount this PVC will be stuck in `Pending`.

#### Part 3: Service & Network Failures
13. **Service Selector Mismatch:** A Deployment with pods labeled `app: my-app` and a `ClusterIP` Service that is selecting for `app: my-service`. The Service will be created but will have 0 endpoints.
14. **Service Port Mismatch:** A Deployment running pods listening on port `80`, but the corresponding Service has `targetPort: 8080`. Any traffic sent to the Service's `port` will be forwarded to port 8080 on the pod and be refused.
15. **Default Deny Ingress:** Two pods (A: "client", B: "server") in the same namespace. Apply a `NetworkPolicy` that sets a default-deny ingress rule on the "server" pod *without* an accompanying rule to allow traffic from the "client" pod. A `curl` from client to server will fail.
16. **Default Deny Egress:** A pod that needs to connect to the outside internet (e.g., `curl 8.8.8.8`). Apply an egress `NetworkPolicy` that only allows traffic within the cluster (or to a specific IP block), effectively blocking all other outbound connections.
17. **Cross-Namespace Block:** Pods in "namespace-a" trying to reach a service in "namespace-b". Apply a Network Policy in "namespace-b" that only allows ingress from pods within "namespace-b", blocking the cross-namespace traffic.

#### Part 4: Config & RBAC Failures
18. **Missing ServiceAccount:** A Pod that specifies a `serviceAccountName` that does not exist. The Pod may fail to start.
19. **RBAC Forbidden (Role):** A `ServiceAccount` bound to a `Role` that only grants `get` permissions on Pods. Assign this `ServiceAccount` to a Pod that then tries to *list* or *delete* pods using `kubectl` (or the API). This will result in a 403 Forbidden error.
20. **RBAC Forbidden (ClusterRole):** A `ServiceAccount` bound to a `ClusterRole` that only grants permissions within one namespace (via a `RoleBinding` instead of a `ClusterRoleBinding`). The pod using this SA then tries to list resources in a different namespace, resulting in a 403 error.