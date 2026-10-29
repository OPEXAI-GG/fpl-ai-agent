# Weekly team check: clickable prototype (FAA-24)

Owner: Mona (design). Static prototype for usability testing. Not product code, not hosted, not real advice.

## Open it

Open `artifacts/design-weekly-check/index.html` directly in a browser (double-click; no server or install).
Test team IDs: `1234567` normal squad, `404` not found, `777` team with no players. Letters show an input error.

Journey: Team ID → "Is this your team?" → This Week (move, captain, reasons, main risk, freshness)
→ Compare with keeping your team → "Ideas we are testing" (paid-feature hypotheses, no price, nothing to buy).
Squad shows a pitch and an accessible list view. Discover and Plan are placeholders.

**Facilitator controls** (bottom of every page) switch test states: stale data (over 6 hours), a sample range
with made-up numbers, and slow loading (5 seconds). By default the range reads **"Uncertainty unavailable"**,
because no validated uncertainty measure exists yet.

## Files

| File | Purpose |
|---|---|
| `index.html`, `styles.css`, `app.js` | the prototype; all text, shapes and icons original; no external requests |
| `usability-script.md` | five-participant session script and scoring sheet |
| `checks/run-checks.mjs` | automated responsive/accessibility checks; writes `screenshots/` and `checks/results.json` |
| `checks/manual-checks.md` | what was checked by hand, what was found and fixed, what is not checked |
| `screenshots/` | 11 states × 360/390/768/1440 px. **Previous version (revision 1, the files Wegz hashed); they do not show revision 2.** |

**Revision 2 (FAA-31 fixes P1–P4) was made by file edits only and has not been run or checked in a browser.**
Runtime checks are deferred until the incident tooling hold is lifted. Re-running `checks/run-checks.mjs` overwrites
`screenshots/` and `checks/results.json`, so copy or rename the revision 1 evidence first, or change the script's output folder.

## Honesty rules this prototype follows

- Every page carries "Prototype. All players, clubs, numbers and times are made up". Club and player names are invented.
- Ranges appear only when the facilitator turns them on, labelled "sample range with made-up numbers".
- The paid preview is a research question, not a plan: no tier, price, checkout or promise.
- The 4-of-5 comprehension goal in the script is a target for this round, not evidence of success.

## Known limits

- No real data, accounts, team import, deadline source or uncertainty contract; these need Pablo/Nabil.
- Design-to-API gaps (from Wegz's review): the prototype shows manager name, team name, a "changed since your last check"
  list, captain and bench advice, and a 3-gameweek gain. Team-import v0 returns none of these (no names by design).
  Before the product build each needs a source or must be removed. Not invented here; tracked for Pablo and Nabil.
- The "Exploratory" tag beside the expected gain is a placeholder for the projection label in the team-import contract.
- On a 360 px phone, long surnames on the pitch wrap mid-word without a hyphen (e.g. "Rademak | er"). The List view shows full names.
- Full-page phone screenshots show the fixed bottom bar over content; on a real screen the page scrolls clear of it.
- Not tested with a screen reader, on real phones, or with Windows high-contrast mode. Automated checks do not establish WCAG 2.2 AA conformance.
- Dark theme only. Usability sessions have not been run; no participants are recruited.
