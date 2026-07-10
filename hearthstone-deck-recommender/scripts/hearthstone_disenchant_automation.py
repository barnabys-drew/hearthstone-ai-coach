#!/usr/bin/env python3
"""
Playwright automation for Hearthstone card disenchantment.

Automates finding and disenchanting cards in the Hearthstone collection UI.
"""

import json
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass

try:
    from playwright.sync_api import sync_playwright, Browser, Page
except ImportError:
    print("Playwright not installed. Install with: pip install playwright", file=sys.stderr)
    sys.exit(1)


@dataclass
class DisenchantLog:
    """Record of a disenchant action."""
    card_name: str
    count: int
    dust_gained: int
    success: bool
    error: Optional[str] = None


class HearthstoneDisenchanter:
    """Automates card disenchantment in Hearthstone."""

    HEARTHSTONE_URL = "https://us.battle.net/games/us/hearthstone/"
    COLLECTION_PATH = "/collection"

    def __init__(self, headless: bool = False, timeout: int = 30000):
        self.headless = headless
        self.timeout = timeout
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.logs: List[DisenchantLog] = []

    def start(self):
        """Launch browser and navigate to Hearthstone collection."""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.page = self.browser.new_page()
        self.page.set_default_timeout(self.timeout)

        print(f"Navigating to {self.HEARTHSTONE_URL}...", file=sys.stderr)
        self.page.goto(self.HEARTHSTONE_URL)

    def stop(self):
        """Close browser."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def wait_for_collection_load(self):
        """Wait for collection page to load."""
        print("Waiting for collection to load...", file=sys.stderr)
        try:
            # Wait for a collection element (adjust selector as needed)
            self.page.wait_for_selector('[data-testid="collection-card"]', timeout=15000)
        except Exception as e:
            print(f"Timeout waiting for collection: {e}", file=sys.stderr)

    def search_for_card(self, card_name: str) -> bool:
        """
        Use collection search to find a card.

        Args:
            card_name: Exact or partial card name

        Returns:
            True if card was found, False otherwise
        """
        try:
            # Find search box
            search_input = self.page.query_selector('[placeholder*="Search"]')
            if not search_input:
                print(f"Could not find search box", file=sys.stderr)
                return False

            # Clear and type
            search_input.click()
            search_input.fill("")
            time.sleep(0.3)
            search_input.type(card_name, delay=50)
            time.sleep(0.5)

            # Wait for results
            self.page.wait_for_selector('[data-testid="collection-card"]', timeout=5000)
            return True
        except Exception as e:
            print(f"Error searching for {card_name}: {e}", file=sys.stderr)
            return False

    def disenchant_card(self, card_name: str, count: int = 1) -> Tuple[bool, Optional[str]]:
        """
        Find a card and disenchant it.

        Args:
            card_name: Name of card to disenchant
            count: Number of copies to disenchant

        Returns:
            (success: bool, error: str | None)
        """
        try:
            # Search for card
            if not self.search_for_card(card_name):
                return False, "Card not found in search"

            # Wait for results
            time.sleep(0.5)

            # Find first matching card (adjust selector as needed)
            card_elements = self.page.query_selector_all('[data-testid="collection-card"]')
            if not card_elements:
                return False, "No cards found in search results"

            # Click first result
            card_elements[0].click()
            time.sleep(0.3)

            # Look for disenchant button (adjust selector as needed)
            disenchant_button = self.page.query_selector('button:has-text("Disenchant")')
            if not disenchant_button:
                disenchant_button = self.page.query_selector('[aria-label*="Disenchant"]')

            if not disenchant_button:
                return False, "Disenchant button not found"

            # Click disenchant
            disenchant_button.click()
            time.sleep(0.5)

            # Handle quantity dialog if needed
            # (This depends on the exact UI implementation)

            # Confirm disenchant
            confirm_button = self.page.query_selector('button:has-text("Confirm")')
            if confirm_button:
                confirm_button.click()
                time.sleep(0.5)

            return True, None
        except Exception as e:
            return False, str(e)

    def disenchant_cards(
        self,
        cards: List[Dict[str, any]],
        dry_run: bool = False,
    ) -> List[DisenchantLog]:
        """
        Disenchant multiple cards.

        Args:
            cards: List of {name, count, dust_per_copy} dicts
            dry_run: If True, just log without actually disenchanting

        Returns:
            List of DisenchantLog entries
        """
        if not dry_run:
            self.start()
            self.wait_for_collection_load()

        for card in cards:
            name = card["name"]
            count = card.get("count", 1)
            dust_per_copy = card.get("dust_per_copy", 0)
            total_dust = dust_per_copy * count

            if dry_run:
                print(f"[DRY RUN] Would disenchant {count}x {name} for {total_dust} dust")
                self.logs.append(DisenchantLog(
                    card_name=name,
                    count=count,
                    dust_gained=total_dust,
                    success=True,
                ))
            else:
                print(f"Disenchanting {count}x {name}...", file=sys.stderr)
                success, error = self.disenchant_card(name, count)
                self.logs.append(DisenchantLog(
                    card_name=name,
                    count=count,
                    dust_gained=total_dust if success else 0,
                    success=success,
                    error=error,
                ))

                if not success:
                    print(f"  ERROR: {error}", file=sys.stderr)
                else:
                    print(f"  SUCCESS: +{total_dust} dust", file=sys.stderr)

        if not dry_run:
            self.stop()

        return self.logs

    def print_summary(self):
        """Print summary of disenchant operations."""
        successful = [log for log in self.logs if log.success]
        failed = [log for log in self.logs if not log.success]

        total_dust = sum(log.dust_gained for log in successful)

        print("\n" + "=" * 50)
        print("DISENCHANT SUMMARY")
        print("=" * 50)
        print(f"Total cards processed: {len(self.logs)}")
        print(f"Successful: {len(successful)}")
        print(f"Failed: {len(failed)}")
        print(f"Total dust gained: {total_dust}")

        if failed:
            print("\nFailed disenchants:")
            for log in failed:
                print(f"  - {log.card_name}: {log.error}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Automate Hearthstone card disenchantment via Playwright"
    )
    parser.add_argument(
        "--cards",
        required=True,
        help="JSON file with disenchant list (array of {name, count, dust_per_copy})",
    )
    parser.add_argument(
        "--headless",
        type=lambda x: x.lower() != "false",
        default=False,
        help="Run browser in headless mode",
    )
    parser.add_argument(
        "--dry-run",
        type=lambda x: x.lower() != "false",
        default=True,
        help="Don't actually disenchant (default: true)",
    )

    args = parser.parse_args()

    # Load cards to disenchant
    with open(args.cards) as f:
        cards = json.load(f)

    # Run automation
    disenchanter = HearthstoneDisenchanter(headless=args.headless)
    disenchanter.disenchant_cards(cards, dry_run=args.dry_run)
    disenchanter.print_summary()


if __name__ == "__main__":
    main()
