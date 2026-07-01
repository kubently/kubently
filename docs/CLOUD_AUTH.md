# Cloud Authentication for Executors

Kubently executors carry **no cloud-provider SDK code**. The executor simply shells out
to `kubectl`, so cloud credentials are picked up the same way any in-cluster workload gets
them: from the executor pod's **ServiceAccount**, wired to a cloud IAM identity.

This means you can give an executor scoped cloud access — including reaching cluster API
servers and assuming roles for richer debugging — **without changing a line of Kubently
code**. You only set annotations on the executor ServiceAccount.

```yaml
# values.yaml
executor:
  serviceAccount:
    annotations:
      # AWS IRSA:
      eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/kubently-executor
      # GKE Workload Identity (use one or the other, per cloud):
      iam.gke.io/gcp-service-account: kubently-executor@my-project.iam.gserviceaccount.com
```

The annotation block is rendered onto `templates/executor-serviceaccount.yaml`; if you set
no annotations, none are emitted.

---

## AWS — IRSA (IAM Roles for Service Accounts)

1. Create an IAM role with a trust policy for the cluster's OIDC provider, scoped to the
   executor ServiceAccount (`system:serviceaccount:<namespace>:<release>-kubently-executor`).
2. Grant that role whatever AWS permissions the executor needs (e.g.
   `eks:DescribeCluster`, or `sts:AssumeRole` for cross-account debugging — see below).
3. Map the IAM identity to in-cluster RBAC. On EKS this is an **access entry** (or the legacy
   `aws-auth` ConfigMap) that maps the role to a Kubernetes group; Kubently's read-only
   executor RBAC then applies as usual.
4. Annotate the executor SA:

   ```yaml
   executor:
     serviceAccount:
       annotations:
         eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/kubently-executor
   ```

### Cross-account / assume-role debugging
For executors that must reach resources or clusters in another AWS account, give the
executor's IRSA role `sts:AssumeRole` on the target account's role, and configure the
target role's trust policy to allow it. Because `kubectl` and the AWS CLI in the pod read
the standard credential chain, an `AWS_*`/profile-based assume-role config (or a
`credential_process`) is honored automatically — no Kubently change required.

> Note: the cloud identity governs **authentication** to the cloud/cluster endpoint.
> What the executor may *do* in a cluster is still bounded by Kubernetes RBAC on the
> executor ServiceAccount (read-only by default) plus the command whitelist.

---

## GKE — Workload Identity

1. Create a Google service account (GSA) with the needed roles (e.g.
   `roles/container.viewer`).
2. Allow the Kubernetes SA to impersonate the GSA:

   ```bash
   gcloud iam service-accounts add-iam-policy-binding \
     kubently-executor@my-project.iam.gserviceaccount.com \
     --role roles/iam.workloadIdentityUser \
     --member "serviceAccount:my-project.svc.id.goog[<namespace>/<release>-kubently-executor]"
   ```

3. Annotate the executor SA:

   ```yaml
   executor:
     serviceAccount:
       annotations:
         iam.gke.io/gcp-service-account: kubently-executor@my-project.iam.gserviceaccount.com
   ```

As with AWS, Workload Identity authenticates the executor to GCP/GKE; Kubernetes RBAC and
the command whitelist still bound what it can run.

---

## Why this design

Keeping cloud auth in deployment config rather than executor code is what makes Kubently
**vendor-neutral**: the same executor image runs against EKS, GKE, AKS, or vanilla
Kubernetes, and the only thing that changes per cloud is a ServiceAccount annotation. There
is no provider SDK to maintain, no per-cloud build, and no credentials baked into the image.
