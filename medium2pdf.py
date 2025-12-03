#!/usr/bin/env python3
"""
medium2pdf - archive a medium author's posts as pdfs
"""

import argparse
import asyncio
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

try:
    from playwright.async_api import async_playwright
    from playwright.async_api import TimeoutError as PWTimeout
except ImportError:
    print("ERROR: Playwright is not installed. Run:")
    print("    python -m pip install playwright")
    print("    python -m playwright install chromium")
    sys.exit(1)

try:
    from pypdf import PdfReader, PdfWriter
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False




# Phrases Cloudflare uses on its challenge interstitials.
CF_TITLES = (
    "Just a moment",
    "Performing security verification",
    "Attention Required",
    "Checking your browser",
)


# --------------------------- cloudflare handling ---------------------------

async def wait_past_cloudflare(page, max_seconds: int = 45) -> bool:
    """Poll the page until the Cloudflare challenge clears.
    Returns True if real content is detected, False on timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max_seconds
    announced = False

    while loop.time() < deadline:
        try:
            title = await page.title()
        except Exception:
            title = ""

        on_challenge = any(s in title for s in CF_TITLES)
        if on_challenge:
            if not announced:
                print("    (Cloudflare challenge - waiting for it to clear...)")
                announced = True
            await asyncio.sleep(2)
            continue

        # Title is no longer the challenge - confirm real content is there.
        try:
            await page.wait_for_selector("article", timeout=3_000)
            return True
        except PWTimeout:
            await asyncio.sleep(1)

    return False



# --------------------------- discovery ---------------------------

async def discover_article_urls(page, profile_url: str,
                                 max_scrolls: int = 300,
                                 scroll_pause: float = 1.5) -> list[str]:
    print(f"[+] Loading profile: {profile_url}")
    await page.goto(profile_url, wait_until="domcontentloaded", timeout=60_000)
    await wait_past_cloudflare(page, max_seconds=30)

    try:
        await page.wait_for_function(
            "Array.from(document.querySelectorAll('a[href]'))"
            ".some(a => /-[a-f0-9]{12}(\\?|$)/.test(a.href))",
            timeout=20_000,
        )
    except PWTimeout:
        print("[!] No article-shaped links found. Is the URL a public Medium profile?")
        return []

    seen: set[str] = set()
    last_count = 0
    stable = 0

    for _ in range(max_scrolls):
        hrefs = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]'))"
            ".map(a => a.href)"
            ".filter(h => /-[a-f0-9]{12}(\\?|$)/.test(h))"
        )
        for h in hrefs:
            seen.add(h.split("?")[0].split("#")[0])

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(scroll_pause)

        if len(seen) == last_count:
            stable += 1
            if stable >= 4:
                print(f"[+] Reached end of feed ({len(seen)} articles).")
                break
        else:
            stable = 0
            print(f"    discovered {len(seen)} so far...")
        last_count = len(seen)

    return sorted(seen)



# --------------------------- rendering ---------------------------

async def save_article_as_pdf(context, url: str, out_path: Path,
                               timeout: int = 60_000) -> tuple[bool, str]:
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)

        if not await wait_past_cloudflare(page, max_seconds=45):
            return False, "(Cloudflare challenge did not clear)"

        title = (await page.title()).replace(" | Medium", "").strip() or "untitled"
        if any(s in title for s in CF_TITLES):
            return False, f"(still on challenge page: {title!r})"

        await page.pdf(
            path=str(out_path),
            format="A4",
            print_background=True,
            margin={"top": "18mm", "bottom": "18mm",
                    "left": "15mm", "right": "15mm"},
        )
        return True, title
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        await page.close()


# --------------------------- helpers ---------------------------

def slugify(text: str, max_len: int = 80) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:max_len].strip("-") or "untitled"


def extract_username(profile_url: str) -> str:
    parsed = urlparse(profile_url)
    m = re.search(r"@([\w.\-]+)", parsed.path)
    if m:
        return m.group(1)
    if parsed.netloc.endswith(".medium.com"):
        return parsed.netloc.split(".")[0]
    raise ValueError(f"Could not parse a Medium username from: {profile_url}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="medium2pdf",
        description="Archive a Medium author's posts as PDFs in a single zip.",
    )
    parser.add_argument("profile_url", help="e.g. https://medium.com/@username")
    parser.add_argument("-o", "--output", type=Path, default=Path("./medium_archives"))
    parser.add_argument("--delay", type=float, default=2.5,
                        help="Seconds between articles (default: 2.5).")
    parser.add_argument("--max", dest="max_articles", type=int, default=None)
    parser.add_argument("--list-only", action="store_true",
                        help="Just write URL list, no PDFs.")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(run(args.profile_url, args.output, args.delay,
                        args.max_articles, args.list_only))
    except KeyboardInterrupt:
        print("\n[!] Interrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
