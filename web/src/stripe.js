// Stripe over its REST API (no SDK). Webhook events are not trusted as sent: each one is read
// back from Stripe by id with the secret key, so no webhook signing secret is needed.

const API = "https://api.stripe.com/v1";
export const LOOKUP = { monthly: "fplsim_pro_monthly", season: "fplsim_season_pass" };
export const GRACE_SECONDS = 3 * 86400; // a late renewal keeps Pro for a few days
const LIVE = new Set(["active", "trialing", "past_due"]);

// {a: {b: [1, 2]}} -> a[b][0]=1&a[b][1]=2
export function form(obj, prefix = "", out = new URLSearchParams()) {
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null) continue;
    const key = prefix ? `${prefix}[${k}]` : k;
    if (typeof v === "object") form(v, key, out);
    else out.append(key, String(v));
  }
  return out;
}

export class Stripe {
  constructor(key, fetcher = fetch) {
    this.key = key;
    this.fetch = fetcher;
  }

  async call(method, path, params) {
    const url = method === "GET" && params ? `${API}${path}?${form(params)}` : `${API}${path}`;
    const res = await this.fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${this.key}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: method === "GET" || !params ? undefined : form(params).toString(),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(`stripe ${method} ${path}: ${body?.error?.message || res.status}`);
    return body;
  }

  async price(kind) {
    const r = await this.call("GET", "/prices", { lookup_keys: [LOOKUP[kind]], active: true });
    if (!r.data?.length) throw new Error(`no active Stripe price with lookup key ${LOOKUP[kind]}`);
    return r.data[0].id;
  }

  async customer(user) {
    if (user.stripe_customer) return user.stripe_customer;
    const c = await this.call("POST", "/customers", {
      email: user.email,
      metadata: { user_id: user.id },
    });
    return c.id;
  }

  async checkout({ kind, customer, userId, site, tax }) {
    const price = await this.price(kind);
    return this.call("POST", "/checkout/sessions", {
      mode: kind === "monthly" ? "subscription" : "payment",
      customer,
      client_reference_id: String(userId),
      line_items: [{ price, quantity: 1 }],
      success_url: `${site}/app?paid=1`,
      cancel_url: `${site}/app?cancelled=1`,
      allow_promotion_codes: true,
      automatic_tax: tax ? { enabled: true } : undefined,
      customer_update: tax ? { address: "auto" } : undefined,
      metadata: { kind, user_id: String(userId) },
      custom_text: {
        submit: {
          message:
            "Your plan starts straight away. By paying you agree to that and to the FPL Sim " +
            "terms, so the 14-day cancellation period ends once your first plan is shown.",
        },
      },
    });
  }

  async portal(customer, returnUrl) {
    const cfg = await this.call("GET", "/billing_portal/configurations", { active: true, limit: 1 });
    return this.call("POST", "/billing_portal/sessions", {
      customer,
      return_url: returnUrl,
      configuration: cfg.data?.[0]?.id,
    });
  }

  event(id) {
    return this.call("GET", `/events/${encodeURIComponent(id)}`);
  }

  subscription(id) {
    return this.call("GET", `/subscriptions/${encodeURIComponent(id)}`);
  }
}

// The paid-up-to time of a subscription: its period end plus grace while it is live, else 0.
// Newer API versions keep the period end on the subscription item instead.
export function paidUntil(sub) {
  if (!LIVE.has(sub.status)) return 0;
  const end = sub.current_period_end ?? sub.items?.data?.[0]?.current_period_end ?? 0;
  return end ? end + GRACE_SECONDS : 0;
}

// End of the season pass: the configured date (YYYY-MM-DD, end of that day UTC).
export const seasonEnd = (date) => Math.floor(Date.parse(`${date}T23:59:59Z`) / 1000);
