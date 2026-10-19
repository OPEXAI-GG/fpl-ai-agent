// FPL's public endpoint for a manager: checks an id exists and reads the team name. No login.

const BASE = "https://fantasy.premierleague.com/api";

export const MANAGER_RE = /^\d{1,9}$/;

// {id, team, manager} when FPL knows the id, {id} when FPL could not be reached (the id is kept
// and checked again by the engine), or null when FPL says there is no such team.
export async function lookupManager(id, fetcher = fetch) {
  try {
    const res = await fetcher(`${BASE}/entry/${id}/`, { headers: { "User-Agent": "fplsim/0.1" } });
    if (res.status === 404) return null;
    if (!res.ok) return { id };
    const e = await res.json();
    const manager = [e.player_first_name, e.player_last_name].filter(Boolean).join(" ");
    return { id, team: e.name || null, manager: manager || null };
  } catch {
    return { id };
  }
}
