"use client";

import useSWR from "swr";
import { useDeferredValue, useState } from "react";
import { fetcher } from "@/lib/api";
import { money, pct, pts } from "@/lib/format";
import { flagOf } from "@/components/Pitch";
import { ErrorState, Loading, Notice } from "@/components/ui";

interface Player {
  id: number;
  name: string;
  team: string;
  position: string;
  price: number;
  status: string;
  chance: number | null;
  news: string;
  owned: number | null;
  projected: boolean;
  xp?: number[];
  xp_total?: number;
  exp_minutes?: number;
  p_haul?: number;
  range?: [number, number];
  components?: Record<string, number>;
  minutes_model?: { p_start: number; p_60: number; p_cameo: number; p_none: number; availability: number };
  stats: Record<string, number | null>;
}
interface PlayersView {
  gws: number[];
  players: Player[];
  total: number;
  missing: Record<string, string>;
  forecast: { key: string; cutoff: string };
}

const POS = ["", "GKP", "DEF", "MID", "FWD"];
const COMPONENTS: [string, string][] = [
  ["minutes", "Appearance"],
  ["goals", "Goals"],
  ["assists", "Assists"],
  ["clean_sheets", "Clean sheet"],
  ["saves", "Saves"],
  ["defcon", "Defensive contribution"],
  ["bonus", "Bonus"],
  ["goals_conceded", "Goals conceded"],
  ["cards", "Cards"],
];

function stat(v: number | null | undefined, d = 1) {
  return v == null ? <span className="text-slate">n/a</span> : v.toFixed(d);
}

