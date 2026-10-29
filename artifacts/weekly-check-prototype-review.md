# Review: weekly team check prototype (FAA-24, review card FAA-31)

Reviewer: Wegz (wegz-mvsc3fox), 2026-10-29. Scope: `artifacts/design-weekly-check/` only.

## What was reviewed (SHA-256)

| File | SHA-256 |
|---|---|
| index.html | `8b912640958bf78808ecd0982885c16e183beae40250caff2abe1d0de3191e67` |
| app.js | `fc989e35d9edef526ced463b690b4abc0287f483d42dd5e5c192ab7c3f6e0657` |
| styles.css | `1f78b5196a7021a166b27bab270a488bd4e0e243478b0a57ce62f880a7ea8089` |
| usability-script.md | `4fde43b20bfb4a4874329c3c77b23ac7725eefc8ba1e66a5d9e2e49c65f687fa` |
| README.md | `a0ca1bd0d3d4aa1606ab1460fbc763e8a32242bbd2546e653df88999f7d3203f` |
| checks/manual-checks.md | `57229befe0c6539036c2eee2208b89ca0042778a4edf5195a1fdfd53e93e3bbc` |
| checks/run-checks.mjs | `af7352cc0770ab2ad56ae2e330242b1f0a27a84a4d70199c83f014cad213ab38` |

## How I checked

- **Read:** the HTML, JS and CSS in full, plus the README, the manual checks and the usability script.
- **Did not rerun Mona's `run-checks.mjs`:** it rewrites her `screenshots/` and `results.json`, which are her evidence.
- **Ran my own probe instead:** written in my scratchpad (`faa24/probe.mjs`) and using the repo's existing
  Playwright; nothing installed, nothing written into her folder. It covered:
  - skip-link behaviour on an inner screen;
  - reflow at 320 px, which is WCAG's 400% zoom equivalent;
  - the pitch accessibility tree;
  - computed non-text contrast.
- **Not done:** no axe-core (not in the repo; installing is barred during the incident), and no real screen reader.
  Accessibility tree output is a proxy, not a screen-reader test.

## Verdict: REQUEST CHANGES

The honesty rules hold. Most accessibility basics are in place. Three accessibility defects and one script wording
issue need fixing before sessions.

## Honesty (her question 1): passes

- Every page carries the prototype banner, and players and clubs are invented.
- The range defaults to "Uncertainty unavailable" with an explanation (`app.js` `rangeNode`). The sample range shows
  only through the facilitator toggle and is labelled "Sample range with made-up numbers". Compare shows
  "Unavailable" by default.
- The paid preview says "There is no price and nothing to buy". No tier, price, checkout or promise.
- The stale threshold (over 6 hours) matches the code's `STALE_SNAPSHOT_HOURS = 6`.
- Recommended, not blocking: the real API labels every projection `exploratory` (team-import contract §7). Add a
  slot for that label beside "Expected gain" now, so sessions test whether people understand it. Also,
  "checking is free in this prototype" (start screen) primes a free/paid split before the preview task. Consider
  dropping "free".

## Required fixes

**P1. The skip link resets the journey (confirmed).** The router treats any hash as a screen. "Skip to content" sets
`#main`, `show()` doesn't recognise it and falls back to `start`. Probe: on This Week at 1280 px, Tab then Enter
shows `screen-start` with focus on `h-start`. Mona's keyboard check only exercised this on the start screen.
Fix: handle the skip link without changing the hash (`preventDefault` and focus `#main`), or make `route()` ignore
`#main`. Test on every screen.

**P2. The pitch view is one unstructured run of text for screen readers (confirmed in the accessibility tree).** The
default Squad view exposes a single text node: "Ilunga 3.9 pts Rademaker 4.4 pts … Lund 2.1 pts". It has no
positions, no starters/bench split and no bench order. Task 6 ("find who is on your bench, in order") cannot be
done by a screen-reader user on the default view, and the List view's Status column is the only alternative.
Fix: either give the pitch structure (a labelled group per line, "Bench" heading, ordered bench list, a full name
for each player), or mark the pitch `aria-hidden` and keep the table as the accessible view, with a visible note.
Separately, the table's status for the bench goalkeeper reads "Bench", not a bench position. That's acceptable but
should be stated.

**P3. Some states are shown only by colour or low-contrast boundaries (measured, WCAG 1.4.1 and 1.4.11).**
- Mobile bottom nav: the current page differs from other links only by colour, `#86cfa0` vs `#f2f0eb`, a contrast of
  **1.61:1**. Add a non-colour cue (underline, bar or weight).
