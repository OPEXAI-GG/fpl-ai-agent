"use client";

import useSWR from "swr";
import { api, fetcher, type Session } from "./api";

export function useSession() {
  const s = useSWR<Session>("/api/session", fetcher, { revalidateOnFocus: false });
  return {
    ...s,
    async update(patch: Partial<{ strategy: string; target_rank: number; timezone: string }>) {
      const next = await api<Session>("/api/session", { method: "PATCH", body: JSON.stringify(patch) });
      await s.mutate(next, { revalidate: false });
      return next;
    },
    async reset() {
      await api("/api/session", { method: "DELETE" });
      try {
        localStorage.removeItem("matchplan.entry");
      } catch {
        /* storage may be blocked */
      }
      await s.mutate();
    },
  };
}

// The selected team is also kept in the browser so the header can link back to it instantly.
export function rememberEntry(entry: number | "sample", teamName: string | null) {
  try {
    localStorage.setItem("matchplan.entry", JSON.stringify({ entry, teamName }));
  } catch {
    /* storage may be blocked: the server session still remembers it */
  }
}

export function rememberedEntry(): { entry: number | "sample"; teamName: string | null } | null {
  try {
    const v = localStorage.getItem("matchplan.entry");
    return v ? JSON.parse(v) : null;
  } catch {
    return null;
  }
}
