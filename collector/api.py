import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from . import store, engine, processing, network, profiles
from .limits import ensure_storage, disk_used
from pydantic import BaseModel, Field
from fastapi.exceptions import RequestValidationError
from .models import JobSpec


@asynccontextmanager
async def lifespan(app):
    store.init()
    processing.init()
    for record in processing.listing():
        if record['status']=='queued':
            processing.enqueue(record)
        elif record['status']=='running':
            record['status']='interrupted'
            processing.save(record)
    for job in store.list_jobs():
        if job["status"] == "queued":
            engine.enqueue(job)
        elif job["status"] == "running":
            job["status"] = "interrupted"
            store.save_job(job)
    yield
    tasks = list(engine.TASKS.values()) + list(processing.TASKS.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for name in list(profiles.SESSIONS):
        await profiles.finish(name,save=False)


app = FastAPI(title="WebCollector", version="0.2.0", lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def invalid_input(request,exc):
    # Pydantic errors otherwise echo raw proxy credentials in their input field.
    return JSONResponse({'detail':[{'loc':e['loc'],'msg':e['msg'],'type':e['type']} for e in exc.errors()]},status_code=422)


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.url.hostname not in ("127.0.0.1", "localhost", "testserver"):
        return JSONResponse({"detail": "Local access only"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "Cross-origin requests disabled"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers['Referrer-Policy']='no-referrer'
    return response


@app.get("/")
def home():
    return FileResponse(Path(__file__).parent / "ui-v2.html")


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0", "data_dir": str(store.DATA), "adapters": ["http", "playwright", "yt-dlp", "gallery-dl"]}


@app.post("/api/shutdown", include_in_schema=False)
def shutdown():
    if not hasattr(app.state, "shutdown"):
        raise HTTPException(409, "Use the local process launcher to enable graceful stop")
    app.state.shutdown()
    return {"status": "stopping"}


@app.post("/api/jobs", status_code=202)
async def create(spec: JobSpec):
    if 'network_mode' not in spec.model_fields_set and network.load().default_strict:
        spec.network_mode='strict'
    try:
        network.candidates(spec)
        ensure_storage()
        if spec.profile and not (store.ROOT/'profiles'/spec.profile/'storage-state.json').is_file():
            raise ValueError('Login profile does not exist; create it in Settings')
        if spec.profile and (store.ROOT/'profiles'/spec.profile/'network.json').exists() and spec.network_mode!='strict':
            raise ValueError('This login profile is pinned to a proxy; strict mode is required')
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    for url in spec.urls:
        try:
            await network.check_target(url,spec.network_mode=='strict')
        except Exception as exc:
            raise HTTPException(422, type(exc).__name__ + ": " + str(exc))
    job = store.new_job(spec.model_dump())
    engine.enqueue(job)
    return job


@app.get("/api/jobs")
def list_jobs():
    return store.list_jobs()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/jobs/{job_id}/results")
def results(job_id: str):
    get_job(job_id)
    return {"job_id": job_id, "items": store.items(job_id)}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    job = get_job(job_id)
    task = engine.TASKS.get(job_id)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        job = store.get_job(job_id)
    return job


@app.post("/api/jobs/{job_id}/retry", status_code=202)
async def retry(job_id: str):
    job = get_job(job_id)
    if job["status"] in ("running", "queued"):
        raise HTTPException(409, "Job is still active")
    # A new job keeps the original evidence intact.
    return await create(JobSpec(**job["spec"]))


@app.get("/api/files/{item_id}")
def download(item_id: str, text: bool = False):
    item = store.get_item(item_id)
    key = "text_path" if text else "path"
    if not item or not item.get(key):
        raise HTTPException(404, "File not available")
    p = (store.DATA / item[key]).resolve()
    if not p.is_relative_to(store.DATA.resolve()) or not p.is_file():
        raise HTTPException(404, "File missing")
    return FileResponse(p, media_type="application/octet-stream", filename=(item.get("filename", "resource") + ".md") if text else item.get("filename", p.name))


@app.get('/api/settings')
def settings():
    return {**network.public(), 'processing':processing.capabilities(),'storage_used_bytes':disk_used(),'profiles':profiles.listing()}

class SettingsUpdate(BaseModel):
    default_strict: bool
    storage_limit_mb: int = Field(ge=500,le=1000000)

@app.patch('/api/settings')
def update_settings(body:SettingsUpdate):
    s=network.load()
    s.default_strict=body.default_strict
    s.storage_limit_mb=body.storage_limit_mb
    network.save(s)
    return settings()

@app.post('/api/proxies')
def add_proxy(body:network.ProxyConfig):
    s=network.load()
    s.proxies=[p for p in s.proxies if p.id!=body.id]+[body]
    network.save(s)
    return network.public()

@app.delete('/api/proxies/{proxy_id}')
def remove_proxy(proxy_id:str):
    if any(j['status'] in ('queued','running') for j in store.list_jobs()):
        raise HTTPException(409,'Wait for active capture jobs to finish')
    s=network.load()
    s.proxies=[p for p in s.proxies if p.id!=proxy_id]
    network.save(s)
    return network.public()

class ProxyProbe(BaseModel):
    url:str='https://example.com/'

@app.post('/api/proxies/{proxy_id}/check')
async def check_proxy(proxy_id:str, body:ProxyProbe):
    try:
        await network.check_target(body.url,True)
        await network.select(JobSpec(urls=[body.url],network_mode='strict',proxy_ids=[proxy_id]))
        return {'proxy_id':proxy_id,'reachable':True,'checked_at':store.now(),'os_isolation':False}
    except Exception as exc:
        return {'proxy_id':proxy_id,'reachable':False,'error':network.scrub(exc),'checked_at':store.now()}

class LoginSpec(BaseModel):
    name:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    url:str
    network_mode:str='strict'
    proxy_ids:list[str]=Field(default_factory=list)

@app.post('/api/profiles/login')
async def login(body:LoginSpec):
    try:
        await profiles.start(body.name,body.url,body.network_mode,body.proxy_ids)
        return {'name':body.name,'status':'awaiting_user_login'}
    except Exception as exc:
        raise HTTPException(422,network.scrub(exc))

@app.post('/api/profiles/{name}/save')
async def save_login(name:str):
    try:
        await profiles.finish(name)
        return profiles.listing()
    except ValueError as exc:
        raise HTTPException(422,str(exc))

@app.post('/api/profiles/{name}/cancel')
async def cancel_login(name:str):
    try:
        await profiles.finish(name,False)
        return {'status':'cancelled'}
    except ValueError as exc:
        raise HTTPException(422,str(exc))

@app.get('/api/library')
def library(q:str='',kind:str='',status:str='',offset:int=0,limit:int=100):
    offset=max(0,offset)
    limit=min(200,max(1,limit))
    with store.connection() as db:
        rows=db.execute('SELECT body FROM items ORDER BY rowid DESC').fetchall()
    results=[]
    for row in rows:
        import json
        item=json.loads(row[0])
        if q and q.casefold() not in ' '.join(str(item.get(k,'')) for k in ['filename','title','label','url','tags']).casefold():
            continue
        if kind and kind not in item.get('content_type',''):
            continue
        if status=='downloaded' and not item.get('downloaded') or status=='failed' and item['status'] not in ('failed','limited','interrupted'):
            continue
        results.append(item)
    return dict(total=len(results),offset=offset,items=results[offset:offset+limit])

class Tags(BaseModel):
    tags:list[str]=Field(max_length=30)

@app.patch('/api/items/{item_id}/tags')
def tag_item(item_id:str, body:Tags):
    item=store.get_item(item_id)
    if not item:
        raise HTTPException(404,'Resource not found')
    item['tags']=list(dict.fromkeys(t.strip()[:80] for t in body.tags if t.strip()))
    item['classified']=bool(item['tags'])
    item['classification_method']='user_tags'
    store.save_item(item)
    return item

@app.post('/api/items/{item_id}/process',status_code=202)
async def process_item(item_id:str,body:processing.ProcessSpec):
    item=store.get_item(item_id)
    if not item:
        raise HTTPException(404,'Resource not found')
    try:
        ensure_storage()
        return processing.create(item,body)
    except ValueError as exc:
        raise HTTPException(422,str(exc))

@app.get('/api/processes')
def list_processes(item_id:str|None=None):
    return processing.listing(item_id)

@app.get('/api/processes/{pid}')
def get_process(pid:str):
    r=processing.get(pid)
    if not r:
        raise HTTPException(404,'Processing job not found')
    return r

@app.post('/api/processes/{pid}/cancel')
async def cancel_process(pid:str):
    r=get_process(pid)
    task=processing.TASKS.get(pid)
    if task:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
    return get_process(pid)

@app.post('/api/processes/{pid}/retry',status_code=202)
async def retry_process(pid:str):
    r=get_process(pid)
    if r['status'] in ('running','queued'):
        raise HTTPException(409,'Processing job is active')
    return await process_item(r['item_id'],processing.ProcessSpec(**r['spec']))

@app.get('/api/processes/{pid}/files/{kind}')
def derived_file(pid:str,kind:str):
    r=get_process(pid)
    if r['status'] not in ('completed','partial') or kind not in r.get('files',{}):
        raise HTTPException(404,'Derived file not available')
    p=processing.safe_path(r['files'][kind])
    return FileResponse(p,media_type='application/json' if kind=='json' else 'text/plain; charset=utf-8')

@app.get('/api/items/{item_id}/preview')
def preview(item_id:str):
    item=store.get_item(item_id)
    if not item or not item.get('path'):
        raise HTTPException(404,'File not available')
    mime=item.get('content_type','')
    if mime not in ('application/pdf','image/jpeg','image/png','image/webp','image/gif','video/mp4','audio/mpeg','audio/mp4','audio/wav'):
        raise HTTPException(415,'Use original file download for this format')
    return FileResponse(processing.safe_path(item['path']),media_type=mime,headers={'Content-Security-Policy':"sandbox; default-src 'none'"})
