import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from . import store, engine
from .models import JobSpec


@asynccontextmanager
async def lifespan(app):
    store.init()
    for job in store.list_jobs():
        if job["status"] == "queued":
            engine.enqueue(job)
        elif job["status"] == "running":
            job["status"] = "interrupted"
            store.save_job(job)
    yield
    tasks = list(engine.TASKS.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="WebCollector 跨网站采集服务", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.url.hostname not in ("127.0.0.1", "localhost", "testserver"):
        return JSONResponse({"detail": "Local access only"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "Cross-origin requests disabled"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/")
def home():
    return FileResponse(Path(__file__).parent / "ui.html")


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0", "data_dir": str(store.DATA), "adapters": ["http", "playwright", "yt-dlp", "gallery-dl"]}


@app.post("/api/shutdown", include_in_schema=False)
def shutdown():
    if not hasattr(app.state, "shutdown"):
        raise HTTPException(409, "Use the local process launcher to enable graceful stop")
    app.state.shutdown()
    return {"status": "stopping"}


@app.post("/api/jobs", status_code=202)
async def create(spec: JobSpec):
    for url in spec.urls:
        try:
            await engine.check_url(url)
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