function Compare({ players, gws, onRemove }: { players: Player[]; gws: number[]; onRemove: (id: number) => void }) {
  const max = Math.max(1, ...players.flatMap((p) => p.xp ?? [0]));
  return (
    <section aria-labelledby="cmp-h" className="mt-6 rounded-[var(--radius-card)] border-2 border-pitch bg-paper p-4">
      <h2 id="cmp-h" className="display text-2xl font-bold">
        Comparing {players.length} player{players.length > 1 ? "s" : ""}
      </h2>
      <div className="mt-3 grid gap-4" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(14rem, 1fr))` }}>
        {players.map((p) => (
          <article key={p.id} className="rounded-lg border border-line p-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h3 className="text-lg font-semibold">{p.name}</h3>
                <p className="text-sm text-slate">
                  {p.team} {p.position}, {money(p.price)}
                </p>
              </div>
              <button type="button" onClick={() => onRemove(p.id)} className="text-sm font-semibold text-slate underline" aria-label={`Remove ${p.name} from comparison`}>
                Remove
              </button>
            </div>
            {!p.projected ? (
              <p className="mt-2 text-sm text-slate">No forecast: no fixture in the window or no usable minutes history.</p>
            ) : (
              <>
                <h4 className="mt-3 text-xs font-semibold text-slate">Expected points by gameweek</h4>
                <ul className="num mt-1 space-y-0.5 text-xs">
                  {gws.map((g, i) => (
                    <li key={g} className="flex items-center gap-2">
                      <span className="w-10 text-slate">GW{g}</span>
                      <span className="h-2.5 rounded-sm bg-pitch" style={{ width: `${((p.xp?.[i] ?? 0) / max) * 60}%` }} aria-hidden />
                      <span>{pts(p.xp?.[i])}</span>
                    </li>
                  ))}
                </ul>
                {p.minutes_model && (
                  <>
                    <h4 className="mt-3 text-xs font-semibold text-slate">Minutes, GW{gws[0]}</h4>
                    <div className="mt-1 flex h-3 w-full overflow-hidden rounded-sm" role="img" aria-label={`Starts ${pct(p.minutes_model.p_start)}, cameo ${pct(p.minutes_model.p_cameo)}, does not play ${pct(p.minutes_model.p_none)}`}>
                      <span className="bg-pitch" style={{ width: pct(p.minutes_model.p_start) }} />
                      <span className="bg-[#7cc49a]" style={{ width: pct(p.minutes_model.p_cameo) }} />
                      <span className="bg-line" style={{ width: pct(p.minutes_model.p_none) }} />
                    </div>
                    <dl className="num mt-1 grid grid-cols-2 text-xs">
                      <dt className="text-slate">Starts</dt>
                      <dd>{pct(p.minutes_model.p_start)}</dd>
                      <dt className="text-slate">Plays 60+</dt>
                      <dd>{pct(p.minutes_model.p_60)}</dd>
                      <dt className="text-slate">Cameo only</dt>
                      <dd>{pct(p.minutes_model.p_cameo)}</dd>
                      <dt className="text-slate">Doesn&apos;t play</dt>
                      <dd>{pct(p.minutes_model.p_none)}</dd>
                    </dl>
                  </>
                )}
                {p.components && (
                  <>
                    <h4 className="mt-3 text-xs font-semibold text-slate">Where GW{gws[0]} points come from</h4>
                    <dl className="num mt-1 grid grid-cols-2 text-xs">
                      {COMPONENTS.filter(([k]) => Math.abs(p.components?.[k] ?? 0) >= 0.005).map(([k, label]) => (
                        <div key={k} className="contents">
                          <dt className="text-slate">{label}</dt>
                          <dd>{pts(p.components?.[k], 2)}</dd>
                        </div>
                      ))}
                    </dl>
                  </>
                )}
              </>
            )}
            <h4 className="mt-3 text-xs font-semibold text-slate">Season so far (FPL)</h4>
            <dl className="num mt-1 grid grid-cols-2 text-xs">
              <dt className="text-slate">Minutes, starts</dt>
              <dd>
                {stat(p.stats.minutes, 0)}, {stat(p.stats.starts, 0)}
              </dd>
              <dt className="text-slate">xG / xA per 90</dt>
              <dd>
                {stat(p.stats.expected_goals_per_90, 2)} / {stat(p.stats.expected_assists_per_90, 2)}
              </dd>
              <dt className="text-slate">Goals, assists</dt>
              <dd>
                {stat(p.stats.goals_scored, 0)}, {stat(p.stats.assists, 0)}
              </dd>
              <dt className="text-slate">Def. contribution / 90</dt>
              <dd>{stat(p.stats.defensive_contribution_per_90, 1)}</dd>
              <dt className="text-slate">Penalty order</dt>
              <dd>{p.stats.penalties_order ?? <span className="text-slate">none</span>}</dd>
            </dl>
            {p.news && <p className="mt-2 rounded bg-amber-soft px-2 py-1 text-xs">{p.news}</p>}
          </article>
        ))}
      </div>
    </section>
  );
}

export default function PlayersPage() {
  const [position, setPosition] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("xp");
  const [maxPrice, setMaxPrice] = useState("");
  const [picked, setPicked] = useState<number[]>([]);
  const dq = useDeferredValue(q);
  const params = new URLSearchParams({ sort, limit: "80" });
  if (position) params.set("position", position);
  if (dq.trim()) params.set("q", dq.trim());
  if (maxPrice) params.set("max_price", String(Math.round(Number(maxPrice) * 10)));
  const { data, error, isLoading } = useSWR<PlayersView>(`/api/players?${params}`, fetcher, { keepPreviousData: true, revalidateOnFocus: false });
  const cmp = useSWR<PlayersView>(picked.length ? `/api/players?ids=${picked.join(",")}&limit=6` : null, fetcher, { keepPreviousData: true });

  if (error) return <ErrorState error={error} />;
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= 4 ? p : [...p, id]));
  return (
    <div>
      <h1 className="display text-4xl font-bold sm:text-5xl">Players</h1>
      <p className="mt-2 max-w-prose text-slate">
        Expected points come from simulating every fixture; minutes chances are the model&apos;s, with FPL&apos;s
        injury flag applied. Choose up to four players to compare.
      </p>
      <form className="mt-4 flex flex-wrap items-end gap-3" onSubmit={(e) => e.preventDefault()} role="search">
        <label className="flex flex-col text-sm font-semibold">
          Search
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name" className="mt-1 min-h-11 w-44 rounded-lg border border-line bg-paper px-3 font-normal" />
        </label>
        <label className="flex flex-col text-sm font-semibold">
          Position
          <select value={position} onChange={(e) => setPosition(e.target.value)} className="mt-1 min-h-11 rounded-lg border border-line bg-paper px-3 font-normal">
            {POS.map((p) => (
              <option key={p} value={p}>
                {p || "All"}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm font-semibold">
          Max price (£m)
          <input value={maxPrice} onChange={(e) => setMaxPrice(e.target.value.replace(/[^0-9.]/g, ""))} inputMode="decimal" placeholder="Any" className="num mt-1 min-h-11 w-24 rounded-lg border border-line bg-paper px-3 font-normal" />
        </label>
        <label className="flex flex-col text-sm font-semibold">
          Sort by
          <select value={sort} onChange={(e) => setSort(e.target.value)} className="mt-1 min-h-11 rounded-lg border border-line bg-paper px-3 font-normal">
            <option value="xp">Next GW xP</option>
            <option value="xp_total">xP over the window</option>
            <option value="value">xP per £m</option>
            <option value="owned">Ownership</option>
            <option value="price">Price</option>
          </select>
        </label>
      </form>

      {picked.length > 0 && cmp.data && (
        <Compare players={picked.map((id) => cmp.data!.players.find((p) => p.id === id)).filter(Boolean) as Player[]} gws={cmp.data.gws} onRemove={toggle} />
      )}

      {isLoading || !data ? (
        <Loading label="Loading the forecast…" />
      ) : (
        <>
          <p className="mt-4 text-sm text-slate" aria-live="polite">
            Showing {data.players.length} of {data.total} players
          </p>
          <div className="mt-2 overflow-x-auto rounded-[var(--radius-card)] border border-line bg-paper">
            <table className="num w-full min-w-[52rem] text-sm">
              <caption className="sr-only">Player forecasts</caption>
              <thead>
                <tr className="border-b border-line text-left text-slate">
                  <th scope="col" className="px-3 py-2 font-semibold">Compare</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Player</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Price</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">GW{data.gws[0]} xP</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">10–90%</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">{data.gws.length}-GW xP</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Starts</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">xG+xA /90</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Owned</th>
                </tr>
              </thead>
              <tbody>
                {data.players.map((p) => {
                  const flag = flagOf(p.status, p.chance);
                  const on = picked.includes(p.id);
                  const gi = (p.stats.expected_goals_per_90 ?? null) != null ? (p.stats.expected_goals_per_90 ?? 0) + (p.stats.expected_assists_per_90 ?? 0) : null;
                  return (
                    <tr key={p.id} className={`border-b border-line/60 last:border-0 ${on ? "bg-flood/20" : ""}`}>
                      <td className="px-3 py-1.5">
                        <input type="checkbox" checked={on} onChange={() => toggle(p.id)} disabled={!on && picked.length >= 4} aria-label={`Compare ${p.name}`} className="h-5 w-5 accent-[var(--color-pitch)]" />
                      </td>
                      <th scope="row" className="px-3 py-1.5 text-left font-semibold">
                        {p.name} <span className="font-normal text-slate">{p.team} {p.position}</span>
                        {flag && (
                          <span className={`ml-2 rounded px-1 text-xs ${flag === "out" ? "bg-kit text-white" : "bg-amber-soft text-amber"}`} title={p.news}>
                            {flag === "out" ? "Out" : `Doubt${p.chance != null ? ` ${p.chance}%` : ""}`}
                          </span>
                        )}
                      </th>
                      <td className="px-3 py-1.5 text-right">{money(p.price)}</td>
                      <td className="px-3 py-1.5 text-right font-semibold">{p.projected ? pts(p.xp?.[0]) : <span className="text-slate">n/a</span>}</td>
                      <td className="px-3 py-1.5 text-right text-slate">{p.range ? `${p.range[0]}–${p.range[1]}` : "n/a"}</td>
                      <td className="px-3 py-1.5 text-right">{p.projected ? pts(p.xp_total) : "n/a"}</td>
                      <td className="px-3 py-1.5 text-right">{p.minutes_model ? pct(p.minutes_model.p_start) : "n/a"}</td>
                      <td className="px-3 py-1.5 text-right">{gi == null ? <span className="text-slate">n/a</span> : gi.toFixed(2)}</td>
                      <td className="px-3 py-1.5 text-right">{p.owned == null ? "n/a" : `${p.owned.toFixed(1)}%`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-4">
            <Notice>
              <ul className="list-disc space-y-1 pl-5">
                {Object.values(data.missing).map((m) => (
                  <li key={m}>{m}</li>
                ))}
                <li>&quot;n/a&quot; marks an input that is missing for that player, never a zero.</li>
              </ul>
            </Notice>
          </div>
        </>
      )}
    </div>
  );
}
