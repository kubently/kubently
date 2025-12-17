"""
Canned kubectl responses for common Kubernetes scenarios.

These scenarios mirror the real test scenarios in test-automation/scenarios/
but provide deterministic responses for fast, isolated testing.

Usage:
    def test_crashloop_diagnosis(kubectl_mocker):
        kubectl_mocker.register_scenario("crashloopbackoff")
        # ... test code
"""

import sys
from pathlib import Path

# Ensure tests directory is in path for imports
TESTS_DIR = Path(__file__).parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from conftest import KubectlResponse

# =============================================================================
# Individual Response Builders
# =============================================================================

def pod_list_response(
    name: str,
    status: str,
    ready: str = "0/1",
    restarts: int = 0,
    age: str = "5m",
    namespace: str = "default"
) -> str:
    """Generate a kubectl get pods output line."""
    return f"{name}   {ready}   {status}   {restarts}   {age}"


def pod_describe_header(name: str, namespace: str = "default") -> str:
    """Generate pod describe header."""
    return f"""Name:             {name}
Namespace:        {namespace}
Priority:         0
Service Account:  default
Node:             kind-control-plane/172.18.0.2
Start Time:       Mon, 01 Jan 2024 00:00:00 +0000
Labels:           app={name}
Annotations:      <none>
Status:           Running"""


def event_line(
    event_type: str,
    reason: str,
    age: str,
    source: str,
    message: str
) -> str:
    """Generate a kubectl events line."""
    return f"{age}   {event_type}   {reason}   {source}   {message}"


# =============================================================================
# Scenario: CrashLoopBackOff
# =============================================================================

CRASHLOOPBACKOFF = {
    "get pods": KubectlResponse(
        stdout="""NAME                        READY   STATUS             RESTARTS      AGE
crashloop-app-7d4f5b6c8d   0/1     CrashLoopBackOff   5 (30s ago)   5m""",
        returncode=0
    ),
    "get pods -o wide": KubectlResponse(
        stdout="""NAME                        READY   STATUS             RESTARTS      AGE   IP           NODE
crashloop-app-7d4f5b6c8d   0/1     CrashLoopBackOff   5 (30s ago)   5m    10.244.0.5   kind-control-plane""",
        returncode=0
    ),
    "describe pod crashloop": KubectlResponse(
        stdout="""Name:             crashloop-app-7d4f5b6c8d
Namespace:        default
Priority:         0
Service Account:  default
Node:             kind-control-plane/172.18.0.2
Start Time:       Mon, 01 Jan 2024 00:00:00 +0000
Labels:           app=crashloop-app
Status:           Running
Containers:
  app:
    Container ID:   containerd://abc123
    Image:          myapp:v1
    State:          Waiting
      Reason:       CrashLoopBackOff
    Last State:     Terminated
      Reason:       Error
      Exit Code:    1
      Started:      Mon, 01 Jan 2024 00:04:30 +0000
      Finished:     Mon, 01 Jan 2024 00:04:31 +0000
    Restart Count:  5
Events:
  Type     Reason     Age                From               Message
  ----     ------     ----               ----               -------
  Normal   Scheduled  5m                 default-scheduler  Successfully assigned default/crashloop-app
  Normal   Pulled     4m (x5 over 5m)    kubelet            Container image "myapp:v1" already present
  Normal   Created    4m (x5 over 5m)    kubelet            Created container app
  Normal   Started    4m (x5 over 5m)    kubelet            Started container app
  Warning  BackOff    30s (x15 over 4m)  kubelet            Back-off restarting failed container""",
        returncode=0
    ),
    "logs crashloop": KubectlResponse(
        stdout="""2024-01-01 00:04:30 INFO  Starting application...
2024-01-01 00:04:30 INFO  Connecting to database at db.default.svc:5432...
2024-01-01 00:04:31 ERROR Connection refused: db.default.svc:5432
2024-01-01 00:04:31 FATAL Cannot connect to database. Exiting.""",
        returncode=0
    ),
    "logs crashloop --previous": KubectlResponse(
        stdout="""2024-01-01 00:04:00 INFO  Starting application...
2024-01-01 00:04:00 INFO  Connecting to database at db.default.svc:5432...
2024-01-01 00:04:01 ERROR Connection refused: db.default.svc:5432
2024-01-01 00:04:01 FATAL Cannot connect to database. Exiting.""",
        returncode=0
    ),
    "get events": KubectlResponse(
        stdout="""LAST SEEN   TYPE      REASON    OBJECT                          MESSAGE
30s         Warning   BackOff   pod/crashloop-app-7d4f5b6c8d   Back-off restarting failed container
4m          Normal    Started   pod/crashloop-app-7d4f5b6c8d   Started container app
5m          Normal    Scheduled pod/crashloop-app-7d4f5b6c8d   Successfully assigned default/crashloop-app""",
        returncode=0
    ),
}


