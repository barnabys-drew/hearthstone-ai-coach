"""Live game-state snapshots from an in-progress Power.log.

Feeds real-time play advice: `hst live` tails the current game and emits a
snapshot every turn. Only information the client is allowed to see is in the
log — the opponent's hand shows up as hidden-card counts.

Two hard-won constraints shape the design (see git history / plan):
- A single LogParser fed a multi-game Power.log can raise
  InconsistentPlayerIdError when player ids shuffle between games, so each
  poll re-parses ONLY the current game's lines with a fresh parser.
- Player entities have no `card_id`; use getattr when walking entities.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from hearthstone.enums import CardType, GameTag, PlayState, Step, Zone
from hslog import LogParser
from hslog.export import FriendlyPlayerExporter

from .capture import _CREATE_GAME_MARKER, TolerantExporter, _player_names
from .cardevents import extract_card_events
from .cards import HeroClassResolver

_FLAG_TAGS = (
    (GameTag.TAUNT, "taunt"),
    (GameTag.DIVINE_SHIELD, "divine shield"),
    (GameTag.STEALTH, "stealth"),
    (GameTag.FROZEN, "frozen"),
    (GameTag.WINDFURY, "windfury"),
    (GameTag.EXHAUSTED, "exhausted"),
    (GameTag.POISONOUS, "poisonous"),
    (GameTag.REBORN, "reborn"),
    (GameTag.LIFESTEAL, "lifesteal"),
    (GameTag.RUSH, "rush"),
    (GameTag.IMMUNE, "immune"),
    (GameTag.UNTOUCHABLE, "untargetable"),
)


def _entity_flags(entity: Any) -> list[str]:
    return [label for tag, label in _FLAG_TAGS if entity.tags.get(tag)]


_TEXT_MARKUP_RE = re.compile(r"<[^>]+>|\[x\]")


def card_text(card: dict) -> str | None:
    """Rules text cleaned of HearthstoneJSON markup ($/# damage markers, tags)."""
    text = card.get("text")
    if not text:
        return None
    text = _TEXT_MARKUP_RE.sub("", text).replace("$", "").replace("#", "")
    return " ".join(text.split()) or None


def snapshot_delta(prev: dict, curr: dict) -> str | None:
    """Compare snapshots, return a one-liner if hand/board changed mid-turn.

    Returns None if nothing meaningful changed (same hand/board names on both
    sides). Otherwise returns e.g. "== UPDATE (turn 5) — my hand gained: X | opp
    board gained: Y". Only compares hand/board *names* (Counter multiset), not
    full details — assumes live.json already has the full card info.
    """
    from collections import Counter

    if not prev or not curr:
        return None

    me_prev = prev.get("me", {})
    me_curr = curr.get("me", {})
    opp_prev = prev.get("opp", {})
    opp_curr = curr.get("opp", {})

    # Friendly side: check hand + board names changed
    my_hand_prev = Counter(c["name"] for c in me_prev.get("hand", []))
    my_hand_curr = Counter(c["name"] for c in me_curr.get("hand", []))
    my_board_prev = Counter(c["name"] for c in me_prev.get("board", []))
    my_board_curr = Counter(c["name"] for c in me_curr.get("board", []))

    my_hand_gained = my_hand_curr - my_hand_prev
    my_hand_lost = my_hand_prev - my_hand_curr
    my_board_gained = my_board_curr - my_board_prev
    my_board_lost = my_board_prev - my_board_curr

    # Opponent side: check hand count + board names changed
    opp_hand_count_prev = opp_prev.get("hand_hidden", 0) + len(opp_prev.get("hand", []))
    opp_hand_count_curr = opp_curr.get("hand_hidden", 0) + len(opp_curr.get("hand", []))
    opp_board_prev = Counter(c["name"] for c in opp_prev.get("board", []))
    opp_board_curr = Counter(c["name"] for c in opp_curr.get("board", []))

    opp_board_gained = opp_board_curr - opp_board_prev
    opp_board_lost = opp_board_prev - opp_board_curr

    # Build output if anything changed
    parts = []
    turn = curr.get("turn")

    # Friendly side: hand/board changes + HP/armor/weapon/secrets
    if my_hand_gained or my_hand_lost or my_board_gained or my_board_lost:
        my_parts = []
        if my_hand_gained:
            my_parts.append(f"hand +{', '.join(sorted(my_hand_gained.elements()))}")
        if my_hand_lost:
            my_parts.append(f"hand -{', '.join(sorted(my_hand_lost.elements()))}")
        if my_board_gained:
            my_parts.append(f"board +{', '.join(sorted(my_board_gained.elements()))}")
        if my_board_lost:
            my_parts.append(f"board -{', '.join(sorted(my_board_lost.elements()))}")
        if my_parts:
            parts.append("my " + ", ".join(my_parts))

    # Friendly side: state changes (HP, armor, weapon, secrets)
    my_hp_prev = me_prev.get("hp")
    my_hp_curr = me_curr.get("hp")
    my_armor_prev = me_prev.get("armor")
    my_armor_curr = me_curr.get("armor")
    my_weapon_prev = me_prev.get("weapon")
    my_weapon_curr = me_curr.get("weapon")
    my_secrets_prev = me_prev.get("secrets")
    my_secrets_curr = me_curr.get("secrets")

    if my_hp_prev != my_hp_curr or my_armor_prev != my_armor_curr:
        hp_change = f"hp {my_hp_prev}→{my_hp_curr}" if my_hp_prev != my_hp_curr else ""
        armor_change = f"armor {my_armor_prev}→{my_armor_curr}" if my_armor_prev != my_armor_curr else ""
        state_parts = [x for x in [hp_change, armor_change] if x]
        if state_parts:
            parts.append("my " + ", ".join(state_parts))
    my_atk_prev = me_prev.get("attack") or 0
    my_atk_curr = me_curr.get("attack") or 0
    if my_atk_prev != my_atk_curr:
        parts.append(f"my attack {my_atk_prev}→{my_atk_curr}")
    if (my_weapon_prev is None and my_weapon_curr is not None) or \
       (my_weapon_prev is not None and my_weapon_curr is None) or \
       (my_weapon_prev and my_weapon_curr and my_weapon_prev != my_weapon_curr):
        if my_weapon_curr:
            durability = my_weapon_curr.get("durability")
            parts.append(f"my weapon: {my_weapon_curr['name']} {durability} durability")
        else:
            parts.append("my weapon: unequipped")
    if my_secrets_prev != my_secrets_curr:
        delta = (my_secrets_curr or 0) - (my_secrets_prev or 0)
        parts.append(f"my secrets {'+' if delta > 0 else ''}{delta}")

    # Opponent side: hand count + board changes
    if opp_hand_count_curr != opp_hand_count_prev or opp_board_gained or opp_board_lost:
        opp_parts = []
        if opp_hand_count_curr != opp_hand_count_prev:
            delta = opp_hand_count_curr - opp_hand_count_prev
            opp_parts.append(f"hand {'+' if delta > 0 else ''}{delta}")
        if opp_board_gained:
            opp_parts.append(f"board +{', '.join(sorted(opp_board_gained.elements()))}")
        if opp_board_lost:
            opp_parts.append(f"board -{', '.join(sorted(opp_board_lost.elements()))}")
        if opp_parts:
            parts.append("opp " + ", ".join(opp_parts))

    # Opponent side: state changes (HP, armor, weapon, secrets)
    opp_hp_prev = opp_prev.get("hp")
    opp_hp_curr = opp_curr.get("hp")
    opp_armor_prev = opp_prev.get("armor")
    opp_armor_curr = opp_curr.get("armor")
    opp_weapon_prev = opp_prev.get("weapon")
    opp_weapon_curr = opp_curr.get("weapon")
    opp_secrets_prev = opp_prev.get("secrets")
    opp_secrets_curr = opp_curr.get("secrets")

    if opp_hp_prev != opp_hp_curr or opp_armor_prev != opp_armor_curr:
        hp_change = f"hp {opp_hp_prev}→{opp_hp_curr}" if opp_hp_prev != opp_hp_curr else ""
        armor_change = f"armor {opp_armor_prev}→{opp_armor_curr}" if opp_armor_prev != opp_armor_curr else ""
        state_parts = [x for x in [hp_change, armor_change] if x]
        if state_parts:
            parts.append("opp " + ", ".join(state_parts))
    if (opp_weapon_prev is None and opp_weapon_curr is not None) or \
       (opp_weapon_prev is not None and opp_weapon_curr is None) or \
       (opp_weapon_prev and opp_weapon_curr and opp_weapon_prev != opp_weapon_curr):
        if opp_weapon_curr:
            durability = opp_weapon_curr.get("durability")
            parts.append(f"opp weapon: {opp_weapon_curr['name']} {durability} durability")
        else:
            parts.append("opp weapon: unequipped")
    if opp_secrets_prev != opp_secrets_curr:
        delta = (opp_secrets_curr or 0) - (opp_secrets_prev or 0)
        parts.append(f"opp secrets {'+' if delta > 0 else ''}{delta}")

    if not parts:
        return None

    turn_str = f"(turn {turn})" if turn else ""
    return f"== UPDATE {turn_str} — {' | '.join(parts)}"


def pending_discovers(
    tree: Any,
    resolver: HeroClassResolver,
    friendly_id: int | None,
) -> list[dict]:
    """Return all unresolved Discover choice details, or empty list.

    Checks for live (unresolved) Choices packets where:
    - type == ChoiceType.GENERAL (Discover, not Mulligan)
    - No matching SendChoices has been received yet
    - Owner matches friendly_id (skip opponent discovers)

    Returns list of {"choice_id": id, "source": <name>, "options": [<card>, ...]}
    dicts, one per unresolved friendly-controlled discover. Cards in options
    have {name, cost, type, text} from resolver.card().
    """
    from hearthstone.enums import ChoiceType
    from hslog.packets import Choices, SendChoices

    if friendly_id is None:
        return []

    try:
        # Build a map of resolved choice ids (those with matching SendChoices)
        resolved_ids = set()
        for send in tree.recursive_iter(SendChoices):
            resolved_ids.add(send.id)

        # Find all unresolved Discovers
        try:
            game = TolerantExporter(tree).export().game
        except Exception:
            return []

        results = []
        for choice in tree.recursive_iter(Choices):
            if choice.type != ChoiceType.GENERAL:
                continue  # Skip non-Discover choices (e.g., Mulligan)
            if choice.id in resolved_ids:
                continue  # Already resolved

            # Check if this choice belongs to the friendly player
            try:
                source_entity = game.find_entity_by_id(choice.source)
                controller = source_entity.tags.get(GameTag.CONTROLLER)
                if controller != friendly_id:
                    continue  # Opponent's discover, skip
            except Exception:
                continue

            # Resolve the source and choices to card dicts
            try:
                source_card = resolver.card(source_entity.card_id)
                source_name = source_card.get("name", str(choice.source))
            except Exception:
                source_name = str(choice.source)

            options = []
            for opt_id in choice.choices:
                try:
                    opt_entity = game.find_entity_by_id(opt_id)
                    opt_card = resolver.card(opt_entity.card_id)
                    options.append({
                        "name": opt_card.get("name", str(opt_id)),
                        "cost": opt_card.get("cost"),
                        "type": opt_card.get("type"),
                        "text": card_text(opt_card),
                    })
                except Exception:
                    pass

            if options:
                results.append({
                    "choice_id": choice.id,
                    "source": source_name,
                    "options": options,
                })

        return results
    except Exception:
        return []


def snapshot_from_tree(
    tree: Any,
    resolver: HeroClassResolver,
    names: dict[int, str] | None = None,
    deck_counts: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    """Current-game snapshot dict, or None if the game can't be exported yet.

    deck_counts is the friendly decklist as {card_id: copies}; when given, the
    snapshot includes which cards are probably still in the deck (decklist
    minus revealed friendly cards — created copies can over-subtract, which
    errs on the conservative side).
    """
    names = names or {}
    try:
        friendly_id = FriendlyPlayerExporter(tree).export()
    except Exception:
        friendly_id = None
    try:
        game = TolerantExporter(tree).export().game
    except Exception:
        return None
    if not game.players:
        return None

    raw_turn = game.tags.get(GameTag.TURN, 0)
    step = game.tags.get(GameTag.STEP)
    snapshot: dict[str, Any] = {
        "turn": (raw_turn + 1) // 2,
        "raw_turn": raw_turn,
        "phase": "mulligan" if step == Step.BEGIN_MULLIGAN else "playing",
        "whose_turn": None,
        "game_over": None,
    }

    for player in game.players:
        side = "me" if player.player_id == friendly_id else "opp"
        pid = player.player_id
        if player.tags.get(GameTag.CURRENT_PLAYER):
            snapshot["whose_turn"] = side
        playstate = player.tags.get(GameTag.PLAYSTATE, 0)
        if side == "me" and playstate in (int(PlayState.WON), int(PlayState.LOST), int(PlayState.TIED)):
            snapshot["game_over"] = PlayState(playstate).name

        hero_hp = hero_armor = None
        hero_dead = False
        hero_class = weapon = None
        hero_atk = 0  # from weapon or temp buffs (e.g. Searing Fissure's +3)
        hand: list[dict[str, Any]] = []
        board: list[dict[str, Any]] = []
        deck_remaining = hidden_in_hand = secrets = 0
        seen_from_deck: dict[str, int] = {}  # cards revealed in other zones
        seen_in_deck: dict[str, int] = {}  # cards with card_id still in Zone.DECK (revealed)

        for entity in game.entities:
            if entity.tags.get(GameTag.CONTROLLER) != pid:
                continue
            card_id = getattr(entity, "card_id", None)
            card = resolver.card(card_id)
            card_type = entity.tags.get(GameTag.CARDTYPE)
            zone = entity.zone

            if card_id and card_type not in (CardType.HERO, CardType.HERO_POWER):
                if zone == Zone.DECK:
                    seen_in_deck[card_id] = seen_in_deck.get(card_id, 0) + 1
                elif zone != Zone.DECK:
                    seen_from_deck[card_id] = seen_from_deck.get(card_id, 0) + 1

            if card_type == CardType.HERO and entity.tags.get(GameTag.HEALTH):
                if zone == Zone.PLAY:
                    hero_hp = max(0, entity.tags.get(GameTag.HEALTH, 30) - entity.tags.get(GameTag.DAMAGE, 0))
                    hero_armor = entity.tags.get(GameTag.ARMOR, 0)
                    hero_atk = entity.tags.get(GameTag.ATK, 0)
                    hero_class = resolver.player_class(card_id)
                elif zone == Zone.GRAVEYARD and hero_hp is None:
                    hero_dead = True  # dead heroes read 0hp, not "None"
                    hero_class = hero_class or resolver.player_class(card_id)
            elif card_type == CardType.WEAPON and zone == Zone.PLAY:
                # Modern cards store durability in HEALTH; older ones in DURABILITY.
                base = max(entity.tags.get(GameTag.DURABILITY, 0), entity.tags.get(GameTag.HEALTH, 0))
                weapon = {
                    "name": card.get("name", card_id),
                    "atk": entity.tags.get(GameTag.ATK, 0),
                    "durability": base - entity.tags.get(GameTag.DAMAGE, 0),
                    "text": card_text(card),
                }
            elif zone == Zone.HAND:
                if card_id:
                    hand.append({
                        "name": card.get("name", card_id),
                        "cost": entity.tags.get(GameTag.COST, card.get("cost")),
                        "type": card.get("type"),
                        "text": card_text(card),
                        "pos": entity.tags.get(GameTag.ZONE_POSITION, 0),
                    })
                else:
                    hidden_in_hand += 1
            elif zone == Zone.PLAY and card_type in (CardType.MINION, CardType.LOCATION):
                flags = _entity_flags(entity)
                if entity.tags.get(GameTag.DAMAGE, 0) > 0:
                    flags.append("damaged")  # e.g. a legal Torch target
                board.append({
                    "name": card.get("name", card_id or "?"),
                    "atk": entity.tags.get(GameTag.ATK, 0),
                    "health": (entity.tags.get(GameTag.HEALTH, 0)
                               - entity.tags.get(GameTag.DAMAGE, 0)),
                    "pos": entity.tags.get(GameTag.ZONE_POSITION, 0),
                    "flags": flags,
                    "location": card_type == CardType.LOCATION,
                    "text": card_text(card),
                })
            elif zone == Zone.DECK:
                deck_remaining += 1
            elif zone == Zone.SECRET and entity.tags.get(GameTag.SECRET):
                secrets += 1

        if hero_hp is None and hero_dead:
            hero_hp, hero_armor = 0, 0
        hand.sort(key=lambda c: c["pos"])
        board.sort(key=lambda c: c["pos"])
        resources = player.tags.get(GameTag.RESOURCES, 0) + player.tags.get(GameTag.TEMP_RESOURCES, 0)
        snapshot[side] = {
            "name": names.get(pid) or player.name,
            "class": hero_class,
            "hp": hero_hp,
            "armor": hero_armor,
            "attack": hero_atk,
            "mana": resources - player.tags.get(GameTag.RESOURCES_USED, 0),
            "mana_max": resources,
            "weapon": weapon,
            "hand": hand,
            "hand_hidden": hidden_in_hand,
            "board": board,
            "deck_remaining": deck_remaining,
            "secrets": secrets,
        }
        if side == "me" and deck_counts:
            # Compute total observed copies of each card across all zones
            total_seen = {}
            for card_id in set(list(seen_from_deck.keys()) + list(seen_in_deck.keys())):
                total_seen[card_id] = seen_from_deck.get(card_id, 0) + seen_in_deck.get(card_id, 0)

            # Cards still in deck (decklist minus what we've seen)
            left = []
            for card_id, count in deck_counts.items():
                remaining = count - seen_from_deck.get(card_id, 0)
                if remaining > 0:
                    card = resolver.card(card_id)
                    left.append({"name": card.get("name", card_id),
                                 "cost": card.get("cost"), "count": remaining,
                                 "type": card.get("type"), "text": card_text(card)})
            left.sort(key=lambda c: (c["cost"] if c["cost"] is not None else 99, c["name"]))
            snapshot[side]["deck_cards_left"] = left

            # Extra cards beyond the decklist (shuffled/generated/created)
            extra = []
            for card_id in total_seen:
                if total_seen[card_id] > deck_counts.get(card_id, 0):
                    card = resolver.card(card_id)
                    count = total_seen[card_id] - deck_counts.get(card_id, 0)
                    extra.append({"name": card.get("name", card_id),
                                  "cost": card.get("cost"), "count": count,
                                  "type": card.get("type"), "text": card_text(card)})
            if extra:
                extra.sort(key=lambda c: (c["cost"] if c["cost"] is not None else 99, c["name"]))
                snapshot[side]["deck_extra"] = extra

    if "me" not in snapshot or "opp" not in snapshot:
        return None

    try:
        played = [
            {"name": resolver.name(row["card_id"]) or row["card_id"],
             "count": row["played"], "turn": row["first_played_turn"]}
            for row in extract_card_events(tree, friendly_id)
            if not row["friendly"] and row["played"]
        ]
        played.sort(key=lambda r: (r["turn"] is None, r["turn"]))
        snapshot["opp"]["played"] = played
    except Exception:
        snapshot["opp"]["played"] = []
    return snapshot


class LiveGameTail:
    """Tail one Power.log, keeping only the current game's lines buffered."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.partial = ""
        self.lines: list[str] = []
        self.game_no = 0  # bumps on each CREATE_GAME, so callers can refresh per-game state
        self.last_tree: Any = None  # stash the last-parsed game tree for pending_discovers()
        self.last_friendly_id: int | None = None  # friendly player id, computed once per poll

    def poll(self) -> bool:
        """Read new bytes; True if the current-game buffer changed."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return False
        if size < self.offset:
            self.offset = 0
            self.partial = ""
            self.lines = []
        if size == self.offset:
            return False
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self.offset)
            chunk = f.read()
            self.offset = f.tell()
        text = self.partial + chunk
        new_lines = text.split("\n")
        self.partial = new_lines.pop()
        changed = False
        for line in new_lines:
            if not line:
                continue
            if _CREATE_GAME_MARKER in line:
                self.lines = []  # a new game starts; drop the previous one
                self.game_no += 1
            self.lines.append(line + "\n")
            changed = True
        return changed

    def snapshot(
        self,
        resolver: HeroClassResolver,
        deck_counts: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        if not self.lines:
            return None
        parser = LogParser()
        for line in self.lines:
            try:
                parser.read_line(line)
            except Exception:
                continue
        if not parser.games:
            return None
        self.last_tree = parser.games[-1]
        # Compute friendly_id once, to avoid re-deriving it incorrectly elsewhere
        try:
            self.last_friendly_id = FriendlyPlayerExporter(self.last_tree).export()
        except Exception:
            self.last_friendly_id = None
        return snapshot_from_tree(
            self.last_tree, resolver,
            names=_player_names(parser), deck_counts=deck_counts,
        )


def write_snapshot_json(snapshot: dict[str, Any], path: Path) -> None:
    """Atomic write so readers never see a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _card_line(cards: list[dict[str, Any]]) -> str:
    parts = []
    for c in cards:
        flags = "".join(f" [{f}]" for f in c.get("flags", []) if f != "exhausted")
        if "cost" in c:
            parts.append(f"{c['name']}({c['cost']})")
        else:
            loc = " (location)" if c.get("location") else ""
            parts.append(f"{c['name']} {c['atk']}/{c['health']}{flags}{loc}")
    return ", ".join(parts) or "(empty)"


def _hp(side: dict[str, Any]) -> str:
    hp = side.get("hp")
    hp_str = "?" if hp is None else str(hp)
    armor = f"(+{side['armor']})" if side.get("armor") else ""
    return f"{hp_str}hp{armor}"


def format_snapshot(snap: dict[str, Any]) -> str:
    me, opp = snap["me"], snap["opp"]

    if snap.get("phase") == "mulligan":
        opp_class = opp.get("class") or "?"
        # Everything on the header line: downstream notification watchers can
        # deliver matched lines in separate batches, so a detail line under the
        # header may arrive minutes late (or read as a separate event).
        return f"== MULLIGAN — {len(me['hand'])} card(s) dealt vs {opp_class}: {_card_line(me['hand'])}"

    whose = "your turn" if snap.get("whose_turn") == "me" else "opponent's turn"
    my_atk = f" {me['attack']} atk," if me.get("attack") else ""
    lines = [
        f"== TURN {snap['turn']} [raw {snap['raw_turn']}] ({whose}) — ME {_hp(me)}{my_atk} "
        f"{me['mana']}/{me['mana_max']} mana vs OPP {_hp(opp)}"
    ]
    weapon = f" | weapon: {me['weapon']['name']} {me['weapon']['atk']}/{me['weapon']['durability']}" if me.get("weapon") else ""
    lines.append(f"   my hand ({len(me['hand'])}): {_card_line(me['hand'])}")
    lines.append(f"   my board: {_card_line(me['board'])}{weapon}")
    opp_weapon = f" | weapon: {opp['weapon']['name']} {opp['weapon']['atk']}/{opp['weapon']['durability']}" if opp.get("weapon") else ""
    opp_extra = f" | opp hand: {opp['hand_hidden'] + len(opp['hand'])} cards"
    if opp["secrets"]:
        opp_extra += f", secrets: {opp['secrets']}"
    lines.append(f"   opp board: {_card_line(opp['board'])}{opp_weapon}{opp_extra}")
    deck_left = me.get("deck_cards_left")
    if deck_left:
        counts = ", ".join(
            f"{c['name']}({c['cost']})" + (f" x{c['count']}" if c["count"] > 1 else "")
            for c in deck_left
        )
        lines.append(f"   my deck ({me['deck_remaining']} left): {counts}")
    deck_extra = me.get("deck_extra")
    if deck_extra:
        counts = ", ".join(
            f"{c['name']}({c['cost']})" + (f" x{c['count']}" if c["count"] > 1 else "")
            for c in deck_extra
        )
        lines.append(f"   extra in deck (generated/shuffled): {counts}")
    if opp.get("played"):
        history = ", ".join(
            f"{p['name']}" + (f" x{p['count']}" if p["count"] > 1 else "")
            for p in opp["played"]
        )
        lines.append(f"   opp has played: {history}")
    if snap.get("game_over"):
        lines.append(f"== GAME OVER: {snap['game_over']}")
    return "\n".join(lines)
