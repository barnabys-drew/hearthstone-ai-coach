"""Jev for the coach's choice decisions: mulligan keep/toss and Discover picks.

Turn plans need generation and arithmetic, so they stay with whatever model
coaches. A mulligan or a Discover is a different shape of problem: a small,
fixed set of options to judge. That is what TypeSafe's Jev classifier is
built for, and the offline A/B in `lab/mulligan-ab/` measured it on 64 real
opening hands (docs/jev.md):

- it agreed with the player's own keeps 70.9% of the time; the best Claude
  arm (Sonnet 5) reached 74.0%, a gap the 95% interval can't tell from zero,
  and Haiku 4.5 / 5.5 and Sonnet 5.5 landed at 67.8–70.0%,
- it gave the same answer on 96% of card decisions across repeats, the most
  of any arm,
- it answered in ~0.2s against 1.8–3.6s, at ~$0.00004 a hand: about 1/100 of
  Sonnet 5 and 1/10 of Haiku 5.5.

So the tracker asks Jev directly, with no coaching session in the loop: the
mulligan card is on the overlay a fraction of a second after the hand is
dealt, and the coach model is free for the turn plans.

Every call runs on a background thread. The live loop never waits on the
network, and a missing key or a failed call changes nothing about tracking,
the overlay panels or game recording: the feed prints one `!!` line and the
coach model handles that decision as it always did. Which model answers is
picked in the overlay (`hstracker/choices.py`: jev, coach, both, off); the
Jev modes need `TYPESAFE_API_KEY`.

Prompt design follows Jev 1.13's published weak spots: each decision is a
`noul` (yes/no probability) question rather than a `choice`, because Jev
leans toward the first option listed; the state is short; untrusted
free text is limited to card rules text from HearthstoneJSON.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"  # pinned: jev-latest would silently change the answers
PRICE_PER_M_INPUT = 0.042  # USD; output tokens are free
KEEP_THRESHOLD = 0.5  # noul probability at/above which a card is kept

_FORMATS = {"FT_WILD": "Wild", "FT_STANDARD": "Standard", 1: "Wild", 2: "Standard",
            3: "Classic", 4: "Twist"}


def jev_available() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY"))


def cost_usd(input_tokens: int) -> float:
    return input_tokens / 1_000_000 * PRICE_PER_M_INPUT


# ------------------------------------------------------------- card helpers

def clean_text(text: str | None) -> str:
    """HearthstoneJSON rules text -> plain text."""
    if not text:
        return ""
    text = re.sub(r"</?[a-z]+>", "", text)
    text = text.replace("[x]", "").replace("$", "").replace("#", "")
    return " ".join(text.replace("\n", " ").split())


def describe_card(card: dict[str, Any], fallback_id: str) -> dict[str, Any]:
    out = {"name": card.get("name") or fallback_id, "cost": card.get("cost"),
           "type": (card.get("type") or "").lower() or None,
           "text": clean_text(card.get("text"))}
    if card.get("type") == "MINION":
        out["stats"] = f"{card.get('attack')}/{card.get('health')}"
    elif card.get("type") == "WEAPON":
        out["stats"] = f"{card.get('attack')}/{card.get('durability')}"
    return out


def decklist_lines(cards_with_counts: list[tuple[dict[str, Any], int]]) -> list[str]:
    """[(HearthstoneJSON card, copies)] -> ["2x Slam (1)", ...] sorted by cost."""
    rows = [(c.get("cost") if isinstance(c.get("cost"), int) else 99,
             c.get("name") or "?", n) for c, n in cards_with_counts]
    return [f"{n}x {name} ({cost})" for cost, name, n in sorted(rows)]


def class_name(cls: str | None) -> str:
    return {"DEATHKNIGHT": "Death Knight", "DEMONHUNTER": "Demon Hunter"}.get(
        cls or "", (cls or "?").title())


def slot_label(i: int) -> str:
    return f"c{i}"


# ------------------------------------------------------------------ mulligan

def mulligan_state(hand: dict[str, Any], *, with_decklist: bool = True) -> str:
    """The state Jev (and every lab arm) sees at the mulligan screen.

    `hand`: format, my_class, opp_class, going_first, deck_name, decklist,
    offered [{name, cost, type, stats, text, copies}], and optionally
    game_label ("ranked" when known). Only what the player can see.
    """
    fmt = _FORMATS.get(hand.get("format"), hand.get("format") or "")
    label = hand.get("game_label", "ranked")
    lines = [
        " ".join(p for p in ("Hearthstone", fmt, label, "game, mulligan screen.") if p),
        f"You are playing {class_name(hand.get('my_class'))}"
        + (f" ({hand['deck_name']})" if hand.get("deck_name") else "")
        + f" against {class_name(hand.get('opp_class'))}.",
        "You are going first." if hand.get("going_first")
        else "You are going second and will also get The Coin.",
    ]
    if with_decklist and hand.get("decklist"):
        lines.append("Your deck (count, name, mana cost): " + "; ".join(hand["decklist"]) + ".")
    lines.append("Cards offered in your opening hand:")
    for i, c in enumerate(hand["offered"]):
        bits = [f"{c['cost']} mana" if c.get("cost") is not None else None,
                c.get("type"), c.get("stats")]
        head = ", ".join(b for b in bits if b)
        copies = f" (x{c['copies']})" if c.get("copies", 1) > 1 else ""
        lines.append(f"- [{slot_label(i)}] {c['name']}{copies}: {head}. {c['text']}".rstrip())
    return "\n".join(lines)


def mulligan_questions(offered: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        slot_label(i): {
            "type": "noul",
            "instructions": f"The player should keep {c['name']} [{slot_label(i)}] in "
                            "their opening hand rather than mulligan it away.",
            "criteria": {"true": "Keeping this card makes the opening hand better.",
                         "false": "Throwing this card back for a new one is better."},
        }
        for i, c in enumerate(offered)
    }


def parse_mulligan(resp: dict[str, Any], offered: list[dict[str, Any]]) -> list[float]:
    """Keep probability per offered card, in offer order."""
    answers = resp["answers"]
    return [float(answers[slot_label(i)]["noul"]) for i in range(len(offered))]


_GAME_LABELS = {"GT_RANKED": "ranked", "GT_CASUAL": "casual"}


def hand_from_snapshot(snap: dict[str, Any], resolver: Any,
                       deck_counts: dict[str, int] | None,
                       deck_name: str | None, game_format: Any = None,
                       game_type: str | None = None) -> dict[str, Any] | None:
    """A mulligan-phase snapshot -> the `hand` dict mulligan_state() takes.

    The Coin is left out: it isn't a mulligan decision. Three cards dealt
    means going first, four means going second. For a ranked game the state
    is word for word what the offline A/B measured.
    """
    me, opp = snap.get("me") or {}, snap.get("opp") or {}
    offered: dict[str, dict[str, Any]] = {}
    for c in me.get("hand") or []:
        if c.get("name") == "The Coin":
            continue
        key = c.get("id") or c.get("name")
        if key in offered:
            offered[key]["copies"] += 1
            continue
        card = resolver.card(c.get("id")) if c.get("id") else {}
        desc = describe_card(card, c.get("name") or "?") if card else {
            "name": c.get("name") or "?", "cost": c.get("cost"),
            "type": (c.get("type") or "").lower() or None, "text": c.get("text") or ""}
        offered[key] = {**desc, "id": c.get("id"), "copies": 1}
    if not offered:
        return None
    dealt = sum(c["copies"] for c in offered.values())
    decklist = decklist_lines([(resolver.card(cid), n) for cid, n in (deck_counts or {}).items()
                               if resolver.card(cid)])
    return {"format": game_format, "game_label": _GAME_LABELS.get(game_type or ""),
            "my_class": me.get("class"), "opp_class": opp.get("class"),
            "going_first": dealt <= 3, "deck_name": deck_name,
            "decklist": decklist, "offered": list(offered.values())}


# ------------------------------------------------------------------ discover

def discover_state(snap: dict[str, Any], discover: dict[str, Any]) -> str:
    me, opp = snap.get("me") or {}, snap.get("opp") or {}

    def board(cards: list[dict[str, Any]]) -> str:
        return ", ".join(f"{c['name']} {c.get('atk', 0)}/{c.get('health', 0)}" for c in cards) or "empty"

    lines = [
        f"Hearthstone game, turn {snap.get('turn')}. You are playing "
        f"{class_name(me.get('class'))} against {class_name(opp.get('class'))}.",
        f"Your hero has {me.get('hp')} health and {me.get('armor') or 0} armor; "
        f"the opponent has {opp.get('hp')} health and {opp.get('armor') or 0} armor.",
        "Your hand: " + (", ".join(c["name"] for c in me.get("hand") or []) or "empty") + ".",
        f"Your board: {board(me.get('board') or [])}.",
        f"Opponent board: {board(opp.get('board') or [])}.",
        f"{discover.get('source', 'A card')} lets you pick one of these:",
    ]
    for i, o in enumerate(discover.get("options") or []):
        cost = f"{o['cost']} mana" if o.get("cost") is not None else ""
        kind = (o.get("type") or "").lower()
        head = ", ".join(b for b in (cost, kind) if b)
        lines.append(f"- [o{i}] {o.get('name')}: {head}. {o.get('text') or ''}".rstrip())
    return "\n".join(lines)


def discover_questions(options: list[dict[str, Any]]) -> dict[str, Any]:
    """One noul per option, scored independently — a `choice` here would
    inherit Jev 1.13's lean toward whichever option is listed first."""
    return {
        f"o{i}": {
            "type": "noul",
            "instructions": f"Picking {o.get('name')} [o{i}] is the best of these options "
                            "for this game right now.",
        }
        for i, o in enumerate(options)
    }


