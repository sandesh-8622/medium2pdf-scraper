#!/usr/bin/env python3
"""
medium2pdf - archive a medium author's posts as pdfs
"""

import argparse
import sys
from pathlib import Path


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
    print(f"profile: {args.profile_url}")
    print(f"output:  {args.output}")


if __name__ == "__main__":
    main()
