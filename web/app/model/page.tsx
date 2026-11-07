"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { dateTime, int, pts, signed } from "@/lib/format";
import { useTimezone } from "@/lib/hooks";
import { ErrorState, Loading, Notice, Section } from "@/components/ui";

interface Err {
  mae: number;
  rmse: number;
  bias: number;
}
interface Week {
  gw: number;
  status: string;
  reason?: string;
  n?: number;
  issued_at?: string;
  deadline?: string;
  legacy_run?: boolean;
  model?: Err;
  fpl_ep?: Err;
  form5?: Err;
}
interface Versus {
  mean_mae_difference: number;
  se: number | null;
  gameweeks: number;
  per_week: number[];
  verdict: string;
}
interface Backtest {
  name: string;
  kind: "backtest";
  title: string;
  status: string;
  summary: string;
  period?: string;
  metrics?: Record<string, unknown>;
  rows?: { arm: string; [k: string]: unknown }[];
  decision?: string;
}
interface Perf {
  label: string;
  gameweeks_scored: number;
  player_weeks: number;
  weeks: Week[];
  pooled?: { model: Err; fpl_ep: Err; form5: Err };
  calibration?: { bin: string; n: number; pred: number; actual: number }[];
  vs_fpl_ep?: Versus;
  vs_form5?: Versus;
  verdict?: string;
  revision: string;
  backtests: Backtest[];
}

/** Every key used by any row, in first-seen order (rows of one report can differ). */
function columns(rows: { [k: string]: unknown }[]): string[] {
  return Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
}

function cell(v: unknown): string {
  if (typeof v === "number") return Number.isInteger(v) ? int(v) : v.toFixed(4);
  return v == null ? "—" : String(v);
}

function Calibration({ rows }: { rows: NonNullable<Perf["calibration"]> }) {
  const max = Math.max(...rows.map((r) => Math.max(r.pred, r.actual)), 1);
  return (
    <figure>
      <ul className="num space-y-2">
        {rows.map((r) => (
          <li key={r.bin} className="grid grid-cols-[4rem_1fr_6rem] items-center gap-3 text-sm">
            <span className="font-semibold">{r.bin} xP</span>
            <span className="flex flex-col gap-1" aria-hidden>
              <span className="h-2.5 rounded-sm bg-slate/40" style={{ width: `${(r.pred / max) * 100}%` }} />
              <span className="h-2.5 rounded-sm bg-pitch" style={{ width: `${(r.actual / max) * 100}%` }} />
            </span>
            <span className="text-right text-xs">
              {pts(r.pred, 2)} → {pts(r.actual, 2)}
              <span className="block text-slate">n={int(r.n)}</span>
            </span>
          </li>
        ))}
      </ul>
      <figcaption className="mt-2 text-xs text-slate">
        Grey: average forecast in each band. Green: average points actually scored. Close bars mean the forecasts
        were well calibrated in that band.
      </figcaption>
    </figure>
  );
}

function VersusBlock({ name, v }: { name: string; v?: Versus }) {
  if (!v) return null;
  return (
    <div className="rounded-lg border border-line bg-paper p-4">
      <h3 className="font-semibold">Against {name}</h3>
      <p className="num display mt-1 text-3xl font-bold">
        {signed(v.mean_mae_difference, 3)}
        <span className="ml-1 text-base font-normal text-slate">MAE ± {v.se == null ? "n/a" : v.se.toFixed(3)}</span>
      </p>
      <p className="text-sm text-slate">Negative means the model&apos;s error was lower. One value per gameweek ({v.gameweeks}): {v.per_week.map((x) => signed(x, 2)).join(", ")}.</p>
      <p className="mt-2 text-sm font-semibold">{v.verdict}</p>
    </div>
  );
}

