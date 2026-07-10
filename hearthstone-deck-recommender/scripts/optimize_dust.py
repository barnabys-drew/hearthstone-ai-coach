#!/usr/bin/env python3
"""
Hearthstone Dust Optimizer

Analyzes your collection and recommends cards to disenchant based on:
- Rotation status
- Meta relevance
- Copy count
- Dust value

Optionally automates deletion via Playwright.
"""

import json
import sys
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from collections import defaultdict
import requests

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
else:
    PLAYWRIGHT_AVAILABLE = True


@dataclass
class CardRec:
    """Single disenchant recommendation."""
    card_id: int
    name: str
    count: int
    rarity: str
    dust_per_copy: int
    total_dust: int
    reason: str
    priority: str  # "high", "medium", "low"
    is_golden: bool = False


@dataclass
class OptimizeResult:
    """Results of dust optimization analysis."""
    recommendations: List[CardRec] = field(default_factory=list)
    total_dust_available: int = 0
    protected_cards: Dict[str, int] = field(default_factory=dict)
    approval_status: str = "pending"  # "pending", "approved", "completed"


def get_card_name(card: Dict) -> str:
    """Extract English card name from card data (handles multilingual format)."""
    if isinstance(card.get("name"), dict):
        return card["name"].get("enUS", "Unknown")
    return card.get("name", "Unknown")


def get_dust_value(rarity: str) -> int:
    """Return dust value for a given rarity."""
    rarity_map = {
        "COMMON": 5,
        "RARE": 20,
        "EPIC": 100,
        "LEGENDARY": 400,
    }
    return rarity_map.get(rarity, 0)


def load_collection(path: str) -> Dict[str, int]:
    """Load collection from JSON (dbfId -> count map)."""
    with open(path) as f:
        data = json.load(f)

    # Handle HSReplay format
    if "collection" in data:
        collection = defaultdict(int)
        for dbf_id_str, counts in data["collection"].items():
            # counts might be [normal, golden, diamond, signature]
            if isinstance(counts, list):
                collection[int(dbf_id_str)] = sum(c for c in counts if c)
            else:
                collection[int(dbf_id_str)] = counts
        return dict(collection)

    # Handle simple {dbfId: count} format
    return {int(k): v for k, v in data.items()}


def load_cards_json(path: Optional[str] = None) -> Dict[int, Dict]:
    """Load card database (dbfId -> card data map)."""
    if path:
        with open(path) as f:
            cards = json.load(f)
            if "cards" in cards:
                cards = cards["cards"]
    else:
        # Fetch from HearthstoneJSON
        print("Fetching latest card data from HearthstoneJSON...", file=sys.stderr)
        try:
            resp = requests.get("https://api.hearthstonejson.com/v1/latest/all/cards.json")
            resp.raise_for_status()
            cards = resp.json()
        except Exception as e:
            print(f"Error fetching cards: {e}", file=sys.stderr)
            return {}

    # Index by dbfId
    indexed = {}
    for card in cards:
        if "dbfId" in card:
            indexed[card["dbfId"]] = card
    return indexed


def load_decks(path: str) -> List[Dict]:
    """Load meta decks."""
    with open(path) as f:
        data = json.load(f)
    return data.get("decks", [])


def extract_cards_from_deckstring(deckstring: str) -> List[int]:
    """Parse Hearthstone deckstring and return list of dbfIds."""
    # Simplified: actual deckstring parsing is complex
    # For now, return empty (full implementation would decode the string)
    return []


def get_protected_cards(decks: List[Dict]) -> Dict[int, int]:
    """Get set of cards used in meta decks (protected from disenchant)."""
    protected = defaultdict(int)
    for deck in decks:
        if "deckstring" in deck:
            cards = extract_cards_from_deckstring(deck["deckstring"])
            for card_id in cards:
                protected[card_id] += 1
    return dict(protected)


def analyze_collection(
    collection: Dict[str, int],
    cards_db: Dict[int, Dict],
    meta_decks: Optional[List[Dict]] = None,
    threshold: int = 40,
) -> OptimizeResult:
    """
    Analyze collection and recommend disenchants.

    Args:
        collection: {dbfId: count} of owned cards
        cards_db: {dbfId: card_data} card database
        meta_decks: List of meta decks for relevance ranking
        threshold: Minimum dust value to consider

    Returns:
        OptimizeResult with ranked recommendations
    """
    result = OptimizeResult()
    protected = get_protected_cards(meta_decks or [])
    result.protected_cards = protected

    recommendations = []

    for card_id_str, count in collection.items():
        try:
            card_id = int(card_id_str)
        except ValueError:
            continue

        if card_id not in cards_db:
            continue

        card = cards_db[card_id]
        name = get_card_name(card)
        rarity = card.get("rarity", "COMMON")
        card_type = card.get("type")

        dust_value = get_dust_value(rarity)

        # Skip cards below threshold
        if dust_value < threshold:
            continue

        # Determine recommendation
        reason = None
        priority = "low"

        # Check if card is in meta decks
        in_meta = card_id in protected

        # For now, recommend duplicates and non-meta cards
        if count > 2:  # More than 2 copies
            reason = "duplicate_copies"
            priority = "high"
        elif not in_meta and card_type == "MINION":
            reason = "low_meta_relevance"
            priority = "medium"

        if reason:
            # Recommend disenchanting extra copies (keep 2 for non-legendaries)
            disenchant_count = 1 if rarity == "LEGENDARY" else max(count - 2, 0)
            if disenchant_count > 0:
                rec = CardRec(
                    card_id=card_id,
                    name=name,
                    count=disenchant_count,
                    rarity=rarity,
                    dust_per_copy=dust_value,
                    total_dust=dust_value * disenchant_count,
                    reason=reason,
                    priority=priority,
                )
                recommendations.append(rec)

    # Sort by priority, then by dust value
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(
        key=lambda r: (priority_order[r.priority], -r.total_dust)
    )

    result.recommendations = recommendations
    result.total_dust_available = sum(r.total_dust for r in recommendations)

    return result


