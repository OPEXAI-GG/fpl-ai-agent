# Customer discovery draft (DRAFT r2, 2026-10-28)

Owner: Layla. Status: **draft for god's review; revised after Wegz's review (FAA-28, R1-R6). Nothing here has been sent, posted or run.** No outreach, publication, purchase or live billing is authorized. Pricing and competitor facts come from [the 21 Oct strategy](subscription-product-strategy-2026-10-21.md) and are **dated 21 Oct 2026**; they must be re-verified before any external use. Model status: do not cite a defect count and make no accuracy claim. Per the latest code read (FAA-21), of the historical model findings one is fixed, one partly fixed, four old paths still present, no tests rerun; see the [model reliability audit](model-reliability-2026-10-21.md). Review status: reviewed once by Wegz; changed lines await re-review.

## 1. What we are trying to learn

Evidence today: desk research only. There have been **no customer interviews, no demand test and no payment test**. Everything marked H is a hypothesis.

**Evidence types.** Every criterion in section 2 is labelled with one of three levels; do not mix them when reporting.

- **C = comprehension**: what a participant can state or explain about a mock-up.
- **S = stated preference or intent**: what a participant says they would do or pay.
- **B = real behavior**: past behavior recalled in an interview (B-past), or observed behavior in a prototype or beta (B-observed). Stated intent (S) is weaker than B-past, which is weaker than B-observed.

| ID | Hypothesis | Type |
|---|---|---|
| H1 | The best first customer is a serious but time-poor manager who wants a dependable weekly decision in about five minutes. | Customer |
| H2 | Managers who pay for FPL tools do so for a recurring decision a squad-specific brief would address; those who do not pay rely on free substitutes that cover the same need. Tested within groups, not as a market share. | Customer / price (B-past) |
| H3 | A squad-specific brief (roll or transfer, captain, bench, alternatives, what changed) is understood and valued over generic projections. | Value |
| H4 | Visible uncertainty and a verifiable record are understood and do not reduce confidence. | Value |
| H5 | The proposed free/paid split is acceptable (free: team check, methodology, history record; paid: saved scenarios, weekly alternatives, news changes). | Packaging |
| H6 | A single paid tier near £6.99/month or £49/year is acceptable. Untested. Dated competitor context: Scout £10/month, Fix about £3.5-4/month billed yearly. | Price (S, then B) |
| H7 | Managers will enter or confirm squad state (bank, free transfers, chips, sell prices) without an FPL password. | Activation |
| H8 | Use continues across deadlines, not just launch curiosity. | Retention |

## 2. Accept / reject criteria

Small samples give directional signals only, never market estimates. **N = the number of completed, non-pilot interviews; N = 12 is the target** (recruit up to 15 to reach 12; pilot interviews are excluded). "Most" is never used: every threshold is a count of N, with an explicit inconclusive band. Thresholds are proposals for god's confirmation.

| H | Evidence | Support | Inconclusive | Reject or revise |
|---|---|---|---|---|
| H1 | S + B-past (interview B/C sections) | ≥ 8 of N describe weekly time pressure and a recurring hard decision | 6-7 of N | ≤ 5 of N, or ≥ 6 of N describe long research as something they enjoy and would not shorten |
| H2 | B-past, past 12 months only, **within groups** | Among payers (n_p): ≥ 3 name a specific benefit matching brief contents. Among non-payers (n_np): ≥ 3 name a free substitute covering the same need | Payers 2 or non-payers 2 | Payers ≤ 1 or non-payers ≤ 1 (or n_p or n_np < 3: record as insufficient, not as rejection) |
| H3 | C (prototype task) | ≥ 8 of N state the suggested action in their own words within 30 seconds | 6-7 of N | ≤ 5 of N |
| H4 | C first, S second | C: ≥ 8 of N correctly explain what the range or "what could change this" means. S: ≥ 7 of N say they would rely on it the same or more | C 6-7, or S 5-6 | C ≤ 5, or S ≤ 4 |
| H5 | S (card sort, defined below) | ≥ 8 of N place at least one proposed paid card in "worth paying for" **and** ≥ 8 of N place at least two of the three proposed free cards in "must be free" | Either condition 6-7 | ≥ 7 of N place at least two of the three proposed paid cards in "must be free" or "not needed" |
| H6 stated | S | ≥ 6 of N give an own-stated upper price limit at or above the matching test price: monthly answers compared with £6.99/month, yearly answers with £49/year. Report monthly and yearly counts separately; never convert one into the other | 4-5 of N | ≤ 3 of N |
| H6 beta intent | S, **beta only** | Of beta users shown the optional interest question (below): ≥ 10 say they would pay the test price **and** ≥ 33% of those shown | 5-9 say yes, or ≥ 10 but < 33% | ≤ 4 say yes |
| H7 | B-observed (prototype or beta) | ≥ 70% of users who start team import complete it with a validated squad state | 50-69% | < 50% |
| H8 | B-observed, **beta only** | ≥ 50% of activated users return for four successive deadlines | 35-49% | < 35% |

