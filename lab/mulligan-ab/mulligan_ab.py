#!/usr/bin/env python3
"""Offline mulligan A/B: Claude Haiku 5.5 vs TypeSafe Jev on the same hands.

Experiment 1 of the coach-model A/B. Every ranked game the tracker recorded
already holds the opening hand and what the player actually kept
(`game_cards.mull_offered` / `mull_kept`), so the mulligan decision can be
replayed against any model with no live game and no Power.log.

    build   games.db -> results/hands.jsonl (one row per opening hand)
    run     ask one arm about every hand, N repeats, resumable
    report  agreement with the player, self-consistency, cost, latency

Arms:
    haiku-low, haiku-medium   claude-haiku-5-5 at that effort, one call per
                              hand, structured JSON keep counts per card
    jev                       jev-1.13.0, one call per hand, one `noul`
                              question per distinct card ("keep this?") —
                              noul rather than choice because Jev 1.13 leans
                              toward the first listed choice

What this measures, honestly: agreement with the player's own keeps (not
correctness — the player's mulligans are not gold labels), how stable each
arm is across repeats, and what each costs. It never touches the live
retrieval log or the overlay.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hearthstone-tracker"))
sys.path.insert(0, str(ROOT / "hearthstone-deck-builder" / "scripts"))

from build_deck_code import decode_deckstring  # noqa: E402
from hstracker.cards import HeroClassResolver  # noqa: E402
from hstracker.config import DEFAULT_DB  # noqa: E402
from hstracker.jev import (  # noqa: E402  the production prompt: the lab tests what ships
    API_URL as JEV_URL, KEEP_THRESHOLD, MODEL as JEV_MODEL,
    PRICE_PER_M_INPUT as JEV_PRICE_IN, JevClient, decklist_lines, describe_card,
    mulligan_questions, mulligan_state, slot_label,
)

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

HAIKU_MODEL = "claude-haiku-5-5"
HAIKU_PRICE_IN, HAIKU_PRICE_OUT = 0.10, 0.50  # USD per 1M tokens, prompts <100K
# The coach's previous model, kept as a baseline arm. Haiku 4.5 takes no
# `effort` and runs without thinking unless given a budget, so it is asked
# plain: the way the live coach used it.
HAIKU45_MODEL = "claude-haiku-4-5"
HAIKU45_PRICE_IN, HAIKU45_PRICE_OUT = 1.00, 5.00
SONNET_PRICE_IN, SONNET_PRICE_OUT = 2.00, 10.00  # Sonnet 5 and Sonnet 5.5

# Claude arms: name -> (model, effort or None, (USD in, USD out) per 1M tokens).
# The Sonnets run at `medium`, the same effort as haiku-medium, so the
# comparison is between models rather than effort settings.
CLAUDE_ARMS = {
    "haiku-low": (HAIKU_MODEL, "low", (HAIKU_PRICE_IN, HAIKU_PRICE_OUT)),
    "haiku-medium": (HAIKU_MODEL, "medium", (HAIKU_PRICE_IN, HAIKU_PRICE_OUT)),
    "haiku-4-5": (HAIKU45_MODEL, None, (HAIKU45_PRICE_IN, HAIKU45_PRICE_OUT)),
    "sonnet-5": ("claude-sonnet-5", "medium", (SONNET_PRICE_IN, SONNET_PRICE_OUT)),
    "sonnet-5-5": ("claude-sonnet-5-5", "medium", (SONNET_PRICE_IN, SONNET_PRICE_OUT)),
}

ARMS = (*CLAUDE_ARMS, "jev")


# --------------------------------------------------------------------- build

def decklist(deckstring: str | None, cards: HeroClassResolver) -> list[str]:
    if not deckstring:
        return []
    try:
        decoded = decode_deckstring(deckstring)
    except Exception:
        return []
    return decklist_lines([(cards.card_by_dbf(dbf) or {"name": f"dbf {dbf}"}, count)
                           for dbf, count in decoded["cards"]])


def build_hands(db_path: Path, cards: HeroClassResolver) -> tuple[list[dict], dict[str, int]]:
    """One row per ranked opening hand. The Coin is dropped: it is not a
    mulligan decision (it is given after the mulligan when going second)."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    games = con.execute(
        "SELECT * FROM games WHERE game_type = 'GT_RANKED' ORDER BY start_time").fetchall()
    hands, skipped = [], {"no_mulligan_rows": 0, "odd_hand_size": 0}
    for g in games:
        rows = con.execute(
            "SELECT card_id, mull_offered, mull_kept FROM game_cards "
            "WHERE game_id = ? AND friendly = 1 AND mull_offered > 0 ORDER BY card_id",
            (g["id"],)).fetchall()
        offered = []
        for r in rows:
            card = cards.card(r["card_id"])
            if card.get("name") == "The Coin":
                continue
            offered.append({"card_id": r["card_id"], "copies": r["mull_offered"],
                            "kept": r["mull_kept"], **describe_card(card, r["card_id"])})
        if not offered:
            skipped["no_mulligan_rows"] += 1
            continue
        if sum(c["copies"] for c in offered) not in (3, 4):
            skipped["odd_hand_size"] += 1
            continue
        hands.append({
            "hand_id": f"g{g['id']}",
            "result": g["result"], "format": g["format_type"],
            "my_class": g["friendly_class"], "opp_class": g["opponent_class"],
            "going_first": bool(g["friendly_first"]),
            "deck_name": g["deck_name"],
            "decklist": decklist(g["deckstring"], cards),
            "offered": offered,
        })
    con.close()
    return hands, skipped


