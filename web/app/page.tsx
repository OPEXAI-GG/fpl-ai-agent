import Link from "next/link";
import { Lookup } from "@/components/Lookup";

export default function Home() {
  return (
    <div className="grid gap-10 lg:grid-cols-[1.15fr_1fr] lg:items-start">
      <div>
        <h1 className="display max-w-[16ch] text-5xl font-bold sm:text-6xl">
          Plan your FPL week from your team ID.
        </h1>
        <p className="mt-4 max-w-[60ch] text-lg text-slate">
          Matchplan reads your public squad, simulates every upcoming fixture a thousand times and
          tells you whether to roll or transfer, who to start, who to captain and whether a chip is
          worth it, with the uncertainty beside each call.
        </p>
        <div className="mt-8">
          <Lookup />
        </div>
        <p className="mt-6 text-sm">
          Just looking?{" "}
          <Link href="/team/sample" className="font-semibold text-pitch underline underline-offset-4">
            Open the labelled sample team
          </Link>
          .
        </p>
      </div>

      <aside className="rounded-[var(--radius-card)] border border-line bg-paper p-6">
        <h2 className="display text-2xl font-bold">What it does with your ID</h2>
        <dl className="mt-4 space-y-4">
          <div>
            <dt className="font-semibold">Reads public data only</dt>
            <dd className="text-slate">
              Your squad at the last deadline, rank history and chips, from FPL&apos;s public pages. No
              password, account connection, screenshot or payment.
            </dd>
          </div>
          <div>
            <dt className="font-semibold">Says what it can&apos;t see</dt>
            <dd className="text-slate">
              FPL doesn&apos;t publish this week&apos;s transfers until the deadline, or your exact free
              transfers. Every estimate is labelled, and advice that depends on them is shown for each
              possibility.
            </dd>
          </div>
          <div>
            <dt className="font-semibold">Compares before it recommends</dt>
            <dd className="text-slate">
              Rolling and every feasible number of transfers are scored in the same simulated
              outcomes. A move is suggested only when it clearly beats rolling.
            </dd>
          </div>
          <div>
            <dt className="font-semibold">Never touches your team</dt>
            <dd className="text-slate">You make any changes yourself on the FPL site.</dd>
          </div>
        </dl>
      </aside>
    </div>
  );
}
