#!/usr/bin/env python3
"""Fetch current Hearthstone deck candidates as deckstrings.

This gathers a batch of current decklists (name, class, deckstring) from a public
deck site so the recommender has real, up-to-date candidates to rank against a
collection. Deck codes are the stable, importable representation, so this scraper
only extracts them plus light metadata.

Sites change their HTML often. This uses conservative, well-documented patterns
and skips anything it can't parse cleanly rather than guessing. If the layout
changes and nothing is found, assemble meta_decks.json by hand (see the skill's
references) — the ranking script does not depend on this fetcher.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "Mozilla/5.0 hearthstone-deck-recommender/1.0 (+meta-deck-fetch)"

# Cap on a fetched deck page, so a misbehaving or hostile endpoint cannot
# exhaust memory. Real listing/detail pages are well under 1 MB.
MAX_PAGE_BYTES = 5 * 1024 * 1024

# Base64 Hearthstone deckstrings start with the reserved(0)+version(1) bytes and
# are long. Anchoring on "AAE" avoids matching short unrelated base64 blobs.
DECKSTRING_RE = re.compile(r"AAE[A-Za-z0-9+/]{40,}={0,2}")

CLASS_SLUGS = {
    "death-knight": "Death Knight",
    "demon-hunter": "Demon Hunter",
    "druid": "Druid",
    "hunter": "Hunter",
    "mage": "Mage",
    "paladin": "Paladin",
    "priest": "Priest",
    "rogue": "Rogue",
    "shaman": "Shaman",
    "warlock": "Warlock",
    "warrior": "Warrior",
}

DEFAULT_LISTINGS = [
    "https://hearthstone-decks.net/standard-decks/",
]
DETAIL_LINK_RE = re.compile(r"https://hearthstone-decks\.net/[a-z0-9%\-]+/")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def fetch(url: str, timeout: int = 30) -> str:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"URL must be http(s), got: {url[:40]}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- scheme validated above
        data = response.read(MAX_PAGE_BYTES + 1)
    if len(data) > MAX_PAGE_BYTES:
        raise ValueError(
            f"Deck page {url} exceeded the {MAX_PAGE_BYTES // (1024 * 1024)} MB size limit; aborting"
        )
    return data.decode("utf-8", "replace")


SENSITIVE_PATH_ROOTS = (
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
    "/boot", "/sys", "/proc", "/dev", "/root", "/var",
)


def is_sensitive_out_path(path: str, home: Path | None = None) -> bool:
    """True for output paths that could clobber system files or home dotfiles."""
    resolved = Path(path).expanduser().resolve()
    for root in SENSITIVE_PATH_ROOTS:
        root_path = Path(root)
        if resolved == root_path or root_path in resolved.parents:
            return True
    home = (home or Path.home()).resolve()
    try:
        relative = resolved.relative_to(home)
    except ValueError:
        return False
    return any(part.startswith(".") for part in relative.parts)


def guess_class(url: str, title: str) -> str | None:
    haystack = f"{url} {title}".lower()
    for slug, name in CLASS_SLUGS.items():
        if slug in haystack or slug.replace("-", " ") in haystack:
            return name
    return None


def clean_title(raw: str, url: str) -> str:
    title = html.unescape(raw or "").strip()
    # Strip common site suffixes like " - Hearthstone-Decks.net".
    title = re.split(r"\s+[-|–]\s+", title)[0].strip()
    if title:
        return title
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return slug.replace("-", " ").title()


def collect_detail_urls(listing_urls: list[str], limit: int) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for listing in listing_urls:
        try:
            page = fetch(listing)
        except Exception as exc:
            print(f"WARNING: could not fetch listing {listing}: {exc}", file=sys.stderr)
            continue
        for link in DETAIL_LINK_RE.findall(page):
            # Detail pages have a descriptive slug; skip obvious non-decks.
            slug = link.rstrip("/").rsplit("/", 1)[-1]
            if not re.search(r"\d", slug) and "legend" not in slug:
                continue
            if any(bad in link for bad in ("/category/", "/standard-decks", "/wild-decks", "/tag/", "/author/")):
                continue
            if link in seen_set:
                continue
            seen_set.add(link)
            seen.append(link)
    return seen[: limit * 2]  # over-collect; some pages may fail to parse


def extract_deck(url: str) -> dict[str, Any] | None:
    try:
        page = fetch(url)
    except Exception as exc:
        print(f"WARNING: could not fetch {url}: {exc}", file=sys.stderr)
        return None
    codes = []
    for m in DECKSTRING_RE.finditer(page):
        code = html.unescape(m.group(0)).strip()
        if code not in codes:
            codes.append(code)
    if not codes:
        return None
    title_match = TITLE_RE.search(page)
    title = clean_title(title_match.group(1) if title_match else "", url)
    # Pick the longest code (full 30-card decks encode longer than snippets).
    code = max(codes, key=len)
    return {
        "name": title,
        "class": guess_class(url, title),
        "format": "standard",
        "source": url,
        "deckstring": code,
    }


def fetch_decks(
    listings: list[str] | None = None,
    *,
    limit: int = 40,
    sleep: float = 0.3,
    one_per_class: bool = False,
) -> list[dict[str, Any]]:
    """Crawl deck-site listings and return current decks as {name,class,deckstring,...}."""
    detail_urls = collect_detail_urls(listings or DEFAULT_LISTINGS, limit)
    decks: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    seen_classes: set[str] = set()
    for url in detail_urls:
        if len(decks) >= limit:
            break
        deck = extract_deck(url)
        time.sleep(max(0.0, sleep))
        if not deck:
            continue
        if deck["deckstring"] in seen_codes:
            continue
        if one_per_class:
            cls = deck.get("class") or "?"
            if cls in seen_classes:
                continue
            seen_classes.add(cls)
        deck.setdefault("source_rank", len(decks) + 1)
        seen_codes.add(deck["deckstring"])
        decks.append(deck)
    return decks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch current Standard deck candidates as deckstrings.")
    parser.add_argument("--out", default="meta_decks.json", help="Output JSON path")
    parser.add_argument("--limit", type=int, default=40, help="Max decks to collect")
    parser.add_argument("--listing", action="append", help="Listing URL(s) to crawl (repeatable)")
    parser.add_argument("--sleep", type=float, default=0.3, help="Delay between requests (be polite)")
    parser.add_argument("--one-per-class", action="store_true", help="Keep only the first deck per class for variety")
    parser.add_argument("--force", action="store_true", help="Allow writing --out to system paths or home dotfiles")
    args = parser.parse_args(argv)

    if is_sensitive_out_path(args.out) and not args.force:
        print(
            f"ERROR: refusing to write to sensitive path {args.out!r} "
            "(system directory or home dotfile); pass --force to override.",
            file=sys.stderr,
        )
        return 2

    decks = fetch_decks(
        args.listing or None,
        limit=args.limit,
        sleep=args.sleep,
        one_per_class=args.one_per_class,
    )
    if not decks:
        print("ERROR: no decks found; site layout may have changed.", file=sys.stderr)
        return 2

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"decks": decks}, f, indent=2)
    print(f"Saved {len(decks)} decks to {args.out}")
    by_class: dict[str, int] = {}
    for d in decks:
        by_class[d.get("class") or "?"] = by_class.get(d.get("class") or "?", 0) + 1
    print("By class:", ", ".join(f"{k}={v}" for k, v in sorted(by_class.items())))
    return 0 if decks else 2


if __name__ == "__main__":
    raise SystemExit(main())
