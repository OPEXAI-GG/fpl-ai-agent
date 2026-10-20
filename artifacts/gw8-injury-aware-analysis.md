# Your GW8 analysis

Generated 21 October 2026 (Dubai), using public entry 8700026. Dry run only: no account
changes. Recorded run `ebf7b3d8409f4255`, projection `aa41fa953903`, job `4fdcb72c3272`.
The final run exited 0, status `ok`, state `planned`, with no safety blocks.

**Recommendation: roll the free transfer, keep Haaland in the XI, use no chip.**
Formation 3-5-2; bank GBP0.2m. Projected starting XI plus captain: **49.5 points**.
The separate pre-final-captain simulated score including legal bench cover was 51.62;
these two figures have different definitions and must not be treated as an improvement.

| Role | Players |
|---|---|
| Goalkeeper | Raya (vice) |
| Defenders | Calafiori (captain), Leif Davis, De Cuyper |
| Midfielders | Mbeumo, Le Fee, Palmer, Gross, Barnes |
| Forwards | Haaland, Evanilson |
| Outfield bench, in order | Truffert, Thomas-Asante, Tarkowski |
| Substitute goalkeeper | Leno |

Calafiori projects 5.09 points; Raya 4.38; Haaland 3.89 with 63.3 expected minutes and a
21% simulated zero-point outcome (zero points is not identical to zero minutes).
These are model outputs, not a claim that Calafiori is certain to outscore Haaland.

Haaland's 75% doubt no longer excludes him. Availability already affects his minutes.
If he plays zero minutes, FPL can use the first playing substitute who preserves a legal
formation. If he makes a cameo, he stays in the XI. With all other starters playing,
Truffert can replace him and form a legal 4-5-1. The goalkeeper bench slot is separate.

The sampled transfer check rejected Truffert to Konsa: it beat rolling in 45% of worlds
and lost 0.4 points over the horizon. Free Hit's best estimated gain was 6.0, below the
existing 8-point threshold; Bench Boost's was 10.5, below 14. Future horizon transfers are
tentative, to be re-evaluated after new games and news.

## Affordable differential watchlist

Overall ownership is from the stored FPL snapshot, not the top-10k sample. These are
predictions across four GWs, not guaranteed returns or automatic buy recommendations.

| Player | Price | Owned | Predicted points | Rate evidence |
|---|---:|---:|---:|---|
| Diego Gomez | GBP5.0m | 3.8% | 17.0 | Position prior: unvalidated |
| James Hill | GBP5.5m | 0.8% | 16.5 | Player-specific league history |
| Luka Vuskovic | GBP5.1m | 5.6% | 16.2 | Player-specific league history |
| Enzo Le Fee | GBP5.7m | 2.9% | 16.3 | Player-specific league history |
| Daichi Kamada | GBP5.0m | 0.7% | 15.5 | Player-specific league history |

The watchlist requires 60+ expected minutes per GW, affordability and positive projected
advantage against same-position peers costing the same or less. Low ownership earns no
extra projected points. Confirm current role, set pieces, injuries and fixture outlook;
one season's limited minutes are not proof of a sustainable edge. Le Fee is already owned.

## News and European workload

City's official [AEK team news](https://www.mancity.com/news/mens/city-v-aek-athens-team-news-63928107)
lists Haaland on the bench on 20 October. The preceding
[press conference](https://www.mancity.com/news/mens/maresca-aek-athen-written-press-conference-63928003)
described an assessment after a knock. These support treating him as doubtful, not a confirmed
weekend absence, and do not establish next-match fitness or a numerical probability.
These pages were checked separately through browser research: automated City requests failed.
The local monitor records those failures rather than claiming verification succeeded.

The imported schedule shows these midweek matches for clubs represented in your squad:

| Club | Competition | Kickoff (UTC) |
|---|---|---|
| Manchester City | Champions League | 20 Oct, 19:00 |
| Manchester United | Champions League | 21 Oct, 16:45 |
| Arsenal | Champions League | 21 Oct, 19:00 |
| Brighton | Conference League | 22 Oct, 16:45 |
| Sunderland | Europa League | 22 Oct, 16:45 |
| Bournemouth | Europa League | 22 Oct, 19:00 |

Recheck actual participation, minutes, new knocks and the subsequent club press conferences.
Scheduled team games alone do not prove individual fatigue or rotation. This implementation
does not invent an extra cup-fatigue penalty. City visit Villa on 24 October at 11:30 UTC;
Arsenal host Everton at 14:00 UTC. Your squad's league fixtures begin Friday 23 October.

## Evidence and remaining limits

The new public-data import added 6,862 appearances and 101,072 player-stat rows across
70 ENG1, 38 UCL, 36 UEL and 18 UECL completed matches. Quality checks reported no issues;
179 source identities remain unlinked rather than force-merged.
304/668 projected players now use player-specific league/translated evidence; 364 still
use position priors. This is improved input coverage, not a measured increase in FPL accuracy.

The independent historical minutes study trained on 27,605 rows from 2024 and tested on
29,747 from 2025: Brier 0.07826 versus 0.10358 baseline; log loss 0.25332 versus 0.35081.
All position groups beat that baseline. Archive snapshots are reconstructed at kickoff;
this does not validate predeadline injury probabilities or overall FPL performance.

News monitoring currently covers City official news and BBC Football. It is advisory,
not comprehensive coverage of every club. Laptop monitoring requires running `news-check`;
the two-hour workflow change is local and has not been deployed remotely. Optional paid
odds/scout integrations were disabled in this dry run. No account actions, notifications or
remote deployment were performed.
