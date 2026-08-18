# Dependency Upgrades

Notes on dependency bumps that are **not** safe to apply as version changes, so
the next person (or the next Dependabot PR) does not have to rediscover why.

## a2a-sdk: held at 0.2.16

Dependabot proposes `a2a-sdk==0.2.16 -> 1.1.2` (PR #35). **Do not merge that
bump on its own.** It is not a version change; a2a-sdk 1.x is a rewrite of the
package, and `kubently/modules/a2a/` needs migrating first. Tracked in #76.

### Why it cannot just be bumped

Verified by installing `a2a-sdk==1.1.2` and resolving every symbol the codebase
imports:

| What the code imports | State in 1.1.2 |
| --- | --- |
| `a2a.server.apps.A2AStarletteApplication` | **Module gone.** Replaced by `a2a.server.routes` (`add_a2a_routes_to_fastapi`, `create_jsonrpc_routes`, `create_agent_card_routes`, …). Not restored by any extra — `[http-server]` and `[fastapi]` both lack it. |
| `a2a.utils.new_agent_text_message` | **Gone.** Not present anywhere in the package. |
| `a2a.utils.new_task`, `a2a.utils.new_text_artifact` | Moved to `a2a.helpers` / `a2a.helpers.proto_helpers`. |
| `a2a.types.Message`, `Task`, `Part`, `Role`, `TextPart`, `Artifact`, `TaskStatus`, `TaskState`, `AgentCard`, … | `a2a.types` is now protobuf-generated (`a2a.types.a2a_pb2`). The pydantic models the bindings construct are not there under these names. |
| `a2a.types.AgentAuthentication` | Gone — and already absent in 0.2.16 (see the dead-code note below). |
| `DefaultRequestHandler`, `InMemoryTaskStore`, `PushNotificationSender`, `AgentExecutor`, `RequestContext`, `EventQueue` | Still resolve. |

`a2a.compat.v0_3` exists but exports nothing useful, so there is no drop-in
shim.

### Why this was dangerous

`kubently/modules/a2a/__init__.py` wraps its SDK imports in a bare
`except Exception` that sets `A2A_AVAILABLE = False`:

```python
try:
    from a2a.server.apps import A2AStarletteApplication
    ...
    A2A_AVAILABLE = True
except Exception as e:
    A2A_AVAILABLE = False
    logger.info(f"A2A support disabled at import time: {e}")
```

An incompatible SDK therefore does **not** crash the API. It starts normally
with the entire A2A protocol surface missing, announced only by one INFO log
line. Combined with the old CI (`pytest ... || true`), that bump could have
merged and deployed with every check green.

`tests/test_a2a_sdk_contract.py` now guards this. Against 1.1.2 all 35 of its
tests fail, including an explicit assertion that `A2A_AVAILABLE is True`.

### What a migration would involve

1. Replace `A2AStarletteApplication(...).build()` in
   `kubently/modules/a2a/__init__.py` with the `a2a.server.routes` factories,
   keeping `get_mount_config()` returning `("/a2a", app)` so `main.py` and its
   API-key ASGI wrapper are unaffected.
2. Repoint the `new_*` helpers in `agent_executor.py` at `a2a.helpers`.
3. Rework `helpers.py` and `agent_executor.py` event construction onto the new
   `a2a.types` representation.
4. Re-run `tests/test_a2a_sdk_contract.py` — updating the `SDK_IMPORT_SURFACE`
   table to the new paths — plus a live check against `docs/TEST_QUERIES.md`,
   since the contract tests cover shape, not wire behaviour.

### Dead code to resolve first

`kubently/modules/a2a/protocol_bindings/a2a_server/__main__.py` does not import
even on the **current** pin. It has two independent bugs:

- `from a2a.types import AgentAuthentication` — no such symbol in 0.2.16.
- `from kubently.protocol_bindings.a2a_server.agent import KubentlyAgent` — the
  real path is `kubently.modules.a2a.protocol_bindings.a2a_server.agent`.

Nothing imports it; the live server is built by `A2AModule.get_app()` in
`kubently/modules/a2a/__init__.py`. It should be deleted or repaired before the
migration, so it does not read as a second entry point. Tracked in #77.

## typescript: held at ^6

Dependabot proposes `typescript ^5.7.0 -> ^7.0.2` inside the nodejs group
(PR #52). The rest of that group is applied; TypeScript is held at `^6.0.3`
because the toolchain does not support 7 yet:

- `ts-jest@29.4.12` peers on `typescript >=4.3 <7` — taking 7 breaks the whole
  jest suite.
- `@typescript-eslint@8.67` peers on `typescript >=4.8.4 <6.1.0`.

`6.0.3` is the highest version both accept. Revisit once ts-jest ships TS 7
support. Tracked in #78.

Note that TS 6 already removes `moduleResolution: node10`, which is why
`kubently-cli/nodejs/tsconfig.json` moved to `module`/`moduleResolution:
nodenext` — the correct setting for a `type: module` package whose relative
imports already carry `.js` extensions.

## Cleanups worth doing separately

- **#81** — `test-automation/requirements.txt` pins `a2a>=0.44`, but nothing
  under `test-automation/` imports it; `kubently-cli/nodejs` declares `ws` and
  `readline-sync`, but neither is imported under `src/`.
- **#80** — `google-generativeai` is end-of-life upstream ("All support for the
  `google.generativeai` package has ended"). `test-automation/analyzer.py`
  should migrate to `google-genai`.
- **#79** — `ruff check kubently/` reports ~971 violations, which is why the CI
  lint job is `continue-on-error`. Clearing that backlog would let lint gate
  too.