export default function ModelPage() {
  const { data, error, isLoading } = useSWR<Perf>("/api/performance", fetcher, { revalidateOnFocus: false });
  const tz = useTimezone();
  if (error) return <ErrorState error={error} />;
  if (isLoading || !data) return <Loading label="Scoring issued forecasts…" />;
  const scored = data.weeks.filter((w) => w.status === "scored");
  return (
    <div>
      <h1 className="display text-4xl font-bold sm:text-5xl">How the forecasts have done</h1>
      <p className="mt-2 max-w-prose text-slate">
        {data.label}. Each gameweek uses the one forecast made before its deadline; nothing is re-forecast after the
        event. Errors are mean absolute error in FPL points per player.
      </p>
      {data.verdict && (
        <div className="mt-4">
          <Notice tone="warn">{data.verdict}</Notice>
        </div>
      )}
      {data.pooled && (
        <Section title="Forward results" id="fwd-h" aside={<span className="text-xs text-slate">{data.gameweeks_scored} gameweeks, {int(data.player_weeks)} player-gameweeks</span>}>
          <div className="overflow-x-auto rounded-[var(--radius-card)] border border-line bg-paper">
            <table className="num w-full min-w-[36rem] text-sm">
              <caption className="sr-only">Forecast error by gameweek</caption>
              <thead>
                <tr className="border-b border-line text-left text-slate">
                  <th scope="col" className="px-3 py-2 font-semibold">Gameweek</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Forecast issued</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Players</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Model MAE</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">FPL xP MAE</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Last-5 MAE</th>
                </tr>
              </thead>
              <tbody>
                {scored.map((w) => (
                  <tr key={w.gw} className="border-b border-line/60 last:border-0">
                    <th scope="row" className="px-3 py-2 text-left font-semibold">GW{w.gw}</th>
                    <td className="px-3 py-2 text-xs">
                      {dateTime(w.issued_at + "Z", tz)}
                      <span className="block text-slate">
                        deadline {dateTime(w.deadline + "Z", tz)}
                        {w.legacy_run ? "; stored before the run registry existed" : ""}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">{int(w.n)}</td>
                    <td className="px-3 py-2 text-right font-semibold">{pts(w.model?.mae, 3)}</td>
                    <td className="px-3 py-2 text-right">{pts(w.fpl_ep?.mae, 3)}</td>
                    <td className="px-3 py-2 text-right">{pts(w.form5?.mae, 3)}</td>
                  </tr>
                ))}
                <tr className="bg-mist font-semibold">
                  <th scope="row" className="px-3 py-2 text-left">All</th>
                  <td />
                  <td className="px-3 py-2 text-right">{int(data.player_weeks)}</td>
                  <td className="px-3 py-2 text-right">{pts(data.pooled.model.mae, 3)}</td>
                  <td className="px-3 py-2 text-right">{pts(data.pooled.fpl_ep.mae, 3)}</td>
                  <td className="px-3 py-2 text-right">{pts(data.pooled.form5.mae, 3)}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-slate">
            Same players in every column: those with the model&apos;s forecast, FPL&apos;s own pre-deadline expected
            points and five gameweeks of history. A forecast issued several days early (for a gameweek two weeks
            ahead) is scored as issued.
          </p>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <VersusBlock name="FPL's own expected points" v={data.vs_fpl_ep} />
            <VersusBlock name="the last-5-gameweek average" v={data.vs_form5} />
          </div>
          <p className="mt-2 text-xs text-slate">
            Uncertainty is measured across gameweeks, not player rows: players in the same gameweek share fixtures and
            are not independent. Four gameweeks is a small sample.
          </p>
        </Section>
      )}

      {data.calibration && (
        <Section title="Calibration" id="cal-h">
          <Calibration rows={data.calibration} />
        </Section>
      )}

      <Section title="Gameweeks not scored" id="ns-h">
        <ul className="text-sm">
          {data.weeks
            .filter((w) => w.status !== "scored")
            .map((w) => (
              <li key={w.gw} className="border-b border-line/60 py-1">
                GW{w.gw}: {w.status}
                {w.reason ? ` (${w.reason})` : ""}
              </li>
            ))}
        </ul>
      </Section>

      <Section title="Backtests" id="bt-h" aside={<span className="text-xs text-slate">historical replays, not live results</span>}>
        {data.backtests.length === 0 ? (
          <p className="text-slate">No backtests have been stored on this server.</p>
        ) : (
          <div className="space-y-4">
            {data.backtests.map((b) => (
              <article key={b.name} className="rounded-lg border border-dashed border-slate/50 bg-paper p-4">
                <p className="text-xs font-semibold text-slate">Backtest{b.period ? `, ${b.period}` : ""}</p>
                <h3 className="text-lg font-semibold">{b.title}</h3>
                <p className="mt-1 text-sm">{b.summary}</p>
                {b.decision && <p className="mt-1 text-sm font-semibold">Decision: {b.decision}</p>}
                {b.rows && b.rows.length > 0 && (
                  <div className="mt-2 overflow-x-auto">
                    <table className="num w-full min-w-[28rem] text-xs">
                      <thead>
                        <tr className="border-b border-line text-left text-slate">
                          {columns(b.rows).map((k) => (
                            <th key={k} scope="col" className="px-2 py-1 font-semibold">
                              {k.replace(/_/g, " ")}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {b.rows.map((r, i) => (
                          <tr key={i} className="border-b border-line/60">
                            {columns(b.rows!).map((k) => (
                              <td key={k} className="px-2 py-1">
                                {cell(r[k])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </Section>
      <p className="mt-6 text-xs text-slate">Data revision {data.revision}. No testimonials or rank claims are made here: past accuracy does not guarantee future points.</p>
    </div>
  );
}
