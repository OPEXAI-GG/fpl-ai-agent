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
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from fpl_agent.news.entities import Mention, Resolver
from fpl_agent.news.store import sha

VERSION = "rules-2026.11.2"
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


def fold(text: str) -> str:
    """Lowercase without accents, one character per input character (offsets stay valid)."""
    out = []
    for ch in text:
        d = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append((d[:1] or ch).lower() if len(d.lower()) >= 1 else ch)
    s = "".join(out)
    return s if len(s) == len(text) else text.lower()


SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
BOUNDARY = re.compile(r",|;|:|\s-\s|\b(?:and|but|while|whereas|although|adding that)\b")
COORDINATED = re.compile(r"^\s*(?:,|and|&|or|as well as)\s*$")
PRONOUN = re.compile(r"^(?:he|she|they)\b")
MAX_GAP = 90  # characters from the subject to its status phrase


@dataclass
class _Occ:
    start: int
    end: int
    mention: Mention


def _occurrences(low: str, mentions: list[Mention]) -> list[_Occ]:
    """Every place each mention's name appears in the folded text, longest names first."""
    occ: list[_Occ] = []
    for m in sorted(mentions, key=lambda m: len(m.text), reverse=True):
        rx = r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(map(re.escape, m.text.split())) + r"(?![a-z0-9])"
        for hit in re.finditer(rx, low):
            if not any(o.start <= hit.start() < o.end for o in occ):
                occ.append(_Occ(hit.start(), hit.end(), m))
    return sorted(occ, key=lambda o: o.start)


def _same(a: Mention, b: Mention) -> bool:
    return (a.player_id, a.text) == (b.player_id, b.text) or (
        a.player_id is not None and a.player_id == b.player_id)  # fmt: skip


def _subject(low: str, occ: list[_Occ], s0: int, at: int, prev: list[Mention]):
    """The one mention a status phrase at `at` is about, or None (abstain)."""
    before = [o for o in occ if s0 <= o.start and o.end <= at]
    if before:
        o = before[-1]
        if at - o.end > MAX_GAP:
            return None
        if len(before) > 1 and COORDINATED.match(low[before[-2].end : o.start]) and not _same(
                before[-2].mention, o.mention):  # fmt: skip
            return None  # "Saka and Saliba are out": a shared subject is not attributed
        return o
    if PRONOUN.match(low[at:]):  # "He's out": only with exactly one candidate in reach
        here = _distinct([o.mention for o in occ if o.start >= s0])
        cands = here or _distinct(prev)
        if len(cands) == 1:
            return next((o for o in occ if _same(o.mention, cands[0])), _Occ(at, at, cands[0]))
    return None


def _distinct(ms: list[Mention]) -> list[Mention]:
    out: list[Mention] = []
    for m in ms:
        if not any(_same(m, x) for x in out):
            out.append(m)
    return out


def _scope(low: str, occ: list[_Occ], subj: _Occ, s0: int, s1: int, at_end: int):
    """The clause that belongs to one subject: from the last clause boundary after the previous
    name (or the sentence start) to the next name (or the sentence end)."""
    prev_end = max([o.end for o in occ if s0 <= o.start and o.end <= subj.start], default=s0)
    start = prev_end
    for b in BOUNDARY.finditer(low, prev_end, max(subj.start, prev_end)):
        start = b.end()
    nxt = min([o.start for o in occ if o.start >= max(at_end, subj.end) and o.start < s1],
              default=s1)  # fmt: skip
    return start, max(nxt, at_end)


def rule_claims(item: dict, resolver: Resolver) -> list[Claim]:
    """`item` holds item_id, revision, title, content, observed_at, source_id, source_tier,
    story_key, published_at.

    Each status phrase is bound to one subject: the nearest name before it in the same
    sentence (a pronoun takes the only name in reach). A shared subject ("A and B are out"),
    a phrase with no name before it and a pronoun with several candidates abstain. Speculation
    and negation count only inside that subject's own clause."""
    text = f"{item['title']}. {item['content']}"
    mentions = resolver.resolve(text)
    if not mentions:
        return []
    low = fold(text)
    occ = _occurrences(low, mentions)
    out: dict[str, Claim] = {}
    prev: list[Mention] = []
    pos = 0
    for part in SENTENCES.split(low):
        s0 = low.find(part, pos)
        s1 = s0 + len(part)
        pos = s1
        if len(part.strip()) < 8:
            continue
        found: list[tuple[int, int, str, str, bool, int | None]] = []
        for pattern, kind, value, confirmed_wording in PATTERNS:
            for hit in re.finditer(pattern, low[s0:s1]):
                found.append((s0 + hit.start(), s0 + hit.end(), kind, value, confirmed_wording,
                              None))  # fmt: skip
        for wk in WEEKS.finditer(low[s0:s1]):
            n = _number(wk.group(4)) or _number(wk.group(2))
            if n:
                weeks = n * (4 if wk.group(5).startswith("month") else 1)
                found.append((s0 + wk.start(), s0 + wk.end(), "return", f"weeks:{weeks}", True,
                              weeks))  # fmt: skip
        for a, b, kind, value, confirmed_wording, weeks in found:
            subj = _subject(low, occ, s0, a, prev)
            if subj is None:
                continue
            c0, c1 = _scope(low, occ, subj, s0, s1, b)
            if NEGATION.search(low[c0:a]):
                continue
            speculative = bool(SPECULATION.search(low[c0:c1]))
            quote = text[c0:c1].strip(" ,;:-")
            quote = re.sub(r"\s+(?:and|but|while|whereas|although)$", "", quote)
            ret = item["observed_at"] + timedelta(weeks=weeks) if weeks else None
            c = make_claim(item, subj.mention, kind, value, confirmed_wording and not speculative,
                           quote, "rules", VERSION, return_at=ret)  # fmt: skip
            out[c.claim_id] = c
        here = _distinct([o.mention for o in occ if s0 <= o.start < s1])
        prev = here or prev
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
