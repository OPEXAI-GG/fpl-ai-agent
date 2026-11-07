"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { browserTimezone } from "@/lib/format";
import { useSession } from "@/lib/session";
import { Button, ErrorState, Loading, Section } from "@/components/ui";

const STRATEGIES = [
  {
    key: "points",
    label: "Points-focused",
    text: "Maximises expected points and ignores what other managers own. Suits mini-leagues and anyone happy with bigger rank swings.",
  },
  {
    key: "balanced",
    label: "Balanced",
    text: "Expected points with protection against big rank swings: very popular players are kept unless unavailable, and a widely owned captain is preferred when it is close.",
  },
  {
    key: "rank",
    label: "Rank-chasing",
    text: "Keeps the balanced squad rules but, while you are behind your target, captains the least-owned option within a small expected-points margin of the best.",
  },
];
const TARGETS = [10000, 100000, 1000000];

function zones(): string[] {
  try {
    const fn = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf;
    return fn ? fn("timeZone") : ["UTC", "Europe/London"];
  } catch {
    return ["UTC", "Europe/London"];
  }
}

export default function SettingsPage() {
  const s = useSession();
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState<string | null>(null);
  if (s.error) return <ErrorState error={s.error} />;
  if (!s.data) return <Loading label="Loading your settings…" />;
  const p = s.data.prefs;

  async function save(patch: Parameters<typeof s.update>[0], what: string) {
    setSaving(true);
    setErr(null);
    try {
      await s.update(patch);
      setSaved(what);
    } catch (e) {
      setErr(e);
    } finally {
      setSaving(false);
    }
  }

  const tzList = zones();
  return (
    <div className="max-w-3xl">
      <h1 className="display text-4xl font-bold sm:text-5xl">Settings</h1>
      <p className="mt-2 text-slate">Saved for this browser only. There is no account.</p>
      {err != null && (
        <div className="mt-4">
          <ErrorState error={err} />
        </div>
      )}
      <p aria-live="polite" className="mt-2 min-h-6 text-sm font-semibold text-pitch">
        {saved ? `Saved: ${saved}.` : ""}
      </p>

      <Section title="Strategy" id="strat-h">
        <p className="mb-3 text-sm text-slate">
          Strategy changes how the plan treats other managers&apos; ownership and risk. It never changes the forecast
          itself: expected points are the same in every mode.
        </p>
        <fieldset className="space-y-2" disabled={saving}>
          <legend className="sr-only">Strategy</legend>
          {STRATEGIES.map((x) => (
            <label key={x.key} className={`flex cursor-pointer gap-3 rounded-lg border-2 bg-paper p-3 ${p.strategy === x.key ? "border-pitch" : "border-line"}`}>
              <input type="radio" name="strategy" className="mt-1 h-5 w-5 accent-[var(--color-pitch)]" checked={p.strategy === x.key} onChange={() => save({ strategy: x.key }, `${x.label} strategy`)} />
              <span>
                <span className="font-semibold">{x.label}</span>
                <span className="block text-sm text-slate">{x.text}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {p.strategy === "rank" && (
          <label className="mt-3 flex flex-col text-sm font-semibold">
            Target overall rank
            <select className="num mt-1 min-h-11 w-48 rounded-lg border border-line bg-paper px-3 font-normal" value={p.target_rank} onChange={(e) => save({ target_rank: Number(e.target.value) }, "target rank")}>
              {TARGETS.map((t) => (
                <option key={t} value={t}>
                  Top {new Intl.NumberFormat("en-GB").format(t)}
                </option>
              ))}
            </select>
          </label>
        )}
        <p className="mt-3 text-sm">A new strategy makes a new plan the next time you open This week.</p>
      </Section>

      <Section title="Timezone" id="tz-h">
        <label className="flex flex-col text-sm font-semibold">
          Show deadlines in
          <select className="mt-1 min-h-11 w-full max-w-sm rounded-lg border border-line bg-paper px-3 font-normal" value={p.timezone ?? ""} onChange={(e) => save({ timezone: e.target.value || browserTimezone() }, "timezone")}>
            <option value="">This device ({browserTimezone()})</option>
            {tzList.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </label>
      </Section>

      <Section title="Team" id="team-h">
        {s.data.entry_id ? (
          <p>
            Selected: <strong>{s.data.team?.team_name ?? `ID ${s.data.entry_id}`}</strong> (ID {s.data.entry_id}).{" "}
            <Link href={`/team/${s.data.entry_id}`} className="font-semibold text-pitch underline">
              Open it
            </Link>
          </p>
        ) : (
          <p className="text-slate">No team selected.</p>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <Link href="/" className="inline-flex min-h-11 items-center rounded-lg border border-line bg-paper px-4 font-semibold hover:border-pitch">
            Change team
          </Link>
          <Button
            variant="danger"
            onClick={async () => {
              await s.reset();
              router.push("/");
            }}
          >
            Reset everything
          </Button>
        </div>
        <p className="mt-2 text-sm text-slate">Reset forgets the selected team, strategy and timezone in this browser.</p>
      </Section>

      <Section title="Fresh data" id="fresh-h">
        <p className="text-sm text-slate">
          Team data is cached for up to 10 minutes. Use <em>Refresh from FPL</em> on your overview for a new copy (at
          most once every five minutes per team). Forecasts are rebuilt when new football data is published, not on
          demand.
        </p>
      </Section>
    </div>
  );
}
