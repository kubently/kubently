"""
Cloud read-operations module for the Kubently executor.

The executor pod assumes a customer-controlled, READ-ONLY cloud role via the
platform's native pod identity (EKS Pod Identity / IRSA on AWS, Workload
Identity Federation on GKE) and queries cloud logs/metrics from inside the
customer's account. No credentials are ever uploaded, stored, or transited
through the Kubently control plane — the customer scopes and revokes the role
entirely in their own IAM.

Black box interface:
- CloudOpsManager: detect identity, probe usable permissions, execute ops
- ALLOWED_CLOUD_OPERATIONS: the code-level operation allowlist (defense in
  depth on top of the customer's IAM policy)

Provider implementations (boto3 / google-cloud) are hidden behind
CloudProvider and are individually replaceable.
"""

from .base import CloudIdentity, CloudOperationResult, CloudProvider, cap_payload
from .operations import (
    ALLOWED_CLOUD_OPERATIONS,
    OPERATION_FAMILIES,
    OperationSpec,
    operations_for_provider,
)
from .manager import CloudOpsManager

__all__ = [
    "ALLOWED_CLOUD_OPERATIONS",
    "OPERATION_FAMILIES",
    "CloudIdentity",
    "CloudOperationResult",
    "CloudOpsManager",
    "CloudProvider",
    "OperationSpec",
    "cap_payload",
    "operations_for_provider",
]