# =============================================================================
# Scenario: ImagePullBackOff
# =============================================================================

IMAGEPULLBACKOFF = {
    "get pods": KubectlResponse(
        stdout="""NAME                         READY   STATUS             RESTARTS   AGE
imagepull-app-8f5g6h7i9j    0/1     ImagePullBackOff   0          3m""",
        returncode=0
    ),
    "describe pod imagepull": KubectlResponse(
        stdout="""Name:             imagepull-app-8f5g6h7i9j
Namespace:        default
Priority:         0
Service Account:  default
Node:             kind-control-plane/172.18.0.2
Start Time:       Mon, 01 Jan 2024 00:00:00 +0000
Labels:           app=imagepull-app
Status:           Pending
Containers:
  app:
    Container ID:
    Image:          myregistry.io/myapp:v999
    State:          Waiting
      Reason:       ImagePullBackOff
    Ready:          False
Events:
  Type     Reason     Age                From               Message
  ----     ------     ----               ----               -------
  Normal   Scheduled  3m                 default-scheduler  Successfully assigned default/imagepull-app
  Normal   Pulling    2m (x4 over 3m)    kubelet            Pulling image "myregistry.io/myapp:v999"
  Warning  Failed     2m (x4 over 3m)    kubelet            Failed to pull image "myregistry.io/myapp:v999": rpc error: code = NotFound desc = failed to pull and unpack image: failed to resolve reference: myregistry.io/myapp:v999: not found
  Warning  Failed     2m (x4 over 3m)    kubelet            Error: ErrImagePull
  Warning  BackOff    30s (x10 over 3m)  kubelet            Back-off pulling image "myregistry.io/myapp:v999"
  Warning  Failed     30s (x10 over 3m)  kubelet            Error: ImagePullBackOff""",
        returncode=0
    ),
    "get events": KubectlResponse(
        stdout="""LAST SEEN   TYPE      REASON      OBJECT                          MESSAGE
30s         Warning   BackOff     pod/imagepull-app-8f5g6h7i9j   Back-off pulling image "myregistry.io/myapp:v999"
2m          Warning   Failed      pod/imagepull-app-8f5g6h7i9j   Failed to pull image: not found
3m          Normal    Scheduled   pod/imagepull-app-8f5g6h7i9j   Successfully assigned default/imagepull-app""",
        returncode=0
    ),
}


# =============================================================================
# Scenario: OOMKilled
# =============================================================================

OOMKILLED = {
    "get pods": KubectlResponse(
        stdout="""NAME                    READY   STATUS      RESTARTS      AGE
oom-app-3c4d5e6f7g     0/1     OOMKilled   3 (1m ago)    5m""",
        returncode=0
    ),
    "describe pod oom": KubectlResponse(
        stdout="""Name:             oom-app-3c4d5e6f7g
Namespace:        default
Priority:         0
Service Account:  default
Node:             kind-control-plane/172.18.0.2
Labels:           app=oom-app
Status:           Running
Containers:
  app:
    Container ID:   containerd://def456
    Image:          memory-hungry:v1
    State:          Waiting
      Reason:       CrashLoopBackOff
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
      Started:      Mon, 01 Jan 2024 00:04:00 +0000
      Finished:     Mon, 01 Jan 2024 00:04:30 +0000
    Restart Count:  3
    Limits:
      memory:  64Mi
    Requests:
      memory:  64Mi
Events:
  Type     Reason     Age                From               Message
  ----     ------     ----               ----               -------
  Normal   Scheduled  5m                 default-scheduler  Successfully assigned default/oom-app
  Normal   Created    4m (x3 over 5m)    kubelet            Created container app
  Normal   Started    4m (x3 over 5m)    kubelet            Started container app
  Warning  BackOff    1m (x8 over 4m)    kubelet            Back-off restarting failed container""",
        returncode=0
    ),
    "logs oom": KubectlResponse(
        stdout="""Allocating memory...
Allocated 32MB
Allocated 64MB
Killed""",
        returncode=0
    ),
    "top pod oom": KubectlResponse(
        stdout="""NAME                    CPU(cores)   MEMORY(bytes)
oom-app-3c4d5e6f7g     100m         64Mi""",
        returncode=0
    ),
}