**Precedence.** If support and reject both hold, or inconclusive and reject both hold, for the same hypothesis (for example H5: one paid card in "worth paying for" while two sit in "must be free"; H4: C 6-7 with S ≤ 4), the result is reject or revise. Always report the split alongside the verdict; never hide a mixed result.

**Card-sort definitions (H5).** Eight cards. Proposed free: *team check*, *methodology*, *history record*. Proposed paid: *saved scenarios*, *weekly alternatives*, *news changes*. Exploratory only, not scored for H5: *price alerts*, *export*. Cards are shown in randomized order.

**Own price versus past spend (H6, H2).** Record three separate fields per participant: (1) highest amount actually paid in the past 12 months (B-past), (2) their own expected price (open question), (3) their own stated upper limit. Never average or merge them. If the stated limit exceeds the highest past spend, mark it "intent above behavior" and weight it lower; if a participant has never paid, their stated range still counts for H6 stated but is flagged "no payment history". Our test prices are never stated to participants.

**Definitions for beta measures.**
- *Activated* = completed team import with a validated squad state and opened at least one weekly brief.
- *Return for a deadline* = opened the brief or saved or changed a plan in the window between the previous deadline and this deadline. H8 denominator: activated users for whom four successive deadlines have elapsed since activation.
- *H7 denominator*: invited beta users who accepted and started team import.
- *Stated beta interest (H6)*: an optional, non-binding answer that the user would pay the test price. No signature, contact details or payment are collected unless separately approved. Reported as S (intent), never as conversion or a pre-order. Denominator = beta users who completed import and were shown the question. Real purchase (B-observed) is a later gate that needs payment approval and is outside this draft.

Stated willingness to pay overstates real purchase. Interview counts never estimate how many managers pay for FPL tools.

## 3. Screener (neutral)

Purpose: confirm a plausible target and ensure variety. Do not reveal the product idea, a price, or any model claim.

1. How often do you play Fantasy Premier League during a season? (never / a few weeks / most weeks / every week)
2. Which seasons have you played? (select all)
3. Roughly what was your best overall rank in the last two seasons? (ranges, including "don't remember")
4. In a normal gameweek, how much time do you spend on your team? (< 10 min / 10-30 / 30-60 / > 1 hour)
5. Which of these have you used for FPL in the past 12 months? (list including free sites, podcasts, social media, community tools, paid sites, spreadsheets, none, other)
6. Have you paid for any FPL tool or content in the past 12 months? (yes/no; if yes, which, roughly how much). Recorded only; not used as a quota.
7. Which country do you live in? (optional; used only to plan a per-market privacy review)
8. Are you 18 or older? (yes/no; under-18 responses end the screener and are discarded, with no identifiers retained)
9. May we contact you about a 30-minute conversation about how you manage FPL? (yes/no; separate, explicit consent)

**Sampling.** Quotas apply to N = 12 completed non-pilot interviews. Payer status is **not** a quota; it is recorded and reported as a recruitment outcome only.
- Weekly time spent (Q4): at least 3 in "< 30 min" and at least 3 in "≥ 30 min".
- Rank (Q3): at least 3 whose best rank is better than 100,000 and at least 3 who are worse or do not remember.
- Markets (Q7): at least 2 countries, none above 8 of 12.
- Channel: no single recruitment channel above 4 of 12.
- Exclude employees of competing FPL tools and anyone under 18.
If a quota cannot be met, record the gap and its effect; do not relax silently.

## 4. Interview guide (30 minutes, non-leading)

