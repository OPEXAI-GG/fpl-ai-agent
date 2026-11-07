"use client";

import { money } from "@/lib/format";

export interface PitchPlayer {
  key: string | number;
  name: string;
  position: string;
  team?: string | null;
  captain?: boolean;
  vice?: boolean;
  primary: string; // the main number under the name (expected points, price…)
  secondary?: string;
  flag?: "doubt" | "out" | null;
  flagText?: string;
  highlight?: "in" | "out" | null;
}

const ROWS = ["GKP", "DEF", "MID", "FWD"] as const;

function Tag({ p, onPitch }: { p: PitchPlayer; onPitch: boolean }) {
  const ring =
    p.highlight === "in"
      ? "ring-3 ring-flood"
      : p.flag === "out"
        ? "ring-2 ring-kit"
        : p.flag === "doubt"
          ? "ring-2 ring-amber"
          : "";
  return (
    <li
      className={`relative flex w-[4.9rem] flex-col items-center rounded-md bg-paper px-1 pb-1 pt-1.5 text-center shadow-[0_1px_0_rgb(0_0_0/0.25)] sm:w-24 ${ring} ${
        onPitch ? "" : "border border-line"
      }`}
    >
      {(p.captain || p.vice) && (
        <span
          className={`absolute -right-2 -top-2 grid h-6 w-6 place-items-center rounded-full text-xs font-bold ${
            p.captain ? "bg-flood text-ink" : "border border-ink bg-paper text-ink"
          }`}
          aria-label={p.captain ? "Captain" : "Vice-captain"}
          title={p.captain ? "Captain" : "Vice-captain"}
        >
          {p.captain ? "C" : "V"}
        </span>
      )}
      <span className="w-full truncate text-[0.8rem] font-semibold leading-tight sm:text-sm" title={p.name}>
        {p.name}
      </span>
      <span className="text-[0.7rem] leading-tight text-slate">
        {p.team ?? ""} {p.position}
      </span>
      <span className="num display mt-0.5 text-lg font-bold leading-none text-pitch-deep">{p.primary}</span>
      {p.secondary && <span className="num text-[0.68rem] leading-tight text-slate">{p.secondary}</span>}
      {p.flag && (
        <span
          className={`mt-0.5 w-full truncate rounded-sm px-1 text-[0.65rem] font-semibold ${
            p.flag === "out" ? "bg-kit text-white" : "bg-amber-soft text-amber"
          }`}
          title={p.flagText}
        >
          {p.flag === "out" ? "Out" : "Doubt"}
        </span>
      )}
    </li>
  );
}

/** A starting XI on a pitch with the bench (in order) underneath. */
export function Pitch({
  xi,
  bench,
  label,
  caption,
}: {
  xi: PitchPlayer[];
  bench: PitchPlayer[];
  label: string;
  caption?: string;
}) {
  return (
    <figure aria-label={label} className="w-full">
      <div className="pitch rounded-[var(--radius-card)] px-1 py-5 sm:px-3">
        <div className="relative z-10 flex flex-col gap-4">
          {ROWS.map((pos) => {
            const row = xi.filter((p) => p.position === pos);
            if (!row.length) return null;
            return (
              <ul key={pos} className="flex flex-wrap justify-center gap-1.5 sm:gap-3" aria-label={pos}>
                {row.map((p) => (
                  <Tag key={p.key} p={p} onPitch />
                ))}
              </ul>
            );
          })}
        </div>
      </div>
      <div className="mt-2 rounded-[var(--radius-card)] border border-line bg-mist px-2 py-3">
        <p className="mb-2 px-1 text-xs font-semibold text-slate">Bench, in substitution order</p>
        <ol className="flex flex-wrap justify-center gap-1.5 sm:gap-3">
          {bench.map((p) => (
            <li key={p.key} className="flex flex-col items-center gap-1">
              <span className="num text-[0.7rem] text-slate">
                {p.position === "GKP" ? "GK" : bench.filter((x) => x.position !== "GKP").indexOf(p) + 1}
              </span>
              <Tag p={p} onPitch={false} />
            </li>
          ))}
        </ol>
      </div>
      {caption && <figcaption className="mt-2 text-xs text-slate">{caption}</figcaption>}
    </figure>
  );
}

export function flagOf(status?: string | null, chance?: number | null): "doubt" | "out" | null {
  if (!status) return null;
  if (status === "a" && (chance == null || chance >= 100)) return null;
  if (status === "a" || status === "d") return chance === 0 ? "out" : "doubt";
  return "out"; // injured, suspended, unavailable or left the club
}

export const priceText = money;
