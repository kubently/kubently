Future Enhancement: Secure Executor Authentication with Asymmetric Key Bootstrapping
1. Executive Summary
This document proposes a significant security enhancement to the Kubently Executor authentication model. The current reliance on a static, long-lived bearer token presents a security risk, as a leaked token could provide an attacker with indefinite access.

The proposed solution is to replace the static token model with an automated, asymmetric key bootstrapping process. This model treats the initial static token as a one-time-use Bootstrap Token for registration. The Executor will then generate its own private/public key pair, persist its private key within its own Kubernetes cluster using a Secret, and register its public key with the Kubently API.

All subsequent authentication will be performed by signing challenges with the private key, which is then verified by the API using the stored public key. This provides cryptographically secure, auto-rotating credentials that survive pod restarts and extended downtime, while still only requiring the operator to provision a single secret at install time.

2. Current State & Security Gap
The current authentication model for Executors is defined in the AuthModule [cite: kubently/modules/auth/auth.py].

Mechanism: An Executor authenticates by presenting a static, long-lived token (e.g., from an environment variable like EXECUTOR_TOKEN_KUBENTLY) in a Bearer header.

Validation: The verify_agent (soon to be verify_executor) function validates this token against a value in Redis or a static environment variable.

Provisioning: The operator provisions this static token into the remote cluster via a Kubernetes Secret [cite: deployment/helm/kubently/templates/secrets.yaml].

The Security Flaw
The root of the security issue is the static, long-lived nature of the token.

High-Impact Leakage: If this token is ever compromised (e.g., from a misconfigured Secret, a git leak, or an insecure operator workflow), an attacker can impersonate that cluster's Executor indefinitely.

No Automated Rotation: The token is permanent and never changes unless a human operator manually intervenes to generate and redeploy a new one. This rarely happens in practice, leaving a perpetual attack window open.

Brittle Revocation: Revocation requires a manual operation on the API server (deleting the token from Redis or updating environment variables and restarting).

3. Proposed Architecture: Asymmetric Key Bootstrapping
This new architecture eliminates the long-lived static token by using it only to bootstrap a more secure, public-key-based identity that is persisted within the Executor's own cluster.

The process is divided into two distinct phases: a one-time registration and a repeatable, secure login flow.

Phase 1: First Boot & Registration (One-Time)
This flow occurs only the very first time an Executor pod starts up in a new cluster.

Provisioning: The operator deploys the Executor with the static Bootstrap Token in its K8s Secret, just as they do today.

Key Generation: On startup, the Executor pod checks for the existence of a local K8s Secret named kubently-executor-identity. Since it doesn't exist, the Executor generates a new public/private key pair (e.g., using RSA or ECDSA).

Secure Persistence: The Executor creates the kubently-executor-identity Secret in its own namespace and securely saves its Private Key there. This private key will never leave the remote Kubernetes cluster.

Registration Call: The Executor makes a one-time call to a new API endpoint, POST /executor/register, sending two pieces of information:

The static Bootstrap Token.

Its newly generated Public Key.

API Validation: The Kubently API's AuthModule validates the Bootstrap Token. If valid, it stores the Public Key in Redis, associating it with the cluster_id. The API then deactivates the Bootstrap Token, rendering it useless for future registrations.

Phase 2: Secure Login Flow (On Every Restart & Token Expiry)
This is the normal, repeatable flow that occurs on every subsequent pod start (e.g., after a crash, deployment update, or node failure).

Load Identity: On startup, the Executor pod checks for the kubently-executor-identity Secret. It finds the secret and loads its Private Key into memory.

Challenge-Response: The Executor calls a standard POST /executor/login endpoint. To authenticate, it generates a challenge (e.g., a signed timestamp or a server-provided nonce) and signs it with its Private Key, creating a JSON Web Signature (JWS).

Cryptographic Verification: The API receives the signed request. It retrieves the registered Public Key for that cluster_id from Redis and cryptographically verifies the signature.

Issue Short-Lived Token: If the signature is valid, the API issues a standard, short-lived Access JWT (e.g., 1 hour validity).

Normal Operation: The Executor uses this short-lived Access JWT to authenticate to all other API endpoints (like the /agent/stream SSE endpoint). When the token expires, it simply repeats this login flow to get a new one.

4. Resilience to Kubernetes Failures
This model is inherently resilient to the ephemeral nature of Kubernetes pods.

ImagePullBackOff / Pre-Registration Failure: If a pod fails before it can register, the Bootstrap Token remains valid. When the issue is fixed, the new pod starts, performs the "First Boot" registration successfully, and normal operation begins.

CrashLoopBackOff / Post-Registration Failure: If a pod crashes after registering, the kubently-executor-identity Secret (containing the private key) is safely persisted in Kubernetes. When a new pod is scheduled, it finds the existing Secret, loads its identity, and proceeds directly to the secure login flow. No re-registration is needed.

5. Implementation Plan
Step 1: Executor Logic Changes
Add Cryptography Library: Add a dependency like cryptography to the Executor's requirements.txt.

Add K8s Client Library: Add kubernetes client to manage the identity Secret.

Implement Startup Logic:

On start, check for the kubently-executor-identity Secret.

If not found:

Generate key pair.

Create the Secret to store the private key.

Call POST /executor/register with the Bootstrap Token and public key.

If found:

Load the private key.

Call POST /executor/login to get the initial Access JWT.

Implement Token Refresh Logic:

Before the Access JWT expires (or upon a 401 Unauthorized), automatically call /executor/login again to get a new Access JWT.

Step 2: API (AuthModule) Changes
Implement POST /executor/register Endpoint:

Validates the Bootstrap Token.

Stores the provided Public Key in Redis against the cluster_id.

Disables the Bootstrap Token.

Implement POST /executor/login Endpoint:

Receives the signed challenge (JWS).

Retrieves the Public Key for the cluster_id from Redis.

Verifies the JWS signature.

If valid, issues a short-lived (e.g., 1 hour) Access JWT.

Update verify_executor Dependency:

Modify this function to validate the short-lived Access JWT instead of the static token.

Step 3: RBAC and Permissions
The Executor's ServiceAccount will require one new RBAC permission:

The ability to get and create a Secret resource, but restricted to the specific resourceName of kubently-executor-identity within its own namespace. This is a minimal, low-risk permission grant.

# Example RBAC rule addition
- apiGroups: [""]
  resources: ["secrets"]
  resourceNames: ["kubently-executor-identity"]
  verbs: ["get", "create"]

Step 4: Documentation
Update all relevant documentation (README.md, DEPLOYMENT.md, etc.) to reflect the new authentication flow and the single, one-time setup step for the Bootstrap Token.

6. Security Benefits
Eliminates Long-Lived Static Tokens: The primary security risk is removed. Operational credentials (Access JWTs) expire quickly, dramatically reducing the attack window.

Private Key Isolation: The Executor's true "secret" (its private key) never leaves its local Kubernetes cluster. It is never transmitted over the network.

Compromise Recovery: If an Executor's cluster is compromised, an operator can simply instruct the API to delete the registered public key for that cluster_id, permanently revoking all access without needing to redeploy or re-key any other part of the system.

One-Time Use Bootstrap: The static token provisioned by the user is only useful for a single registration event, minimizing its exposure risk.