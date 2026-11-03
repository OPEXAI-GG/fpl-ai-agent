"""Text -> structured, traceable football claims.

A claim is about one resolved player (or an explicitly unresolved mention) and carries the
clause that supports it, the item and revision it came from, the source tier, how sure the name
match is, whether the wording is a confirmed statement or speculation, when we knew it
(`effective_at` = when this revision was first observed, never the publication time), when it
stops being useful (`expires_at`) and the extractor version.

Two extractors produce the same validated shape:

* **rules** (default, free, deterministic): phrase patterns per clause, with negation and
  speculation markers. Recall is modest by design; it never invents a player or a status.
* **llm** (optional: ANTHROPIC_API_KEY and NEWS_LLM=1): one bounded Claude call per batch through
  the scout. The text is passed as data, the answer is validated (enums, the quote must be a
  verbatim substring of the item, the name is resolved by our own resolver), and any failure
  falls back to the rules for that batch.

Kinds and values:
  availability: out | doubtful | fit        training: full | partial
  return: weeks:<n> (with return_at)        selection: rotation_risk | expected_start | benched
  set_piece: penalties | set_pieces
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from fpl_agent.news.entities import Mention, Resolver
from fpl_agent.news.store import sha

VERSION = "rules-2026.11.1"
LLM_VERSION = "llm-2026.11.1"

KINDS = {
    "availability": {"out", "doubtful", "fit"},
    "training": {"full", "partial"},
    "return": None,  # weeks:<n>
    "selection": {"rotation_risk", "expected_start", "benched"},
    "set_piece": {"penalties", "set_pieces"},
}
TTL = {("availability", "out"): timedelta(days=7), ("availability", "doubtful"): timedelta(days=4),
       ("availability", "fit"): timedelta(days=5), ("training", "full"): timedelta(days=4),
       ("training", "partial"): timedelta(days=3), ("selection", None): timedelta(days=4),
       ("set_piece", None): timedelta(days=60)}  # fmt: skip

PATTERNS: list[tuple[str, str, str, bool]] = [  # (regex, kind, value, confirmed wording)
    (r"\b(has been |have been |was |is |are )?ruled out\b", "availability", "out", True),
    (r"\bwill (definitely )?miss\b", "availability", "out", True),
    (r"\b(is|are) (out|unavailable|sidelined|suspended)\b", "availability", "out", True),
    (r"\b(he|she|they|who)(?:'s|\u2019s| is) (out|unavailable)\b", "availability", "out", True),
    (r"\b(sidelined|suspended|out injured|serving a ban)\b", "availability", "out", True),
    (r"\b(could|may|might|set to|expected to|likely to|in danger of) (miss|missing)\b",
     "availability", "out", False),
    (r"\b(major |injury )?doubts?\b|\bdoubtful\b|\bfitness tests?\b|\b50-50\b|\blate fitness\b",
     "availability", "doubtful", False),
    (r"\b(is|are) (fully )?fit\b|\bpassed fit\b|\bavailable (again|for selection)\b",
     "availability", "fit", True),
    (r"\b(back|returned|returns|returning) (in|to) (full |first-team )?training\b",
     "training", "full", True),
    (r"\btrained (separately|alone|individually|away from the group)\b|\b(light|modified|partial) "
     r"training\b", "training", "partial", True),
    (r"\b(will be |could be |set to be )?(rested|rotated)\b|\brotation\b", "selection",
     "rotation_risk", False),
    (r"\bwill start\b|\bstarts? (on|against|at|for)\b|\bin line to start\b", "selection",
     "expected_start", True),
    (r"\b(dropped|on the bench|benched)\b", "selection", "benched", True),
    (r"\b(penalty taker|take (the )?penalt(y|ies)|on penalties|on pens)\b", "set_piece",
     "penalties", True),
    (r"\b(set[- ]piece|free[- ]kick|corner) duties\b", "set_piece", "set_pieces", True),
]  # fmt: skip
SPECULATION = re.compile(
    r"\b(reportedly|according to|understood|believed|rumou?r|could|may|might|set to|expected to|"
    r"likely|fears?|feared|hopes?|hoping|suggest|claims?|speculation|potentially|possibly)\b"
)
NEGATION = re.compile(r"\b(not|no|never|isn't|aren't|won't|hasn't|without)\s+(\w+\s+){0,2}$")
WEEKS = re.compile(r"\bout for (up to |around |about |at least )?(\w+)( to (\w+))? "
                   r"(weeks?|months?)\b")  # fmt: skip
NUMBERS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "eight": 8, "nine": 9, "ten": 10, "several": 4, "few": 3, "couple": 2}  # fmt: skip
CLAUSES = re.compile(r"(?<=[.!?;])\s+|\n+|,\s*(?:but|while|whereas|although)\s+|\s+but\s+")


@dataclass
class Claim:
    claim_id: str
    item_id: str
    revision: int
    player_id: int | None
    mention: str
    kind: str
    value: str
    certainty: str  # "confirmed" | "speculative"
    quote: str
    source_id: str
    source_tier: int
    entity_confidence: float
    story_key: str
    published_at: datetime | None
    effective_at: datetime
    expires_at: datetime
    return_at: datetime | None
    extractor: str
    extraction_version: str


def _number(word: str | None) -> int | None:
    if not word:
        return None
    return int(word) if word.isdigit() else NUMBERS.get(word.lower())


def _expiry(kind: str, value: str, at: datetime, return_at: datetime | None) -> datetime:
    if return_at is not None:
        return return_at + timedelta(days=3)
    return at + TTL.get((kind, value), TTL.get((kind, None), timedelta(days=4)))


def _mentions_in(clause: str, mentions: list[Mention], resolver: Resolver) -> list[Mention]:
    names = {m.text for m in resolver.resolve(clause)}
    return [m for m in mentions if m.text in names]


def make_claim(
    item: dict,
    m: Mention,
    kind: str,
    value: str,
    confirmed: bool,
    quote: str,
    extractor: str,
    version: str,
    return_at: datetime | None = None,
) -> Claim:
    at = item["observed_at"]
    if kind == "return" and return_at is None:
        raise ValueError("a return claim needs a return date")
    certainty = "confirmed" if confirmed else "speculative"
    who = str(m.player_id) if m.player_id is not None else f"?{m.text}"
    return Claim(
        sha(item["item_id"], str(item["revision"]), who, kind, value, quote), item["item_id"],
        int(item["revision"]), m.player_id, m.text, kind, value, certainty, quote[:300],
        item["source_id"], int(item["source_tier"]), m.confidence, item["story_key"],
        item.get("published_at"), at, _expiry(kind, value, at, return_at), return_at,
        extractor, version,
    )  # fmt: skip


def rule_claims(item: dict, resolver: Resolver) -> list[Claim]:
    """`item` holds item_id, revision, title, content, observed_at, source_id, source_tier,
    story_key, published_at."""
    text = f"{item['title']}. {item['content']}"
    mentions = resolver.resolve(text)
    if not mentions:
        return []
    out: dict[str, Claim] = {}
    for clause in (c.strip() for c in CLAUSES.split(text)):
        if not clause or len(clause) < 8:
            continue
        low = clause.lower()
        who = _mentions_in(clause, mentions, resolver)
        if not who:
            continue
        speculative = bool(SPECULATION.search(low))
        for pattern, kind, value, confirmed_wording in PATTERNS:
            hit = re.search(pattern, low)
            if not hit or NEGATION.search(low[: hit.start()]):
                continue
            confirmed = confirmed_wording and not speculative
            for m in who:
                c = make_claim(item, m, kind, value, confirmed, clause, "rules", VERSION)
                out[c.claim_id] = c
        wk = WEEKS.search(low)
        if wk:
            n = _number(wk.group(4)) or _number(wk.group(2))
            if n:
                weeks = n * (4 if wk.group(5).startswith("month") else 1)
                ret = item["observed_at"] + timedelta(weeks=weeks)
                for m in who:
                    c = make_claim(item, m, "return", f"weeks:{weeks}", not speculative, clause,
                                   "rules", VERSION, return_at=ret)  # fmt: skip
                    out[c.claim_id] = c
    # one claim per player, kind and value within an item (a headline and its body say the
    # same thing): keep the best-matched name, then the longest supporting clause
    best: dict[tuple, Claim] = {}
    for c in out.values():
        key = (c.player_id if c.player_id is not None else c.mention, c.kind, c.value)
        cur = best.get(key)
        if cur is None or (c.entity_confidence, len(c.quote)) > (cur.entity_confidence,
                                                                  len(cur.quote)):  # fmt: skip
            best[key] = c
    return list(best.values())


def validate_llm(raw: object, item: dict, resolver: Resolver) -> list[Claim]:
    """Turn a model's JSON list for one item into claims; anything not verifiable is dropped."""
    text = re.sub(r"\s+", " ", f"{item['title']}. {item['content']}")
    out = []
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        kind, value = str(x.get("kind", "")), str(x.get("value", ""))
        quote = re.sub(r"\s+", " ", str(x.get("quote", ""))).strip()
        name = str(x.get("player", ""))
        if kind not in KINDS or not quote or quote not in text or len(quote) > 400:
            continue
        allowed = KINDS[kind]
        ret = None
        if kind == "return":
            m = re.fullmatch(r"weeks:(\d{1,2})", value)
            if not m:
                continue
            ret = item["observed_at"] + timedelta(weeks=int(m.group(1)))
        elif value not in allowed:
            continue
        named = resolver.resolve(name)
        if len(named) != 1:
            continue
        if named[0].player_id is None:  # an ambiguous name is never linked
            continue
        found = [m for m in resolver.resolve(quote) if m.player_id == named[0].player_id]
        if not found:  # the player must be named in the supporting quote itself
            continue
        confirmed = x.get("certainty") == "confirmed" and not SPECULATION.search(quote.lower())
        out.append(make_claim(item, found[0], kind, value, confirmed, quote, "llm", LLM_VERSION,
                              return_at=ret))  # fmt: skip
    return out
