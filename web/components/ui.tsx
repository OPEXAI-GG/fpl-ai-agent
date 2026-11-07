"use client";

import type { ReactNode } from "react";
import type { Fact, FactStatus, SourceMeta } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";

const STATUS_TEXT: Record<FactStatus, string> = {
  verified: "Verified",
  "as of last deadline": "As of last deadline",
  estimated: "Estimated",
  conflicting: "Conflicting",
  unknown: "Unknown",
};

const STATUS_STYLE: Record<FactStatus, string> = {
  verified: "border-pitch bg-info-soft text-pitch-deep",
  "as of last deadline": "border-pitch/60 bg-paper text-pitch-deep",
  estimated: "border-amber border-dashed bg-amber-soft text-amber",
  conflicting: "border-kit bg-paper text-kit",
  unknown: "border-slate/50 bg-mist text-slate",
};

const STATUS_MARK: Record<FactStatus, string> = {
  verified: "●",
  "as of last deadline": "◐",
  estimated: "◌",
  conflicting: "!",
  unknown: "?",
};

/** How much a value can be trusted, always in words (never colour alone). */
export function StatusChip({ status, title }: { status: FactStatus; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-semibold ${STATUS_STYLE[status]}`}
    >
      <span aria-hidden>{STATUS_MARK[status]}</span>
      {STATUS_TEXT[status]}
    </span>
  );
}

export function FactLine({
  label,
  fact,
  render,
}: {
  label: string;
  fact: Fact;
  render: (v: unknown) => ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-2">
      <dt className="text-sm text-slate">{label}</dt>
      <dd className="flex flex-wrap items-center gap-2">
        <span className="num font-semibold">{fact.value == null ? "Not known" : render(fact.value)}</span>
        <StatusChip status={fact.status} title={`${fact.source}${fact.note ? `: ${fact.note}` : ""}`} />
      </dd>
      {fact.note && <p className="w-full text-xs text-slate">{fact.note}</p>}
    </div>
  );
}

export function Freshness({ meta, label = "FPL data" }: { meta?: SourceMeta; label?: string }) {
  if (!meta) return null;
  return (
    <span className={`text-xs ${meta.stale ? "font-semibold text-kit" : "text-slate"}`}>
      {label}: {meta.source}, retrieved {ago(meta.age_s)}
      {meta.stale ? ` — stale copy (${meta.error ?? "FPL unavailable"})` : ""}
    </span>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-3 py-10 text-slate">
      <span className="relative block h-1.5 w-32 overflow-hidden rounded-full bg-mist">
        <span className="progress-sweep absolute inset-y-0 left-0 w-1/3 rounded-full bg-pitch" />
      </span>
      {label}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`animate-pulse rounded-md bg-mist ${className}`} />;
}

export function ErrorState({
  error,
  title,
  action,
}: {
  error: unknown;
  title?: string;
  action?: ReactNode;
}) {
  const e = error instanceof ApiError ? error : null;
  const heading =
    title ??
    (e?.code === "not_found"
      ? "Team not found"
      : e?.code === "provider_unavailable"
        ? "FPL isn't responding"
        : e?.code === "rate_limited"
          ? "Slow down a little"
          : e?.code === "no_data"
            ? "Data not published yet"
            : e?.code === "invalid_entry"
              ? "That isn't a team ID"
              : "Something went wrong");
  return (
    <div role="alert" className="rounded-[var(--radius-card)] border border-kit/40 bg-paper p-5">
      <h2 className="display text-2xl font-bold text-kit">{heading}</h2>
      <p className="mt-1 max-w-prose">{e?.message ?? (error as Error)?.message ?? "Unknown error."}</p>
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function Notice({
  tone = "info",
  children,
}: {
  tone?: "info" | "warn" | "sample";
  children: ReactNode;
}) {
  const cls =
    tone === "warn"
      ? "border-amber/50 bg-amber-soft text-ink"
      : tone === "sample"
        ? "border-flood bg-flood/30 text-ink"
        : "border-pitch/30 bg-info-soft text-ink";
  return <div className={`rounded-lg border px-4 py-3 text-sm ${cls}`}>{children}</div>;
}

export function SampleBanner() {
  return (
    <Notice tone="sample">
      <strong>Sample data.</strong> This is a fictional squad built from this week&apos;s player data
      for demonstration. It is not a real FPL team and has no season history.
    </Notice>
  );
}

export function Section({
  title,
  aside,
  children,
  id,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  return (
    <section aria-labelledby={id} className="mt-8">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2 border-b border-line pb-2">
        <h2 id={id} className="display text-2xl font-bold sm:text-3xl">
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function When({ iso, tz }: { iso: string | number | null | undefined; tz?: string | null }) {
  return <time dateTime={typeof iso === "string" ? iso : undefined}>{dateTime(iso, tz)}</time>;
}

export function Button({
  children,
  variant = "primary",
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "quiet" | "danger" }) {
  const cls =
    variant === "primary"
      ? "bg-pitch text-white hover:bg-pitch-deep disabled:bg-mist disabled:text-slate disabled:hover:bg-mist"
      : variant === "danger"
        ? "border border-kit text-kit hover:bg-kit hover:text-white"
        : "border border-line bg-paper text-ink hover:border-pitch";
  return (
    <button
      {...rest}
      className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 font-semibold transition-colors disabled:cursor-not-allowed ${cls} ${rest.className ?? ""}`}
    >
      {children}
    </button>
  );
}
