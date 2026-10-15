// Sign-in emails through Resend (https://resend.com).

import { html } from "./html.js";

export async function sendLink(env, to, link, fetcher = fetch) {
  const body = html`<div style="font-family:system-ui,sans-serif;font-size:16px;line-height:1.5;color:#111">
    <p>Here is your link to sign in to FPL Sim:</p>
    <p><a href="${link}" style="display:inline-block;background:#d4ff3a;color:#0a0d0b;padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:700">Sign in to FPL Sim</a></p>
    <p style="color:#555;font-size:14px">The link works once and for 30 minutes. If you did not ask for it, ignore this email.</p>
  </div>`;
  const res = await fetcher("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: env.EMAIL_FROM,
      to: [to],
      subject: "Your FPL Sim sign-in link",
      html: body.toString(),
      text: `Sign in to FPL Sim: ${link}\n\nThe link works once and for 30 minutes.`,
    }),
  });
  if (!res.ok) throw new Error(`resend: ${res.status}`);
}