# =============================================================================
# Scenario: Pending Pod (Insufficient Resources)
# =============================================================================

PENDING_RESOURCES = {
    "get pods": KubectlResponse(
        stdout="""NAME                      READY   STATUS    RESTARTS   AGE
pending-app-9h8i7j6k5l   0/1     Pending   0          10m""",
        returncode=0
    ),
    "describe pod pending": KubectlResponse(
        stdout="""Name:             pending-app-9h8i7j6k5l
Namespace:        default
Priority:         0
Service Account:  default
Node:             <none>
Labels:           app=pending-app
Status:           Pending
Containers:
  app:
    Image:      myapp:v1
    Limits:
      cpu:     8
      memory:  64Gi
    Requests:
      cpu:     8
      memory:  64Gi
Conditions:
  Type           Status
  PodScheduled   False
Events:
  Type     Reason            Age                From               Message
  ----     ------            ----               ----               -------
  Warning  FailedScheduling  10m (x5 over 10m)  default-scheduler  0/1 nodes are available: 1 Insufficient cpu, 1 Insufficient memory. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod.""",
        returncode=0
    ),
    "get nodes": KubectlResponse(
        stdout="""NAME                 STATUS   ROLES           AGE   VERSION
kind-control-plane   Ready    control-plane   30d   v1.28.0""",
        returncode=0
    ),
    "describe nodes": KubectlResponse(
        stdout="""Name:               kind-control-plane
Roles:              control-plane
Labels:             kubernetes.io/hostname=kind-control-plane
Capacity:
  cpu:              4
  memory:           8Gi
Allocatable:
  cpu:              4
  memory:           7Gi
Allocated resources:
  cpu:              2 (50%)
  memory:           3Gi (43%)""",
        returncode=0
    ),
}


# =============================================================================
# Scenario: Service Selector Mismatch
# =============================================================================

SERVICE_SELECTOR_MISMATCH = {
    "get pods": KubectlResponse(
        stdout="""NAME                    READY   STATUS    RESTARTS   AGE
webapp-abc123def456    1/1     Running   0          5m""",
        returncode=0
    ),
    "--show-labels": KubectlResponse(
        stdout="""NAME                    READY   STATUS    RESTARTS   AGE   LABELS
webapp-abc123def456    1/1     Running   0          5m    app=webapp,version=v2""",
        returncode=0
    ),
    "get svc": KubectlResponse(
        stdout="""NAME      TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)   AGE
webapp    ClusterIP   10.96.100.50   <none>        80/TCP    5m""",
        returncode=0
    ),
    "describe svc webapp": KubectlResponse(
        stdout="""Name:              webapp
Namespace:         default
Labels:            <none>
Annotations:       <none>
Selector:          app=webapp,version=v1
Type:              ClusterIP
IP Family Policy:  SingleStack
IP Families:       IPv4
IP:                10.96.100.50
Port:              http  80/TCP
TargetPort:        8080/TCP
Endpoints:         <none>
Session Affinity:  None
Events:            <none>""",
        returncode=0
    ),
    "get endpoints webapp": KubectlResponse(
        stdout="""NAME      ENDPOINTS   AGE
webapp    <none>      5m""",
        returncode=0
    ),
}


# =============================================================================
# Scenario: Readiness Probe Failure
# =============================================================================

READINESS_PROBE_FAILURE = {
    "get pods": KubectlResponse(
        stdout="""NAME                    READY   STATUS    RESTARTS   AGE
probe-app-1a2b3c4d5e   0/1     Running   0          5m""",
        returncode=0
    ),
    "describe pod probe": KubectlResponse(
        stdout="""Name:             probe-app-1a2b3c4d5e
Namespace:        default
Priority:         0
Service Account:  default
Node:             kind-control-plane/172.18.0.2
Labels:           app=probe-app
Status:           Running
Containers:
  app:
    Container ID:   containerd://xyz789
    Image:          myapp:v1
    State:          Running
      Started:      Mon, 01 Jan 2024 00:00:00 +0000
    Ready:          False
    Restart Count:  0
    Readiness:      http-get http://:8080/health delay=5s timeout=1s period=10s #success=1 #failure=3
Conditions:
  Type              Status
  Initialized       True
  Ready             False
  ContainersReady   False
  PodScheduled      True
Events:
  Type     Reason     Age               From               Message
  ----     ------     ----              ----               -------
  Normal   Scheduled  5m                default-scheduler  Successfully assigned default/probe-app
  Normal   Pulled     5m                kubelet            Container image "myapp:v1" already present
  Normal   Created    5m                kubelet            Created container app
  Normal   Started    5m                kubelet            Started container app
  Warning  Unhealthy  30s (x20 over 5m) kubelet            Readiness probe failed: HTTP probe failed with statuscode: 503""",
        returncode=0
    ),
    "logs probe": KubectlResponse(
        stdout="""2024-01-01 00:00:00 INFO  Starting application...
2024-01-01 00:00:01 INFO  Connecting to cache service...
2024-01-01 00:00:05 WARN  Cache connection failed, health check will fail
2024-01-01 00:00:10 INFO  Serving requests (degraded mode)""",
        returncode=0
    ),
}