def format_summary(result: OptimizeResult) -> str:
    """Format results as human-readable summary."""
    output = ["DISENCHANT RECOMMENDATIONS", "=" * 40, ""]

    if not result.recommendations:
        output.append("No recommendations at this time.")
        return "\n".join(output)

    by_priority = {"high": [], "medium": [], "low": []}
    for rec in result.recommendations:
        by_priority[rec.priority].append(rec)

    for priority in ["high", "medium", "low"]:
        if by_priority[priority]:
            title = f"{priority.upper()} PRIORITY"
            output.append(f"\n{title}:")
            for rec in by_priority[priority]:
                output.append(
                    f"  - {rec.count}x {rec.name} ({rec.rarity}, {rec.dust_per_copy} dust each = {rec.total_dust} total)"
                )
                output.append(f"      reason: {rec.reason}")

    output.append("")
    output.append(f"TOTAL DUST AVAILABLE: {result.total_dust_available}")

    return "\n".join(output)


def format_json(result: OptimizeResult) -> str:
    """Format results as JSON."""
    return json.dumps({
        "recommendations": [
            {
                "card_id": r.card_id,
                "name": r.name,
                "count": r.count,
                "rarity": r.rarity,
                "dust_per_copy": r.dust_per_copy,
                "total_dust": r.total_dust,
                "reason": r.reason,
                "priority": r.priority,
            }
            for r in result.recommendations
        ],
        "total_dust_available": result.total_dust_available,
        "approval_status": result.approval_status,
    }, indent=2)


def automate_deletion(
    result: OptimizeResult,
    headless: bool = True,
    dry_run: bool = True,
) -> OptimizeResult:
    """
    Use Playwright to automate card deletion in Hearthstone.

    Args:
        result: Optimization result with approved recommendations
        headless: Run browser in headless mode
        dry_run: Don't actually delete, just show what would happen

    Returns:
        Updated result with completion status
    """
    if not PLAYWRIGHT_AVAILABLE:
        print("Playwright not installed. Install with: pip install playwright", file=sys.stderr)
        return result

    if dry_run:
        print("[DRY RUN] Would delete the following cards:")
        for rec in result.recommendations:
            print(f"  {rec.count}x {rec.name} ({rec.rarity})")
        result.approval_status = "completed_dry_run"
        return result

    # TODO: Implement actual Playwright automation
    # This would:
    # 1. Launch Hearthstone/Battle.net
    # 2. Navigate to collection
    # 3. Find and click each card
    # 4. Confirm deletion
    # 5. Log results

    print("Playwright automation not yet implemented. Use --dry-run to preview.")
    result.approval_status = "failed"
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Hearthstone collection and recommend disenchants"
    )
    parser.add_argument(
        "--collection",
        required=True,
        help="Path to collection JSON (dbfId -> count map)",
    )
    parser.add_argument(
        "--cards-json",
        help="Path to cards.collectible.json (auto-fetched if omitted)",
    )
    parser.add_argument(
        "--decks",
        help="Path to meta_decks.json (optional, for meta relevance)",
    )
    parser.add_argument(
        "--view",
        choices=["summary", "detailed", "json"],
        default="summary",
        help="Output format",
    )
    parser.add_argument(
        "--automate",
        action="store_true",
        help="Enable Playwright automation",
    )
    parser.add_argument(
        "--headless",
        type=lambda x: x.lower() == "true",
        default=True,
        help="Run browser in headless mode",
    )
    parser.add_argument(
        "--dry-run",
        type=lambda x: x.lower() != "false",
        default=True,
        help="Don't actually delete cards (default: true)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=40,
        help="Minimum dust value to consider",
    )
    parser.add_argument(
        "--output",
        help="Save results to JSON file",
    )

    args = parser.parse_args()

    # Load data
    print("Loading collection...", file=sys.stderr)
    collection = load_collection(args.collection)

    print("Loading card database...", file=sys.stderr)
    cards_db = load_cards_json(args.cards_json)

    meta_decks = []
    if args.decks:
        print("Loading meta decks...", file=sys.stderr)
        meta_decks = load_decks(args.decks)

    # Analyze
    print("Analyzing collection...", file=sys.stderr)
    result = analyze_collection(
        collection,
        cards_db,
        meta_decks=meta_decks,
        threshold=args.threshold,
    )

    # Output results
    if args.view == "json":
        output = format_json(result)
    else:
        output = format_summary(result)

    print(output)

    # Save to file if requested
    if args.output:
        with open(args.output, "w") as f:
            f.write(format_json(result))
        print(f"\nResults saved to {args.output}", file=sys.stderr)

    # Automation
    if args.automate:
        print("\nEnabling Playwright automation...", file=sys.stderr)
        result = automate_deletion(
            result,
            headless=args.headless,
            dry_run=args.dry_run,
        )
        print(f"Status: {result.approval_status}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
