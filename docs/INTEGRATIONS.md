# Integrations

RippleBack talks to project-management tools in one direction (import plans)
and, optionally, the other (push committed recovery changes back). Every
credential stays in the backend environment; the API reports configuration as
**variable names only**, never values.

Set `RIPPLEBACK_PM_PROVIDER` to `jira`, `linear`, or `github`. Anything else
(including the default `none`) leaves PM features off, and `rippleback doctor`
prints exactly which variables are missing.

## The provider contract

All providers implement `rippleback.integrations.base.PMProvider`:

- `require_configured()` — raises `IntegrationError(missing=[...])` naming the
  unset variables.
- `fetch_plan()` → an `ImportedPlan` of normalized `ImportedEntity` /
  `ImportedDependency` records.
- `push_changes(scenario, changes, kind)` — apply a committed change set back to
  the PM.

`build_scenario(plan)` converts an `ImportedPlan` into the validated scenario
schema. Mapping rules (`integrations/base.py`):

- **entity id** — the PM's stable human identifier (Jira key, Linear `ENG-123`,
  GitHub `#number`); the PM's opaque id is kept in `metadata.external_id`.
- **status** — mapped through an explicit, configurable table
  (`*_STATUS_MAP`, plus the defaults in `base.DEFAULT_STATUS_MAP`). It is never
  inferred. Statuses are `pending | in_progress | done | blocked`.
- **dependencies** — PM "blocks" links, oriented so `dependent` requires
  `prerequisite`.
- **effort / duration / cost** — only from explicitly configured fields or
  labels; missing values stay missing (they are never guessed).
- **resources** — one per distinct assignee; capacity from `RESOURCES_FILE`
  when present, else `RIPPLEBACK_DEFAULT_CAPACITY`.
- **release gate** — `RIPPLEBACK_RELEASE_ENTITY` adds an
  `all_entities_done_before` constraint (it must be an imported entity).
- **out-of-scope links** — `RIPPLEBACK_OUT_OF_SCOPE_LINKS=stub` (default) keeps
  a clearly-labeled stub entity so nothing is silently dropped; `=error`
  rejects the import with the offending key.

## Jira Cloud

| Variable | Required | Purpose |
| --- | --- | --- |
| `JIRA_BASE_URL` | yes | e.g. `https://acme.atlassian.net` |
| `JIRA_EMAIL` | yes | API-token auth user |
| `JIRA_API_TOKEN` | yes | secret |
| `JIRA_PROJECT_KEY` | yes | project to import |
| `JIRA_JQL` | no | overrides the default `project = "KEY" ORDER BY key ASC` |
| `JIRA_STATUS_MAP` | no | `"In Review:in_progress,Blocked:blocked"` |
| `JIRA_FIELD_EFFORT` / `_DURATION` / `_COST` | no | custom numeric field ids |
| `JIRA_BLOCKED_LABEL` | no | label marking an issue blocked (default `blocked`) |

- Search uses `/rest/api/3/search/jql` token pagination and falls back to the
  legacy `/rest/api/3/search` offset pagination on `404`/`410`.
- `429` responses honor `Retry-After`; transient transport errors retry with
  backoff (3 attempts) before failing loudly.
- Links of type `Blocks` become dependencies. Write-back: reassignment via the
  assignee endpoint, `add_dependency` via `POST /issueLink`, and
  `remove_dependency` by finding and deleting the matching link.

## Linear

| Variable | Required | Purpose |
| --- | --- | --- |
| `LINEAR_API_KEY` | yes | secret (sent as the `Authorization` header) |
| `LINEAR_TEAM_KEY` | yes | team to import, e.g. `ENG` |
| `LINEAR_STATUS_MAP` | no | status overrides |
| `LINEAR_FIELD_EFFORT` | no | effort field (default `estimate`) |
| `LINEAR_BLOCKED_LABEL` | no | default `blocked` |

Uses the GraphQL API with cursor pagination. Only `blocks` relations where the
fetched issue is the subject are read, which keeps the orientation
deterministic. Write-back uses `issueUpdate` (assignee) and issue-relation
mutations; UUIDs travel in `metadata.external_id`.

## GitHub (issues)

| Variable | Required | Purpose |
| --- | --- | --- |
| `GITHUB_TOKEN` | yes | secret |
| `GITHUB_REPO` | yes | `owner/repo` |
| `GITHUB_STATUS_MAP` | no | status overrides |
| `GITHUB_LABEL_EFFORT_PREFIX` | no | default `effort:` |
| `GITHUB_LABEL_DURATION_PREFIX` | no | default `duration:` |
| `GITHUB_BLOCKED_LABEL` | no | default `blocked` |
| `GITHUB_BLOCKED_BY_PATTERN` | no | regex for the dependency body line |

Dependencies are read from a documented body line such as
`Blocked by: #12, #34` (or `Depends on:`). Links to issues outside the imported
set follow `RIPPLEBACK_OUT_OF_SCOPE_LINKS`. Write-back manages dependencies
inside a dedicated body block (`<!-- rippleback:deps -->`) so the rest of an
issue body is never rewritten.

## PagerDuty (incidents → risk metric)

| Variable | Required | Purpose |
| --- | --- | --- |
| `PAGERDUTY_API_KEY` | yes | secret |
| `PAGERDUTY_SERVICE_MAP` | yes | `SERVICE_ID:entity_id,...` |
| `PAGERDUTY_SINCE_DAYS` | no | lookback window (default 30) |
| `PAGERDUTY_SCENARIO_ID` | no | target scenario for stored incidents; stored as `pagerduty:<id>` (default id `incidents`) |

Severity mapping: `critical→critical`, `error→high`, `warning→medium`,
`info→low`, anything else `unknown`. Incidents whose service is **not** in the
map are stored with no entity (so they cannot inflate any option's risk) and
counted in the returned `unmapped` stat, making the gap visible instead of
silent. Ingest is idempotent on `(source, external_id)`. PagerDuty is read-only.

## Sync, write-back, and the outbox

- `RIPPLEBACK_SYNC_INTERVAL_SECONDS` (default 300) runs a periodic pass in the
  adapter; `0` disables it. `POST /api/sync` and `python -m rippleback sync`
  run one pass on demand. A pass imports the plan and (if configured) ingests
  PagerDuty incidents, then drains the outbox.
- `RIPPLEBACK_PM_WRITEBACK_ENABLED=true` queues each committed change set in the
  outbox and pushes it idempotently. Failures retry up to `MAX_ATTEMPTS`, then
  the commit is marked `writeback_failed` (retry via the UI or
  `POST /api/commits/{id}/retry`).
- A sync **never erases** a local commit the PM has not yet reflected: such
  commits are re-applied as overlays and retired to `synced_back` only once the
  imported data contains their effect. See `docs/DECISIONS.md`.

## Notifications

Slack (`SLACK_WEBHOOK_URL`) and email (`SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`,
`SMTP_USERNAME`, `SMTP_PASSWORD`) are optional. `NOTIFY_EVENTS` narrows which
events are sent; empty means all of:

`disruption_recorded`, `disruption_resolved`, `commit_recorded`, `sync_failed`,
`writeback_failed`.

Messages are one-line and pass through `security.redact_mapping` first, so a
secret in a payload is never delivered.