# =============================================================================
# Scenario: ConfigMap Missing
# =============================================================================

CONFIGMAP_MISSING = {
    "get pods": KubectlResponse(
        stdout="""NAME                      READY   STATUS                       RESTARTS   AGE
configmap-app-2b3c4d5e6f  0/1     CreateContainerConfigError   0          2m""",
        returncode=0
    ),
    "describe pod configmap": KubectlResponse(
        stdout="""Name:             configmap-app-2b3c4d5e6f
Namespace:        default
Priority:         0
Service Account:  default
Node:             kind-control-plane/172.18.0.2
Labels:           app=configmap-app
Status:           Pending
Containers:
  app:
    Container ID:
    Image:          myapp:v1
    State:          Waiting
      Reason:       CreateContainerConfigError
    Ready:          False
    Environment Variables from:
      app-config    ConfigMap  Optional: false
Events:
  Type     Reason     Age               From               Message
  ----     ------     ----              ----               -------
  Normal   Scheduled  2m                default-scheduler  Successfully assigned default/configmap-app
  Normal   Pulled     2m                kubelet            Container image "myapp:v1" already present
  Warning  Failed     30s (x8 over 2m)  kubelet            Error: configmap "app-config" not found""",
        returncode=0
    ),
    "get configmaps": KubectlResponse(
        stdout="""NAME               DATA   AGE
kube-root-ca.crt   1      30d""",
        returncode=0
    ),
}


# =============================================================================
# Scenario: Secret Missing
# =============================================================================

SECRET_MISSING = {
    "get pods": KubectlResponse(
        stdout="""NAME                     READY   STATUS                       RESTARTS   AGE
secret-app-3c4d5e6f7g   0/1     CreateContainerConfigError   0          2m""",
        returncode=0
    ),
    "describe pod secret": KubectlResponse(
        stdout="""Name:             secret-app-3c4d5e6f7g
Namespace:        default
Priority:         0
Service Account:  default
Node:             kind-control-plane/172.18.0.2
Labels:           app=secret-app
Status:           Pending
Containers:
  app:
    Container ID:
    Image:          myapp:v1
    State:          Waiting
      Reason:       CreateContainerConfigError
    Ready:          False
    Environment:
      DB_PASSWORD:  <set to the key 'password' in secret 'db-credentials'>  Optional: false
Events:
  Type     Reason     Age               From               Message
  ----     ------     ----              ----               -------
  Normal   Scheduled  2m                default-scheduler  Successfully assigned default/secret-app
  Normal   Pulled     2m                kubelet            Container image "myapp:v1" already present
  Warning  Failed     30s (x8 over 2m)  kubelet            Error: secret "db-credentials" not found""",
        returncode=0
    ),
}


# =============================================================================
# Scenario: Network Policy Blocking
# =============================================================================

NETWORK_POLICY_BLOCK = {
    "get pods": KubectlResponse(
        stdout="""NAME                    READY   STATUS    RESTARTS   AGE
backend-1a2b3c4d5e     1/1     Running   0          5m
frontend-2b3c4d5e6f    1/1     Running   0          5m""",
        returncode=0
    ),
    "get networkpolicies": KubectlResponse(
        stdout="""NAME           POD-SELECTOR   AGE
deny-all       <none>         5m
allow-egress   app=backend    5m""",
        returncode=0
    ),
    "describe networkpolicy deny-all": KubectlResponse(
        stdout="""Name:         deny-all
Namespace:    default
Labels:       <none>
Annotations:  <none>
Spec:
  PodSelector:     <none> (Coverage: all pods in the namespace)
  Allowing ingress traffic:
    <none> (Selected pods are isolated for ingress connectivity)
  Allowing egress traffic:
    <none> (Selected pods are isolated for egress connectivity)
  Policy Types: Ingress, Egress""",
        returncode=0
    ),
    "logs frontend": KubectlResponse(
        stdout="""2024-01-01 00:00:00 INFO  Starting frontend...
2024-01-01 00:00:05 ERROR Failed to connect to backend:8080 - Connection timed out
2024-01-01 00:00:15 ERROR Failed to connect to backend:8080 - Connection timed out""",
        returncode=0
    ),
}


