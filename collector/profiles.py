"""Interactive login in isolated browsers, with explicit user-driven save."""
import json
import time
from urllib.parse import urlsplit
from . import store, network
from .models import JobSpec

SESSIONS={}

def listing():
    root=store.ROOT/'profiles'
    out=[]
    if not root.exists():
        return out
    for folder in root.iterdir():
        state=folder/'storage-state.json'
        if not state.is_file():
            continue
        cookies=json.loads(state.read_text(encoding='utf-8')).get('cookies',[])
        expired=sum(0<c.get('expires',-1)<time.time() for c in cookies)
        pinned=folder/'network.json'
        out.append(dict(name=folder.name,cookies=len(cookies),expired_cookies=expired,
                        status='possibly_expired' if expired else 'saved_not_verified',
                        proxy_id=json.loads(pinned.read_text())['proxy_id'] if pinned.exists() else None))
    return out

async def start(name,url,mode,proxy_ids):
    spec=JobSpec(urls=[url],profile=name,network_mode=mode,proxy_ids=proxy_ids)
    if name in SESSIONS:
        raise ValueError('Login window is already open')
    await network.check_target(url,mode=='strict')
    proxy=await network.select(spec)
    from playwright.async_api import async_playwright
    pw=await async_playwright().start()
    browser=None
    try:
        browser=await pw.chromium.launch(headless=False,**network.browser_options(proxy))
        context=await browser.new_context(service_workers='block')
        async def route(r):
            try:
                await network.check_target(r.request.url,mode=='strict')
                await r.continue_()
            except Exception:
                await r.abort()
        await context.route('**/*',route)
        page=await context.new_page()
        await page.goto(url,wait_until='domcontentloaded',timeout=45000)
        SESSIONS[name]=(pw,browser,context,proxy)
    except BaseException:
        if browser:
            await browser.close()
        await pw.stop()
        raise

async def finish(name,save=True):
    if name not in SESSIONS:
        raise ValueError('No login window is open')
    pw,browser,context,proxy=SESSIONS.pop(name)
    try:
        if save:
            folder=store.ROOT/'profiles'/name
            folder.mkdir(parents=True,exist_ok=True)
            await context.storage_state(path=str(folder/'storage-state.json'))
            rows=['# Netscape HTTP Cookie File']
            for c in await context.cookies():
                rows.append('\t'.join([c['domain'],'TRUE' if c['domain'].startswith('.') else 'FALSE',c.get('path','/'),
                          'TRUE' if c['secure'] else 'FALSE',str(max(0,int(c.get('expires',0)))),c['name'],c['value']]))
            (folder/'cookies.txt').write_text('\n'.join(rows)+'\n',encoding='utf-8')
            if proxy:
                (folder/'network.json').write_text(json.dumps({'proxy_id':proxy.id}),encoding='utf-8')
    finally:
        await browser.close()
        await pw.stop()
