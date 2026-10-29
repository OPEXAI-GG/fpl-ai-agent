# Deadline worker decision

Status: defer hosted dispatch until missed-run evidence warrants it. A12 is optional in
TASK.md and requires a separate deployment handoff. No hosting, remote schedule, credentials
or recurring paid service has been changed.

The existing plan workflow runs every two hours. The separate token-check workflow already
contains a coverage-check job that warns when a deadline is within three hours and no good
run is recorded. That is a second Actions schedule, not an independent hosting provider;
GitHub-wide scheduler delays can affect both. The local dashboard is not a scheduler.

The existing Discord Worker could be reused as a deadline-offset dispatcher. A production
version would need persistent dedupe and an external heartbeat in addition to a cron trigger.
Do not add a second component that merely observes its own health.

Required design before deployment:

1. Refresh the official next-event deadline with a timestamp; reject missing/stale responses.
2. Derive offsets within the configured action window. Key dispatches by entry, GW,
   exact deadline version, dry/live policy and offset. Atomically claim each key in persistent
   storage; mark success only after GitHub accepts the dispatch. Retries reuse the same key.
3. Reconcile moved deadlines and expire old claims. Never catch up after the deadline or
   change the user's dry/live policy. Disabled/unreadable switches prohibit dispatch.
4. Use a narrowly scoped Actions-dispatch credential stored only on the Worker. The Worker
   receives no FPL token. Bound retry backoff and dispatch count; record failure and lag.
5. An independent heartbeat service checks the last successful observation/dispatch and
   verified run coverage. Alerts need a separate delivery path from the failing scheduler.

| Required simulation | Expected behavior |
|---|---|
| Deadline moves earlier/later | Replace pending offsets; old version cannot dispatch |
| Repeated cron tick/duplicate retry | One accepted action per intended offset |
| GitHub timeout after acceptance | Reconcile dispatch before retry; no blind duplicate |
| Disabled or unreadable switch | No action |
| FPL/GitHub/storage outage | Bounded retry, durable failure, independent alert |
| Worker outage | External heartbeat alerts without relying on the Worker |

These are acceptance requirements, not test results. No worker prototype or clock simulation
was added because the prerequisite measured missed-run evidence is absent in this checkout.
Before deployment, document the selected storage/heartbeat providers, their current pricing,
credential scopes and operator ownership, then implement and test the table above.
