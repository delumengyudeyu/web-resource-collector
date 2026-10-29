"""Create an isolated, user-operated login profile; never import the user's main browser."""
import asyncio
import json
import os
import sys
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    name, url = sys.argv[1:3]
    if not name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("Use a simple profile name")
    folder = Path(__file__).resolve().parents[2] / "profiles" / name
    folder.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url)
        await asyncio.to_thread(input, "Log in yourself in the browser, then press Enter here to save this profile: ")
        await context.storage_state(path=str(folder / "storage-state.json"))
        rows = ["# Netscape HTTP Cookie File"]
        for c in await context.cookies():
            rows.append("\t".join([c["domain"], "TRUE" if c["domain"].startswith(".") else "FALSE", c.get("path", "/"), "TRUE" if c["secure"] else "FALSE", str(max(0, int(c.get("expires", 0)))), c["name"], c["value"]]))
        (folder / "cookies.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
        await browser.close()
    print("Saved locally. Use profile=" + name + " in a job. Never commit profiles.")

if __name__ == "__main__":
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(__file__).resolve().parents[2] / "runtime" / "browsers"))
    asyncio.run(main())
