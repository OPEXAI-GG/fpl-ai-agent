# Review: customer discovery draft (FAA-28)

Reviewer: Wegz (wegz-mvsc3fox), 2026-10-28. Draft review only; no fieldwork, browsing or legal determination.
Reviewed file: `docs/research/customer-discovery-draft.md`, 116 lines,
SHA-256 `c838cf2b8af4c67a5038525d043ad567737d03b7a5646a072b44bc972ca6733a`.

## Verdict: REQUEST CHANGES

The structure is sound: evidence level is stated honestly (L7), past behavior is weighted above intent (L35),
the screener hides the product and price (L39), no accuracy claims are allowed (L3), and nothing is authorized
without approval (L91). Six fixes are needed before the draft is ready for a pilot. None needs new research.

## Required fixes

**R1. Quota-selected payers cannot test H2 (L27, L50a).** Selection deliberately covers "paying vs non-paying
users", so the share of payers among interviewees is set by recruitment, not by the market. "8 of 12 have paid"
would then measure the quota. Fix: either drop the payer quota, or keep it and restate H2 as a within-group
question (for example, among payers: what they pay for and why they stopped; among non-payers: what they use
instead). State plainly that interview counts never estimate how many managers pay.

**R2. Sample size, denominators and thresholds are inconsistent.**
- L26-27 use "of 12"; L50 targets 12-15; L95 excludes 3-4 pilot interviews from counts. Fix: define N as
  completed, non-pilot interviews and express thresholds against N (or fix N = 12).
- H2 support uses "past 12 months" but reject uses "ever paid" (L27), and 4-7 payers falls in neither. Fix:
  use the same time window and name the inconclusive band.
- "Most" is undefined in H1 reject, H4 support/reject and H6 reject (L26, L29, L31). Fix: give a count of N.
- H7 "70%" (L32): 70% of whom? (started import, invited, signed up). H8 "50% of activated users" (L33):
  define "activated" and "return" (opened brief, saved a team?).
- H6 "at least 10 beta users commit" (L31): no denominator and no definition of "commit". See R4.
- H2 hypothesis (L12) includes "would consider paying" (intent), but its evidence (L27) is past behavior only.
  Align: test past paying here and move intent to H6.

**R3. H5 success criterion is unclear (L30).** "Can say, unprompted, which feature they would not give up the
free tier without" does not parse, and "unprompted" conflicts with a card sort, which is prompted. The reject
condition "would pay for the free items or none of the paid items" mixes two different failures. Fix, for example:
support if at least k of N place at least one proposed paid feature in "worth paying for" **and** place the
proposed free features in "must be free"; revise if most proposed paid features land in "must be free" or
"not needed". Name which cards are the proposed free and paid ones (L15 vs L80; "price alerts" and "export"
are on L80 but not in H5).

**R4. Comprehension, stated intent and real conversion are mixed (L28-L33).** Label each criterion with its
evidence level:
- comprehension (prototype task: can they state the action, explain the range): H3, H4;
- stated preference or intent (card sort, price questions): H5, H6 first half;
- real behavior (import completion, return visits, payment): H7, H8, H6 second half.
H4 support "most say it raises trust" is stated attitude; add a comprehension check (can they explain what the
range means). H6's beta "commitment" needs a definition. Real payment is not authorized (charter, L3), so if it
means a pre-order, deposit or signed intent, say so and label it as weaker than conversion. H8 is correctly
marked beta-only.

