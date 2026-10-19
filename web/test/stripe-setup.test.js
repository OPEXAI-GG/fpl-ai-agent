import assert from "node:assert/strict";
import { test } from "node:test";

import { setup } from "../scripts/stripe-setup.mjs";

function fakeStripe(state) {
  const calls = [];
  return {
    calls,
    async call(method, path, params) {
      calls.push(`${method} ${path}`);
      if (method === "GET" && path === "/prices") return { data: state.prices };
      if (method === "GET" && path === "/billing_portal/configurations") return { data: state.portals };
      if (method === "GET" && path === "/webhook_endpoints") return { data: state.hooks };
      if (path === "/products") return { id: "prod_1" };
      if (path === "/prices") state.prices.push({ lookup_key: params.lookup_key, product: params.product });
      if (path === "/billing_portal/configurations") state.portals.push({ id: "bpc" });
      if (path === "/webhook_endpoints") state.hooks.push({ url: params.url });
      return {};
    },
  };
}

test("stripe setup creates everything once and is safe to re-run", async () => {
  const state = { prices: [], portals: [], hooks: [] };
  const s = fakeStripe(state);
  await setup(s, "https://fplsim.com", () => {});
  assert.deepEqual(state.prices.map((p) => p.lookup_key).sort(), ["fplsim_pro_monthly", "fplsim_season_pass"]);
  assert.deepEqual(state.hooks, [{ url: "https://fplsim.com/stripe/webhook" }]);
  assert.equal(state.portals.length, 1);
  const again = fakeStripe(state);
  await setup(again, "https://fplsim.com", () => {});
  assert.ok(again.calls.every((c) => c.startsWith("GET")), again.calls.join(", "));
});