# ------------------------------------------------------------------ prompts

def hand_state(hand: dict[str, Any], *, with_decklist: bool = True) -> str:
    """The shared game state every arm sees — the same text `hst live` sends Jev."""
    return mulligan_state(hand, with_decklist=with_decklist)


def jev_questions(hand: dict[str, Any]) -> dict[str, Any]:
    return mulligan_questions(hand["offered"])


HAIKU_SYSTEM = (
    "You are a Hearthstone mulligan coach. Decide, for each offered card, how many "
    "copies to keep in the opening hand. Judge from the card text given, not from "
    "memory of the card. Reply only with the JSON the schema asks for; keep each "
    "reason under 15 words."
)


def haiku_schema(hand: dict[str, Any]) -> dict[str, Any]:
    labels = [slot_label(i) for i in range(len(hand["offered"]))]
    return {
        "type": "object",
        "properties": {"cards": {"type": "array", "items": {
            "type": "object",
            "properties": {"slot": {"type": "string", "enum": labels},
                           "keep": {"type": "integer"},
                           "reason": {"type": "string"}},
            "required": ["slot", "keep", "reason"],
            "additionalProperties": False}}},
        "required": ["cards"],
        "additionalProperties": False,
    }


# --------------------------------------------------------------------- arms

def clamp_keep(raw: Any, copies: int) -> int:
    try:
        return max(0, min(int(raw), copies))
    except (TypeError, ValueError):
        return 0


def parse_haiku(text: str, hand: dict[str, Any]) -> tuple[dict[str, int], dict[str, str]]:
    data = json.loads(text)
    keeps, reasons = {}, {}
    by_slot = {slot_label(i): c for i, c in enumerate(hand["offered"])}
    for row in data.get("cards", []):
        c = by_slot.get(row.get("slot"))
        if c is None:
            continue
        keeps[c["card_id"]] = clamp_keep(row.get("keep"), c["copies"])
        reasons[c["card_id"]] = str(row.get("reason", ""))[:200]
    for c in hand["offered"]:  # a card the model skipped counts as tossed
        keeps.setdefault(c["card_id"], 0)
    return keeps, reasons


def parse_jev(resp: dict[str, Any], hand: dict[str, Any]) -> tuple[dict[str, int], dict[str, float]]:
    answers = resp["answers"]
    keeps, probs = {}, {}
    for i, c in enumerate(hand["offered"]):
        p = float(answers[slot_label(i)]["noul"])
        probs[c["card_id"]] = round(p, 4)
        keeps[c["card_id"]] = c["copies"] if p >= KEEP_THRESHOLD else 0
    return keeps, probs


