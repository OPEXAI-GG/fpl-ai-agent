"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { Dashboard, SquadPlayer } from "@/lib/api";
import { CHIP_NAMES, int, money, until } from "@/lib/format";
import { useDashboard, useTimezone } from "@/lib/hooks";
import { rememberEntry } from "@/lib/session";
import { Pitch, flagOf, type PitchPlayer } from "@/components/Pitch";
import { RankChart } from "@/components/RankChart";
import { Button, ErrorState, FactLine, Freshness, Loading, Section, StatusChip, When } from "@/components/ui";

const DIFF = ["", "bg-[#1f8a4c] text-white", "bg-[#7cc49a] text-ink", "bg-mist text-ink", "bg-[#f2a08f] text-ink", "bg-kit text-white"];

function toPitch(p: SquadPlayer): PitchPlayer {
  return {
    key: p.fpl_id,
    name: p.name,
    position: p.position,
    team: p.team,
    captain: p.captain,
    vice: p.vice,
    primary: p.next ? p.next.xp.toFixed(1) : money(p.price),
    secondary: p.next ? "xP next GW" : undefined,
    flag: flagOf(p.status, p.chance),
    flagText: p.news,
  };
}

function Fixtures({ d }: { d: Dashboard }) {
  return (
    <div className="overflow-x-auto rounded-[var(--radius-card)] border border-line bg-paper">
      <table className="num w-full min-w-[34rem] text-sm">
        <caption className="sr-only">Your squad&apos;s next fixtures with FPL difficulty</caption>
        <thead>
          <tr className="border-b border-line text-left text-slate">
            <th scope="col" className="px-3 py-2 font-semibold">
              Player
            </th>
            {d.fixture_gws.map((g) => (
              <th key={g} scope="col" className="px-2 py-2 text-center font-semibold">
                GW{g}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {d.squad.map((p) => (
            <tr key={p.fpl_id} className="border-b border-line/60 last:border-0">
              <th scope="row" className="whitespace-nowrap px-3 py-1.5 text-left font-semibold">
                {p.name} <span className="font-normal text-slate">{p.team}</span>
              </th>
              {p.fixtures.map((f) => (
                <td key={f.gw} className="px-1 py-1 text-center">
                  {f.games.length === 0 ? (
                    <span className="block rounded bg-ink/80 px-1 py-1 text-xs font-semibold text-white">Blank</span>
                  ) : (
                    <span className="flex flex-col gap-0.5">
                      {f.games.map((g, i) => (
                        <span
                          key={i}
                          className={`block rounded px-1 py-1 text-xs font-semibold ${DIFF[g.difficulty ?? 3]}`}
                          title={`FPL difficulty ${g.difficulty ?? "unknown"}`}
                        >
                          {g.opp} {g.home ? "(H)" : "(A)"}
                        </span>
                      ))}
                    </span>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function TeamOverview() {
  const { entry } = useParams<{ entry: string }>();
  const [refresh, setRefresh] = useState(0);
  const { data: d, error, isLoading, isValidating } = useDashboard(entry, refresh);
  const tz = useTimezone();

  useEffect(() => {
    if (d && entry !== "sample") rememberEntry(Number(entry), d.manager.team_name);
  }, [d, entry]);

  if (error)
    return (
      <ErrorState
        error={error}
        action={
          <Link href="/" className="font-semibold text-pitch underline">
            Try another team ID
          </Link>
        }
      />
    );
  if (isLoading || !d) return <Loading label="Reading this team's public FPL data…" />;

  const m = d.manager;
  const starters = d.squad.filter((p) => p.starting);
  const bench = d.squad.filter((p) => !p.starting);
  const left = until(d.gameweek.deadline);
  const f = d.facts;
  const last = d.rank_history[d.rank_history.length - 1];
  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="display text-4xl font-bold sm:text-5xl">{m.team_name}</h1>
          <p className="text-slate">
            {m.manager_name ?? "Manager"}
            {m.region ? `, ${m.region}` : ""}
            {entry !== "sample" ? ` · ID ${m.entry_id}` : ""}
          </p>
        </div>
        <div className="text-right">
          <Freshness meta={d.sources.entry} />
          {entry !== "sample" && (
            <div className="mt-1">
              <Button
                variant="quiet"
                onClick={() => setRefresh((n) => n + 1)}
                disabled={isValidating}
                className="min-h-9 px-3 text-sm"
              >
                {isValidating ? "Refreshing…" : "Refresh from FPL"}
              </Button>
              {d.refresh.requested && !d.refresh.applied && (
                <p className="mt-1 text-xs text-slate">Refreshed recently: showing the cached copy (limit one refresh per team every 5 minutes).</p>
              )}
            </div>
          )}
        </div>
      </div>

      <dl className="num mt-6 grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-card)] border border-line bg-line sm:grid-cols-4">
        <div className="bg-paper p-4">
          <dt className="text-sm text-slate">Overall rank</dt>
          <dd className="display text-3xl font-bold">{int(m.overall_rank)}</dd>
          {m.players_in_game && <dd className="text-xs text-slate">of {int(m.players_in_game)}</dd>}
        </div>
        <div className="bg-paper p-4">
          <dt className="text-sm text-slate">Total points</dt>
          <dd className="display text-3xl font-bold">{int(m.overall_points)}</dd>
        </div>
        <div className="bg-paper p-4">
          <dt className="text-sm text-slate">GW{last?.gw ?? m.current_event ?? "—"} points</dt>
          <dd className="display text-3xl font-bold">{int(last?.points ?? m.event_points)}</dd>
          {last?.gw_rank != null && <dd className="text-xs text-slate">gameweek rank {int(last.gw_rank)}</dd>}
        </div>
        <div className="bg-flood/25 p-4">
          <dt className="text-sm text-ink">GW{d.gameweek.next} deadline</dt>
          <dd className="display text-2xl font-bold">
            <When iso={d.gameweek.deadline} tz={tz} />
          </dd>
          {left && <dd className="text-xs">{left === "passed" ? "Deadline passed" : left} · {tz}</dd>}
        </div>
      </dl>

      <div className="mt-6 flex flex-wrap items-center gap-3 rounded-[var(--radius-card)] border-2 border-pitch bg-info-soft p-4">
        <p className="flex-1">
          <strong>GW{d.gameweek.next} recommendation:</strong> roll or transfer, lineup, captain and chips, compared
          across every free-transfer count FPL could be hiding.
        </p>
        <Link
          href={`/team/${entry}/plan`}
          className="inline-flex min-h-11 items-center rounded-lg bg-pitch px-5 font-semibold text-white hover:bg-pitch-deep"
        >
          See this week&apos;s plan
        </Link>
      </div>

      <div className="mt-8 grid gap-8 lg:grid-cols-[1.25fr_1fr]">
        <section aria-labelledby="squad-h">
          <div className="mb-3 flex flex-wrap items-end justify-between gap-2 border-b border-line pb-2">
            <h2 id="squad-h" className="display text-2xl font-bold sm:text-3xl">
              Squad
            </h2>
            <StatusChip status={f.squad.status} title={f.squad.note} />
          </div>
          <p className="mb-3 text-sm text-slate">
            {d.squad_label}
            {d.forecast?.matches_next ? `. Numbers are expected points for GW${d.gameweek.next}.` : ". Prices shown: no forecast for the next gameweek yet."}
          </p>
          <Pitch
            xi={starters.map(toPitch)}
            bench={bench.map(toPitch)}
            label={`GW${d.gameweek.squad_gameweek} deadline lineup`}
            caption="This is the lineup you set at the last deadline. Transfers made since are not public until the next deadline."
          />
        </section>

        <div>
          <section aria-labelledby="facts-h">
            <div className="mb-1 border-b border-line pb-2">
              <h2 id="facts-h" className="display text-2xl font-bold sm:text-3xl">
                Team state
              </h2>
            </div>
            <dl className="divide-y divide-line">
              <FactLine label="Bank" fact={f.bank} render={(v) => money(v as number)} />
              <FactLine label="Free transfers" fact={f.free_transfers} render={(v) => String(v)} />
              <FactLine
                label={`Transfers made for GW${f.gameweek}`}
                fact={f.transfers_made}
                render={(v) => String(v)}
              />
            </dl>
            <h3 className="mt-4 font-semibold">Chips</h3>
            <ul className="mt-1 divide-y divide-line">
              {Object.entries(f.chips).map(([chip, fact]) => (
                <li key={chip} className="flex flex-wrap items-center justify-between gap-2 py-1.5">
                  <span>{CHIP_NAMES[chip] ?? chip}</span>
                  <span className="flex items-center gap-2 text-sm">
                    <span className="font-semibold">{String(fact.value ?? "unknown")}</span>
                    <StatusChip status={fact.status} title={fact.source} />
                  </span>
                </li>
              ))}
            </ul>
            <details className="mt-4 rounded-lg border border-line bg-paper p-3 text-sm">
              <summary className="cursor-pointer font-semibold">What FPL doesn&apos;t make public</summary>
              <ul className="mt-2 list-disc pl-5 text-slate">
                {f.not_public.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
              <p className="mt-2 text-slate">Selling prices are {f.selling_prices}.</p>
            </details>
          </section>

          <Section title="Rank history" id="rank-h">
            <RankChart rows={d.rank_history} />
          </Section>
        </div>
      </div>

      <Section title="Upcoming fixtures" id="fx-h" aside={<span className="text-xs text-slate">FPL difficulty: green easier, red harder</span>}>
        <Fixtures d={d} />
      </Section>

      <p className="mt-6 text-xs text-slate">
        Sources:{" "}
        {Object.entries(d.sources)
          .map(([k, v]) => `${k} ${v.stale ? "(stale)" : ""} retrieved ${new Date(v.fetched_at * 1000).toLocaleString("en-GB", { timeZone: tz })}`)
          .join("; ")}
        .
      </p>
    </div>
  );
}
