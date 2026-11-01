import asyncio,json,os
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2]
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'runtime/browsers')
async def main():
    errors=[]
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        page=await b.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto('http://127.0.0.1:8765')
        await page.wait_for_selector('#proxy option',state='attached')
        await page.screenshot(path=str(ROOT/'research/upgrade-desktop.png'),full_page=True)
        for lang in ['en','zh']:
            await page.select_option('#language',lang)
            for tab in ['settings','library','tasks','new']:
                await page.locator('[data-page='+tab+']').click()
                await page.wait_for_timeout(500)
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        await page.locator('[data-page=library]').click()
        await page.wait_for_timeout(700)
        await page.locator('#libraryresults button').first.click()
        await page.wait_for_selector('dialog[open]')
        await page.locator('#closeViewer').click()
        await page.set_viewport_size({'width':768,'height':1024})
        await page.locator('[data-page=new]').click()
        assert await page.locator('#qualityPolicy').is_visible()
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        await page.screenshot(path=str(ROOT/'research/quality-tablet.png'),full_page=True)
        await page.set_viewport_size({'width':390,'height':844})
        await page.locator('[data-page=new]').click()
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        await page.screenshot(path=str(ROOT/'research/upgrade-mobile.png'),full_page=True)
        await b.close()
    assert not errors,errors
    print('UI: both languages, four workspaces, resource dialog, desktop/tablet/mobile overflow checks passed')
asyncio.run(main())
