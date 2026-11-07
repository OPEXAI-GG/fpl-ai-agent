"use client";

import type { RankRow } from "@/lib/api";
import { int } from "@/lib/format";

/** Overall rank by gameweek (lower is better, so the axis is inverted, on a log scale). */
export function RankChart({ rows }: { rows: RankRow[] }) {
  const pts = rows.filter((r) => r.overall_rank != null) as (RankRow & { overall_rank: number })[];
  if (pts.length < 2) {
    return <p className="text-slate">Rank history appears after two gameweeks.</p>;
  }
  const W = 640,
    H = 220,
    L = 64,
    R = 16,
    T = 14,
    B = 30;
  const logs = pts.map((p) => Math.log10(p.overall_rank));
  const lo = Math.floor(Math.min(...logs) * 4) / 4;
  const hi = Math.ceil(Math.max(...logs) * 4) / 4 || lo + 0.25;
  const x = (i: number) => L + (i * (W - L - R)) / (pts.length - 1);
  const y = (v: number) => T + ((v - lo) / (hi - lo || 1)) * (H - T - B);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(Math.log10(p.overall_rank)).toFixed(1)}`).join("");
  const ticks: number[] = [];
  for (let v = Math.ceil(lo * 2) / 2; v <= hi + 1e-9; v += 0.5) ticks.push(v);
  const best = pts.reduce((a, b) => (b.overall_rank < a.overall_rank ? b : a));
  const last = pts[pts.length - 1];
  return (
    <figure>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="img"
        aria-label={`Overall rank from GW${pts[0].gw} to GW${last.gw}: now ${int(last.overall_rank)}, best ${int(best.overall_rank)} in GW${best.gw}`}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--color-line)" />
            <text x={L - 8} y={y(t) + 4} textAnchor="end" fontSize="12" fill="var(--color-slate)">
              {int(Math.round(10 ** t))}
            </text>
          </g>
        ))}
        {pts.map((p, i) => (
          <text key={p.gw} x={x(i)} y={H - 10} textAnchor="middle" fontSize="12" fill="var(--color-slate)">
            {p.gw}
          </text>
        ))}
        <path d={path} fill="none" stroke="var(--color-pitch)" strokeWidth="3" strokeLinejoin="round" />
        {pts.map((p, i) => (
          <circle key={p.gw} cx={x(i)} cy={y(Math.log10(p.overall_rank))} r={p === last ? 5 : 3} fill={p === last ? "var(--color-flood)" : "var(--color-pitch)"} stroke="var(--color-ink)" strokeWidth={p === last ? 1.5 : 0}>
            <title>{`GW${p.gw}: rank ${int(p.overall_rank)}, ${p.points} pts`}</title>
          </circle>
        ))}
      </svg>
      <figcaption className="text-xs text-slate">Gameweek along the bottom; higher on the chart is a better rank.</figcaption>
    </figure>
  );
}