# =============================================================================
# Scenario: RBAC Permission Denied
# =============================================================================

RBAC_FORBIDDEN = {
    "get pods": KubectlResponse(
        stdout="""NAME                    READY   STATUS    RESTARTS   AGE
rbac-test-4d5e6f7g8h   1/1     Running   0          5m""",
        returncode=0
    ),
    "auth can-i list secrets": KubectlResponse(
        stdout="no",
        returncode=1
    ),
    "auth can-i get secrets": KubectlResponse(
        stdout="no",
        returncode=1
    ),
    "auth can-i list pods": KubectlResponse(
        stdout="yes",
        returncode=0
    ),
    "logs rbac-test": KubectlResponse(
        stdout="""2024-01-01 00:00:00 INFO  Starting application...
2024-01-01 00:00:01 INFO  Loading configuration from secrets...
2024-01-01 00:00:01 ERROR secrets is forbidden: User "system:serviceaccount:default:default" cannot list resource "secrets" in API group "" in the namespace "default"
2024-01-01 00:00:01 FATAL Unable to load required secrets. Exiting.""",
        returncode=0
    ),
}


# =============================================================================
# Scenario: Healthy Cluster (Control Case)
# =============================================================================

HEALTHY_CLUSTER = {
    "get pods": KubectlResponse(
        stdout="""NAME                    READY   STATUS    RESTARTS   AGE
webapp-1a2b3c4d5e      1/1     Running   0          1h
backend-2b3c4d5e6f     1/1     Running   0          1h
database-3c4d5e6f7g    1/1     Running   0          1h""",
        returncode=0
    ),
    "get pods -A": KubectlResponse(
        stdout="""NAMESPACE     NAME                                      READY   STATUS    RESTARTS   AGE
default       webapp-1a2b3c4d5e                         1/1     Running   0          1h
default       backend-2b3c4d5e6f                        1/1     Running   0          1h
default       database-3c4d5e6f7g                       1/1     Running   0          1h
kube-system   coredns-5d78c9869d-abcde                  1/1     Running   0          30d
kube-system   etcd-kind-control-plane                   1/1     Running   0          30d
kube-system   kube-apiserver-kind-control-plane         1/1     Running   0          30d
kube-system   kube-controller-manager-kind-control-plane 1/1   Running   0          30d
kube-system   kube-proxy-xyz12                          1/1     Running   0          30d
kube-system   kube-scheduler-kind-control-plane         1/1     Running   0          30d""",
        returncode=0
    ),
    "get svc": KubectlResponse(
        stdout="""NAME       TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)    AGE
kubernetes ClusterIP   10.96.0.1      <none>        443/TCP    30d
webapp     ClusterIP   10.96.100.10   <none>        80/TCP     1h
backend    ClusterIP   10.96.100.20   <none>        8080/TCP   1h
database   ClusterIP   10.96.100.30   <none>        5432/TCP   1h""",
        returncode=0
    ),
    "get events": KubectlResponse(
        stdout="""No events found.""",
        returncode=0
    ),
}


# =============================================================================
# Master Scenario Registry
# =============================================================================

SCENARIOS = {
    "crashloopbackoff": CRASHLOOPBACKOFF,
    "imagepullbackoff": IMAGEPULLBACKOFF,
    "oomkilled": OOMKILLED,
    "pending_resources": PENDING_RESOURCES,
    "service_selector_mismatch": SERVICE_SELECTOR_MISMATCH,
    "readiness_probe_failure": READINESS_PROBE_FAILURE,
    "configmap_missing": CONFIGMAP_MISSING,
    "secret_missing": SECRET_MISSING,
    "network_policy_block": NETWORK_POLICY_BLOCK,
    "rbac_forbidden": RBAC_FORBIDDEN,
    "healthy": HEALTHY_CLUSTER,
}


def get_scenario_names() -> list:
    """Get list of all available scenario names."""
    return list(SCENARIOS.keys())


def get_scenario(name: str) -> dict:
    """Get a specific scenario by name."""
    if name not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {name}. Available: {get_scenario_names()}")
    return SCENARIOS[name]