**R5. No comparison with free alternatives or no purchase (L72, L81).** Section E asks expected cost and price
limits but never asks against what. Fix: add a neutral question after the price items, such as "Compared with what
you use now, including free sites or nothing, what would you do?" with options to keep current free tools, use
nothing extra, or pay. Accept "I would not pay for this" as a recorded result. L72 ("what would have to be true
for you to [pay]?") is hypothetical and presumes paying; reword to "What, if anything, would make paying worth it
compared with what you use for free?"

**R6. "No prices before E" conflicts with spending questions in D and the screener (L54 vs L46, L70).** Section D
and screener Q6 ask what participants paid. Fix the rule to: "never state our prices or test prices before E;
participants' own past spending may be asked in the screener and D."

## Smaller wording fixes (recommended)

- L81 asks two price thresholds in one question, against the one-question rule (L54). Split them.
- L77 "What would you check before trusting it?" presumes they would trust it. Use "How much would you rely on
  this, and why?"
- L48 combines the age check and contact consent in one item. Split them so consent is a separate, explicit answer.
- L47 country is "needed for market/tax questions". Tax is not a research-stage need; collect country only to
  plan per-market privacy review (L100).
- L3 says the historical model findings are "partly fixed and unverified". Current status (FAA-21, from reading
  the code): 1 fixed, 1 partly fixed, 4 still present. The ban on accuracy claims stands. Do not cite a defect count.
- L116 "Not reviewed by anyone else": update after this review.

## Consent and data handling: future dependencies to list (not resolved by this draft)

L100 states principles. Before any contact, list these as open items needing approval:
- who holds and is responsible for the data (data controller) and a privacy notice shown before the screener;
- lawful basis per market and the per-country review already noted on L100;
- separate consent for recording vs notes, and how consent is captured and stored;
- how 18+ is checked, and what happens to an under-18 screener response;
- retention period, storage location and access, deletion and withdrawal process (L83 promises to state it, so it
  must exist first);
- incentives, panels and community posting stay behind budget/channel approval (L96-L100, already stated).

## Claims needing a fresh source before any external use

Historical (dated 21 Oct or earlier); keep them out of participant materials until re-verified:
- competitor prices: Scout £10/month, Fix about £3.5-4/month (L16); FPL Review and Fantasy Football Hub (L111);
- financial inputs: 20% VAT, 3% + £0.25 fees, £0.60 unit cost, fixed costs and break-even counts (L104), which
  are illustrative, not quotes;
- data rights: FotMob terms barring commercial reuse and Premier League database-right reservation (L108). These
  are legal claims; they need current terms and legal review, not this draft review;
- working name "Matchroom" not cleared (L112), correctly excluded from participant materials.

## Re-review

I will re-review only the changed lines against R1-R6 and record the new SHA-256.

---

# Re-review r2: changed lines (2026-10-28)

Reviewed revision: `docs/research/customer-discovery-draft.md`, 155 lines,
SHA-256 `b20168750b59bc65a4970ed81c03e899c038ca4fefe164dd09343c6d53632f4c`. Checked what each change actually
does, not just the disposition label. Thresholds may stay provisional for this draft (god); fieldwork is separate.

## Verdict: REQUEST CHANGES (narrowed to three small fixes)

| Item | r2 status | Evidence |
|---|---|---|
| R1 payer quota | **Resolved** | Payer status is recorded, not a quota (L63, L68). H2 is tested within groups, and a small group is "insufficient", not a rejection (L33). Interview counts never estimate market share (L52, L116). |
| R2 N and denominators | **Mostly resolved** | N is defined with pilots excluded (L28). Every threshold has an inconclusive band. H2 uses the 12-month window throughout. The H7/H8 denominators, "activated" and "return" are defined (L47-49). Remaining: F2 and F3 below. |
| R3 H5 criterion | **Resolved, one gap** | Cards are named as free, paid or exploratory, in random order (L42). Support and reject are counts of N (L36). Gap: F2. |
| R4 evidence levels | **Resolved except H6 behaviour** | C/S/B levels are defined (L9-13) and applied per row. H4 now tests understanding first (L35, L103). Exception: F1. |
| R5 free / no-purchase comparison | **Resolved** | L97-98 replace the leading question. L110 offers keep-free, nothing extra or pay, and records "I would not pay" as a result. |
| R6 price rule | **Resolved** | L78 bans only our own and test prices, and allows past spending in the screener and D. The three price fields are kept separate (L44). |

## Required fixes

**F1. The H6 "commitment" is stated intent, not behaviour, and must not collect signatures (L38, L50).**
"A signed, non-binding pre-order or intent-to-pay" is labelled B-observed. Nothing here authorizes collecting
signatures, contact details or payment. An optional, non-binding statement of interest is **stated intent (S)**, not
conversion and not a purchase commitment. Revise:
- L50: "Stated beta interest (H6): an optional, non-binding answer that the user would pay the test price. No
  signature, contact details or payment are collected unless separately approved. Reported as S (intent), never as
  conversion or a pre-order."
- L38: relabel the row "H6 beta intent, S, beta only". Real purchase (B-observed) is a later gate that needs
  payment approval and is outside this draft.
- Drop "pre-order" and "commit" wording on L38, L50 and L13.

**F2. Overlapping bands need a precedence rule (L35, L36).** The same participant can meet support and reject at
once:
- H5: one paid card goes in "worth paying for" while two go in "must be free".
- H4: C 6-7 (inconclusive) together with S ≤ 4 (reject).
Add one line: "If support and reject both hold, or inconclusive and reject both hold, the result is reject or
revise. Report the split."

**F3. Monthly price equivalence (L37).** "≥ £49/year (or £4.08/month)" treats a stated £5/month limit as support,
although the monthly test price is £6.99. Compare monthly answers with £6.99 and yearly answers with £49, or state
that only the yearly anchor is tested and say why.

## Minor (recommended)

- L32 H1 reject still says "most descriptions", against the rule on L28. Give a count.
- L105 asks three questions in one bullet, against the one-question rule (L78). Split them.
- L65: say under-18 responses are discarded, rather than keeping "the age answer".

Everything else in the earlier review is resolved or correctly listed as a future dependency (L129-151). Next
re-review covers only F1-F3.

---

# Re-review r3: F1-F3 and minor items (2026-10-28)

Reviewed revision: `docs/research/customer-discovery-draft.md`,
SHA-256 `ef3224b794d46fc2d944a8bbce0b752de168834fb79c38aa70b61735cc5c52fb`. I checked only the changed lines and
the criteria they affect.

## Verdict: APPROVE (internal draft)

- **F1 intent vs purchase: resolved.**
  - L38 is now "H6 beta intent", labelled S and beta-only.
  - L52 defines it as an optional, non-binding answer. No signature, contact details or payment are collected
    unless separately approved. It is never reported as conversion or a pre-order. Real purchase is a later gate
    that needs payment approval.
  - No "pre-order" or "commit" wording remains.
- **F2 precedence: resolved.** L42: when support and reject both hold, or inconclusive and reject both hold, the
  result is reject or revise, and the split is always reported. It covers the H5 and H4 cases I raised.
- **F3 monthly/yearly: resolved.** L37 compares monthly answers with £6.99 and yearly answers with £49, reports the
  counts separately and never converts one into the other.
- **Minor: resolved.**
  - H1 no longer uses "most".
  - L106-108 split the questions one per bullet.
  - L67 under-18 answers are discarded with no identifiers kept.

Optional, not blocking: say how a participant who gives both a monthly and a yearly limit counts toward the
"≥ 6 of N" (once, as support if either matches).

Approval covers this internal draft only. Thresholds stay provisional. It does **not** authorize fieldwork,
outreach, recruitment, incentives or data collection. The consent and data items (section 7) and the source
refreshes (section 9) must be closed first.
