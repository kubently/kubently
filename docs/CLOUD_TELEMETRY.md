# Cloud Telemetry via Workload Identity

Kubently's executor can query your cloud provider's logs, metrics, and audit
trail **from inside your own account**, using a **read-only role that you
create, scope, and can revoke at any time**.

**Zero stored credentials.** There is nothing to upload, paste, or rotate:

- No access keys or service-account keys exist anywhere in this feature.
- The executor pod picks up short-lived credentials from the platform's native
  pod identity (EKS Pod Identity / IRSA on AWS, Workload Identity on GKE).
- Query results travel over the executor's existing **outbound-only** channel.
  The Kubently control plane never holds a cloud credential — not in Redis,
  not in transit, not in memory.
- Revoking the IAM role in **your** console kills the capability instantly.
  Kubently cannot restore it.

Two independent guardrails keep this read-only:

1. **Your IAM policy** (below) grants only read permissions.
2. **A code-level operation allowlist** in the executor
   (`kubently/modules/executor/cloud/operations.py`): only these exact
   operations can run, even if the role were accidentally over-scoped.

| Provider | What the agent can query |
|----------|--------------------------|
| AWS | CloudWatch Logs Insights, CloudWatch metrics (`GetMetricData`), EKS control-plane logs, recent CloudTrail events (change correlation) |
| GCP | Cloud Logging queries, Cloud Monitoring time series, GKE audit-log slices |

Result sizes are strictly capped, and every truncated result carries an
explicit truncation note.

---

## AWS onboarding

You will create one IAM role with a read-only policy, wire it to the
executor's Kubernetes ServiceAccount, and turn the feature on in Helm.

The executor's ServiceAccount is named `<release-name>-executor`
(e.g. `kubently-executor` for a release named `kubently`). Confirm with:

```bash
kubectl get serviceaccounts -n kubently
```

### The minimal IAM policy (exact)

This is everything Kubently uses on AWS — nothing more:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "KubentlyCloudWatchLogsRead",
      "Effect": "Allow",
      "Action": [
        "logs:DescribeLogGroups",
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:FilterLogEvents"
      ],
      "Resource": "*"
    },
    {
      "Sid": "KubentlyCloudWatchMetricsRead",
      "Effect": "Allow",
      "Action": ["cloudwatch:GetMetricData"],
      "Resource": "*"
    },
    {
      "Sid": "KubentlyEKSDescribe",
      "Effect": "Allow",
      "Action": ["eks:DescribeCluster"],
      "Resource": "*"
    },
    {
      "Sid": "KubentlyCloudTrailRead",
      "Effect": "Allow",
      "Action": ["cloudtrail:LookupEvents"],
      "Resource": "*"
    }
  ]
}
```

> **Tightening further (optional):** `logs:StartQuery` and
> `logs:FilterLogEvents` support resource scoping. To restrict log access to
> EKS control-plane logs only, replace the first statement's `Resource` with
> `["arn:aws:logs:*:<ACCOUNT_ID>:log-group:/aws/eks/*", "arn:aws:logs:*:<ACCOUNT_ID>:log-group:/aws/eks/*:*"]`
> (keep `logs:GetQueryResults` and `logs:DescribeLogGroups` on `"*"` — they
> don't support per-group scoping). If you scope log groups, the executor's
> startup permission probe (an unscoped `DescribeLogGroups` with `limit=1`)
> still passes, so capability detection is unaffected.

### Option A: EKS Pod Identity (recommended — no OIDC setup, no annotations)

**CLI:**

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
CLUSTER_NAME=my-eks-cluster          # your EKS cluster name
NAMESPACE=kubently                   # namespace the executor runs in
KSA=kubently-executor                # the executor's ServiceAccount name

# 1. Ensure the Pod Identity agent add-on is installed (once per cluster)
aws eks create-addon --cluster-name "$CLUSTER_NAME" --addon-name eks-pod-identity-agent || true

# 2. Create the role, trusting the EKS Pod Identity service
cat > trust.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "pods.eks.amazonaws.com" },
      "Action": ["sts:AssumeRole", "sts:TagSession"]
    }
  ]
}
EOF
aws iam create-role \
  --role-name kubently-executor-readonly \
  --assume-role-policy-document file://trust.json \
  --description "Kubently executor: read-only cloud telemetry"

# 3. Attach the minimal policy (save the JSON above as policy.json)
aws iam put-role-policy \
  --role-name kubently-executor-readonly \
  --policy-name kubently-cloud-telemetry-readonly \
  --policy-document file://policy.json

# 4. Associate the role with the executor's ServiceAccount
aws eks create-pod-identity-association \
  --cluster-name "$CLUSTER_NAME" \
  --namespace "$NAMESPACE" \
  --service-account "$KSA" \
  --role-arn "arn:aws:iam::${ACCOUNT_ID}:role/kubently-executor-readonly"
```