def make_claude_arm(effort: str | None, client: Any = None, *, model: str = HAIKU_MODEL,
                   prices: tuple[float, float] = (HAIKU_PRICE_IN, HAIKU_PRICE_OUT),
                   ) -> Callable[[dict, bool], dict]:
    """A Claude arm. effort=None leaves `effort` out (Haiku 4.5 rejects it)."""
    if client is None:
        import anthropic  # optional dependency: only the Claude arms need it
        client = anthropic.Anthropic(max_retries=4)

    def ask(hand: dict[str, Any], with_decklist: bool) -> dict[str, Any]:
        t0 = time.monotonic()
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": haiku_schema(hand)}}
        if effort:
            output_config["effort"] = effort
        resp = client.messages.create(
            model=model,
            max_tokens=8000,
            system=HAIKU_SYSTEM,
            messages=[{"role": "user", "content": hand_state(hand, with_decklist=with_decklist)}],
            output_config=output_config,
        )
        latency = time.monotonic() - t0
        if resp.stop_reason == "refusal":
            raise RuntimeError("refusal")
        text = next(b.text for b in resp.content if b.type == "text")
        keeps, reasons = parse_haiku(text, hand)
        tin, tout = resp.usage.input_tokens, resp.usage.output_tokens
        return {"model": resp.model, "keeps": keeps, "reasons": reasons,
                "latency_s": round(latency, 2), "input_tokens": tin, "output_tokens": tout,
                "cost_usd": (tin * prices[0] + tout * prices[1]) / 1e6}
    return ask


def make_jev_arm(api_key: str, url: str = JEV_URL, timeout: float = 30.0,
                 retries: int = 3, post: Callable | None = None) -> Callable[[dict, bool], dict]:
    """The Jev arm, asked through the tracker's own JevClient (same HTTP,
    headers and retries as production; only the timeout is longer)."""
    client = JevClient(api_key, url=url, timeout=timeout, retries=retries, post=post)

    def ask(hand: dict[str, Any], with_decklist: bool) -> dict[str, Any]:
        t0 = time.monotonic()
        resp = client.ask(hand_state(hand, with_decklist=with_decklist), jev_questions(hand))
        latency = time.monotonic() - t0
        keeps, probs = parse_jev(resp, hand)
        tin = int(resp.get("usage", {}).get("input_tokens", 0))
        return {"model": resp.get("model", JEV_MODEL), "keeps": keeps, "probs": probs,
                "latency_s": round(latency, 2), "input_tokens": tin, "output_tokens": 0,
                "cost_usd": tin * JEV_PRICE_IN / 1e6}
    return ask


# ---------------------------------------------------------------------- run

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def arm_results_path(arm: str, with_decklist: bool) -> Path:
    return RESULTS / f"{arm}{'' if with_decklist else '-nodeck'}.jsonl"


def run_arm(arm: str, ask: Callable[[dict, bool], dict], hands: list[dict], repeats: int,
            out: Path, *, with_decklist: bool = True, workers: int = 4) -> dict[str, int]:
    """Ask every (hand, repeat) not already in `out`. Appends one JSON line per
    answer, so an interrupted run resumes where it stopped."""
    done = {(r["hand_id"], r["repeat"]) for r in read_jsonl(out) if not r.get("error")}
    todo = [(h, k) for h in hands for k in range(repeats) if (h["hand_id"], k) not in done]
    out.parent.mkdir(parents=True, exist_ok=True)
    counts = {"asked": 0, "errors": 0, "skipped_done": len(hands) * repeats - len(todo)}

    def one(item: tuple[dict, int]) -> dict[str, Any]:
        hand, k = item
        base = {"arm": arm, "hand_id": hand["hand_id"], "repeat": k,
                "with_decklist": with_decklist, "ts": time.time()}
        try:
            return {**base, **ask(hand, with_decklist)}
        except Exception as e:  # recorded, not raised: one bad hand must not stop the run
            return {**base, "error": f"{type(e).__name__}: {e}"[:300]}

    with ThreadPoolExecutor(max_workers=workers) as pool, out.open("a", encoding="utf-8") as fh:
        for row in pool.map(one, todo):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            counts["asked"] += 1
            counts["errors"] += 1 if row.get("error") else 0
            mark = "!" if row.get("error") else "."
            print(mark, end="", flush=True)
    print()
    return counts


# ------------------------------------------------------------------- report

def _pct(xs: list[float], p: float) -> float | None:
    s = sorted(xs)
    return s[int(p * (len(s) - 1))] if s else None