- Pitch/List toggle: the group border `#343b44` on the background is **1.62:1**. The pressed state is a background of
  `#232830` against `#1b1f23` plus bolder text. Give the pressed state at least 3:1 against its neighbour, or add
  a check mark or underline.
- These pass: the input and ghost-button borders (**3.15:1**) and the focus ring (14.1:1 against the page). The ring
  sits outside the green button, so its 1.41:1 contrast against the button doesn't matter.

**P4. Usability script task 7 leads (`usability-script.md`, Task 7).** "What would make one worth paying for?"
assumes paying. The approved discovery draft (FAA-28 R5) treats "I would not pay" as a valid result. Reword:
"Would you use either of these? Would either be worth paying for, compared with what you use now, or not?" Record
"would not pay" as a result.

## Recommended (not blocking)

- **Reflow:** 0 px horizontal overflow at 320 px on start, confirm, week, compare, squad and preview (with stale and
  range on). This meets the reflow test. A CSS-zoom probe overflowed, but CSS zoom doesn't trigger media queries, so
  it's not a valid browser-zoom test. Still check real browser zoom at 200% and 400% once.
- `aria-label` on the brand `<div>` (no role) is ignored by assistive technology. Use visible text only, or a role.
- Script Task 6's second question ("Is there another way to view the squad?") prompts discovery of List view. Score
  it as prompted.
- Script setup needs the consent and data items from the discovery draft (section 7: data controller, recording
  consent, retention). Sessions can't run until those are closed and god approves recruitment. The script already
  says god must approve contact.
- **Design-to-API gaps, for Mona and Pablo to track:** the prototype shows manager name, team name, a "changed since
  your last check" list, captain/bench advice and a 3-GW gain. Team-import v0 returns none of these (no names by
  design). Before the product build, they need a source or must be removed.

## Her question 2 (beyond her script): summary

- **Focus order:** logical; the skip link comes first; focus moves to each new heading. Defect P1.
- **Screen reader:** not run. The accessibility tree shows defect P2.
- **Zoom/reflow:** passes at 320 px.
- **Non-text contrast:** P3.
- **Reduced motion:** the spinner respects `prefers-reduced-motion`.
- No WCAG conformance is claimed, correctly.

## Re-review

I'll recheck P1-P4 against the new file hashes using the same probe (`faa24/probe.mjs`).

---

# Addendum: static review per god's FAA-31 scope, and which findings come from runtime (2026-10-29)

**Disclosure.** God's hold on runtime checks during incident containment reached me after I had already run the
read-only Playwright probe described above. It installed nothing, wrote only to my scratchpad and left Mona's files
untouched. No further runtime checks will run until Zaza confirms the tooling is safe. The same file hashes apply as
above; nothing changed.

## How each finding was established

| Finding | Static evidence (source, screenshots, saved results) | Runtime observation |
|---|---|---|
| P1 skip link resets the journey | Static: `index.html` `href="#main"` + `app.js` `route()`/`show()` fall back to `start` for any unknown hash | Observed in probe |
| P2 pitch not structured | Static: `renderSquad()` builds plain divs with no group labels. Screenshot `squad-pitch-360.png`: the bench row has **no "Bench" label and no order numbers for sighted users either**, and the goalkeeper is shown first | Accessibility tree observed in probe |
| P3 colour-only and low-contrast states | Static: CSS values (`.bottom-nav a[aria-current]` colour only; `.seg` border `#343b44`); ratios computed from those values | Same numbers computed in the browser |
| P4 script Task 7 leads | Static only | none |
| Reflow at 320 px passes | none | Observed only. Statically, Mona's saved results cover 360 px and up |

## Additional static checks (her and god's list)

- **Journey:** start → confirm → This Week → compare → preview, plus squad, matching the README. The empty (777) and
  not-found (404) states exist (`app.js`).
- **Freshness states:** fresh shows time and age. Stale (`week-stale-range-390.png`) shows a warning box, an amber
  dot and "Check again". The 6-hour threshold matches the code constant. The deadline is labelled "sample times".
- **Mobile layout (screenshots):** no horizontal overflow (saved `results.json`, generated 2026-10-28T20:01:19Z;
  lowest contrast 8.29:1 matches her report). Long surnames on the 360 px pitch break mid-word without hyphens
  ("Rademak|er", "Achterber|g", "Nakamur|a"); this is a known limit in her README. Recommended: use hyphens or a
  shorter display name.
- **Focus semantics (source):** a visible 3 px focus ring; focus moves to the new heading on each screen change; the
  form error is linked through `aria-describedby` and sets `aria-invalid`; loading is announced in `role="status"`;
  the stale warning uses `role="alert"`. Only P1 breaks this.
- **Comprehension script:** apart from P4, it is fair. It asks open questions, uses the synthetic team ID, and says
  its target is not proof. Task 6's second question is a prompt; score it as prompted.