**Terraform:**

```hcl
resource "aws_iam_role" "kubently_executor" {
  name        = "kubently-executor-readonly"
  description = "Kubently executor: read-only cloud telemetry"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy" "kubently_readonly" {
  name = "kubently-cloud-telemetry-readonly"
  role = aws_iam_role.kubently_executor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "KubentlyCloudWatchLogsRead"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:StartQuery",
          "logs:GetQueryResults",
          "logs:FilterLogEvents",
        ]
        Resource = "*"
      },
      {
        Sid      = "KubentlyCloudWatchMetricsRead"
        Effect   = "Allow"
        Action   = ["cloudwatch:GetMetricData"]
        Resource = "*"
      },
      {
        Sid      = "KubentlyEKSDescribe"
        Effect   = "Allow"
        Action   = ["eks:DescribeCluster"]
        Resource = "*"
      },
      {
        Sid      = "KubentlyCloudTrailRead"
        Effect   = "Allow"
        Action   = ["cloudtrail:LookupEvents"]
        Resource = "*"
      },
    ]
  })
}

resource "aws_eks_pod_identity_association" "kubently_executor" {
  cluster_name    = "my-eks-cluster"
  namespace       = "kubently"
  service_account = "kubently-executor"
  role_arn        = aws_iam_role.kubently_executor.arn
}
```

**Console:** IAM → Roles → Create role → *AWS service* → **EKS – Pod
Identity** → attach a customer-managed policy containing the JSON above →
name it `kubently-executor-readonly`. Then EKS → your cluster → *Access* →
*Pod Identity associations* → Create: namespace `kubently`, service account
`kubently-executor`, the new role.

With Pod Identity, **no Helm annotation is needed** — skip to
[Enable in Helm](#enable-in-helm).

### Option B: IRSA (IAM Roles for Service Accounts)

Use this on clusters that already standardize on IRSA, or where the Pod
Identity add-on isn't available.

**CLI:**

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
CLUSTER_NAME=my-eks-cluster
NAMESPACE=kubently
KSA=kubently-executor

# 1. Ensure the cluster has an OIDC provider (once per cluster)
eksctl utils associate-iam-oidc-provider --cluster "$CLUSTER_NAME" --approve

OIDC_PROVIDER=$(aws eks describe-cluster --name "$CLUSTER_NAME" \
  --query "cluster.identity.oidc.issuer" --output text | sed 's|https://||')

# 2. Create the role with a web-identity trust policy
cat > trust.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_PROVIDER}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_PROVIDER}:sub": "system:serviceaccount:${NAMESPACE}:${KSA}",
          "${OIDC_PROVIDER}:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
EOF
aws iam create-role \
  --role-name kubently-executor-readonly \
  --assume-role-policy-document file://trust.json

# 3. Attach the minimal policy (same policy.json as Option A)
aws iam put-role-policy \
  --role-name kubently-executor-readonly \
  --policy-name kubently-cloud-telemetry-readonly \
  --policy-document file://policy.json
```

**Terraform** (reuse `aws_iam_role_policy.kubently_readonly` from Option A):

```hcl
data "aws_eks_cluster" "this" { name = "my-eks-cluster" }

