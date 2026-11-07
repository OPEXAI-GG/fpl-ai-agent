// Typed access to the Python service. Every call goes to /api on this site (proxied).

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "network", "The service could not be reached. Check your connection.");
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    const d = (body as { detail?: { code?: string; message?: string } } | null)?.detail;
    const message =
      d?.message ??
      (res.status >= 500
        ? "The service is unavailable right now. Try again shortly."
        : "Something went wrong with that request.");
    throw new ApiError(res.status, d?.code ?? `http_${res.status}`, message);
  }
  return body as T;
}

export const fetcher = <T,>(path: string) => api<T>(path);

// ---- payload types (the subset the screens use) -------------------------------------------

export type FactStatus = "verified" | "as of last deadline" | "estimated" | "conflicting" | "unknown";

export interface Fact<T = unknown> {
  value: T;
  status: FactStatus;
  source: string;
  observed_at: string | null;
  note: string;
  kind: string;
}

export interface SourceMeta {
  fetched_at: number;
  stale: boolean;
  source: string;
  age_s: number;
  error: string | null;
}

export interface Manager {
  entry_id: number;
  team_name: string | null;
  manager_name: string | null;
  region: string | null;
  started_event: number | null;
  years_active: number | null;
  overall_points: number | null;
  overall_rank: number | null;
  event_points: number | null;
  event_rank: number | null;
  current_event: number | null;
  last_deadline_bank: number | null;
  last_deadline_value: number | null;
  players_in_game: number | null;
}

export interface TeamFacts {
  gameweek: number;
  squad: Fact<number[]>;
  bank: Fact<number>;
  free_transfers: Fact<number>;
  transfers_made: Fact<number>;
  chips: Record<string, Fact<string>>;
  ft_scenarios: number[];
  selling_prices: string;
  not_public: string[];
}

export interface FixtureCell {
  gw: number;
  games: { opp: string | null; home: boolean; difficulty: number | null }[];
}

export interface SquadPlayer {
  fpl_id: number;
  name: string;
  position: string;
  team: string;
  team_name: string;
  slot: number;
  starting: boolean;
  captain: boolean;
  vice: boolean;
  multiplier: number;
  price: number;
  status: string;
  chance: number | null;
  news: string;
  form: string;
  total_points: number;
  next: { xp: number; p_start: number | null } | null;
  fixtures: FixtureCell[];
}

export interface RankRow {
  gw: number;
  points: number;
  total: number;
  overall_rank: number | null;
  gw_rank: number | null;
  transfers: number;
  hit_cost: number;
  bench_points: number;
  bank: number;
  value: number;
}

export interface Dashboard {
  manager: Manager;
  gameweek: { next: number; deadline: string; name: string; squad_gameweek: number };
  facts: TeamFacts;
  squad: SquadPlayer[];
  squad_label: string;
  active_chip: string | null;
  rank_history: RankRow[];
  chips_used: { name: string; event: number; time: string }[];
  fixture_gws: number[];
  sources: Record<string, SourceMeta>;
  forecast: { key: string; gw: number; cutoff: string; matches_next: boolean } | null;
  sample: boolean;
  refresh: { requested: boolean; applied: boolean };
}

export interface PlanPlayer {
  id: number;
  name: string;
  fpl_id?: number;
  team?: string;
  position?: string;
  price?: number;
  status?: string;
  chance?: number | null;
  news?: string;
  xp: number;
  p_start?: number;
}

export interface ComparisonRow {
  transfers: number;
  hits: number;
  feasible: boolean;
  week_xp: number;
  horizon_xp: number;
  objective: number | null;
  win_share: number | null;
  gain: number | null;
  passes: boolean;
  reason: string;
  solve: string;
  gain_sd: number | null;
  gain_q10: number | null;
  gain_q90: number | null;
  worlds: number;
  week_xp_gross: number | null;
  moves: { out: string; in: string }[];
  captain: string | null;
}

export interface Recommendation {
  gw: number;
  action: string;
  chip: string | null;
  moves: { out: PlanPlayer; in: PlanPlayer }[];
  hits: number;
  hit_cost: number;
  captain: PlanPlayer;
  vice: PlanPlayer;
  xi: PlanPlayer[];
  bench: PlanPlayer[];
  xp: number;
  xp_after_hits: number;
  xp_no_transfers: number;
  objective: number | null;
  bank_after: number;
  ft_after: number;
  solve: string;
  captain_options: { player: PlanPlayer; xp: number; p_haul: number }[];
  chips: {
    play_now: string | null;
    held: Record<string, string>;
    scenarios: { chip: string; gw: number; gain: number; win_share: number; ok: boolean }[];
    searched: boolean;
  };
  comparison: ComparisonRow[];
  comparison_note: string;
  baseline: {
    xi: PlanPlayer[];
    bench: PlanPlayer[];
    captain: PlanPlayer | null;
    week_xp: number;
    horizon_xp: number;
  } | null;
  horizon: {
    gw: number;
    moves: { out: string; in: string }[];
    hits: number;
    chip: string | null;
    captain: string;
    xp: number;
    ft_before: number;
  }[];
  warnings: string[];
  checks: string[];
  field: string[];
  rails: { ok: boolean; lineup_blocked: boolean; transfers_blocked: boolean; violations: string[] };
}

export interface Scenario {
  free_transfers: number;
  status: "planned" | "pending";
  action?: string;
  moves?: { out: string; in: string }[];
  hits?: number;
  captain?: string;
  xp?: number;
  chips_searched?: boolean;
}

export interface PlanResult {
  version: string;
  stage: "preliminary" | "final" | "timed_out";
  final: boolean;
  timed_out: boolean;
  created_at: number;
  gameweek: number;
  entry_id: number;
  strategy: {
    key: string;
    label: string;
    summary: string;
    effect: string;
    target_rank: number | null;
    notes: string[];
  };
  forecast: { key: string; cutoff: string; created_at: number; model: string; samples: number; revision: string };
  team: TeamFacts;
  sources: Record<string, SourceMeta>;
  assumptions: string[];
  primary_ft: number;
  scenarios: Scenario[];
  conditional: boolean;
  same_across_scenarios: boolean;
  chips_possible: boolean;
  recommendation: Recommendation;
  lineup_shown: boolean;
  conditions: string[];
  timings: Record<string, number>;
  sample?: boolean;
}

export interface Job {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed" | "timed_out";
  stage: string;
  progress: number;
  error: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export interface PlanStatus {
  status: "none" | "queued" | "running" | "ready" | "done" | "failed" | "timed_out";
  key?: string;
  result: PlanResult | null;
  stage?: string | null;
  computed_at?: number | null;
  job: Job | null;
  forecast_job?: Job | null;
  forecast_ready?: boolean;
}

export interface Session {
  entry_id: number | null;
  prefs: { strategy: string; target_rank: number; timezone: string | null };
  team: { entry_id: number; team_name: string | null } | null;
}

export interface Health {
  ok: boolean;
  revision: { id: string; cutoff: string; fpl_snapshot_at: string; snapshot_gws: number[] } | null;
  forecast: { key: string; gws: number[]; cutoff: string; created_at: number; build_seconds: number } | null;
  queue: { plan: number; forecast: number };
  sample_ready: boolean;
}
