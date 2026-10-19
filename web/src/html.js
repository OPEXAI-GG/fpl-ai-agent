// A tiny HTML template tag: every interpolated value is escaped unless it is already markup
// (the result of another `html` call) or an array of those.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export class Markup {
  constructor(s) {
    this.s = s;
  }
  toString() {
    return this.s;
  }
}

export const escape = (v) => String(v).replace(/[&<>"']/g, (c) => ESC[c]);

function piece(v) {
  if (v === null || v === undefined || v === false) return "";
  if (v instanceof Markup) return v.s;
  if (Array.isArray(v)) return v.map(piece).join("");
  return escape(v);
}

export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((v, i) => {
    out += piece(v) + strings[i + 1];
  });
  return new Markup(out);
}

export const raw = (s) => new Markup(s);