def copy_agreement(hand: dict[str, Any], keeps: dict[str, int]) -> tuple[int, int]:
    """(agreeing copies, total copies) between an arm's keeps and the player's."""
    agree = total = 0
    for c in hand["offered"]:
        k = keeps.get(c["card_id"], 0)
        agree += c["copies"] - abs(k - c["kept"])
        total += c["copies"]
    return agree, total


def kappa(pairs: list[tuple[int, int]]) -> float | None:
    """Cohen's kappa over per-copy keep(1)/toss(0) pairs (arm, player)."""
    n = len(pairs)
    if not n:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else round((po - pe) / (1 - pe), 3)


def copy_pairs(hand: dict[str, Any], keeps: dict[str, int]) -> list[tuple[int, int]]:
    out = []
    for c in hand["offered"]:
        k = keeps.get(c["card_id"], 0)
        for i in range(c["copies"]):
            out.append((int(i < k), int(i < c["kept"])))
    return out


def auc(scored: list[tuple[float, int]]) -> float | None:
    """Rank AUC: chance a card the player kept scored above one they tossed."""
    pos = [s for s, y in scored if y]
    neg = [s for s, y in scored if not y]
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def summarize_arm(rows: list[dict], hands_by_id: dict[str, dict]) -> dict[str, Any]:
    ok = [r for r in rows if not r.get("error") and r["hand_id"] in hands_by_id]
    agree = total = exact = 0
    pairs: list[tuple[int, int]] = []
    kept_copies = 0
    scored: list[tuple[float, int]] = []
    by_hand: dict[str, list[dict[str, int]]] = {}
    for r in ok:
        hand = hands_by_id[r["hand_id"]]
        a, t = copy_agreement(hand, r["keeps"])
        agree, total = agree + a, total + t
        exact += int(a == t)
        pairs.extend(copy_pairs(hand, r["keeps"]))
        kept_copies += sum(r["keeps"].values())
        by_hand.setdefault(r["hand_id"], []).append(r["keeps"])
        for c in hand["offered"]:
            p = (r.get("probs") or {}).get(c["card_id"])
            if p is not None:
                scored.extend((p, int(i < c["kept"])) for i in range(c["copies"]))
    # self-consistency: share of (hand, card) decisions identical across every repeat
    stable = decided = 0
    for hid, answers in by_hand.items():
        if len(answers) < 2:
            continue
        for c in hands_by_id[hid]["offered"]:
            decided += 1
            stable += int(len({a.get(c["card_id"], 0) for a in answers}) == 1)
    lat = [r["latency_s"] for r in ok]
    cost = sum(r.get("cost_usd", 0) for r in ok)
    return {
        "answers": len(ok), "errors": len(rows) - len(ok),
        "hands": len(by_hand),
        "agree_copies": f"{agree}/{total}" if total else "0/0",
        "agree_pct": round(100 * agree / total, 1) if total else None,
        "kappa": kappa(pairs),
        "hand_exact_pct": round(100 * exact / len(ok), 1) if ok else None,
        "keep_rate_pct": round(100 * kept_copies / total, 1) if total else None,
        "stable_pct": round(100 * stable / decided, 1) if decided else None,
        "auc": auc(scored) if scored else None,
        "median_s": _pct(lat, 0.5), "p90_s": _pct(lat, 0.9),
        "cost_usd": round(cost, 4),
        "usd_per_hand": round(cost / len(ok), 6) if ok else None,
    }


