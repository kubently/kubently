
# Goal

Refactor the auth stack to follow Black Box Design principles by:

1. Injecting dependencies (no self-built concretes),
2. Adding an `AuthenticationService` facade so the API depends on an interface (not a class),
3. Centralizing configuration via a `ConfigProvider`,
4. Splitting the CLI login command into small, swappable modules.

# Constraints

* Keep all current behavior and public HTTP API stable.
* No breaking changes to request/response schemas.
* Code must be typed (Python typing for backend, TypeScript types for CLI).
* Include unit tests and minimal integration tests for the new pieces.
* Avoid direct environment reads outside the `ConfigProvider`.
* Avoid logging secrets.

# Existing Repo (assumptions from the bundle)

* Python server using FastAPI (e.g., `kubently/main.py`, auth modules under `kubently/modules/auth/`).
* OIDC discovery and validator in/under `kubently/modules/auth/oidc.py` (or nearby).
* Hybrid auth module `EnhancedAuthModule` and a base API-key `AuthModule`.
* Node/TypeScript CLI with a `login` command (e.g., `cli/src/commands/login.ts`).

If file names differ slightly, adapt paths but keep the architecture.

---

## Part 1 — Dependency Injection for the validator

### What to change

* **Today:** `EnhancedAuthModule` (or equivalent) creates `OIDCValidator(...)` internally.
* **Target:** `EnhancedAuthModule` must *accept* a `TokenValidator` interface (Protocol) via its constructor; do not instantiate concretes inside.

### Implementation

1. Create a protocol/interface:

```python
# kubently/modules/auth/interfaces.py
from typing import Protocol, Optional, Tuple, Dict

class TokenValidator(Protocol):
    async def validate_jwt_async(self, token: str) -> Tuple[bool, Optional[Dict]]:
        ...
```

2. Update the concrete:

```python
# kubently/modules/auth/oidc_validator.py
from .interfaces import TokenValidator

class OIDCValidator(TokenValidator):
    def __init__(self, config):  # config type from ConfigProvider (see Part 3)
        self._config = config
    async def validate_jwt_async(self, token: str):
        # existing validation logic (async, cached JWKS, etc.)
        ...
```

3. Update `EnhancedAuthModule`:

```python
# kubently/modules/auth/enhanced.py
from .interfaces import TokenValidator

class EnhancedAuthModule:
    def __init__(self, redis_client, base_auth_module, validator: TokenValidator):
        self._redis = redis_client
        self._base_auth = base_auth_module
        self._validator = validator

    async def verify_credentials(self, api_key: str | None, authorization: str | None):
        # unchanged logic except calling self._validator
        ...
```

### Acceptance Criteria

* `EnhancedAuthModule` has **no** `OIDCValidator(...)` constructor calls.
* All tests still pass; new tests cover injection via a fake `TokenValidator`.

---

## Part 2 — `AuthenticationService` facade for the API

### What to add

* A minimal facade so FastAPI depends on an **interface** rather than concrete auth modules.

### Implementation

1. Add result type + facade:

```python
# kubently/modules/auth/service.py
from dataclasses import dataclass
from typing import Optional, Literal, Protocol

@dataclass
class AuthResult:
    ok: bool
    identity: Optional[str]
    method: Optional[Literal["api_key", "jwt"]]
    error: Optional[str] = None

class AuthenticationService(Protocol):
    async def authenticate(self, api_key: Optional[str], authorization: Optional[str]) -> AuthResult: ...

class DefaultAuthenticationService:
    def __init__(self, auth_module):  # accept any object with verify_credentials(...)
        self._auth = auth_module
    async def authenticate(self, api_key, authorization) -> AuthResult:
        ok, identity, method = await self._auth.verify_credentials(api_key, authorization)
        return AuthResult(ok=ok, identity=identity, method=method, error=None if ok else "invalid_credentials")
```

2. Update FastAPI composition root to depend on `AuthenticationService`:

```python
# kubently/main.py (or app wiring)
auth_service = DefaultAuthenticationService(enhanced_auth_module)

# In request handlers, use auth_service.authenticate(...) instead of calling EnhancedAuthModule directly.
```

### Acceptance Criteria

* No handler calls `EnhancedAuthModule.verify_credentials` directly.
* Handlers call `AuthenticationService.authenticate` only.
* Existing HTTP behavior is unchanged.

---

## Part 3 — Centralized configuration (`ConfigProvider`)

### What to add

* A central provider for OIDC and auth-related configuration, so modules do **not** call `os.getenv` directly.

### Implementation

1. Define config dataclasses and provider:

```python
# kubently/config/provider.py
import os
from dataclasses import dataclass
from typing import Optional, Protocol

@dataclass
class OIDCConfig:
    issuer: Optional[str]
    client_id: str
    jwks_uri: Optional[str]
    token_endpoint: Optional[str]
    device_endpoint: Optional[str]
    audience: Optional[str]

class ConfigProvider(Protocol):
    def get_oidc_config(self) -> OIDCConfig: ...

class EnvConfigProvider:
    def get_oidc_config(self) -> OIDCConfig:
        issuer = os.getenv("OIDC_ISSUER")
        client_id = os.getenv("OIDC_CLIENT_ID", "kubently-cli")
        return OIDCConfig(
            issuer=issuer,
            client_id=client_id,
            jwks_uri=os.getenv("OIDC_JWKS_URI") or (f"{issuer}/jwks" if issuer else None),
            token_endpoint=os.getenv("OIDC_TOKEN_ENDPOINT"),
            device_endpoint=os.getenv("OIDC_DEVICE_AUTH_ENDPOINT"),
            audience=os.getenv("OIDC_AUDIENCE") or client_id,
        )
```