def parse_discover(resp: dict[str, Any], options: list[dict[str, Any]]) -> list[float]:
    answers = resp["answers"]
    return [float(answers[f"o{i}"]["noul"]) for i in range(len(options))]


# -------------------------------------------------------------------- client

class JevError(RuntimeError):
    pass


class JevClient:
    def __init__(self, api_key: str, *, url: str = API_URL, model: str = MODEL,
                 timeout: float = 10.0, retries: int = 2,
                 post: Callable[[bytes], dict[str, Any]] | None = None) -> None:
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("Jev API url must be http(s)")
        self.api_key, self.url, self.model = api_key, url, model
        self.timeout, self.retries = timeout, retries
        self._post = post or self._http_post

    def _http_post(self, body: bytes) -> dict[str, Any]:
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- scheme checked in __init__
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                # 429 = rate limited, 529 = overloaded, 5xx = server trouble
                if (e.code in (429, 529) or e.code >= 500) and attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise JevError(f"HTTP {e.code}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise JevError(f"unreachable: {e}") from e
        raise JevError("unreachable")  # pragma: no cover

    def ask(self, state: str, questions: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"model": self.model, "state": state, "questions": questions}).encode()
        return self._post(body)


# ------------------------------------------------------------------- advisor

def _pct(p: float) -> str:
    return f"{round(100 * p)}%"


