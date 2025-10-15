#!/usr/bin/env python3
"""
Enhanced Mocked Data for Kubently LangSmith Experiments
Provides realistic Kubernetes debugging workflows with proper diagnostic depth
"""

from typing import Dict, List, Any
from dataclasses import dataclass


@dataclass
class DebugStep:
    """Represents a single debugging step."""
    tool: str
    command: str
    purpose: str
    output: str


class EnhancedMockData:
    """Provides realistic debugging workflows for each scenario type."""

    def __init__(self):
        self.workflows = self._create_realistic_workflows()

    def _create_realistic_workflows(self) -> Dict[str, List[DebugStep]]:
        """Create realistic debugging workflows with proper diagnostic depth."""

        return {
            "image_pull": [
                DebugStep(
                    tool="execute_kubectl",
                    command="get pods -n {namespace}",
                    purpose="Initial triage - check pod status",
                    output="""NAME                          READY   STATUS              RESTARTS   AGE
nginx-deployment-5d59d67564   0/1     ImagePullBackOff    0          2m"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="describe pod nginx-deployment-5d59d67564 -n {namespace}",
                    purpose="Root cause analysis - check events",
                    output="""Events:
  Type     Reason     Age   From               Message
  ----     ------     ----  ----               -------
  Normal   Scheduled  3m    default-scheduler  Successfully assigned test-ns/nginx-deployment-5d59d67564 to node-1
  Normal   Pulling    2m    kubelet            Pulling image "nginy:latest"
  Warning  Failed     1m    kubelet            Failed to pull image "nginy:latest": rpc error: code = Unknown desc = Error response from daemon: pull access denied for nginy, repository does not exist
  Warning  Failed     1m    kubelet            Error: ErrImagePull
  Normal   BackOff    30s   kubelet            Back-off pulling image "nginy:latest"
  Warning  Failed     30s   kubelet            Error: ImagePullBackOff"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get deployment nginx-deployment -n {namespace} -o yaml | grep image:",
                    purpose="Verify current configuration",
                    output="""        image: nginy:latest"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="patch deployment nginx-deployment -n {namespace} --type='json' -p='[{\"op\": \"replace\", \"path\": \"/spec/template/spec/containers/0/image\", \"value\": \"nginx:latest\"}]'",
                    purpose="Apply fix - correct image name",
                    output="deployment.apps/nginx-deployment patched"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="rollout status deployment nginx-deployment -n {namespace}",
                    purpose="Monitor rollout progress",
                    output="Waiting for deployment \"nginx-deployment\" rollout to finish: 1 old replicas are pending termination..."
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get pods -n {namespace}",
                    purpose="Verify fix - check pod is running",
                    output="""NAME                          READY   STATUS    RESTARTS   AGE
nginx-deployment-7848d4b86f   1/1     Running   0          30s"""
                ),
            ],

            "crash_loop": [
                DebugStep(
                    tool="execute_kubectl",
                    command="get pods -n {namespace}",
                    purpose="Initial status check",
                    output="""NAME                          READY   STATUS             RESTARTS   AGE
app-deployment-5d59d67564     0/1     CrashLoopBackOff   5          10m"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="logs app-deployment-5d59d67564 -n {namespace} --previous",
                    purpose="Check crash logs from previous run",
                    output="""Error: Missing required environment variable 'DATABASE_URL'
Application cannot start without database configuration
Exit code: 1"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="describe pod app-deployment-5d59d67564 -n {namespace}",
                    purpose="Check events and container details",
                    output="""Containers:
  app:
    State:          Waiting
      Reason:       CrashLoopBackOff
    Last State:     Terminated
      Reason:       Error
      Exit Code:    1
      Started:      2024-01-20T10:15:00Z
      Finished:     2024-01-20T10:15:01Z
    Environment:
      APP_ENV: production
    Mounts:
      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-token
Events:
  Warning  BackOff  30s (x6 over 2m)  kubelet  Back-off restarting failed container"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get deployment app-deployment -n {namespace} -o yaml | grep -A 5 env:",
                    purpose="Check current environment configuration",
                    output="""        env:
        - name: APP_ENV
          value: production"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="set env deployment/app-deployment DATABASE_URL=postgres://localhost/myapp -n {namespace}",
                    purpose="Add missing environment variable",
                    output="deployment.apps/app-deployment env updated"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get pods -n {namespace} -w",
                    purpose="Watch pod restart with fix",
                    output="""NAME                          READY   STATUS    RESTARTS   AGE
app-deployment-6f7d8b9c5      1/1     Running   0          15s"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="logs app-deployment-6f7d8b9c5 -n {namespace} | head -5",
                    purpose="Verify application started successfully",
                    output="""[INFO] Database connection established
[INFO] Loading configuration...
[INFO] Starting web server on port 8080
[INFO] Application ready to serve requests
[INFO] Health check endpoint available at /health"""
                ),
            ],

            "service_issue": [
                DebugStep(
                    tool="execute_kubectl",
                    command="get svc,pods -n {namespace}",
                    purpose="Overview of services and pods",
                    output="""NAME                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/webapp-svc   ClusterIP   10.96.134.23    <none>        80/TCP     5m

NAME                          READY   STATUS    RESTARTS   AGE
pod/webapp-deployment-abc123  1/1     Running   0          5m"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="describe svc webapp-svc -n {namespace}",
                    purpose="Check service configuration and selector",
                    output="""Name:              webapp-svc
Namespace:         test-ns
Labels:            <none>
Annotations:       <none>
Selector:          app=web
Type:              ClusterIP
IP Family Policy:  SingleStack
IP Families:       IPv4
IP:                10.96.134.23
IPs:               10.96.134.23
Port:              <unset>  80/TCP
TargetPort:        8080/TCP
Endpoints:         <none>"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get pods -n {namespace} --show-labels",
                    purpose="Check pod labels to compare with selector",
                    output="""NAME                          READY   STATUS    RESTARTS   AGE   LABELS
webapp-deployment-abc123      1/1     Running   0          5m    app=webapp,version=v1"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get endpoints webapp-svc -n {namespace}",
                    purpose="Verify endpoints (should be empty due to mismatch)",
                    output="""NAME         ENDPOINTS   AGE
webapp-svc   <none>      5m"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="patch service webapp-svc -n {namespace} --type='json' -p='[{\"op\": \"replace\", \"path\": \"/spec/selector/app\", \"value\": \"webapp\"}]'",
                    purpose="Fix selector to match pod labels",
                    output="service/webapp-svc patched"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get endpoints webapp-svc -n {namespace}",
                    purpose="Verify endpoints now populated",
                    output="""NAME         ENDPOINTS         AGE
webapp-svc   10.244.0.5:8080   5m"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="run test-curl --image=curlimages/curl --rm -it --restart=Never -- curl webapp-svc.{namespace}.svc.cluster.local",
                    purpose="Test service connectivity",
                    output="""<!DOCTYPE html>
<html><body><h1>Welcome to webapp</h1></body></html>
pod \"test-curl\" deleted"""
                ),
            ],

            "rbac": [
                DebugStep(
                    tool="execute_kubectl",
                    command="get pods -n {namespace}",
                    purpose="Check pod status",
                    output="""NAME                          READY   STATUS    RESTARTS   AGE
rbac-test-pod                 0/1     Error     2          3m"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="logs rbac-test-pod -n {namespace}",
                    purpose="Check application logs for errors",
                    output="""Error from server (Forbidden): pods is forbidden: User \"system:serviceaccount:test-ns:default\" cannot list resource \"pods\" in API group \"\" in the namespace \"test-ns\""""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get serviceaccount -n {namespace}",
                    purpose="List service accounts in namespace",
                    output="""NAME      SECRETS   AGE
default   0         10m"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="auth can-i list pods --as=system:serviceaccount:{namespace}:default -n {namespace}",
                    purpose="Check current permissions",
                    output="no"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get rolebindings,clusterrolebindings -n {namespace} | grep default",
                    purpose="Check existing role bindings",
                    output="# No output - no bindings exist"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="create rolebinding pod-reader -n {namespace} --clusterrole=view --serviceaccount={namespace}:default",
                    purpose="Grant read permissions to service account",
                    output="rolebinding.rbac.authorization.k8s.io/pod-reader created"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="auth can-i list pods --as=system:serviceaccount:{namespace}:default -n {namespace}",
                    purpose="Verify permissions granted",
                    output="yes"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="delete pod rbac-test-pod -n {namespace}",
                    purpose="Delete failed pod to trigger restart",
                    output="pod \"rbac-test-pod\" deleted"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get pods -n {namespace}",
                    purpose="Verify pod now running successfully",
                    output="""NAME                          READY   STATUS    RESTARTS   AGE
rbac-test-pod                 1/1     Running   0          30s"""
                ),
            ],

            "configmap": [
                DebugStep(
                    tool="execute_kubectl",
                    command="get pods -n {namespace}",
                    purpose="Initial pod status",
                    output="""NAME                          READY   STATUS              RESTARTS   AGE
config-app-deployment-xyz     0/1     ContainerCreating   0          2m"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="describe pod config-app-deployment-xyz -n {namespace}",
                    purpose="Check events for mount issues",
                    output="""Events:
  Type     Reason       Age   From               Message
  ----     ------       ----  ----               -------
  Normal   Scheduled    2m    default-scheduler  Successfully assigned test-ns/config-app-deployment-xyz to node-1
  Warning  FailedMount  1m    kubelet            MountVolume.SetUp failed for volume \"config\" : configmap \"app-config\" not found"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get configmap -n {namespace}",
                    purpose="List existing configmaps",
                    output="No resources found in test-ns namespace."
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get deployment config-app-deployment -n {namespace} -o yaml | grep -A 10 volumes:",
                    purpose="Check deployment volume configuration",
                    output="""      volumes:
      - name: config
        configMap:
          name: app-config
          items:
          - key: application.properties
            path: application.properties"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="create configmap app-config -n {namespace} --from-literal=application.properties='server.port=8080\\napp.name=MyApp'",
                    purpose="Create missing configmap",
                    output="configmap/app-config created"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get configmap app-config -n {namespace}",
                    purpose="Verify configmap created",
                    output="""NAME         DATA   AGE
app-config   1      5s"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get pods -n {namespace}",
                    purpose="Check if pod transitions to running",
                    output="""NAME                          READY   STATUS    RESTARTS   AGE
config-app-deployment-xyz     1/1     Running   0          3m"""
                ),
            ],

            "memory": [
                DebugStep(
                    tool="execute_kubectl",
                    command="get pods -n {namespace}",
                    purpose="Check pod status",
                    output="""NAME                          READY   STATUS      RESTARTS   AGE
memory-app-deployment-abc     0/1     OOMKilled   3          5m"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="describe pod memory-app-deployment-abc -n {namespace}",
                    purpose="Check resource limits and last termination",
                    output="""Containers:
  memory-app:
    State:          Waiting
      Reason:       CrashLoopBackOff
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
      Started:      2024-01-20T10:20:00Z
      Finished:     2024-01-20T10:20:45Z
    Limits:
      memory:  128Mi
    Requests:
      memory:  64Mi"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="top pod memory-app-deployment-abc -n {namespace}",
                    purpose="Check current memory usage (if running)",
                    output="Error from server (NotFound): the server could not find the requested resource (pod is not running)"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="logs memory-app-deployment-abc -n {namespace} --previous | tail -10",
                    purpose="Check logs before OOM kill",
                    output="""[INFO] Processing batch 1000...
[INFO] Memory usage: 120MB
[INFO] Processing batch 1001...
[INFO] Memory usage: 125MB
[INFO] Processing batch 1002...
[INFO] Memory usage: 130MB
[WARN] Memory usage approaching limit
[INFO] Processing batch 1003...
[INFO] Memory usage: 135MB
Killed"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get deployment memory-app-deployment -n {namespace} -o yaml | grep -A 5 resources:",
                    purpose="Check current resource configuration",
                    output="""        resources:
          limits:
            memory: 128Mi
          requests:
            memory: 64Mi"""
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="patch deployment memory-app-deployment -n {namespace} --type='json' -p='[{\"op\": \"replace\", \"path\": \"/spec/template/spec/containers/0/resources/limits/memory\", \"value\": \"512Mi\"}]'",
                    purpose="Increase memory limit",
                    output="deployment.apps/memory-app-deployment patched"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="patch deployment memory-app-deployment -n {namespace} --type='json' -p='[{\"op\": \"replace\", \"path\": \"/spec/template/spec/containers/0/resources/requests/memory\", \"value\": \"256Mi\"}]'",
                    purpose="Increase memory request",
                    output="deployment.apps/memory-app-deployment patched"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="rollout restart deployment memory-app-deployment -n {namespace}",
                    purpose="Restart deployment with new limits",
                    output="deployment.apps/memory-app-deployment restarted"
                ),
                DebugStep(
                    tool="execute_kubectl",
                    command="get pods -n {namespace} -w",
                    purpose="Monitor new pod startup",
                    output="""NAME                          READY   STATUS    RESTARTS   AGE
memory-app-deployment-def     1/1     Running   0          30s"""
                ),
            ],
        }

    def get_workflow(self, scenario_type: str) -> List[DebugStep]:
        """Get the debugging workflow for a scenario type."""
        return self.workflows.get(scenario_type, self.workflows["image_pull"])

    def get_tool_sequence(self, scenario_type: str) -> List[str]:
        """Get just the tool names used in a workflow."""
        workflow = self.get_workflow(scenario_type)
        return [step.tool for step in workflow]

    def get_response_narrative(self, scenario_type: str, namespace: str) -> str:
        """Generate a narrative response from the workflow."""
        workflow = self.get_workflow(scenario_type)

        narrative_parts = [
            f"I've investigated the issue in namespace {namespace}. Here's what I found:\n"
        ]

        # Add key findings
        if scenario_type == "image_pull":
            narrative_parts.append("**Root Cause**: The deployment is trying to pull a non-existent image 'nginy:latest' (typo in nginx).")
        elif scenario_type == "crash_loop":
            narrative_parts.append("**Root Cause**: The application is crashing due to a missing DATABASE_URL environment variable.")
        elif scenario_type == "service_issue":
            narrative_parts.append("**Root Cause**: The service selector 'app=web' doesn't match the pod labels 'app=webapp'.")
        elif scenario_type == "rbac":
            narrative_parts.append("**Root Cause**: The service account lacks permissions to list pods in the namespace.")
        elif scenario_type == "configmap":
            narrative_parts.append("**Root Cause**: The pod references a ConfigMap 'app-config' that doesn't exist.")
        elif scenario_type == "memory":
            narrative_parts.append("**Root Cause**: The container is being OOMKilled due to insufficient memory limit (128Mi).")

        # Add diagnostic steps summary
        narrative_parts.append(f"\n**Diagnostic Steps** ({len(workflow)} commands executed):")
        for i, step in enumerate(workflow[:3], 1):  # Show first 3 diagnostic steps
            narrative_parts.append(f"{i}. {step.purpose}")

        # Add the fix
        fix_steps = [s for s in workflow if "fix" in s.purpose.lower() or "patch" in s.command or "create" in s.command]
        if fix_steps:
            narrative_parts.append(f"\n**Fix Applied**:\n```bash\n{fix_steps[0].command}\n```")

        # Add verification
        narrative_parts.append("\n**Verification**: The issue has been resolved and the pod is now running successfully.")

        return "\n".join(narrative_parts)


# Example usage
if __name__ == "__main__":
    mock_data = EnhancedMockData()

    # Example: Get workflow for image pull issue
    workflow = mock_data.get_workflow("image_pull")

    print("Image Pull Debugging Workflow:")
    print(f"Total steps: {len(workflow)}")
    print("\nSteps:")
    for i, step in enumerate(workflow, 1):
        print(f"{i}. [{step.tool}] {step.purpose}")
        print(f"   Command: {step.command}")
        print()

    # Generate narrative
    narrative = mock_data.get_response_narrative("image_pull", "test-ns-1")
    print("\nGenerated Narrative Response:")
    print(narrative)