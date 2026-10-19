import assert from "node:assert/strict";
import { test } from "node:test";

import { page } from "../local/app.js";

const view = {
  kind: "full", gw: 7, action: "roll", chip: null, xp: 53.7, xp_no_transfers: 54, hits: 0, bank_after: 0.2,
  ft_after: 1, transfers: [], captain: { id: 1, name: "Haaland", team: "MCI", pos: "FWD", xp: 5.9 },
  vice: { id: 2, name: "Groß", team: "BHA", pos: "MID", xp: 5.2 }, captain_options: [], xi: [], bench: [],
  held: {}, warnings: [], field: [], checks: [], horizon: [],
};

test("the local dashboard shows what the agent did and the plan", () => {
  const out = page({
    plan: { mode: "live", decision: { gw: 7 }, outcome: { done: ["set captain Haaland"], errors: ["oops <b>"] }, view, notes: ["odds: 20 matches priced", "rank target top 10,000"], created_at: new Date().toISOString() },
    report: "fpl-agent weekly plan", next: { gw: 8, deadline: "2099-01-01T00:00:00Z" },
  }).toString();
  assert.match(out, /Live on your team/);
  assert.match(out, /Set captain Haaland/);
  assert.match(out, /Oops &lt;b&gt;/);
  assert.match(out, /This is the GW7 plan/);
  assert.match(out, /Rank target top 10,000/);
  assert.doesNotMatch(out, /Odds: 20/); // routine notes are left out
  assert.match(out, /Full report/);
});

test("without a structured view it falls back to the text report, and says when there is no plan", () => {
  const out = page({ plan: { mode: "dry", decision: { gw: 8 }, outcome: null }, report: "GW8: roll", next: { gw: 8, deadline: "2099-01-01T00:00:00Z" } }).toString();
  assert.match(out, /Dry run/);
  assert.match(out, /GW8: roll/);
  assert.doesNotMatch(out, /This is the GW/);
  assert.match(page({ plan: null, error: "offline" }).toString(), /offline/);
});