def majority_keeps(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Per hand, the median keep count per card across repeats."""
    out: dict[str, dict[str, list[int]]] = {}
    for r in rows:
        if r.get("error"):
            continue
        for cid, k in r["keeps"].items():
            out.setdefault(r["hand_id"], {}).setdefault(cid, []).append(k)
    return {h: {c: int(statistics.median_low(v)) for c, v in cards.items()}
            for h, cards in out.items()}


def cross_agreement(a: dict[str, dict[str, int]], b: dict[str, dict[str, int]],
                    hands_by_id: dict[str, dict]) -> str:
    agree = total = 0
    for hid in set(a) & set(b):
        for c in hands_by_id[hid]["offered"]:
            agree += c["copies"] - abs(a[hid].get(c["card_id"], 0) - b[hid].get(c["card_id"], 0))
            total += c["copies"]
    return f"{agree}/{total} ({100 * agree / total:.1f}%)" if total else "n/a"


def format_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(none)"
    cols = list(rows[0])
    cells = [[("" if r.get(c) is None else str(r.get(c))) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    line = lambda vals: "  ".join(v.ljust(w) for v, w in zip(vals, widths))  # noqa: E731
    return "\n".join([line(cols), line(["-" * w for w in widths]), *(line(r) for r in cells)])


def hand_agreement(rows: list[dict], hands_by_id: dict[str, dict]) -> dict[str, tuple[int, int]]:
    """Per hand: (agreeing copies, total copies), summed over every repeat."""
    out: dict[str, tuple[int, int]] = {}
    for r in rows:
        if r.get("error") or r["hand_id"] not in hands_by_id:
            continue
        a, t = copy_agreement(hands_by_id[r["hand_id"]], r["keeps"])
        pa, pt = out.get(r["hand_id"], (0, 0))
        out[r["hand_id"]] = (pa + a, pt + t)
    return out


def bootstrap(per_hand: list[dict[str, tuple[int, int]]], n: int = 2000,
              seed: int = 7) -> list[tuple[float, float]]:
    """95% CI of agreement (one arm) or of the difference (two arms, first
    minus second), resampling whole hands: the unit that is independent."""
    ids = sorted(set.intersection(*(set(d) for d in per_hand)))
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        sample = [rng.choice(ids) for _ in ids]
        pcts = [100 * sum(d[h][0] for h in sample) / sum(d[h][1] for h in sample)
                for d in per_hand]
        stats.append(pcts[0] - pcts[1] if len(pcts) == 2 else pcts[0])
    stats.sort()
    return [(round(stats[int(0.025 * n)], 1), round(stats[int(0.975 * n)], 1))]


def report(hands: list[dict], results_dir: Path = RESULTS) -> str:
    hands_by_id = {h["hand_id"]: h for h in hands}
    player_keep = sum(c["kept"] for h in hands for c in h["offered"])
    player_total = sum(c["copies"] for h in hands for c in h["offered"])
    out = [f"# Mulligan A/B — {len(hands)} hands, {player_total} offered card copies "
           f"(player kept {100 * player_keep / player_total:.1f}%)", ""]
    files = sorted(p for p in results_dir.glob("*.jsonl") if p.name != "hands.jsonl")
    summaries, majorities = [], {}
    for p in files:
        rows = read_jsonl(p)
        summaries.append({"arm": p.stem, **summarize_arm(rows, hands_by_id)})
        majorities[p.stem] = majority_keeps(rows)
    out += ["## Arms vs the player's own keeps", format_table(summaries), ""]
    per_hand = {p.stem: hand_agreement(read_jsonl(p), hands_by_id) for p in files}
    ref = "jev" if "jev" in per_hand else None
    ci_rows = []
    for s in sorted(summaries, key=lambda r: -(r["agree_pct"] or 0)):
        arm = s["arm"]
        if not per_hand[arm]:
            continue
        lo, hi = bootstrap([per_hand[arm]])[0]
        row = {"arm": arm, "agree_pct": s["agree_pct"], "ci95": f"{lo}–{hi}"}
        if ref and arm != ref:
            dlo, dhi = bootstrap([per_hand[arm], per_hand[ref]])[0]
            diff = round(s["agree_pct"] - next(x["agree_pct"] for x in summaries if x["arm"] == ref), 1)
            row.update({f"vs_{ref}": f"{diff:+}", "diff_ci95": f"{dlo:+}–{dhi:+}",
                        "clear": "yes" if dlo > 0 or dhi < 0 else "no"})
        ci_rows.append(row)
    out += ["## Agreement with confidence intervals (paired bootstrap over hands)",
            format_table(ci_rows),
            "`clear` = the difference's 95% interval excludes zero.", ""]
    names = list(majorities)
    if len(names) > 1:
        out.append("## Arms vs each other (median keep across repeats)")
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                out.append(f"- {a} vs {b}: {cross_agreement(majorities[a], majorities[b], hands_by_id)}")
        out.append("")
    split = []
    for p in files:
        rows = [r for r in read_jsonl(p) if not r.get("error")]
        for result in ("WON", "LOST"):
            sub = [r for r in rows if hands_by_id.get(r["hand_id"], {}).get("result") == result]
            s = summarize_arm(sub, hands_by_id)
            split.append({"arm": p.stem, "game": result, "answers": s["answers"],
                          "agree_pct": s["agree_pct"]})
    if split:
        out += ["## Agreement split by how the game went (small n — a hint, not a finding)",
                format_table(split), ""]
    out += [
        "Read with care: `agree_pct` is agreement with the player's keeps, not with a",
        "correct answer. `stable_pct` is the share of card decisions identical across",
        "every repeat. `auc` (Jev only) ranks noul probabilities against the player's",
        f"keeps, independent of the {KEEP_THRESHOLD} keep threshold.",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------- cli

def load_hands(path: Path) -> list[dict]:
    hands = read_jsonl(path)
    if not hands:
        sys.exit(f"no hands at {path} — run `build` first")
    return hands


def estimate(hands: list[dict], arm: str, repeats: int, with_decklist: bool) -> str:
    chars = sum(len(hand_state(h, with_decklist=with_decklist)) for h in hands)
    tin = chars / 4 * repeats  # ~4 chars per token
    if arm == "jev":
        return f"~{tin / 1e3:.0f}K input tokens -> ~${tin * JEV_PRICE_IN / 1e6:.3f}"
    sys_tokens = len(HAIKU_SYSTEM) / 4 * len(hands) * repeats
    tout = len(hands) * repeats * {"haiku-low": 400, "haiku-4-5": 200}.get(arm, 1200)  # json + thinking, rough
    pin, pout = CLAUDE_ARMS[arm][2]
    usd = ((tin + sys_tokens) * pin + tout * pout) / 1e6
    return f"~{(tin + sys_tokens) / 1e3:.0f}K in / ~{tout / 1e3:.0f}K out -> ~${usd:.3f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="extract opening hands from games.db")
    b.add_argument("--db", type=Path, default=DEFAULT_DB)
    r = sub.add_parser("run", help="ask one arm about every hand")
    r.add_argument("arm", choices=ARMS)
    r.add_argument("--repeats", type=int, default=3)
    r.add_argument("--limit", type=int, help="first N hands only (smoke test)")
    r.add_argument("--no-decklist", action="store_true",
                   help="omit the deck list from the state (tests Jev's short-input preference)")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--dry-run", action="store_true", help="print one prompt and a cost estimate; no calls")
    sub.add_parser("report", help="score every arm in results/")
    args = ap.parse_args(argv)

    hands_path = RESULTS / "hands.jsonl"
    if args.cmd == "build":
        cards = HeroClassResolver(allow_fetch=True)
        hands, skipped = build_hands(args.db, cards)
        RESULTS.mkdir(parents=True, exist_ok=True)
        hands_path.write_text("".join(json.dumps(h) + "\n" for h in hands), encoding="utf-8")
        unknown = sum(1 for h in hands for c in h["offered"] if c["name"] == c["card_id"])
        print(f"{len(hands)} hands -> {hands_path}  (skipped: {skipped}; cards without data: {unknown})")
        return 0

    hands = load_hands(hands_path)
    if args.cmd == "report":
        print(report(hands))
        return 0

    hands = hands[: args.limit] if args.limit else hands
    with_decklist = not args.no_decklist
    if args.dry_run:
        print(hand_state(hands[0], with_decklist=with_decklist))
        print()
        print(f"{args.arm}: {len(hands)} hands x {args.repeats} repeats, "
              f"{estimate(hands, args.arm, args.repeats, with_decklist)}")
        return 0
    if args.arm == "jev":
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            sys.exit("TYPESAFE_API_KEY is not set")
        ask = make_jev_arm(key)
    else:
        model, effort, prices = CLAUDE_ARMS[args.arm]
        ask = make_claude_arm(effort, model=model, prices=prices)
    out = arm_results_path(args.arm, with_decklist)
    counts = run_arm(args.arm, ask, hands, args.repeats, out,
                     with_decklist=with_decklist, workers=args.workers)
    print(f"{args.arm}: {counts} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
