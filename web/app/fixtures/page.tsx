"use client";

import useSWR from "swr";
import { useState } from "react";
import { fetcher } from "@/lib/api";
import { pct, pts } from "@/lib/format";
import { ErrorState, Loading } from "@/components/ui";

interface Game {
  opp: string | null;
  home: boolean;
  difficulty: number | null;
  kickoff: string | null;
  xg_for: number | null;
  xg_against: number | null;
  cs_chance: number | null;
}
interface FixturesView {
  gws: number[];
  forecast_gws: number[];
  teams: { id: number; name: string; short: string; gws: { gw: number; games: Game[] }[] }[];
  note: string;
}

const DIFF = ["", "bg-[#1f8a4c] text-white", "bg-[#7cc49a] text-ink", "bg-mist text-ink", "bg-[#f2a08f] text-ink", "bg-kit text-white"];

function score(t: FixturesView["teams"][number], mode: "attack" | "defence" | "fpl") {
  let s = 0;
  let n = 0;
  for (const g of t.gws)
    for (const x of g.games) {
      const v = mode === "attack" ? x.xg_for : mode === "defence" ? x.xg_against : x.difficulty;
      if (v != null) {
        s += v;
        n += 1;
      }
    }
  return n ? s / n : null;
}

export default function FixturesPage() {
  const { data, error, isLoading } = useSWR<FixturesView>("/api/fixtures", fetcher, { revalidateOnFocus: false });
  const [mode, setMode] = useState<"attack" | "defence" | "fpl">("attack");
  if (error) return <ErrorState error={error} />;
  if (isLoading || !data) return <Loading label="Loading fixtures…" />;
  const sorted = [...data.teams].sort((a, b) => {
    const sa = score(a, mode) ?? 99,
      sb = score(b, mode) ?? 99;
    return mode === "attack" ? (sb === 99 ? -1 : sb) - (sa === 99 ? -1 : sa) : sa - sb;
  });
  return (
    <div>
      <h1 className="display text-4xl font-bold sm:text-5xl">Fixtures</h1>
      <p className="mt-2 max-w-prose text-slate">
        The next {data.gws.length} gameweeks for every club. Inside the forecast window (GW
        {data.forecast_gws[0]}–{data.forecast_gws[data.forecast_gws.length - 1]}) each match shows the model&apos;s
        expected goals for the club and its clean-sheet chance.
      </p>
      <fieldset className="mt-4 flex flex-wrap items-center gap-2">
        <legend className="sr-only">Sort clubs by</legend>
        {(
          [
            ["attack", "Best attacking outlook"],
            ["defence", "Best clean-sheet outlook"],
            ["fpl", "FPL difficulty"],
          ] as const
        ).map(([k, label]) => (
          <label key={k} className={`cursor-pointer rounded-full border px-3 py-1.5 text-sm font-semibold ${mode === k ? "border-pitch bg-pitch text-white" : "border-line bg-paper"}`}>
            <input type="radio" name="sort" className="sr-only" checked={mode === k} onChange={() => setMode(k)} />
            {label}
          </label>
        ))}
      </fieldset>
      <div className="mt-4 overflow-x-auto rounded-[var(--radius-card)] border border-line bg-paper">
        <table className="num w-full min-w-[44rem] text-sm">
          <caption className="sr-only">Upcoming fixtures by club</caption>
          <thead>
            <tr className="border-b border-line text-left text-slate">
              <th scope="col" className="sticky left-0 bg-paper px-3 py-2 font-semibold">Club</th>
              {data.gws.map((g) => (
                <th key={g} scope="col" className="px-2 py-2 text-center font-semibold">GW{g}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((t) => (
              <tr key={t.id} className="border-b border-line/60 last:border-0">
                <th scope="row" className="sticky left-0 whitespace-nowrap bg-paper px-3 py-1.5 text-left font-semibold">
                  {t.name}
                </th>
                {t.gws.map((g) => (
                  <td key={g.gw} className="px-1 py-1 text-center align-top">
                    {g.games.length === 0 ? (
                      <span className="block rounded bg-ink/80 px-1 py-1 text-xs font-semibold text-white">Blank</span>
                    ) : (
                      g.games.map((x, i) => (
                        <span key={i} className={`mb-0.5 block rounded px-1 py-1 text-xs ${DIFF[x.difficulty ?? 3]}`}>
                          <span className="font-semibold">
                            {x.opp} ({x.home ? "H" : "A"})
                          </span>
                          {x.xg_for != null ? (
                            <span className="block">
                              {pts(x.xg_for, 2)} xG, CS {pct(x.cs_chance)}
                            </span>
                          ) : (
                            <span className="block opacity-80">no forecast yet</span>
                          )}
                        </span>
                      ))
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-slate">{data.note}</p>
    </div>
  );
}
