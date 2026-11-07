"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSyncExternalStore } from "react";
import { useSession, rememberedEntry } from "@/lib/session";

function useRemembered() {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener("storage", cb);
      return () => window.removeEventListener("storage", cb);
    },
    () => JSON.stringify(rememberedEntry()),
    () => "null",
  );
}

export function Header() {
  const path = usePathname() ?? "/";
  const { data: session } = useSession();
  const remembered = JSON.parse(useRemembered()) as ReturnType<typeof rememberedEntry>;
  const fromPath = path.match(/^\/team\/([^/]+)/)?.[1];
  const entry = fromPath ?? session?.entry_id ?? remembered?.entry ?? null;
  const teamName = fromPath === "sample" ? "Sample XI" : (session?.team?.team_name ?? remembered?.teamName ?? null);

  const links = [
    ...(entry != null
      ? [
          { href: `/team/${entry}`, label: "Overview", exact: true },
          { href: `/team/${entry}/plan`, label: "This week", exact: false },
        ]
      : []),
    { href: "/fixtures", label: "Fixtures", exact: false },
    { href: "/players", label: "Players", exact: false },
    { href: "/model", label: "Model", exact: false },
    { href: "/settings", label: "Settings", exact: false },
  ];

  return (
    <header className="border-b border-pitch-deep bg-pitch text-white">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
        <Link href="/" className="display flex items-baseline gap-2 text-2xl font-bold tracking-wide">
          <span aria-hidden className="inline-block h-3 w-3 rotate-45 bg-flood" />
          Matchplan
        </Link>
        {teamName && (
          <span className="hidden truncate text-sm text-white/80 sm:inline" title="Selected team">
            {teamName}
          </span>
        )}
        <nav aria-label="Main" className="-mx-1 flex w-full overflow-x-auto sm:ml-auto sm:w-auto">
          <ul className="flex gap-1">
            {links.map((l) => {
              const active = l.exact ? path === l.href : path === l.href || path.startsWith(l.href + "/");
              return (
                <li key={l.href}>
                  <Link
                    href={l.href}
                    aria-current={active ? "page" : undefined}
                    className={`block whitespace-nowrap rounded-md px-3 py-2 text-[0.95rem] font-semibold ${
                      active ? "bg-white text-pitch-deep" : "text-white hover:bg-white/10"
                    }`}
                  >
                    {l.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>
    </header>
  );
}
