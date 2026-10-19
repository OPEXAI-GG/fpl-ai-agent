// The site's one stylesheet, served at /style.css.

export const CSS = String.raw`
:root {
  --ink: #0b0e0c; --ink-2: #111612; --ink-3: #18201a; --line: #243028; --line-2: #33423a;
  --chalk: #eef2ea; --mute: #9aa79e; --dim: #6b786f;
  --lime: #d4ff3a; --lime-ink: #10140a; --coral: #ff7a59; --sky: #8ccbff;
  --r: 14px; --r-sm: 10px;
  --display: "Bricolage Grotesque", ui-sans-serif, system-ui, sans-serif;
  --body: "Geist", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "Geist Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
  color-scheme: dark;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--ink); color: var(--chalk); font: 16px/1.55 var(--body);
  background-image:
    radial-gradient(1200px 600px at 85% -10%, rgba(212,255,58,.07), transparent 60%),
    radial-gradient(900px 500px at -10% 30%, rgba(140,203,255,.05), transparent 60%);
  min-height: 100vh; overflow-x: hidden;
}
a { color: inherit; }
img, svg { max-width: 100%; display: block; }
.wrap { width: min(1120px, 100% - 32px); margin-inline: auto; }
.num { font-family: var(--mono); font-variant-numeric: tabular-nums; letter-spacing: -.01em; }

/* nav */
.nav { position: sticky; top: 0; z-index: 10; backdrop-filter: blur(12px);
  background: rgba(11,14,12,.72); border-bottom: 1px solid var(--line); }
.nav .wrap { display: flex; align-items: center; gap: 20px; height: 64px; }
.brand { display: flex; align-items: center; gap: 10px; text-decoration: none;
  font: 800 20px/1 var(--display); letter-spacing: -.02em; }
.brand-mark { width: 28px; height: 28px; }
.brand b { color: var(--lime); font-weight: 800; }
.nav-links { display: flex; gap: 22px; margin-left: auto; align-items: center; }
.nav-links a { text-decoration: none; color: var(--mute); font-size: 15px; }
.nav-links a:hover { color: var(--chalk); }
.nav-links a.btn-lime, .nav-links a.btn-lime:hover { color: var(--lime-ink); }
@media (max-width: 720px) { .nav-links .hide-sm { display: none; } }

/* buttons and forms */
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  border: 1px solid var(--line-2); background: var(--ink-3); color: var(--chalk);
  font: 600 15px/1 var(--body); padding: 13px 18px; border-radius: 999px; cursor: pointer;
  text-decoration: none; white-space: nowrap; transition: transform .15s, background .15s, border-color .15s; }
.btn:hover { border-color: var(--mute); transform: translateY(-1px); }
.btn-lime { background: var(--lime); color: var(--lime-ink); border-color: var(--lime); }
.btn-lime:hover { background: #e2ff70; border-color: #e2ff70; }
.btn-sm { padding: 9px 14px; font-size: 14px; }
.btn-block { width: 100%; }
.field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
.field label { font-size: 14px; color: var(--mute); }
input[type=email], input[type=text], input[type=number] {
  width: 100%; background: var(--ink); color: var(--chalk); border: 1px solid var(--line-2);
  border-radius: var(--r-sm); padding: 13px 14px; font: 16px var(--body); }
input:focus { outline: 2px solid var(--lime); outline-offset: 1px; border-color: transparent; }
.hint { font-size: 13px; color: var(--dim); }
.idform { display: flex; gap: 8px; background: var(--ink-2); border: 1px solid var(--line-2);
  padding: 6px; border-radius: 999px; max-width: 460px; }
.idform input { border: 0; background: transparent; border-radius: 999px; padding: 10px 14px; }
.idform input:focus { outline: none; }
.idform:focus-within { border-color: var(--lime); }

/* hero */
.hero { padding: 72px 0 40px; }
.hero .wrap { display: grid; grid-template-columns: 1.1fr .9fr; gap: 48px; align-items: center; }
@media (max-width: 900px) { .hero .wrap { grid-template-columns: 1fr; } .hero { padding-top: 40px; } }
.eyebrow { display: inline-flex; align-items: center; gap: 8px; font: 500 13px var(--mono);
  color: var(--lime); text-transform: uppercase; letter-spacing: .08em; }
.eyebrow::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--lime);
  box-shadow: 0 0 0 4px rgba(212,255,58,.15); animation: pulse 2.4s infinite; }
@keyframes pulse { 50% { box-shadow: 0 0 0 8px rgba(212,255,58,0); } }
h1, h2, h3 { font-family: var(--display); letter-spacing: -.03em; margin: 0; }
h1 { font-size: clamp(40px, 6.4vw, 76px); line-height: .98; font-weight: 800; margin: 18px 0 20px; }
h1 em { font-style: normal; color: var(--lime); }
h2 { font-size: clamp(30px, 4vw, 46px); line-height: 1.04; font-weight: 800; }
h3 { font-size: 20px; font-weight: 700; letter-spacing: -.01em; }
.lede { font-size: 19px; color: var(--mute); max-width: 560px; margin: 0 0 28px; }
.hero-note { margin-top: 12px; font-size: 14px; color: var(--dim); }

/* simulation card */
.sim { background: linear-gradient(180deg, var(--ink-2), var(--ink)); border: 1px solid var(--line);
  border-radius: 22px; padding: 22px; position: relative; overflow: hidden; }
.sim::after { content: ""; position: absolute; inset: 0; pointer-events: none;
  background: repeating-linear-gradient(0deg, rgba(255,255,255,.015) 0 1px, transparent 1px 4px); }
.sim-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.sim-name { font: 700 22px var(--display); }
.sim-meta { font: 12px var(--mono); color: var(--dim); text-transform: uppercase; letter-spacing: .08em; }
.sim-bars { display: flex; align-items: flex-end; gap: 3px; height: 180px; margin: 20px 0 10px; }
.sim-bars span { flex: 1; background: var(--line-2); border-radius: 3px 3px 0 0;
  transform-origin: bottom; animation: grow .9s cubic-bezier(.2,.8,.2,1) both; }
.sim-bars span.hi { background: var(--lime); }
.sim-bars span.haul { background: var(--coral); }
@keyframes grow { from { transform: scaleY(0); } }
.sim-axis { display: flex; justify-content: space-between; font: 12px var(--mono); color: var(--dim); }
.sim-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 18px; }
.stat { background: var(--ink); border: 1px solid var(--line); border-radius: var(--r-sm); padding: 12px; }
.stat b { display: block; font: 600 22px var(--mono); }
.stat small { color: var(--dim); font-size: 12px; }
.stat .lime { color: var(--lime); } .stat .coral { color: var(--coral); }

/* sections */
section { padding: 72px 0; }
.section-head { display: flex; justify-content: space-between; align-items: end; gap: 24px;
  margin-bottom: 32px; flex-wrap: wrap; }
.section-head p { color: var(--mute); max-width: 460px; margin: 0; }
.kicker { font: 500 13px var(--mono); color: var(--dim); text-transform: uppercase;
  letter-spacing: .1em; margin-bottom: 12px; }
.rule { border-top: 1px solid var(--line); }

/* table of captains */
.table { width: 100%; border-collapse: collapse; font-size: 15px; }
.table th { text-align: left; font: 500 12px var(--mono); color: var(--dim); text-transform: uppercase;
  letter-spacing: .08em; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.table td { padding: 14px 12px; border-bottom: 1px solid var(--line); }
.table tr:hover td { background: var(--ink-2); }
.table .r { text-align: right; }
.rank { color: var(--dim); font-family: var(--mono); width: 36px; }
.who b { font-weight: 600; } .who small { color: var(--dim); margin-left: 8px; font-family: var(--mono); }
.bar { position: relative; height: 6px; background: var(--ink-3); border-radius: 3px; min-width: 60px; }
.bar i { position: absolute; inset: 0 auto 0 0; background: var(--coral); border-radius: 3px; }
@media (max-width: 640px) { .table .opt { display: none; } }

/* steps and features */
.grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
@media (max-width: 860px) { .grid3 { grid-template-columns: 1fr; } }
.card { background: var(--ink-2); border: 1px solid var(--line); border-radius: var(--r); padding: 24px; }
.card p { color: var(--mute); margin: 10px 0 0; }
.step-n { font: 600 13px var(--mono); color: var(--lime); }

/* pricing */
.prices { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; align-items: stretch; }
@media (max-width: 900px) { .prices { grid-template-columns: 1fr; } }
.price { display: flex; flex-direction: column; background: var(--ink-2); border: 1px solid var(--line);
  border-radius: 20px; padding: 26px; position: relative; }
.price.best { border-color: var(--lime); box-shadow: 0 0 0 1px var(--lime), 0 30px 80px -40px rgba(212,255,58,.35); }
.price .tag { position: absolute; top: -12px; left: 24px; background: var(--lime); color: var(--lime-ink);
  font: 700 12px var(--mono); padding: 5px 10px; border-radius: 999px; text-transform: uppercase; letter-spacing: .06em; }
.price h3 { font-size: 18px; color: var(--mute); font-family: var(--body); font-weight: 600; letter-spacing: 0; }
.amount { font: 800 46px/1 var(--display); letter-spacing: -.03em; margin: 14px 0 4px; }
.amount small { font: 500 15px var(--body); color: var(--dim); letter-spacing: 0; }
.price ul { list-style: none; padding: 0; margin: 18px 0 24px; display: grid; gap: 10px; flex: 1; }
.price li { padding-left: 26px; position: relative; color: var(--mute); }
.price li::before { content: ""; position: absolute; left: 2px; top: 7px; width: 12px; height: 7px;
  border-left: 2px solid var(--lime); border-bottom: 2px solid var(--lime); transform: rotate(-45deg); }
.price form { margin: 0; }

/* faq */
details { border-bottom: 1px solid var(--line); padding: 18px 0; }
summary { cursor: pointer; font: 600 18px var(--body); list-style: none; display: flex;
  justify-content: space-between; gap: 16px; }
summary::-webkit-details-marker { display: none; }
summary::after { content: "+"; color: var(--lime); font: 400 24px/1 var(--mono); }
details[open] summary::after { content: "−"; }
details p { color: var(--mute); margin: 10px 0 0; max-width: 720px; }

/* footer */
footer { border-top: 1px solid var(--line); padding: 36px 0 48px; color: var(--dim); font-size: 14px; }
footer .wrap { display: flex; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
footer a { color: var(--mute); text-decoration: none; margin-right: 18px; }
footer a:hover { color: var(--chalk); }
.disclaimer { max-width: 560px; }

/* auth pages */
.narrow { width: min(460px, 100% - 32px); margin: 64px auto; }
.panel { background: var(--ink-2); border: 1px solid var(--line); border-radius: 20px; padding: 28px; }
.panel h1 { font-size: 34px; margin: 0 0 8px; }
.panel p { color: var(--mute); }
.flash { border: 1px solid var(--line-2); background: var(--ink-3); padding: 12px 14px;
  border-radius: var(--r-sm); margin-bottom: 18px; font-size: 15px; }
.flash.bad { border-color: rgba(255,122,89,.5); color: #ffc2b2; }
.flash.good { border-color: rgba(212,255,58,.45); color: #e9ffa6; }

/* dashboard */
.dash { padding: 36px 0 72px; }
.dash-head { display: flex; justify-content: space-between; align-items: end; gap: 16px; flex-wrap: wrap;
  margin-bottom: 26px; }
.dash-head h1 { font-size: clamp(32px, 4vw, 44px); margin: 6px 0 0; }
.pill { display: inline-flex; align-items: center; gap: 6px; font: 600 12px var(--mono);
  text-transform: uppercase; letter-spacing: .08em; padding: 5px 10px; border-radius: 999px;
  border: 1px solid var(--line-2); color: var(--mute); }
.pill.pro { color: var(--lime-ink); background: var(--lime); border-color: var(--lime); }
.dgrid { display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; }
@media (max-width: 900px) { .dgrid { grid-template-columns: 1fr; } }
.box { background: var(--ink-2); border: 1px solid var(--line); border-radius: 18px; padding: 22px; }
.box h3 { margin-bottom: 14px; }
.box-k { font: 500 12px var(--mono); color: var(--dim); text-transform: uppercase; letter-spacing: .1em; }
.big { font: 800 56px/1 var(--display); letter-spacing: -.03em; }
.cap { display: flex; align-items: center; gap: 16px; }
.armband { width: 56px; height: 56px; border-radius: 50%; display: grid; place-items: center;
  background: var(--lime); color: var(--lime-ink); font: 800 26px var(--display); flex: none; }
.armband.v { background: var(--ink-3); color: var(--chalk); border: 1px solid var(--line-2); width: 40px; height: 40px; font-size: 18px; }
.cap-name { font: 700 26px/1.1 var(--display); }
.cap-sub { color: var(--dim); font: 13px var(--mono); }
.moves { display: grid; gap: 10px; }
.move { display: grid; grid-template-columns: 1fr auto 1fr; gap: 10px; align-items: center;
  background: var(--ink); border: 1px solid var(--line); border-radius: var(--r-sm); padding: 12px 14px; }
.move .out { color: var(--coral); text-decoration: line-through; text-decoration-thickness: 1px; }
.move .in { color: var(--lime); text-align: right; font-weight: 600; }
.move .arrow { color: var(--dim); font-family: var(--mono); }
.pitch { background:
    linear-gradient(90deg, transparent 49.8%, rgba(255,255,255,.06) 50%, transparent 50.2%),
    radial-gradient(circle at 50% 50%, transparent 58px, rgba(255,255,255,.06) 59px, transparent 60px),
    repeating-linear-gradient(0deg, #0f1a12 0 44px, #0d1710 44px 88px);
  border: 1px solid var(--line); border-radius: 18px; padding: 18px 10px; display: grid; gap: 14px; }
.line { display: flex; justify-content: center; gap: 8px; flex-wrap: wrap; }
.chip { background: rgba(11,14,12,.85); border: 1px solid var(--line-2); border-radius: 10px;
  padding: 8px 10px; min-width: 92px; text-align: center; position: relative; }
.chip b { display: block; font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 110px; }
.chip small { color: var(--dim); font: 12px var(--mono); }
.chip .badge { position: absolute; top: -8px; right: -8px; width: 22px; height: 22px; border-radius: 50%;
  background: var(--lime); color: var(--lime-ink); font: 800 12px/22px var(--display); }
.chip .badge.v { background: var(--chalk); }
.bench { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
.bench .chip { opacity: .75; }
.weeks { display: grid; gap: 10px; }
.week { display: grid; grid-template-columns: 64px 1fr auto; gap: 12px; align-items: center;
  border: 1px solid var(--line); border-radius: var(--r-sm); padding: 12px 14px; background: var(--ink); }
.week .gw { font: 700 15px var(--mono); color: var(--lime); }
.week .what { color: var(--mute); font-size: 14px; }
.week .what b { color: var(--chalk); font-weight: 600; }
.notes { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; color: var(--mute); font-size: 14px; }
.notes li { padding-left: 16px; position: relative; }
.notes li::before { content: ""; position: absolute; left: 0; top: 9px; width: 6px; height: 6px; border-radius: 50%; background: var(--line-2); }
.locked { position: relative; overflow: hidden; }
.locked .blur { filter: blur(6px); opacity: .5; pointer-events: none; user-select: none; }
.locked .over { position: absolute; inset: 0; display: grid; place-items: center; text-align: center;
  background: linear-gradient(180deg, rgba(17,22,18,.2), rgba(17,22,18,.95) 55%); padding: 24px; }
.locked .over p { color: var(--mute); max-width: 360px; margin: 8px auto 18px; }
.row-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.empty { text-align: center; padding: 40px 20px; color: var(--mute); }
.prose { max-width: 760px; margin: 48px auto 72px; }
.prose h1 { font-size: 44px; margin-bottom: 18px; }
.prose h2 { font-size: 24px; margin: 32px 0 10px; letter-spacing: -.01em; }
.prose p, .prose li { color: var(--mute); }
`;