locals {
  oidc = replace(data.aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")
}

resource "aws_iam_role" "kubently_executor" {
  name = "kubently-executor-readonly"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/${local.oidc}" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc}:sub" = "system:serviceaccount:kubently:kubently-executor"
          "${local.oidc}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
}
```

IRSA requires the ServiceAccount annotation — set it in your Helm values:

```yaml
executor:
  serviceAccount:
    annotations:
      eks.amazonaws.com/role-arn: "arn:aws:iam::123456789012:role/kubently-executor-readonly"
```

---

## GCP onboarding (GKE Workload Identity)

You will create one Google service account (GSA) with viewer roles, let the
executor's Kubernetes ServiceAccount (KSA) impersonate it, and annotate the
KSA via Helm.

Prerequisite: Workload Identity enabled on the GKE cluster
(`--workload-pool=PROJECT_ID.svc.id.goog`; on by default for Autopilot).

### The minimal role grants (exact)

| Role | Grants | Used for |
|------|--------|----------|
| `roles/logging.viewer` | `logging.logEntries.list` (and log metadata) | Cloud Logging queries, GKE **Admin Activity** audit logs |
| `roles/monitoring.viewer` | `monitoring.timeSeries.list` (and metric metadata) | Cloud Monitoring time series |

> **Optional:** add `roles/logging.privateLogViewer` only if you also want the
> agent to see **Data Access** audit logs. Not required for the default
> feature set.

**CLI:**

```bash
PROJECT_ID=my-project
NAMESPACE=kubently
KSA=kubently-executor              # kubectl get sa -n kubently to confirm
GSA=kubently-executor

# 1. Create the read-only Google service account
gcloud iam service-accounts create "$GSA" \
  --project "$PROJECT_ID" \
  --display-name "Kubently executor (read-only telemetry)"

# 2. Grant the two viewer roles
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:${GSA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role roles/logging.viewer

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:${GSA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role roles/monitoring.viewer

# 3. Allow the executor's KSA to impersonate the GSA (Workload Identity)
gcloud iam service-accounts add-iam-policy-binding \
  "${GSA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project "$PROJECT_ID" \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${KSA}]"
```

**Terraform:**

```hcl
resource "google_service_account" "kubently_executor" {
  project      = "my-project"
  account_id   = "kubently-executor"
  display_name = "Kubently executor (read-only telemetry)"
}

resource "google_project_iam_member" "kubently_logging" {
  project = "my-project"
  role    = "roles/logging.viewer"
  member  = "serviceAccount:${google_service_account.kubently_executor.email}"
}

resource "google_project_iam_member" "kubently_monitoring" {
  project = "my-project"
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.kubently_executor.email}"
}

resource "google_service_account_iam_member" "kubently_wi" {
  service_account_id = google_service_account.kubently_executor.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:my-project.svc.id.goog[kubently/kubently-executor]"
}
```

**Console:** IAM & Admin → Service Accounts → Create (`kubently-executor`) →
grant *Logs Viewer* and *Monitoring Viewer*. Then on the new service account →
*Permissions* → Grant access → principal
`my-project.svc.id.goog[kubently/kubently-executor]`, role *Workload Identity
User*.

Then annotate the KSA via Helm values:

```yaml
executor:
  serviceAccount:
    annotations:
      iam.gke.io/gcp-service-account: "kubently-executor@my-project.iam.gserviceaccount.com"
```

---

## Enable in Helm

```yaml
# your executor values
executor:
  cloud:
    enabled: true
    provider: "auto"      # or "aws" / "gcp" to skip auto-detection
    # awsRegion: "us-west-2"   # optional; auto-detected on EKS
    # gcpProject: "my-project" # optional; auto-detected on GKE
```

```bash
helm upgrade kubently-executor ./deployment/helm/kubently \
  --namespace kubently --reuse-values -f cloud-values.yaml
```

## Verify

```bash
# 1. Executor detected its identity
kubectl logs -n kubently deploy/kubently-executor | grep -i "cloud identity"
#    -> "Cloud identity detected: aws (arn:aws:sts::...:assumed-role/kubently-executor-readonly/...)"

# 2. The control plane sees the capability (note: no credentials in here)
curl -s -H "X-API-Key: $KUBENTLY_API_KEY" \
  "https://your-kubently-api/api/v1/clusters/<cluster-id>/capabilities" | jq .capabilities.cloud
```

The `cloud` section lists the provider, the identity (account + principal —
never a credential), and exactly which whitelisted operations the role's
permissions make usable. The agent registers cloud tools for this cluster
only when that section is present. Detection re-runs periodically
(`executor.cloud.refreshInterval`, default hourly), so IAM changes are picked
up without a restart.

## Revoke

Everything is under your control, in your account:

- **Instant, total:** delete the pod identity association (AWS), or the
  `iam.workloadIdentityUser` binding (GCP), or the role/GSA itself.
- **Narrow instead:** edit the policy to drop a permission — the executor's
  next periodic probe notices, and the agent stops offering the affected
  operations.
- **Feature off:** set `executor.cloud.enabled: false` and upgrade.

No cleanup is needed on the Kubently side, because nothing was ever stored
there.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Log: `No cloud identity detected` | AWS: association/annotation missing or wrong ServiceAccount name; GCP: Workload Identity not enabled on the node pool, or the KSA annotation/binding mismatch |
| Capability shows provider but few operations | The role is missing a permission — each operation family is probed individually; check the policy against the minimal policy above |
| `AccessDenied` in results after working before | The role was narrowed or revoked (that's the design working) |
| Agent says cluster has no cloud access | Executor's capability TTL expired (executor offline?) or `executor.cloud.enabled` is false |
