# Manual and automated checks: what was actually done (2026-10-29, Mona)

> **Everything below this note describes revision 1** (the files Wegz hashed in `artifacts/weekly-check-prototype-review.md`).
> Revision 2 (FAA-31 fixes) was made by file edits only. It has **not** been run, screenshotted or checked in a browser.
> Deferred until the incident tooling hold lifts: all runtime checks, including the skip link on every screen, the pitch
> accessibility tree, measured contrast of the new selected-state cues, and browser zoom at 200% and 400%.
>
> Revision 2 changes, by finding:
> - P1: the skip link now moves focus with `preventDefault` and leaves the hash alone; `route()` ignores `#main`.
> - P2: the pitch lines are labelled lists (Goalkeeper, Defenders, Midfielders, Forwards). The bench is an ordered list
>   under a visible "Bench, in order of substitution" label. Each player has a screen-reader-only full name, role, club and
>   points, and the visible short labels are `aria-hidden`. The table status for the bench goalkeeper now reads "Bench goalkeeper".
> - P3: the current page in the phone menu adds a top bar and bold text to the colour change. The Pitch/List border is `#7a838e`
>   (calculated by hand at about 4.8:1 against the page; not measured). The pressed button adds a ✓ and an underline.
> - P4: script Task 7 offers "or not" and records "would not pay" as a result.
> - Recommended items applied: "Exploratory" tag beside expected gain; "free" removed from the start screen; `aria-label`
>   removed from the brand `div`; Task 6 list-view finding scored as prompted; blocking consent items added to the script.

## Automated (`run-checks.mjs`, Playwright Chromium 1.64.1 from the repo)

11 states × 4 widths (360, 390, 768, 1440 px), 40 audited combinations. Final run: **0 findings, 0 console/page errors**.
Per state it checks: horizontal page overflow; visible controls under 44 × 44 px; text under 12 px;
text contrast against the nearest solid background (4.5:1 normal, 3:1 large); controls without an accessible name.
Lowest measured text contrast: 8.29:1. Results: `checks/results.json`.

Keyboard journey at 390 px, scripted: Tab reaches the skip link first; Enter moves focus to main; the ID form submits with Enter;
after each screen change focus lands on the new page heading; confirm button and compare link reachable by Tab;
the focus ring is a 3 px solid outline.

Limits of the contrast script: it ignores background images and transparency layers and does not check non-text contrast
(icons, borders). It is not a replacement for axe or a manual audit. axe-core was not used (see below).

## First automated run found, then fixed

- Squad pitch overflowed horizontally at 360/390 px (402 px wide): grid column grew to fit names. Fixed with `minmax(0, 1fr)`.
- Skip link 40 px high and "Check again" button 36 px high. Raised to 44 px.
- Facilitator checkboxes flagged as unnamed: false positive from the closed `<details>`; the script now skips closed panels.

## By eye (screenshots opened and read)

Inspected: `week-1440`, `week-stale-range-390`, `squad-pitch-360` (three times, before and after fixes), `compare-390`.
Found three defects the automated checks missed, all fixed:
1. The "may be out of date" warning showed on fresh data, because `display:flex` overrode `hidden`. Added `[hidden]{display:none !important}`.
2. Pitch names truncated on phones ("S. Rade…"). The pitch now shows surnames and wraps them; the List view keeps full names.
3. A focus ring was drawn on the page heading after navigation. Removed for programmatically focused headings only.

## Not done

- Screen reader (NVDA, VoiceOver, TalkBack): not tested.
- Real phones, browser zoom to 200%/400%, Windows high-contrast, reduced-motion by eye: not tested.
  (360/390 px widths approximate 400% reflow at 1440 px but are not a zoom test.)
- axe-core: an attempt to install it outside the repo caused an incident in the user's home folder
  (reported to god, FAA-24). No checker was installed; Wegz may run axe with an approved setup.
- WCAG 2.2 AA conformance is **not** claimed.
