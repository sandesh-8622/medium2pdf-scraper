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

        await page.add_style_tag(content="""
            [data-testid="sign-up-prompt"],
            [class*="signup"],
            [class*="banner"],
            [class*="cookie"],
            [class*="paywall"],
            div[role="dialog"],
            nav, header[role="banner"] {
                display: none !important;
            }
            article { max-width: 100% !important; }
        """)

        await page.evaluate("""
            async () => {
                const sleep = ms => new Promise(r => setTimeout(r, ms));
                const total = document.body.scrollHeight;
                for (let y = 0; y < total; y += 500) {
                    window.scrollTo(0, y);
                    await sleep(80);
                }
                window.scrollTo(0, 0);
            }
        """)
        await asyncio.sleep(0.6)

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



# --------------------------- merging ---------------------------

def merge_pdfs(entries: list[dict], work_dir: Path, output_path: Path) -> bool:
    """Combine all per-article PDFs into one file with bookmarks per article.

    `entries` are manifest dicts with keys: file, title, url.
    """
    if not HAS_PYPDF:
        print("[!] pypdf not installed - skipping merged PDF.")
        print("    To enable: python -m pip install pypdf")
        return False

    writer = PdfWriter()
    page_offset = 0
    for entry in entries:
        pdf_path = work_dir / entry["file"]
        try:
            reader = PdfReader(str(pdf_path))
        except Exception as e:
            print(f"    [!] Could not read {entry['file']}: {e}")
            continue
        for page in reader.pages:
            writer.add_page(page)
        try:
            writer.add_outline_item(entry["title"][:100], page_offset)
        except Exception:
            pass  # bookmark failure shouldn't break merging
        page_offset += len(reader.pages)

    with output_path.open("wb") as f:
        writer.write(f)
    return True


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



# --------------------------- main run ---------------------------

async def run(profile_url: str, output_dir: Path, delay: float,
              max_articles: int | None, list_only: bool) -> None:
    username = extract_username(profile_url)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    work_dir = output_dir / f"medium-{username}-{stamp}"
    work_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        # Try real installed Chrome first - Cloudflare flags the bundled
        # chrome-headless-shell almost immediately.
        launch_args = ["--disable-blink-features=AutomationControlled"]
        browser = None
        for channel in ("chrome", "msedge", None):
            try:
                kwargs = dict(headless=False, args=launch_args)
                if channel:
                    kwargs["channel"] = channel
                browser = await p.chromium.launch(**kwargs)
                label = channel or "bundled chromium"
                print(f"[+] Launched browser: {label}")
                break
            except Exception as e:
                print(f"[!] Could not launch {channel or 'chromium'}: "
                      f"{type(e).__name__}")
                continue
        if browser is None:
            print("[!] No browser could be launched. Exiting.")
            return

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 1800},
        )
        # Light stealth - hide the navigator.webdriver flag.
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', "
            "{get: () => undefined});"
        )

        page = await context.new_page()
        urls = await discover_article_urls(page, profile_url)
        await page.close()

        if max_articles:
            urls = urls[:max_articles]

        if not urls:
            print("[!] No articles found.")
            print(f"\n[+] Saving {len(urls)} articles as PDF...\n")

        manifest: list[dict] = []
        failures: list[tuple[str, str]] = []
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}")
            tmp_path = work_dir / f"_tmp_{i:04d}.pdf"
            success, info = await save_article_as_pdf(context, url, tmp_path)
            if success:
                final_name = f"{i:03d}-{slugify(info)}.pdf"
                final_path = work_dir / final_name
                tmp_path.rename(final_path)
                manifest.append({"url": url, "title": info, "file": final_name})
                print(f"    saved: {final_name}")
            else:
                if tmp_path.exists():
                    tmp_path.unlink()
                failures.append((url, info))
                print(f"    [!] {info}")
            await asyncio.sleep(delay)

        await browser.close()
            return

        if list_only:
            list_path = work_dir / "urls.txt"
            list_path.write_text("\n".join(urls), encoding="utf-8")
            print(f"\n[*] --list-only set. Wrote {len(urls)} URLs to:\n    {list_path}")
            print(f"\n[+] Saving {len(urls)} articles as PDF...\n")

        manifest: list[dict] = []
        failures: list[tuple[str, str]] = []
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}")
            tmp_path = work_dir / f"_tmp_{i:04d}.pdf"
            success, info = await save_article_as_pdf(context, url, tmp_path)
            if success:
                final_name = f"{i:03d}-{slugify(info)}.pdf"
                final_path = work_dir / final_name
                tmp_path.rename(final_path)
                manifest.append({"url": url, "title": info, "file": final_name})
                print(f"    saved: {final_name}")
            else:
                if tmp_path.exists():
                    tmp_path.unlink()
                failures.append((url, info))
                print(f"    [!] {info}")
            await asyncio.sleep(delay)

        await browser.close()
            return

        print(f"\n[+] Saving {len(urls)} articles as PDF...\n")

        manifest: list[dict] = []
        failures: list[tuple[str, str]] = []
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}")
            tmp_path = work_dir / f"_tmp_{i:04d}.pdf"
            success, info = await save_article_as_pdf(context, url, tmp_path)
            if success:
                final_name = f"{i:03d}-{slugify(info)}.pdf"
                final_path = work_dir / final_name
                tmp_path.rename(final_path)
                manifest.append({"url": url, "title": info, "file": final_name})
                print(f"    saved: {final_name}")
            else:
                if tmp_path.exists():
                    tmp_path.unlink()
                failures.append((url, info))
                print(f"    [!] {info}")
            await asyncio.sleep(delay)

        await browser.close()

    # Build a single merged PDF (in article order, with bookmarks) if we have any.
    merged_filename = None
    if manifest:
        merged_filename = f"_MERGED_all-articles-{username}.pdf"
        merged_path = work_dir / merged_filename
        print(f"\n[+] Merging {len(manifest)} PDFs into one file...")
        if merge_pdfs(manifest, work_dir, merged_path):
            print(f"    saved: {merged_filename}")
        else:
            merged_filename = None

    manifest_path = work_dir / "manifest.txt"
    with manifest_path.open("w", encoding="utf-8") as f:
        f.write(f"Medium archive - @{username}\n")
        f.write(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write(f"Source: {profile_url}\n")
        f.write(f"Total articles saved: {len(manifest)}\n")
        f.write(f"Failures: {len(failures)}\n")
        if merged_filename:
            f.write(f"Merged PDF: {merged_filename}\n")
        f.write("\n")
        for entry in manifest:
            f.write(f"- {entry['title']}\n")
            f.write(f"  URL : {entry['url']}\n")
            f.write(f"  File: {entry['file']}\n\n")
        if failures:
            f.write("\nFAILURES:\n")
            for url, reason in failures:
                f.write(f"- {url}\n  {reason}\n\n")

    zip_path = output_dir / f"medium-{username}-{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for pdf in sorted(work_dir.glob("*.pdf")):
            zf.write(pdf, arcname=pdf.name)
        zf.write(manifest_path, arcname=manifest_path.name)

    print(f"\n[done] Archive: {zip_path}")
    print(f"       {len(manifest)} PDFs saved, {len(failures)} failed.")


# --------------------------- cli ---------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="medium2pdf",
        description="Archive a Medium author's posts as PDFs in a single zip.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
