# GW8 dry analysis — entry <owner-entry-id>

No account changes were made. This uses the public squad and refreshed local inputs; current
post-transfer account state is unavailable without signing in, which this run did not do.

The corrected plan rolls the free transfer and saves chips. Stored predicted score: **46.45
points** (about 46; predictions are not guaranteed). Captain: **Evanilson**. Vice: **Calafiori**.

Formation: **3-5-2**.

- Goalkeeper: Raya.
- Defence: Calafiori, Leif Davis, De Cuyper.
- Midfield: Mbeumo, Le Fée, Palmer, Groß, Barnes.
- Forwards: Thomas-Asante, Evanilson.
- Outfield bench: Haaland, Truffert, Tarkowski. Reserve goalkeeper: Leno.
- Bank after plan: £0.2m. No transfer hits.

Haaland is on the bench because the current doubtful/unavailable flag fails the agent's strict
starting-availability policy. Recheck the official flag and team news before the deadline.
The Truffert → Konsa move was rejected: it beat rolling in only 48% of simulated worlds,
with approximately +0.1 points over the horizon. Free Hit (+4.8) and Bench Boost (+12.0) failed
the configured 8- and 14-point gain thresholds respectively.

**Confidence limitation:** all 668 projected players currently use position priors for player
event rates, because no usable detailed appearance/xG/xA history is stored. Injury/price/news,
public squad and ownership inputs are real, but this is not a fully evidence-calibrated
player model. Optional odds and AI scouting keys were not enabled. Treat captaincy and
future transfer suggestions as provisional; no parameter tuning or historical accuracy claim
was made on missing inputs.

The first dry plan exposed a planner/rail mismatch that allowed a flagged owned player into
the proposed XI. The corrected planner excludes such starters without weakening the safety
rail. The final run has no blocks or errors, and only dry "would" actions.

Full raw report: next-gameweek-dry-plan.txt. Run verification: dry-run-verification.json.
