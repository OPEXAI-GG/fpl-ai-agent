"""T05: independent legality check of a dry gw8.json against FPL's own bootstrap and picks."""
import json, sys
from collections import Counter
from pathlib import Path
import duckdb

W = Path(__file__).parent
plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["decision"]
boot = json.loads((W / "inputs/bootstrap.json").read_bytes())
picks = json.loads((W / "inputs/picks_gw7.json").read_bytes())
con = duckdb.connect(str(W / "work.duckdb"), read_only=True)
to_el = {pid: int(sid) for sid, pid in con.execute(
    "SELECT source_id, player_id FROM player_ids WHERE source = 'fpl-2026'").fetchall()}
el = {e["id"]: e for e in boot["elements"]}
POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
squad_now = {p["element"] for p in picks["picks"]}
outs = {to_el[p] for p in plan["transfers_out"]}; ins = {to_el[p] for p in plan["transfers_in"]}
squad = (squad_now - outs) | ins
xi = [to_el[p] for p in plan["xi"]]; bench = [to_el[p] for p in plan["bench"]]
pos = Counter(POS[el[e]["element_type"]] for e in xi)
cost_in = sum(el[e]["now_cost"] for e in ins)
checks = {
    "15 players": len(squad) == 15,
    "squad 2/5/5/3": Counter(POS[el[e]["element_type"]] for e in squad) == Counter({"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}),
    "max 3 per club": max(Counter(el[e]["team"] for e in squad).values()) <= 3,
    "XI+bench = squad": set(xi) | set(bench) == squad and len(xi) == 11 and len(bench) == 4,
    "XI formation (1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD)": pos["GKP"] == 1 and 3 <= pos["DEF"] <= 5 and 2 <= pos["MID"] <= 5 and 1 <= pos["FWD"] <= 3,
    "captain/vice distinct, in XI": to_el[plan["captain"]] in xi and to_el[plan["vice"]] in xi and plan["captain"] != plan["vice"],
    "chip available": plan["chip"] in (None, "bboost", "freehit") ,
    "no hits": plan["hits"] == 0,
    "bank not negative": plan["bank_after"] >= 0,
    "no transfers -> squad unchanged": bool(ins) or squad == squad_now,
}
flagged = {el[e]["web_name"]: (el[e]["status"], el[e]["chance_of_playing_next_round"], el[e]["news"]) for e in squad if el[e]["status"] != "a"}
out = {"checks": checks, "all_pass": all(checks.values()), "xi_formation": dict(pos), "transfer_cost_in": cost_in,
       "bank_now": picks["entry_history"]["bank"], "flagged_in_squad": flagged,
       "captain": el[to_el[plan["captain"]]]["web_name"], "vice": el[to_el[plan["vice"]]]["web_name"],
       "bench_order": [el[e]["web_name"] for e in sorted(bench, key=lambda e: el[e]["element_type"] != 1)]}
print(json.dumps(out, indent=1, ensure_ascii=False))
