// One-off (safe to re-run): creates FPL Sim's product, its two prices, the customer portal
// settings and the webhook in the Stripe account whose key is in STRIPE_SECRET_KEY.
// Run by .github/workflows/site-deploy.yml. Prices are found again by their lookup keys.
//   STRIPE_SECRET_KEY=sk_test_... SITE_URL=https://fplsim.com node scripts/stripe-setup.mjs

import { LOOKUP, Stripe } from "../src/stripe.js";

const EVENTS = [
  "checkout.session.completed",
  "checkout.session.async_payment_succeeded",
  "customer.subscription.created",
  "customer.subscription.updated",
  "customer.subscription.deleted",
  "invoice.paid",
];
const PRICES = {
  monthly: { unit_amount: Number(process.env.MONTHLY_PENCE || 499), recurring: { interval: "month" }, nickname: "Pro monthly" },
  season: { unit_amount: Number(process.env.SEASON_PENCE || 2999), nickname: "Season pass 2026/27" },
};

export async function setup(stripe, site, log = console.log) {
  const found = await stripe.call("GET", "/prices", { lookup_keys: Object.values(LOOKUP), active: true });
  const have = new Set(found.data.map((p) => p.lookup_key));
  let product = found.data[0]?.product;
  if (!product) {
    const p = await stripe.call("POST", "/products", {
      name: "FPL Sim Pro",
      description: "Full FPL plan: transfers, chips, XI, bench and the six-week horizon.",
      tax_code: "txcd_10000000", // general electronically supplied services
    });
    product = p.id;
    log(`created product ${product}`);
  }
  for (const [kind, spec] of Object.entries(PRICES)) {
    if (have.has(LOOKUP[kind])) {
      log(`price ${LOOKUP[kind]} exists`);
      continue;
    }
    await stripe.call("POST", "/prices", {
      product, currency: "gbp", lookup_key: LOOKUP[kind], tax_behavior: "inclusive", ...spec,
    });
    log(`created price ${LOOKUP[kind]}`);
  }

  const portals = await stripe.call("GET", "/billing_portal/configurations", { active: true, limit: 1 });
  if (!portals.data.length) {
    await stripe.call("POST", "/billing_portal/configurations", {
      business_profile: { privacy_policy_url: `${site}/privacy`, terms_of_service_url: `${site}/terms` },
      features: {
        invoice_history: { enabled: true },
        payment_method_update: { enabled: true },
        customer_update: { enabled: true, allowed_updates: ["email", "address"] },
        subscription_cancel: { enabled: true, mode: "at_period_end" },
      },
    });
    log("created customer portal settings");
  }

  const url = `${site}/stripe/webhook`;
  const hooks = await stripe.call("GET", "/webhook_endpoints", { limit: 100 });
  if (!hooks.data.some((h) => h.url === url)) {
    await stripe.call("POST", "/webhook_endpoints", { url, enabled_events: EVENTS });
    log(`created webhook ${url}`);
  } else log(`webhook ${url} exists`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const key = process.env.STRIPE_SECRET_KEY;
  if (!key) {
    console.log("STRIPE_SECRET_KEY is not set: Stripe setup skipped");
    process.exit(0);
  }
  await setup(new Stripe(key), (process.env.SITE_URL || "https://fplsim.com").replace(/\/$/, ""));
}
