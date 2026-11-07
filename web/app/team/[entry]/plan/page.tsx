"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { api, ApiError, fetcher, type PlanPlayer, type PlanResult, type PlanStatus, type Recommendation } from "@/lib/api";
import { CHIP_NAMES, actionText, agoFrom, money, pct, pts, signed } from "@/lib/format";
import { useTimezone } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { Pitch, flagOf, type PitchPlayer } from "@/components/Pitch";
import { Button, ErrorState, Loading, Notice, Section, StatusChip, When } from "@/components/ui";

function toPitch(p: PlanPlayer, captain?: number, vice?: number, highlight?: "in" | null): PitchPlayer {
  return {
    key: p.id,
    name: p.name,
    position: p.position ?? "MID",
    team: p.team,
    captain: p.id === captain,
    vice: p.id === vice,
    primary: pts(p.xp),
    secondary: p.p_start != null ? `start ${pct(p.p_start)}` : undefined,
    flag: flagOf(p.status, p.chance),
    flagText: p.news,
    highlight: highlight ?? null,
  };
}

function StageBadge({ r }: { r: PlanResult }) {
  if (r.final) return <span className="rounded-full bg-pitch px-3 py-1 text-sm font-semibold text-white">Final</span>;
  if (r.timed_out)
    return <span className="rounded-full bg-kit px-3 py-1 text-sm font-semibold text-white">Stopped: time limit</span>;
  return <span className="rounded-full border border-amber bg-amber-soft px-3 py-1 text-sm font-semibold text-amber">Preliminary</span>;
}

function Progress({ st }: { st: PlanStatus }) {
  const j = st.job;
  const waiting = j?.stage?.includes("forecast") || st.forecast_job;
  return (
    <div role="status" aria-live="polite" className="rounded-[var(--radius-card)] border border-line bg-paper p-5">
      <p className="display text-2xl font-bold">
        {j?.status === "queued" ? "Queued" : "Planning"}
        {waiting ? ": preparing this week's shared forecast" : ""}
      </p>
      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-mist" aria-hidden>
        <div className="h-full rounded-full bg-pitch transition-[width]" style={{ width: `${Math.max(4, (j?.progress ?? 0) * 100)}%` }} />
      </div>
      <p className="mt-2 text-sm text-slate">
        {j?.stage || "Waiting for a worker"}. The first result usually appears within a minute or two; the
        chip search adds several minutes. You can leave this page and come back.
      </p>
    </div>
  );
}

function Moves({ r }: { r: Recommendation }) {
  if (!r.moves.length) return null;
  return (
    <ul className="mt-3 space-y-2">
      {r.moves.map((m) => (
        <li key={`${m.out.id}-${m.in.id}`} className="flex flex-wrap items-center gap-2 text-lg">
          <span className="rounded-md bg-kit/10 px-2 py-1 font-semibold text-kit line-through decoration-2">{m.out.name}</span>
          <span aria-hidden>→</span>
          <span className="rounded-md bg-pitch/10 px-2 py-1 font-semibold text-pitch-deep">{m.in.name}</span>
          <span className="sr-only">replaces</span>
          <span className="num text-sm text-slate">
            {money(m.in.price)} · {pts(m.in.xp)} xP
          </span>
        </li>
      ))}
    </ul>
  );
}

function Headline({ res }: { res: PlanResult }) {
  const r = res.recommendation;
  if (res.conditional) {
    return (
      <div>
        <p className="display text-4xl font-bold sm:text-5xl">It depends on your free transfers</p>
        <p className="mt-2 max-w-prose text-slate">
          FPL doesn&apos;t publish the count, and the best move differs between the possibilities. Check the
          number on FPL&apos;s Transfers page and follow the matching row.
        </p>
      </div>
    );
  }
  return (
    <div>
      <p className="display text-4xl font-bold sm:text-5xl">{actionText(r.action, r.chip)}</p>
      <Moves r={r} />
      {res.same_across_scenarios && res.scenarios.length > 1 && (
        <p className="mt-2 text-sm text-slate">
          Holds whether you have {res.scenarios.map((s) => s.free_transfers).join(", ")} free transfers.
        </p>
      )}
    </div>
  );
}