2. Replace all direct env reads in:

* OIDC discovery route
* OIDC validator constructor
* Any other auth modules
  with calls to `EnvConfigProvider().get_oidc_config()` injected at composition time (not constructed inside modules).

### Acceptance Criteria

* Grep for `os.getenv(` in auth-related modules returns **no matches** (allowed only in `EnvConfigProvider`).
* Unit tests include one that injects a fake provider returning test config.

---

## Part 4 — Auth factory (composition root)

### What to add

* A factory to construct auth stack based on config flags, ensuring the API layer never references concrete constructors.

### Implementation

```python
# kubently/modules/auth/factory.py
from .enhanced import EnhancedAuthModule
from .apikey import AuthModule
from .oidc_validator import OIDCValidator
from ...config.provider import ConfigProvider
from .service import DefaultAuthenticationService

class AuthFactory:
    @staticmethod
    def build(config_provider: ConfigProvider, redis_client):
        base_auth = AuthModule()
        oidc_cfg = config_provider.get_oidc_config()
        validator = OIDCValidator(oidc_cfg)  # injected, not created inside EnhancedAuthModule
        enhanced = EnhancedAuthModule(redis_client, base_auth, validator)
        return DefaultAuthenticationService(enhanced)  # return the facade
```

### Acceptance Criteria

* `main.py` (or app init) constructs `AuthFactory.build(...)` and registers the returned `AuthenticationService`.
* API handlers only depend on the facade.

---

## Part 5 — CLI login refactor (single responsibility)

### What to change

Refactor `cli/src/commands/login.ts` (or equivalent) into small modules:

**New files:**

* `cli/src/auth/AuthDiscoveryClient.ts` — fetches discovery JSON.
* `cli/src/auth/OAuthDeviceFlowClient.ts` — starts device flow & polls token.
* `cli/src/auth/CliAuthUI.ts` — all prompts/printing only.
* `cli/src/auth/CliConfigStore.ts` — read/write CLI auth config.
* `cli/src/commands/LoginController.ts` — orchestrates the above.

**Rules:**

* No network code in UI class.
* No prompts in clients or controller.
* `LoginController` chooses strategy (API key vs OAuth) and coordinates calls.

### Acceptance Criteria

* `login.ts` becomes a thin shell that delegates to `LoginController`.
* Unit tests for each class with fakes (e.g., fake discovery response, fake token polling).
* Behavior remains unchanged for users.

---

## Tests

### Python

* New tests for:

  * `EnhancedAuthModule` with a **fake** `TokenValidator` to verify DI.
  * `DefaultAuthenticationService` returning `AuthResult`.
  * `EnvConfigProvider` building `OIDCConfig` from env (use `monkeypatch`).
  * OIDC discovery route now pulling values via `ConfigProvider` (mock provider in test).

### TypeScript

* Tests for:

  * `AuthDiscoveryClient` (mock HTTP).
  * `OAuthDeviceFlowClient` (mock HTTP + polling).
  * `CliAuthUI` (pure I/O, test via injected streams or mock prompts).
  * `LoginController` (end-to-end with fakes).

---

## Deliverables Checklist

* [ ] `interfaces.py` with `TokenValidator`.
* [ ] `oidc_validator.py` implementing `TokenValidator`.
* [ ] `enhanced.py` updated to accept injected `TokenValidator`.
* [ ] `service.py` with `AuthResult`, `AuthenticationService`, `DefaultAuthenticationService`.
* [ ] `provider.py` with `OIDCConfig`, `ConfigProvider`, `EnvConfigProvider`.
* [ ] `factory.py` building the whole stack and returning the facade.
* [ ] `main.py` (or equivalent) wiring updated to use `AuthFactory` and `AuthenticationService`.
* [ ] Remove all direct `os.getenv` calls from auth modules (centralized in `EnvConfigProvider`).
* [ ] CLI split into `AuthDiscoveryClient`, `OAuthDeviceFlowClient`, `CliAuthUI`, `CliConfigStore`, `LoginController`, with `login.ts` delegating.
* [ ] Unit tests covering new modules (Python + TS); minimal integration test for authenticate flow.

---

## Definition of Done

* Running tests: all green.
* Manual smoke test:

  * API-key auth path works as before.
  * OIDC/JWT path validates tokens via injected `OIDCValidator`.
  * OIDC discovery endpoint returns values fed by `EnvConfigProvider`.
  * CLI `login` command UX unchanged; internal structure split into single-purpose classes.
* Quick grep checks:

  * `grep -R "os.getenv(" kubently/modules/auth` → **no results**.
  * `grep -R "OIDCValidator(" kubently/modules/auth/enhanced.py` → **no results** (should be only in factory).

---

## Notes for the agent

* Prefer small, explicit constructors and pass dependencies top-down (composition root in app startup).
* Keep public symbols stable; if you must rename internals, provide adapters.
* Write straightforward, white-box unit tests with fakes rather than heavy mocks.
* Avoid logging tokens, API keys, or PII; keep secrets out of test fixtures.

---

**If any file names don’t match exactly in this repo, keep the architecture and adapt paths accordingly.**
