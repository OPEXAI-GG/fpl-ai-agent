"use client";

import useSWR from "swr";
import { fetcher, type Dashboard } from "./api";
import { useSession } from "./session";
import { browserTimezone } from "./format";

export function useDashboard(entry: string, refresh = 0) {
  const key = `/api/entries/${encodeURIComponent(entry)}/dashboard${refresh ? `?refresh=1&n=${refresh}` : ""}`;
  return useSWR<Dashboard>(key, fetcher, { revalidateOnFocus: false, shouldRetryOnError: false });
}

export function useTimezone(): string {
  const { data } = useSession();
  return data?.prefs.timezone || browserTimezone();
}
