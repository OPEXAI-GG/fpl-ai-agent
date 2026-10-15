// Sign-in by emailed link and a session cookie. Only SHA-256 hashes of tokens are stored.

const enc = new TextEncoder();
export const COOKIE = "fplsim_session";
export const LINK_MINUTES = 30;
export const SESSION_DAYS = 60;
export const RESEND_GAP_SECONDS = 60;

export const now = () => Math.floor(Date.now() / 1000);

export function token(bytes = 32) {
  const b = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function hash(s) {
  const d = await crypto.subtle.digest("SHA-256", enc.encode(s));
  return [...new Uint8Array(d)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

// Constant-time comparison for the engine's bearer token.
export function same(a, b) {
  const x = enc.encode(a || "");
  const y = enc.encode(b || "");
  if (!x.length || x.length !== y.length) return false;
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

export function readCookie(req, name = COOKIE) {
  const header = req.headers.get("Cookie") || "";
  for (const part of header.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return null;
}

export function sessionCookie(value, maxAge = SESSION_DAYS * 86400) {
  return `${COOKIE}=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}

export const EMAIL_RE = /^[^\s@<>"]{1,64}@[^\s@<>"]{1,190}\.[a-z]{2,}$/i;
export const cleanEmail = (s) => String(s || "").trim().toLowerCase();
