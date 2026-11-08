"""Chromium check shared by the container and live smoke commands."""

import re
from pathlib import Path


def check_browser(url: str, artifacts: Path) -> None:
    """Check that a pasted parts list produces a shopping list in Chromium.

    Args:
        url: The site's base URL without a trailing slash.
        artifacts: Where to save the screenshot and browser trace.
    """
    from playwright.sync_api import expect, sync_playwright

    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_default_timeout(60_000)
        try:
            page.goto(f"{url}/", wait_until="domcontentloaded")
            expect(page.get_by_role("heading", level=1)).to_be_visible()
            page.goto(f"{url}/app/", wait_until="domcontentloaded")
            page.get_by_role("button", name="Paste", exact=True).click()
            page.get_by_role("textbox", name="Parts list", exact=True).fill(
                "R1 10k\nC1 100n\nU1 TL072"
            )
            page.get_by_role("button", name="Add", exact=True).click()
            # The shopping list has its own page, reached without a reload.
            page.get_by_role("link", name=re.compile(r"^To buy")).click()
            table = page.get_by_role("table")
            for part in ("10k", "100n", "TL072"):
                expect(table.get_by_role("cell", name=part, exact=True)).to_be_visible()
            assert not errors, errors
        finally:
            try:
                page.screenshot(path=str(artifacts / "browser.png"), full_page=True)
            finally:
                context.tracing.stop(path=str(artifacts / "trace.zip"))
                browser.close()