- **Original assets:** no external URLs, fonts or images (only `app.js` and `styles.css` are loaded). Icons and
  shirts are inline SVG paths. Clubs and players are invented. No real club marks or FPL branding.
- **Honesty:** unchanged from above; it passes. The large green "+2.1" is the most prominent number on the page and
  has no exploratory label (recommendation stands).
- **Minor:** position labels are inconsistent ("Mid"/"Fwd" on the captain card vs "MID" elsewhere).

## Verdict unchanged: REQUEST CHANGES (P1-P4)

P1, P3 and P4 stand on static evidence alone. P2 is established statically for sighted users (no bench label or
order) and by the accessibility tree for screen readers. This review does not authorize fieldwork or publication.

---

# Re-review r2: changed lines, static only (2026-10-29)

I reviewed the files and nothing else. No scripts or browser were run (FAA-30 hold). The revision 1 results above are
kept unchanged.

Reviewed hashes (match god's verified values):

| File | SHA-256 |
|---|---|
| index.html | `a1ef662c746b5ebe366339d03e442c1f30907839dbfadecbf7def4af0c60df6b` |
| app.js | `98784f8fdaca9f115ccf203c5818812c790173003b8fd3a271e119e26fbdca89` |
| styles.css | `11dfc6254e41cfd8b2ffbef0a10bcc94f7a708d029e3776fbaa93755ce267a3a` |
| usability-script.md | `8fbcb274551127d722bfa1bcc3373879e36572e0c4670a0ae36e84d732822728` |
| README.md | `8d4168215e5ce91821adefb71d95f499f0e40434cb9e7abaee255653363572d0` |
| checks/manual-checks.md | `eed7b96272b3125b1e9a5077a2d7744479d1a789f14544ed5426a5c0fa5df943` |

## Verdict: APPROVE for round-1 usability sessions, as a design prototype

This approval does not cover WCAG conformance (none is claimed), does not replace runtime verification, and does not
authorize fieldwork. Sessions still need god's recruitment approval and the consent items.

| Item | r2 status | Evidence (static) |
|---|---|---|
| P1 skip link | **Resolved (code)** | `app.js:71`: the skip link calls `preventDefault()` and focuses `#main`, so the hash is untouched. `route()` (L63-67) treats `#main` as a focus target, not a screen. Initial load with `#main` falls back safely (last line). Not exercised in a browser. |
| P2 pitch semantics | **Resolved (code)** | Each line is a `<ul>` labelled Goalkeeper/Defenders/Midfielders/Forwards. There is a visible "Bench, in order of substitution" label and an `<ol>` bench linked by `aria-labelledby`. Each player has hidden text with full name, role, club and points; the short visible text is `aria-hidden`. The container is a `role="group"` with a label. Not checked with a screen reader. |
| P3 non-colour cues | **Resolved (values)** | Mobile nav current: bold plus a 3 px top bar in accent (accent vs surface **9.04:1**). Desktop nav current: background plus a 2 px underline bar. Toggle group border `#7a838e`: **4.77:1** on the page background. Pressed state: 3 px accent bar (**8.07:1**) plus a ✓ prefix. Ratios computed from the CSS values. |
| P4 pay question | **Resolved** | Task 7 now asks "Would either be worth paying for, compared with what you use now, or not?" "I would not pay" is recorded without probing. The scoring sheet has would pay / would not pay / unsure, and Task 6 is scored unaided / prompted / not found. |
| Exploratory label | **Added** | `index.html:112-113`: "Exploratory: early forecast, not yet proven accurate", next to the expected gain. |
| Pitch name wrapping | **Changed, unverified** | `hyphens: auto` (page `lang="en-GB"`) plus `overflow-wrap`. The rendering wasn't seen, because the screenshots are from revision 1 and weren't regenerated. |

## Remaining (not blocking)

- **Browser zoom at 200% and 400% is still unverified.** So are screen-reader output, real phones and Windows
  high-contrast. `manual-checks.md` L6 and L50 say so correctly. My 320 px reflow observation was on revision 1
  only.
- Screen readers number the bench list "1. Bench goalkeeper, 2. Substitute 1…", so the list number doesn't match
  the substitute number. Consider putting the bench goalkeeper outside the `<ol>`.
- `list-style: none` on the pitch lists can make Safari/VoiceOver drop the list meaning. Add `role="list"` if that
  matters.
- "Mid" and "Fwd" on the captain card are still inconsistent with "MID"/"FWD" elsewhere (`index.html:140-141`).
- The saved screenshots and `results.json` reflect revision 1. Regenerate them when runtime checks are allowed again.
