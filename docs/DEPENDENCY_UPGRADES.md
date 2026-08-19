# Dependency Upgrades

Notes on dependency bumps that are **not** safe to apply as version changes, so
the next person (or the next Dependabot PR) does not have to rediscover why.

## a2a-sdk: migrated to 1.1.2 (#76)

Resolved. Dependabot's `0.2.16 -> 1.1.2` bump (#35) could not be applied as a
version change, because 1.x is a rewrite of the package. The migration landed in
#76; this section records what actually moved, because the reconnaissance that
preceded it was written without porting the code and got some of it wrong.

### What 1.1.2 actually changed

| What the code imported (0.2.16) | 1.1.2 |
| --- | --- |
| `a2a.server.apps.A2AStarletteApplication` | **Module gone.** The endpoint is assembled from `a2a.server.routes`: `create_agent_card_routes(card, card_url=...)` + `create_jsonrpc_routes(handler, rpc_url="/", enable_v0_3_compat=...)`, wrapped in a plain `Starlette`. |
| `a2a.utils.new_agent_text_message(text, ctx, task)` | Gone. Replaced by `a2a.helpers.new_text_message(text, context_id=..., task_id=..., role=Role.ROLE_AGENT)` — the agent role is the default. |
| `a2a.utils.new_task(message)` | `a2a.helpers.new_task_from_user_message(message)`. (`a2a.helpers.new_task` also exists but takes `(task_id, context_id, state)` — *not* a drop-in.) |
| `a2a.utils.new_text_artifact` | `a2a.helpers.new_text_artifact`, same keywords. |
| `a2a.types` pydantic models | Protobuf-generated (`a2a.types.a2a_pb2`). `Message`, `Task`, `Part`, `Role`, `Artifact`, `TaskStatus`, `TaskState`, `AgentCard`, `AgentSkill`, `AgentCapabilities` all still resolve **under the same names**, but the field names are snake_case (`context_id`, `task_id`, `last_chunk`, `message_id`, `artifact_id`) and enums are `TaskState.TASK_STATE_WORKING` / `Role.ROLE_AGENT`. |
| `a2a.types.TextPart` | Gone — proto `Part` carries `text` directly; build one with `a2a.helpers.new_text_part`. |
| `DefaultRequestHandler` | Still resolves (now an alias for `DefaultRequestHandlerV2`) but **takes `agent_card` as a required argument**. |
| `PushNotificationSender.send_notification(task)` | Now `send_notification(task_id, event)` — artifact updates are pushable too. |
| `EventQueue` | Now an abstract producer-side interface; `EventQueueLegacy` is the concrete one with an inspectable backing queue (used by the contract tests). |
| `TaskStatusUpdateEvent.final` | **Gone.** The stream ends when the task reaches a terminal `TaskState`; `EventConsumer` derives it, and the v0.3 layer re-derives the wire `final: true` from the same state. |
| `AgentCard.url` / `AgentCard.protocolVersion` | Replaced by `supported_interfaces: [AgentInterface(url, protocol_binding, protocol_version)]`. The serialiser (`agent_card_to_dict`) still emits the legacy top-level `url`/`protocolVersion`/`preferredTransport`, derived from the first 0.3-compatible interface — so a card that lists **no** 0.3 interface silently drops those fields and breaks every pre-1.0 client. |
| extras | `starlette` and `sse-starlette` moved behind the `[http-server]` extra. The pin is `a2a-sdk[http-server]==1.1.2`; without the extra the route factories raise `ImportError` at app build. |

Two claims in the earlier reconnaissance were wrong and cost time:

- **`a2a.compat.v0_3` is not empty.** Its `__init__` exports nothing, but the
  submodules are the whole v0.3 compatibility layer, and it is reachable through
  a supported flag: `create_jsonrpc_routes(..., enable_v0_3_compat=True)`.
- **1.x renamed the JSON-RPC methods.** `message/send`, `message/stream`,
  `tasks/get`, `tasks/resubscribe` became `SendMessage`,
  `SendStreamingMessage`, `GetTask`, `SubscribeToTask`. Without
  `enable_v0_3_compat` the migration is not "does it still import" but a **wire
  break**: the Kubently CLI (`kubently-cli/nodejs/src/lib/a2aClient.ts` sends
  `message/stream`) would get `-32601 Method not found` from a server that looks
  perfectly healthy. Kubently enables it, and advertises both protocol versions
  on the agent card because it serves both.

### What the next bump should check

`tests/test_a2a_sdk_contract.py` pins the import surface, the helper signatures,
the constructed event shapes and the card; `tests/test_a2a_streaming.py` drives
the real `A2AModule.get_app()` over `message/stream` and asserts the response is
a non-empty SSE stream with a terminal event (#65), and that both well-known
agent-card paths answer. Run both against any proposed `a2a-sdk` version before
merging it — and note that `A2A_AVAILABLE` no longer swallows failures (#97), so
a broken bump fails at startup instead of quietly removing `/a2a/`.

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
