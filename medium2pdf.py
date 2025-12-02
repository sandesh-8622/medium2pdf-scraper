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