Script rules: ask about past behavior; one question at a time; never describe our product before section E; never state our prices or test prices before, or in, section E (participants' own past spending may be asked in the screener and in D); never mention accuracy or rank gains; do not suggest answers; silence is fine; record only with explicit consent.

**A. Opening (2 min).** This is research about how people manage FPL, not a sales call; no right answers; can stop anytime; ask consent for notes and, separately, for recording.

**B. Current routine (8 min)**
- Walk me through what you did for your last gameweek, from first thought to confirming your team.
- What tools, sites or people did you use? What did each give you?
- What took the longest? What do you usually skip?
- When do you make the final decision relative to the deadline?

**C. Last painful decision (6 min)**
- Tell me about a recent decision that you felt unsure about. What was the situation?
- What information did you wish you had? Where did you look?
- What did you decide, and why?
- Looking back, how did you judge whether it was a good decision?

**D. Spending and alternatives (6 min)**
- Which FPL tools or content, if any, did you pay for in the past 12 months? How did you choose them? How much, how often?
- Have you stopped paying for anything? What made you stop?
- If you pay for nothing: what do you use instead, and what does that not give you?
- What, if anything, would make paying worth it compared with what you use for free?

**E. Concept reaction (6 min, after B-D are complete)**
Show one neutral stimulus (a mocked weekly brief with placeholder data; no accuracy claim; no real team, club or brand marks).
- In your own words, what is this telling you to do? (H3, C)
- What does the range, or "what could change this", mean to you? (H4, C)
- How much would you rely on this, and why? (H4, S)
- What would you do with it in a normal week?
- Which part would you use most?
- Which part would you ignore?
- Card sort: place each of the eight cards as "must be free", "worth paying for" or "not needed". Ask why for the two most surprising placements. (H5, S)
- What would you expect something like this to cost? (open; H6, S)
- At what price would it feel too expensive to consider? (one question)
- At what price would you doubt its quality? (one question)
- Compared with what you use now, including free sites or nothing at all, what would you do: keep your current free tools, use nothing extra, or pay for something like this? "I would not pay for this" is a valid answer and is recorded as a result. (H6, S)

**F. Close (2 min).** Anything we did not ask that matters? Would you take part in a later private beta (no obligation)? State how the data will be used and how to withdraw (see section 7; this promise requires the retention and deletion process to exist before fieldwork).

## 5. Analysis plan

Code each interview for: time spent, tools used and spending (three price fields kept separate), decision pain points, comprehension results, trust cues, card-sort placements, price range, and the free/no-purchase choice. Keep verbatims separate from interpretation. Two people code independently where possible. Report counts against N with the sample size, label each as C, S or B, mark claims "directional", and list disconfirming cases. Report recruitment payer share only as a recruitment outcome. Do not report percentages as market-size estimates.

## 6. Recruitment plan (planning only; no contact)

Nothing below is authorized until god approves in writing, per channel.

| Channel | Use | Risks / controls |
|---|---|---|
| Referrals from the team's own network | First 3-4 pilot interviews | Biased toward the team; pilots are excluded from N |
| FPL communities (forums, subreddits) | Variety of ranks and spending | Check each community's rules on research and promotion; disclose purpose; no incentive promise without budget approval |
| Social posts / creator audiences | Reach non-community players | Disclosed terms; creators must consent; no paid placement |
| Paid screener panel | Controlled sampling | Needs a purchase approval; not assumed |

## 7. Consent and data handling: open items needing approval before any contact

This draft states principles only (minimum data, deletion allowed, no identifiable quotes). The following are **unresolved future dependencies**:

- who holds the data and is responsible for it (data controller), and a privacy notice shown before the screener;
- lawful basis per market, with the per-country review noted in section 3 (Q7);
- separate consent for notes versus recording, and how consent is captured and stored;
- how age 18+ is checked, and what happens to an under-18 screener response;
- retention period, storage location and access, and the deletion and withdrawal process (section 4F promises it, so it must exist first);
- incentives, panels and community posting stay behind budget and channel approval.

## 8. Financial assumptions (not quotes)

Carried from the strategy's **illustrative** model and dated 21 Oct 2026; **not** actual tax obligations, processor quotes, vendor quotes or measured costs: 20% VAT-inclusive pricing, 3% + £0.25 per charge, £0.60 variable cost per active subscriber-month, fixed-cost scenarios £350, £1,200 and £3,350 (including an illustrative £3,000 founder pay). Break-even at £6.99/month: 74 / 252 / 703 subscribers; at £49/year: 132 / 452 / 1,260. Acquisition, refunds, data licence minimums and human news review are excluded. Real inputs needed: processor fee schedule for chosen markets, tax advice, a licensed-data quote, measured per-user compute cost.

## 9. Future dependencies (claims needing a fresh source before external use)

- Competitor prices (Scout, Fix; FPL Review and Fantasy Football Hub unverified) are historical and stay out of participant materials until re-verified.
- Financial inputs in section 8 are illustrative until replaced by real quotes.
- Data rights: FotMob terms barring commercial reuse and Premier League database-right reservation are legal claims from 21 Oct; they need current terms and legal review. Pricing cannot be finalized without a lawful data path.
- Target markets, payment provider, and cancellation and renewal terms are undecided.
- The stimulus mock needs Mona's design; this draft produces none.
- The working name "Matchroom" is not cleared; keep it out of participant materials.

## 10. Limitations

Desk research only. Reviewed once by Wegz (FAA-28); revisions await re-review. Question wording has not been piloted; run a 3-4 person pilot before the real sample. No interview, survey or contact has taken place.
