"use client";

import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";
import { api, ApiError, type Manager } from "@/lib/api";
import { int } from "@/lib/format";
import { rememberEntry } from "@/lib/session";
import { Button } from "./ui";

interface LookupResult {
  entry_id: number;
  source: "id" | "url";
  manager: Manager;
}

export function Lookup({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const id = useId();
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [match, setMatch] = useState<LookupResult | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setMatch(null);
    try {
      const r = await api<LookupResult>("/api/lookup", {
        method: "POST",
        body: JSON.stringify({ query }),
      });
      setMatch(r);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
    } finally {
      setBusy(false);
    }
  }

  function open() {
    if (!match) return;
    rememberEntry(match.entry_id, match.manager.team_name);
    router.push(`/team/${match.entry_id}`);
  }

  const m = match?.manager;
  return (
    <div className={compact ? "" : "max-w-xl"}>
      <form onSubmit={submit} noValidate aria-describedby={`${id}-help`}>
        <label htmlFor={`${id}-q`} className="display block text-xl font-semibold">
          Your FPL team ID or team link
        </label>
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <input
            id={`${id}-q`}
            name="entry"
            inputMode="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="e.g. 1234567 or fantasy.premierleague.com/entry/1234567/event/10"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={`${id}-help ${error ? `${id}-err` : ""}`}
            className="num min-h-12 w-full min-w-0 rounded-lg border-2 border-line bg-paper px-4 text-lg outline-none focus:border-pitch"
          />
          <Button type="submit" disabled={busy || !query.trim()} className="min-h-12 px-6 text-lg">
            {busy ? "Finding…" : "Find my team"}
          </Button>
        </div>
        <p id={`${id}-help`} className="mt-2 text-sm text-slate">
          On the FPL site, open <em>Points</em>: the number after <span className="num">/entry/</span> in
          the address is your ID. No password or sign-in is needed.
        </p>
        {error && (
          <p id={`${id}-err`} role="alert" className="mt-2 font-semibold text-kit">
            {error.message}
          </p>
        )}
      </form>

      {m && (
        <div
          role="status"
          aria-live="polite"
          className="mt-5 rounded-[var(--radius-card)] border-2 border-pitch bg-paper p-4"
        >
          <p className="text-sm text-slate">Found on FPL (public data, ID {match.entry_id})</p>
          <p className="display mt-1 text-3xl font-bold">{m.team_name}</p>
          <p className="text-slate">
            {m.manager_name}
            {m.region ? `, ${m.region}` : ""}
          </p>
          <dl className="num mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-slate">Overall rank</dt>
              <dd className="text-lg font-semibold">{int(m.overall_rank)}</dd>
            </div>
            <div>
              <dt className="text-slate">Total points</dt>
              <dd className="text-lg font-semibold">{int(m.overall_points)}</dd>
            </div>
            <div>
              <dt className="text-slate">Last gameweek</dt>
              <dd className="text-lg font-semibold">
                {m.current_event ? `GW${m.current_event}: ${int(m.event_points)} pts` : "—"}
              </dd>
            </div>
          </dl>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button onClick={open}>Open the dashboard</Button>
            <Button variant="quiet" onClick={() => setMatch(null)}>
              Not this team
            </Button>
          </div>
          <p className="mt-3 text-xs text-slate">
            Anyone can look up any team ID: it shows public information only and does not prove you
            own the team.
          </p>
        </div>
      )}
    </div>
  );
}