function Scenarios({ res }: { res: PlanResult }) {
  return (
    <div className="overflow-x-auto">
      <table className="num w-full min-w-[30rem] text-sm">
        <caption className="sr-only">Advice for each possible free-transfer count</caption>
        <thead>
          <tr className="border-b border-line text-left text-slate">
            <th scope="col" className="py-2 pr-3 font-semibold">If you have</th>
            <th scope="col" className="py-2 pr-3 font-semibold">Do this</th>
            <th scope="col" className="py-2 pr-3 font-semibold">Captain</th>
            <th scope="col" className="py-2 text-right font-semibold">xP after hits</th>
          </tr>
        </thead>
        <tbody>
          {res.scenarios.map((s) => (
            <tr key={s.free_transfers} className={`border-b border-line/60 ${s.free_transfers === res.primary_ft ? "bg-info-soft" : ""}`}>
              <th scope="row" className="py-2 pr-3 text-left font-semibold">
                {s.free_transfers} free transfer{s.free_transfers === 1 ? "" : "s"}
                {s.free_transfers === res.primary_ft && <span className="ml-1 text-xs font-normal text-slate">(estimate)</span>}
              </th>
              <td className="py-2 pr-3">
                {s.status === "pending"
                  ? "Planning…"
                  : s.moves && s.moves.length
                    ? s.moves.map((m) => `${m.out} → ${m.in}`).join(", ") + (s.hits ? ` (−${4 * s.hits} hit)` : "")
                    : actionText(s.action ?? "", null)}
              </td>
              <td className="py-2 pr-3">{s.captain ?? "—"}</td>
              <td className="py-2 text-right">{pts(s.xp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Numbers({ res }: { res: PlanResult }) {
  const r = res.recommendation;
  const row = r.comparison.find((c) => c.transfers === r.moves.length);
  const vsRoll = r.moves.length && row ? row.gain : null;
  return (
    <dl className="num mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-4">
      <div className="bg-paper p-3">
        <dt className="text-xs text-slate">Expected points, GW{r.gw}</dt>
        <dd className="display text-3xl font-bold">{pts(r.xp_after_hits)}</dd>
        <dd className="text-xs text-slate">XI with captain{r.hits ? `, after −${r.hit_cost} hit` : ""}</dd>
      </div>
      <div className="bg-paper p-3">
        <dt className="text-xs text-slate">Same squad, no moves</dt>
        <dd className="display text-3xl font-bold">{pts(r.xp_no_transfers)}</dd>
        <dd className="text-xs text-slate">best XI this week</dd>
      </div>
      <div className="bg-paper p-3">
        <dt className="text-xs text-slate">Gain over rolling, whole plan</dt>
        <dd className="display text-3xl font-bold">{vsRoll == null ? "—" : signed(vsRoll)}</dd>
        <dd className="text-xs text-slate">
          {row && row.gain_q10 != null && r.moves.length
            ? `10–90% range ${signed(row.gain_q10)} to ${signed(row.gain_q90)}`
            : "rolling is the baseline"}
        </dd>
      </div>
      <div className="bg-paper p-3">
        <dt className="text-xs text-slate">Hit cost</dt>
        <dd className="display text-3xl font-bold">{r.hit_cost ? `−${r.hit_cost}` : "0"}</dd>
        <dd className="text-xs text-slate">points this week</dd>
      </div>
    </dl>
  );
}

function Comparison({ r }: { r: Recommendation }) {
  if (!r.comparison.length) return <p className="text-slate">{r.comparison_note || "No comparison was needed."}</p>;
  return (
    <div className="overflow-x-auto rounded-[var(--radius-card)] border border-line bg-paper">
      <table className="num w-full min-w-[46rem] text-sm">
        <caption className="sr-only">Rolling compared with each feasible number of transfers, in the same simulated outcomes</caption>
        <thead>
          <tr className="border-b border-line text-left text-slate">
            <th scope="col" className="px-3 py-2 font-semibold">Option</th>
            <th scope="col" className="px-3 py-2 font-semibold">Moves</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">GW xP</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Plan xP</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Gain vs roll</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Beats roll in</th>
            <th scope="col" className="px-3 py-2 font-semibold">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {r.comparison.map((c) => {
            const chosen = c.transfers === r.moves.length && !r.chip;
            return (
              <tr key={c.transfers} className={`border-b border-line/60 align-top last:border-0 ${chosen ? "bg-flood/20" : ""}`}>
                <th scope="row" className="whitespace-nowrap px-3 py-2 text-left font-semibold">
                  {c.transfers === 0 ? "Roll" : `${c.transfers} transfer${c.transfers > 1 ? "s" : ""}`}
                  {c.hits ? <span className="block text-xs font-normal text-kit">−{4 * c.hits} hit</span> : null}
                  {chosen && <span className="block text-xs font-normal">recommended</span>}
                </th>
                <td className="px-3 py-2">{c.feasible ? (c.moves.length ? c.moves.map((m) => `${m.out} → ${m.in}`).join(", ") : "—") : "Not feasible"}</td>
                <td className="px-3 py-2 text-right">{c.feasible ? pts(c.week_xp) : "—"}</td>
                <td className="px-3 py-2 text-right">{c.feasible ? pts(c.horizon_xp) : "—"}</td>
                <td className="px-3 py-2 text-right">
                  {c.transfers === 0 ? "baseline" : c.gain == null ? "—" : signed(c.gain)}
                  {c.gain_q10 != null && c.transfers > 0 && (
                    <span className="block text-xs text-slate">
                      {signed(c.gain_q10)} to {signed(c.gain_q90)}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right">{c.win_share == null ? "—" : pct(c.win_share)}</td>
                <td className="px-3 py-2 text-xs text-slate">
                  {c.transfers === 0 ? "the comparison point" : c.reason}
                  {c.solve !== "optimal" && <span className="block text-kit">{c.solve}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="border-t border-line px-3 py-2 text-xs text-slate">
        GW xP is this gameweek after hits; plan xP sums the {r.horizon.length} planned gameweeks after hits (undiscounted).
        Gain and win share come from {r.comparison.find((c) => c.worlds)?.worlds ?? "the"} simulated outcomes shared by every
        option. A move must beat rolling in 55% of outcomes or gain 1.0 point to be recommended. The range is the 10th to
        90th percentile of the gain: a guide to uncertainty, not a guarantee.
      </p>
    </div>
  );
}

function Lineups({ res }: { res: PlanResult }) {
  const r = res.recommendation;
  if (!res.lineup_shown) {
    return (
      <Notice tone="warn">
        <strong>No lineup shown.</strong> The safety checks would reject this week&apos;s lineup:{" "}
        {r.rails.violations.join("; ")}. Pick your XI on FPL from available players.
      </Notice>
    );
  }
  const ins = new Set(r.moves.map((m) => m.in.id));
  const proposed = (
    <Pitch
      xi={r.xi.map((p) => toPitch(p, r.captain.id, r.vice.id, ins.has(p.id) ? "in" : null))}
      bench={r.bench.map((p) => toPitch(p, r.captain.id, r.vice.id, ins.has(p.id) ? "in" : null))}
      label="Recommended lineup"
      caption="Numbers are expected points; yellow outline marks a new signing. Bench order is the automatic-substitution order."
    />
  );
  if (!r.moves.length || !r.baseline) return proposed;
  const b = r.baseline;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div>
        <h3 className="mb-2 font-semibold">
          If you roll <span className="num font-normal text-slate">({pts(b.week_xp)} xP)</span>
        </h3>
        <Pitch xi={b.xi.map((p) => toPitch(p, b.captain?.id))} bench={b.bench.map((p) => toPitch(p))} label="Lineup if you roll" />
      </div>
      <div>
        <h3 className="mb-2 font-semibold">
          With the recommended moves <span className="num font-normal text-slate">({pts(r.xp_after_hits)} xP)</span>
        </h3>
        {proposed}
      </div>
    </div>
  );
}

function Result({ res, tz }: { res: PlanResult; tz: string }) {
  const r = res.recommendation;
  return (
    <div>
      <div className="rounded-[var(--radius-card)] border-2 border-ink bg-paper p-5 sm:p-6">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <StageBadge r={res} />
          <span className="text-sm text-slate">
            {res.strategy.label} strategy · computed {agoFrom(res.created_at)} · forecast from data up to{" "}
            <When iso={res.forecast.cutoff + "Z"} tz={tz} />
          </span>
        </div>
        {!res.final && (
          <p className="mb-4 text-sm">
            {res.timed_out
              ? "The planner hit its time limit before finishing. What is shown was planned without the full chip search and may change."
              : "Planned without the chip search so far; the final version replaces this automatically."}
          </p>
        )}
        <Headline res={res} />
        {(res.conditional || res.scenarios.length > 1) && (
          <div className="mt-5">
            <h2 className="mb-1 font-semibold">By free-transfer count</h2>
            <Scenarios res={res} />
          </div>
        )}
        <Numbers res={res} />

        <div className="mt-6 grid gap-6 md:grid-cols-3">
          <div>
            <h2 className="font-semibold">Captain</h2>
            <p className="display text-3xl font-bold">
              <span className="mr-2 inline-grid h-7 w-7 place-items-center rounded-full bg-flood align-middle text-base text-ink">C</span>
              {r.captain.name}
            </p>
            <p className="text-sm text-slate">Vice: {r.vice.name}</p>
            <ul className="num mt-2 text-sm">
              {r.captain_options.map((o) => (
                <li key={o.player.id} className="flex justify-between gap-2 border-b border-line/60 py-1">
                  <span>{o.player.name}</span>
                  <span className="text-slate">
                    {pts(o.xp)} xP, {pct(o.p_haul)} chance of 10+
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h2 className="font-semibold">Chip</h2>
            <p className="display text-3xl font-bold">
              {r.chip ? `Play ${CHIP_NAMES[r.chip]}` : r.chips.searched ? "Hold your chips" : res.chips_possible ? "Not searched yet" : "None available"}
            </p>
            <ul className="mt-2 space-y-1 text-sm text-slate">
              {Object.entries(r.chips.held).map(([c, why]) => (
                <li key={c}>
                  <strong className="text-ink">{CHIP_NAMES[c] ?? c}:</strong> {why}
                </li>
              ))}
              {!r.chips.searched && res.chips_possible && <li>The chip search runs in the final stage.</li>}
            </ul>
          </div>
          <div>
            <h2 className="font-semibold">Why</h2>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
              {[...r.checks, ...r.warnings].slice(0, 6).map((w) => (
                <li key={w}>{w}</li>
              ))}
              {!r.checks.length && !r.warnings.length && <li>No move beat rolling by enough to recommend.</li>}
            </ul>
          </div>
        </div>

        {res.conditions.length > 0 && (
          <div className="mt-6 border-t border-line pt-4">
            <h2 className="font-semibold">What would change this</h2>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
              {res.conditions.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-6 border-t border-line pt-4">
          <h2 className="font-semibold">Built on these assumptions</h2>
          <ul className="mt-1 space-y-1 text-sm">
            <li className="flex flex-wrap items-center gap-2">
              <StatusChip status={res.team.squad.status} /> {res.assumptions[0]}
            </li>
            <li className="flex flex-wrap items-center gap-2">
              <StatusChip status={res.team.free_transfers.status} /> {res.assumptions[1]}
            </li>
            <li className="flex flex-wrap items-center gap-2">
              <StatusChip status={res.team.bank.status} /> Bank {money(res.team.bank.value)}. {res.assumptions[2]}
            </li>
          </ul>
        </div>
      </div>

      <Section title="Lineup" id="lineup-h">
        <Lineups res={res} />
      </Section>

      <Section title="Rolling against each transfer count" id="cmp-h">
        <Comparison r={r} />
      </Section>

      <Section title="The plan ahead" id="hz-h" aside={<span className="text-xs text-slate">re-planned every week; only GW{r.gw} is advice</span>}>
        <ol className="num divide-y divide-line rounded-[var(--radius-card)] border border-line bg-paper">
          {r.horizon.map((w) => (
            <li key={w.gw} className="flex flex-wrap items-baseline justify-between gap-2 px-4 py-2">
              <span className="font-semibold">GW{w.gw}</span>
              <span className="flex-1 text-sm">
                {w.chip ? `${CHIP_NAMES[w.chip]}; ` : ""}
                {w.moves.length ? w.moves.map((m) => `${m.out} → ${m.in}`).join(", ") : "roll"}
                {w.hits ? ` (−${4 * w.hits})` : ""}; captain {w.captain}
              </span>
              <span className="text-sm text-slate">
                {pts(w.xp)} xP, {w.ft_before} free
              </span>
            </li>
          ))}
        </ol>
      </Section>

      <Section title="Points, strategy and the planner's score" id="obj-h">
        <div className="grid gap-4 text-sm md:grid-cols-3">
          <div>
            <h3 className="font-semibold">Expected points</h3>
            <p className="text-slate">
              The forecast: {pts(r.xp)} for GW{r.gw} from the XI and captain, the same for every strategy. Hits are
              subtracted where shown.
            </p>
          </div>
          <div>
            <h3 className="font-semibold">Strategy adjustments</h3>
            <p className="text-slate">
              {res.strategy.summary} {res.strategy.effect}
            </p>
            {res.strategy.notes.map((n) => (
              <p key={n} className="mt-1 text-slate">
                {n}
              </p>
            ))}
          </div>
          <div>
            <h3 className="font-semibold">Planner score</h3>
            <p className="text-slate">
              {r.objective == null ? "—" : pts(r.objective)}: not points. It discounts later weeks, weighs the bench,
              values banked transfers and money in the bank and, outside points-focused mode, ownership risk. It is
              used to choose between plans, not as a prediction.
            </p>
          </div>
        </div>
      </Section>

      <details className="mt-8 rounded-lg border border-line bg-paper p-4 text-sm">
        <summary className="cursor-pointer font-semibold">Technical details</summary>
        <dl className="num mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2">
          <dt className="text-slate">Forecast</dt>
          <dd>
            {res.forecast.key} ({res.forecast.model}, {res.forecast.samples} simulations per fixture)
          </dd>
          <dt className="text-slate">Data revision</dt>
          <dd>{res.forecast.revision}</dd>
          <dt className="text-slate">Solver</dt>
          <dd>{r.solve}</dd>
          <dt className="text-slate">Safety checks</dt>
          <dd>{r.rails.ok ? "passed" : r.rails.violations.join("; ")}</dd>
          <dt className="text-slate">Timings</dt>
          <dd>
            {Object.entries(res.timings)
              .map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`)
              .join(", ")}
          </dd>
        </dl>
      </details>
    </div>
  );
}

export default function PlanPage() {
  const { entry } = useParams<{ entry: string }>();
  const tz = useTimezone();
  const { data: session } = useSession();
  const key = `/api/entries/${encodeURIComponent(entry)}/plan?s=${session?.prefs.strategy ?? ""}-${session?.prefs.target_rank ?? ""}`;
  const [startError, setStartError] = useState<ApiError | null>(null);
  const started = useRef<string | null>(null);
  const { data: st, error, mutate } = useSWR<PlanStatus>(session ? key : null, fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
    refreshInterval: (d) => (d && (d.status === "queued" || d.status === "running") ? 2500 : 0),
  });

  async function start() {
    setStartError(null);
    try {
      const r = await api<PlanStatus>(`/api/entries/${encodeURIComponent(entry)}/plan`, { method: "POST" });
      await mutate(r.result ? { ...r } : r, { revalidate: true });
    } catch (e) {
      setStartError(e instanceof ApiError ? e : new ApiError(0, "unknown", String(e)));
    }
  }

  useEffect(() => {
    if (st && st.status === "none" && started.current !== key) {
      started.current = key;
      void start();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st, key]);

  if (error) return <ErrorState error={error} action={<Link href={`/team/${entry}`} className="font-semibold text-pitch underline">Back to the overview</Link>} />;
  if (!st) return <Loading label="Checking for this week's plan…" />;
  const res = st.result;
  const running = st.status === "queued" || st.status === "running";
  const failed = st.job?.status === "failed";
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <h1 className="display text-4xl font-bold sm:text-5xl">This week</h1>
        <Link href="/settings" className="text-sm font-semibold text-pitch underline underline-offset-4">
          Strategy: {session?.prefs.strategy ?? "balanced"}
        </Link>
      </div>
      {startError && (
        <div className="mb-4">
          <ErrorState error={startError} />
        </div>
      )}
      {running && <div className="mb-4"><Progress st={st} /></div>}
      {failed && !running && (
        <div className="mb-4">
          <ErrorState
            title="The plan could not be finished"
            error={new Error(st.job?.error ?? "Unknown error")}
            action={<Button onClick={start}>Try again</Button>}
          />
        </div>
      )}
      {res ? <Result res={res} tz={tz} /> : !running && !failed && <Loading label="Starting the planner…" />}
    </div>
  );
}