class JevAdvisor:
    """Answers mulligans and Discovers off the live loop's thread.

    Each answer is written to `jev.json`, logged as an `advice` event (model =
    the pinned Jev version, variant "jev", plus the choices mode), and queued
    as one feed line (`== JEV MULLIGAN` / `== JEV DISCOVER`) that the live loop
    prints. With `publish=True` (mode `jev`) it is also the main overlay card
    and the coach doesn't redo it; with `publish=False` (mode `both`) it stays
    a second opinion beside the coach's card.
    """

    def __init__(self, client: JevClient, overlay_dir: Any = None, *,
                 publish_advice: Callable | None = None,
                 publish_discover: Callable | None = None,
                 publish_jev: Callable | None = None,
                 log_event: Callable | None = None,
                 background: bool = True) -> None:
        from .overlay import write_advice, write_discover, write_jev
        from .raglog import append_event

        self.client, self.overlay_dir = client, overlay_dir
        self._publish_advice = publish_advice or write_advice
        self._publish_discover = publish_discover or write_discover
        self._publish_jev = publish_jev or write_jev
        self._log = log_event or append_event
        self._background = background
        self.lines: queue.Queue[str] = queue.Queue()

    def _spawn(self, fn: Callable[[], None]) -> None:
        if self._background:
            threading.Thread(target=fn, daemon=True).start()
        else:
            fn()

    def drain(self) -> list[str]:
        out = []
        while True:
            try:
                out.append(self.lines.get_nowait())
            except queue.Empty:
                return out

    # mulligan -------------------------------------------------------------

    def _write(self, fn: Callable, *args: Any) -> None:
        try:
            fn(*args, self.overlay_dir)
        except Exception:
            pass  # overlay folder unavailable: the feed line still carries the answer

    def mulligan(self, hand: dict[str, Any], *, publish: bool = True, mode: str = "jev",
                 session: str | None = None, game_no: int | None = None) -> None:
        self._spawn(lambda: self._mulligan(hand, publish, mode, session, game_no))

    def _mulligan(self, hand: dict[str, Any], publish: bool, mode: str,
                  session: str | None, game_no: int | None) -> None:
        t0 = time.monotonic()
        try:
            resp = self.client.ask(mulligan_state(hand), mulligan_questions(hand["offered"]))
            probs = parse_mulligan(resp, hand["offered"])
        except Exception as e:
            self.lines.put(f"!! Jev mulligan failed ({type(e).__name__}: {e}) — coach the mulligan yourself")
            return
        latency = time.monotonic() - t0
        rows, keep, toss = [], [], []
        for c, p in zip(hand["offered"], probs):
            label = c["name"] + (f" x{c['copies']}" if c["copies"] > 1 else "")
            kept = p >= KEEP_THRESHOLD
            rows.append({"card": label, "keep": kept, "reason": f"Jev: {_pct(p)} keep"})
            (keep if kept else toss).append(f"{label} {_pct(p)}")
        model = resp.get("model") or self.client.model
        tokens = int((resp.get("usage") or {}).get("input_tokens", 0))
        payload = {"kind": "mulligan", "turn": 0, "headline": "Mulligan (Jev)",
                   "mulligan": rows, "model": model, "variant": "jev"}
        self._write(self._publish_jev, {**payload, "session": session, "game_no": game_no})
        if publish:
            self._write(self._publish_advice, payload)
        self._log({"ev": "advice", "kind": "mulligan", "turn": 0,
                   "mulligan_cards": [r["card"] for r in rows],
                   "mulligan_keep": [r["keep"] for r in rows], "steps": [],
                   "model": model, "variant": "jev", "choices_mode": mode,
                   "session": session, "game_no": game_no,
                   "jev": {"probs": [round(p, 3) for p in probs], "latency_s": round(latency, 2),
                           "input_tokens": tokens, "cost_usd": round(cost_usd(tokens), 7)}})
        tag = "" if publish else "second opinion, "
        self.lines.put(f"== JEV MULLIGAN ({tag}{latency:.1f}s) — keep: {', '.join(keep) or 'none'}"
                       f" | toss: {', '.join(toss) or 'none'}")

    # discover -------------------------------------------------------------

    def discover(self, snap: dict[str, Any], discover: dict[str, Any], *,
                 publish: bool = True, mode: str = "jev",
                 session: str | None = None, game_no: int | None = None) -> None:
        self._spawn(lambda: self._discover(snap, discover, publish, mode, session, game_no))

    def _discover(self, snap: dict[str, Any], discover: dict[str, Any], publish: bool,
                  mode: str, session: str | None, game_no: int | None) -> None:
        options = discover.get("options") or []
        if len(options) < 2:
            return
        t0 = time.monotonic()
        try:
            resp = self.client.ask(discover_state(snap, discover), discover_questions(options))
            probs = parse_discover(resp, options)
        except Exception as e:
            self.lines.put(f"!! Jev discover failed ({type(e).__name__}: {e}) — pick it yourself")
            return
        latency = time.monotonic() - t0
        ranked = sorted(zip(probs, (o.get("name") for o in options)), reverse=True)
        best_p, best = ranked[0]
        others = ", ".join(f"{n} {_pct(p)}" for p, n in ranked[1:])
        text = f"Pick {best} — Jev {_pct(best_p)} (vs {others})"
        model = resp.get("model") or self.client.model
        tokens = int((resp.get("usage") or {}).get("input_tokens", 0))
        self._write(self._publish_jev, {"kind": "discover", "turn": snap.get("turn"),
                                        "discover": text, "model": model,
                                        "session": session, "game_no": game_no})
        if publish:
            self._write(self._publish_discover, text)
        self._log({"ev": "advice", "kind": "discover", "turn": snap.get("turn"),
                   "discover": text, "steps": [], "model": model, "variant": "jev",
                   "choices_mode": mode, "session": session, "game_no": game_no,
                   "jev": {"options": [o.get("name") for o in options],
                           "probs": [round(p, 3) for p in probs], "latency_s": round(latency, 2),
                           "input_tokens": tokens, "cost_usd": round(cost_usd(tokens), 7)}})
        tag = "" if publish else "second opinion, "
        self.lines.put(f"== JEV DISCOVER ({tag}{latency:.1f}s) — {discover.get('source')}: {text}")


def advisor_from_env(overlay_dir: Any = None) -> JevAdvisor | None:
    """The live loop's advisor, or None without a Jev key (the overlay's
    picker then has only the coach and off modes to work with)."""
    if not jev_available():
        return None
    return JevAdvisor(JevClient(os.environ["TYPESAFE_API_KEY"]), overlay_dir)
