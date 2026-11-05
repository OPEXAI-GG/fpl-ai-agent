# Current team and transfer comparison

A plan is only as good as the team it starts from. FPL's public endpoints show the squad at
the *last deadline*; they do not publish the free-transfer count, and they cannot see an
unconfirmed transfer basket. The `team` path combines three sources into one validated current
team, with a status, source and age for every important field, and the planner then compares
every transfer count it could make this week.

## Sources

| Source | What it gives | Status it can carry |
| --- | --- | --- |
| Public API (no login) | Last deadline's picks, lineup and bank; this gameweek's confirmed transfers; chip history; a free-transfer count *reconstructed* from history | inferred (chips played: confirmed) |
| Screenshot read | Pick Team: the confirmed 15, XI, bench order, captain, vice. Transfers: the 15 with selling prices, Budget, Free transfers, Cost. Chips screens | confirmed when the screen shows a confirmed team |
| Correction | Any field the manager states: free transfers, bank, transfers made, chips, or a squad | confirmed |

The live mode's authenticated team read is unchanged and remains the source in live mode. No new
login or account connection is introduced, and nothing here can change an FPL team.

## Statuses and merging

* **confirmed**: shown by a confirmed screen, or stated, for this gameweek, and nothing newer
  contradicts it.
* **inferred**: derived. The public squad with this week's public transfers applied, the
  reconstructed free-transfer count, estimated selling prices, or an older confirmation adjusted
  for public transfers made after it.
* **unknown**: no source.
* **conflicting**: confirmed sources of the same time disagree.

Rules applied by `fpl_agent.team.merge`:

1. A screen or correction counts only for the entry and gameweek it was made for. Others are
   listed as "not used" with their age.
2. A Transfers screen with pending changes (`pending: true`, a non-zero Cost, or, when pending
   is unclear, a squad that differs from the public squad) is an unconfirmed basket. It is
   reported as a preview and never replaces the confirmed squad, bank or count.
3. The newest confirmed squad wins over the public one, unless a public transfer was made after
   the screen was taken. In that case the public squad is used, as inferred.
4. A confirmed free-transfer count or bank is reduced by any public transfer made after it, and
   then becomes inferred.
5. A name that does not resolve to exactly one player (name, position, club, fixture) stays
   unresolved. The squad is then not confirmed by that screen.
6. Merging sorts its inputs, so the result does not depend on their order.
7. Every field shows its age relative to the information cutoff. Saved context is never shown
   as freshly verified.

Money is integer tenths of £m throughout (`bank: 2` is £0.2m).

## Submitting a screenshot read

The image is read outside the application. In practice, Claude reads the attached screenshot.
The visible fields are then submitted as JSON, and the application validates them. No vision
service is called and no image is stored. Only the extracted fields are saved, under the
git-ignored `data_cache/team/<entry>/observations/`.

```json
{
  "kind": "screenshot",
  "screen": "transfers",
  "entry": 123456,
  "gameweek": 10,
  "observed_at": "2026-11-05T13:45:00Z",
  "pending": null,
  "bank": "£0.2m",
  "free_transfers": 3,
  "transfer_cost": 0,
  "chips": {"wildcard": "used", "freehit": "available"},
  "players": [
    {"name": "Raya", "position": "GKP", "opponent": "HUL (H)", "price": "£6.1m"}
  ]
}
```

The `players` list holds all 15 cards. On a Pick Team screen, add `"slot": "xi"` or
`"bench"`, a `bench_order` (0 for the bench goalkeeper, then 1 to 3) and `captain`/`vice`.
Money may be written as shown (`"£6.1m"`, `6.1`) or as integer tenths (`61`). `club` (for
example `"ARS"`) or `opponent` helps resolve common names. Set `pending` to `true` when the
screen shows unconfirmed changes, `false` when it clearly shows none, and `null` when it is
unclear.

```bash
uv run --frozen fpl-agent team add --file read.json --check   # validate and resolve only
uv run --frozen fpl-agent team add --file read.json           # save, then show the team
uv run --frozen fpl-agent team correct --entry 123456 --set free_transfers=2 --set bank=0.5
uv run --frozen fpl-agent team show --entry 123456            # merged team and open questions
```

`team show` lists only the specific missing fields and the screen that shows them. It never
asks for the entry ID or for a squad it already knows.

## Planning from it

`fpl-agent plan` merges the stored reads for the entry in dry mode (`--team-dir`, default
`data_cache/team`). Without stored reads, the public view alone is used: the last deadline's
squad with this week's confirmed transfers applied.

**Transfer counts.** The planner's preferred plan used to be checked once against rolling. If
it failed the robustness policy, the transfer was rolled without trying a smaller move. Now
every count from 0 (roll) up to the planner's own maximum is compared. The maximum is the
usable free transfers plus the hits allowed in one week. Each count is a constraint in the same
MILP (`PlanInput.first_transfers`). Every count uses the same forecast, sampled worlds (seed 0),
budget, selling prices, hit costs, kept players and configuration. The plan followed is the one
with the best planner objective among those that the planner prefers to rolling and that pass
the unchanged policy: beat rolling in at least `ROBUST_MIN_WIN` of the worlds, or gain at least
`ROBUST_MIN_GAIN` points on average. If no count passes, the transfer is rolled. The comparison
is skipped, and the report says why, when a chip is in the plan, a Wildcard is active, or this
week's transfers are already made.

**Free-transfer uncertainty.** A confirmed count is planned alone. Otherwise the plan is made
for the estimate and for a 1-transfer balance. A 0 balance is added when this week's transfers
are made or unknown, and conflicting values are added too. Each balance is re-planned with the same forecast
and thresholds. Chips stay held as in the main plan, because the chip search is not repeated for
each balance. If every balance gives the same
action, the report says so. Otherwise it marks the advice **CONDITIONAL**, keeps the lineup and
captain, and asks for the count. When the bank is unknown, transfer options assume £0.0m and are
marked conditional.

## Reading the report

* `current team for GWn`: each field with its status, source and age; unresolved names,
  previews and unused stale reads.
* `transfer options this week`: for each count, the moves, any hit, expected points this week
  and over the horizon, and the planner objective. Expected points (xP) are less 4 per hit. The
  objective is not points: it is discounted, bench-weighted, includes banked-transfer and bank
  value, and is less hit buffers and rank risk. Each count also shows its share of worlds won
  against rolling, its mean gain, and why it passed or failed.
* Solver status: a solve stopped by its time limit is reported as `time limit, not proven
  optimal`, with its bound and gap. A held chip whose decision rests on such a scenario is
  marked `not proven` when its bound could still reach the threshold.
* `free transfers are ... planned for ...`: the advice for each plausible balance.
* `needed:` the specific screen or value that would settle what is still open.

## Limits

* Selling prices from a screen are as of that screen. A price change after it is not detected
  until a new screen or the next public refresh.
* The live authenticated read is not merged as an adapter. In live mode it replaces the merge,
  as before.
* A plan that holds a chip in a later week of the horizon is not re-compared by transfer count.
  The chip search weighs it instead, as before.
* No automated image extraction is implemented. Only the structured-input path is tested.
