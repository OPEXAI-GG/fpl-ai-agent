"""Player mentions in text -> canonical player ids, with a confidence, or explicitly unresolved.

Only players in the newest FPL squad snapshot can be resolved (the agent never needs anyone
else). A full name is a strong match; a surname or FPL short name is accepted only when exactly
one squad player carries it, or when the text also names that player's club and that leaves
exactly one. Anything else stays *ambiguous* (several candidates) and is never linked: a claim
about "James" must not move Reece James and Daniel James at once.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from fpl_agent.data.id_map import normalise
from fpl_agent.db.database import FootballDB, team_key

FULL, UNIQUE, BY_CLUB = 0.95, 0.8, 0.7
MIN_LEN = 4  # shorter surnames ("Rice" is fine at 4) are too easy to hit by accident
STOP = {"will", "said", "after", "with", "when", "this", "that", "they", "were", "have", "from",
        "united", "city", "town", "star", "boss", "coach", "manager", "head", "first", "team",
        "players", "player", "news", "injury", "back", "fresh", "young", "white", "king", "long",
        "hope", "march", "may", "june", "early", "late", "more", "less", "best", "rule", "free",
        "says", "keeper", "goal", "goals", "cup", "game", "match", "week", "league", "season"}  # fmt: skip


@dataclass
class Mention:
    text: str  # the normalised name as found
    player_id: int | None
    team_id: int | None
    status: str  # "resolved" | "ambiguous"
    confidence: float
    candidates: list[int] = field(default_factory=list)
    start: int = 0  # character offset in the normalised text


@dataclass
class Resolver:
    full: dict[str, list[int]]
    short: dict[str, list[int]]
    team_of: dict[int, int]
    club_words: dict[int, set[str]]  # team id -> normalised name forms

    @classmethod
    def from_db(cls, db: FootballDB, season: int | None = None) -> Resolver:
        season = season or (db.con.execute("SELECT max(season) FROM fpl_players_gw").fetchone()[0])
        rows = db.query(
            "SELECT f.player_id, f.team_id, f.raw, p.name FROM fpl_players_gw f "
            "LEFT JOIN players p USING (player_id) WHERE f.season = ? AND f.player_id IS NOT NULL "
            "QUALIFY row_number() OVER (PARTITION BY f.player_id ORDER BY f.snapshot_at DESC) = 1",
            [season],
        )
        teams = db.query("SELECT team_id, name, key FROM teams")
        r = cls({}, {}, {}, {})
        for t in teams.itertuples():
            forms = {normalise(str(t.name)), str(t.key)}
            forms |= {team_key(str(t.name))}
            r.club_words[int(t.team_id)] = {f for f in forms if f}
        for x in rows.itertuples():
            pid = int(x.player_id)
            if x.team_id is not None and x.team_id == x.team_id:
                r.team_of[pid] = int(x.team_id)
            names = {normalise(str(x.name))} if isinstance(x.name, str) else set()
            short = set()
            try:
                raw = json.loads(x.raw) if isinstance(x.raw, str) else {}
            except ValueError:
                raw = {}
            if raw.get("first_name") or raw.get("second_name"):
                names.add(normalise(f"{raw.get('first_name', '')} {raw.get('second_name', '')}"))
                if raw.get("second_name"):
                    short.add(normalise(str(raw["second_name"])).split()[-1])
            if raw.get("web_name"):
                short.add(normalise(str(raw["web_name"])))
            for n in names:
                if len(n.split()) >= 2:
                    r.full.setdefault(n, []).append(pid)
                    short.add(n.split()[-1])
            for s in short:
                if len(s) >= MIN_LEN and s not in STOP:
                    r.short.setdefault(s, []).append(pid)
        return r

    def clubs_in(self, norm: str) -> set[int]:
        padded = f" {norm} "
        return {t for t, forms in self.club_words.items()
                if any(f" {f} " in padded for f in forms if len(f) >= 3)}  # fmt: skip

    def resolve(self, text: str) -> list[Mention]:
        """Every player mention in `text`: resolved ones and ambiguous ones (never guessed)."""
        norm = normalise(text)
        padded = f" {norm} "
        clubs = self.clubs_in(norm)
        out: dict[str, Mention] = {}
        taken: list[tuple[int, int]] = []
        for name in sorted(self.full, key=len, reverse=True):
            i = padded.find(f" {name} ")
            if i < 0:
                continue
            pids = sorted(set(self.full[name]))
            taken.append((i, i + len(name) + 1))
            out[name] = self._pick(name, pids, clubs, FULL, i)
        for name, pids in self.short.items():
            for m in re.finditer(rf"(?<= ){re.escape(name)}(?= )", padded):
                if any(a <= m.start() < b for a, b in taken):
                    continue  # part of a full name already matched
                if name not in out:
                    out[name] = self._pick(name, sorted(set(pids)), clubs, UNIQUE, m.start())
                break
        return sorted(out.values(), key=lambda m: m.start)

    def _pick(self, name, pids, clubs, conf, start) -> Mention:
        if len(pids) == 1:
            return Mention(name, pids[0], self.team_of.get(pids[0]), "resolved", conf, pids, start)
        narrowed = [p for p in pids if self.team_of.get(p) in clubs]
        if len(narrowed) == 1:
            p = narrowed[0]
            return Mention(name, p, self.team_of.get(p), "resolved", min(conf, BY_CLUB), pids,
                           start)  # fmt: skip
        return Mention(name, None, None, "ambiguous", 0.0, pids, start)
