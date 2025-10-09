Future Enhancement: User-Level Authentication (OIDC/JWT)
1. Executive Summary
This document proposes an enhancement to the Kubently authentication model to securely differentiate between machine clients (trusted A2A systems) and human users (developers using the CLI).

The current model authenticates all clients (human and machine) using a single pool of static, shared API keys. This creates a security risk and prevents user-level auditing.

The proposed solution is to implement a dual-authentication system:

Machine Authentication (Legacy): Continue to support static X-API-Key headers for trusted server-to-server A2A systems.

Human Authentication (New): Add support for a standard OAuth 2.0 / OIDC flow to authenticate human users via the CLI, issuing them short-lived JWTs for access.

This change isolates human user credentials from service credentials, provides true user-level auditability, and vastly improves the security posture of the system without violating the black-box module principles.

2. Current State & Security Gap
Kubently currently has two distinct authentication paths, both managed by the AuthModule:

Executor Authentication: This path is for the remote cluster executors. It uses a long-lived, per-cluster token passed via the Authorization: Bearer <token> header and validated by the verify_agent function. This model is considered acceptable for the service-to-service link.

Client Authentication: This path is for all other clients, including both trusted A2A agentic systems and individual human developers using the kubently-cli. All these clients authenticate using a static key passed in the X-API-Key header, which is validated against a shared list in the API_KEYS environment variable.

The Security Flaw
The flaw is that human users and trusted machine services are authenticated using the same mechanism.

No Identity: When a human uses the CLI, they configure it with a static key from the shared API_KEYS pool. The system has no way to differentiate this user from a production A2A service.

No User Audit: All audit logs are tied to the shared API key, not the human user who performed the action. We can't answer "What did user@company.com do?"

High Risk: A static API key saved in a developer's local config file (~/.kubently/config.json) is a significant liability. If that key leaks, an attacker gains the same level of privilege as all trusted A2A services.

3. Proposed Architecture: Dual Authentication Model
This enhancement upgrades the AuthModule to support both authentication models simultaneously.

+---------------+      +-------------------+      +------------------+
|               |----->| Identity Provider |----->|                  |
|  Human User   | Auth |  (Okta, Auth0, ..)| JWT  | [Kubently CLI]   |
| (via Browser) |      +-------------------+      | (stores JWT)     |
+---------------+                                 +--------|---------+
                                                           | Authorization: Bearer <JWT>
+---------------+                                          |
| A2A Service / |                                          |
| Machine Agent |------------------------------------------+
+---------------+ X-API-Key: <static_service_key>          |
                                                           |
                                                           v
                                                +--------------------+
                                                | [Kubently Service] |
                                                |   (FastAPI App)    |
                                                |         |          |
                                                | +--------------+   |
                                                | |  Auth Module |   |
                                                | | (Validates     |   |
                                                | |  JWT or APIKey) |   |
                                                | +--------------+   |
                                                +--------------------+
The API endpoints (like /debug/execute) will be modified to accept either:

A valid X-API-Key (for machines).

A valid Authorization: Bearer <JWT> (for humans).

4. Implementation Plan
Step 1: Update the AuthModule (Backend)
The core AuthModule black box will be upgraded.

Configure the module to be OIDC-aware by adding environment variables for the Identity Provider's JWKS URL and the expected Audience/Issuer.

Create a new, separate FastAPI dependency function (e.g., verify_user_jwt) that extracts the Authorization: Bearer <token> header.

This new function will validate the JWT signature against the IdP's public keys (fetched from the JWKS URL) and validate its claims (like iss, aud, and exp).

Create a "master" dependency function that endpoints will use, which attempts to validate via verify_user_jwt first. If that fails (or no JWT is present), it falls back to validating via the existing verify_api_key function. This ensures backward compatibility for all machine agents.

Step 2: Update the kubently-cli (nodejs) (Client)
Add a new command: kubently login.

This command will execute an OAuth 2.0 Device Authorization Grant flow. The CLI will print a clickable URL and a user code. The user opens the URL in their browser, authenticates with the IdP, and enters the code.

Once the user approves, the CLI's polling request will receive a short-lived JWT (access token) and refresh token from the IdP.

The CLI will securely store these tokens in ~/.kubently/config.json, replacing the need for a static API key for human users.

All other CLI commands (like debug and admin) will be modified to automatically use this JWT in the Authorization: Bearer <token> header. The logic for handling X-API-Key can be retained as a fallback for service accounts.

5. Benefits of This Upgrade
True User Security: Replaces static, shared keys on developer machines with short-lived, revocable JWTs tied to a user's identity.

User-Level Auditing: The API can now extract the sub (user email) claim from the JWT. All audit logs and session data can be tied directly to a human user, not just a shared key.

Separation of Concerns: Creates a clean, logical separation between human identity (transient, OIDC-managed) and machine identity (static, service-level API keys).

Foundation for RBAC: This change is the prerequisite for implementing user-level permissions (e.g., "Allow dev-team to access dev-cluster but not prod-cluster").

6. Alignment with Black-Box Principles
This enhancement fully adheres to the project's black-box design:

No other module is affected. The SessionModule, QueueModule, and A2A-Agent do not need to change. They only care that a request was successfully authenticated by the AuthModule.

The AuthModule itself is the only component that changes. Its internal implementation becomes more sophisticated (handling two auth types), but its "contract" with the rest of the system (approving or denying requests) remains the same.