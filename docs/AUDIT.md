# Audit Trail

Kubently records every kubectl command it executes against a cluster, and
`kubently audit` reads that record back.

The question this answers is the one an operator asks before letting an AI
agent near a production cluster: **what exactly did it run, where, and when.**

```bash
kubently audit
```

```
Audit trail for team-a — 3 entries

TIME                  CLUSTER           OUTCOME   COMMAND
2026-08-20 19:25:51   prod-a            failure   get secrets -n default
2026-08-20 19:25:51   prod-a            success   describe pod web-0 -n default
2026-08-20 19:25:51   prod-a            success   get pods -n default
```

## What is recorded

One entry per command executed through `/debug/execute`, written after the
result comes back so the outcome is real:

| Field | Meaning |
| --- | --- |
| `timestamp` | When the command completed (ISO 8601, UTC) |
| `cluster_id` | Cluster the command ran against |
| `session_id` | Debug session, when the command belonged to one |
| `command_id` | Unique ID, correlatable with the API logs |
| `command` | The full kubectl argv, including defaulted flags like `-n default` |
| `outcome` | `success`, `failure`, or `timeout` |
| `error` | The error, truncated to 200 characters |
| `correlation_id` | A2A correlation ID, when the caller supplied one |

**Command output is deliberately not recorded.** The trail says what ran and
how it ended, not what came back. Storing kubectl output would put pod logs,
ConfigMap contents and anything else the agent read into a list that is
retained for a long time and readable by every holder of the issuing API key.

The same list also holds the authentication events Kubently has always
written (`api_key_verified`, `executor_token_created`, `executor_token_revoked`).
`kubently audit` hides those by default; `--all` includes them.

## Scope: you see what you ran

The audit trail is scoped by **service identity** — the `service` half of a
`service:key` entry in `API_KEYS`. A caller reads the entries their own
identity produced, and nothing else. There is no parameter that widens this
and no admin identity that bypasses it: asking for a cluster somebody else
touched returns an empty list, not their commands.

Two consequences worth knowing before you deploy this:

- **An API key with no service identity cannot read the trail at all.** A bare
  `key` in `API_KEYS`, rather than `service-name:key`, has no scope to filter
  on, so `/audit` answers `403` rather than quietly showing it everything. If
  `kubently audit` returns 403, name your keys.
- **Identity scoping is not cluster ownership.** Kubently has no model of
  which clusters belong to which caller — every valid API key may target every
  registered cluster (see `/debug/execute`). So the trail can answer "the
  commands *you* ran" but cannot answer "everything that happened to *your*
  cluster", because the second question has no owner to resolve against. If
  you need per-tenant cluster isolation, that has to be built at the
  authorization layer first; audit scoping will follow it.

Entries carrying no identity are dropped from every read rather than shared
with everybody — the filter fails closed.

## Retention

The trail lives in a single Redis list, `auth:audit`. The numbers below were
read from a running deployment, not assumed.

**Capped by count, not by time.** Every write runs `LTRIM auth:audit 0 9999`,
so the list holds the **most recent 10,000 events** and older ones are
discarded. There is **no TTL** on the key (`TTL auth:audit` returns `-1`); an
entry is never removed because it aged, only because 10,000 newer ones
arrived.

**How long 10,000 events actually lasts is shorter than it looks.** The list
is shared with authentication events, and one `api_key_verified` entry is
written per authenticated API request. Commands are therefore a minority of
the list, and a busy deployment can evict a day's commands well before 10,000
commands have run. Export anything you need to keep:

```bash
kubently audit --limit 1000 --output csv > audit-$(date +%F).csv
```

**Durability is snapshot-based.** The Helm chart runs
`redis-stack-server --dir /data --dbfilename dump.rdb` with `/data` backed by
a PersistentVolumeClaim (`redis.master.persistence`, 2Gi by default), so the
trail survives pod restarts and Helm upgrades. It inherits redis-stack's
default persistence policy, which is RDB snapshots and no append-only file:

```
save      3600 1 300 100 60 10000
appendonly no
```

That means a snapshot after 3600s if at least 1 key changed, after 300s if at
least 100 changed, or after 60s if at least 10,000 changed. **An ungraceful
pod termination can lose the entries written since the last snapshot** — up to
an hour of them on a quiet deployment. If you need the audit trail to be
durable to the second, set `appendonly yes` on Redis, or ship entries to an
external log sink as they are produced.

Retention is a property of the deployment. There is deliberately no way to
change it, extend it, or delete from it through the API — see below.

## Filters

```bash
kubently audit --cluster prod-a                 # one cluster
kubently audit --session <session-id>           # one debug session
kubently audit --since 2h                       # relative: 30s, 15m, 2h, 7d
kubently audit --since 2026-08-20T10:00:00Z     # or absolute ISO 8601
kubently audit --until 2026-08-20T18:00:00Z
kubently audit --limit 500                       # default 100, max 1000
kubently audit --all                             # include auth events
```

## Export

`--output json` and `--output csv` write machine-readable records to stdout
and suppress every decoration, so redirecting them produces a valid file:

```bash
kubently audit --output json > trail.json
kubently audit --cluster prod-a --since 7d --output csv > prod-a-week.csv
```

The CSV is RFC 4180 with a header row and every field quoted, which matters
because kubectl arguments contain commas (`-l app=web,tier=api`) and quotes
(`-o jsonpath="{.items[0]}"`).

## API

```
GET /audit
```

Authentication: `X-API-Key`. **Read-only** — this path never writes, deletes,
trims, or alters retention, and no other method is routed at `/audit`.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `event_type` | all | e.g. `command_executed` |
| `cluster_id` | all | Only entries for this cluster |
| `session_id` | all | Only entries for this session |
| `since` / `until` | unbounded | ISO 8601 timestamps |
| `limit` | 100 | 1–1000 |

```bash
curl -H "X-API-Key: team-a:..." \
  "https://kubently.example.com/audit?cluster_id=prod-a&limit=50"
```

```json
{
  "entries": [
    {
      "timestamp": "2026-08-20T19:25:51.952254+00:00",
      "type": "command_executed",
      "service_identity": "team-a",
      "cluster_id": "prod-a",
      "session_id": "ba9feb75-1699-4c9b-8d69-67640639c351",
      "command_id": "23a4976c-fc89-4957-9c9c-339ddaa4aa4b",
      "command": "get secrets -n default",
      "outcome": "failure",
      "error": "Error from server (Forbidden): secrets is forbidden",
      "correlation_id": null
    }
  ],
  "count": 1,
  "service_identity": "team-a"
}
```

`service_identity` is echoed back so an exported file records whose trail it
is.
