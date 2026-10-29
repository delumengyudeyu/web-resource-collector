import asyncio
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit, urljoin
import httpx
import psutil
from . import store
from .models import JobSpec
from .content import absolute, discover, filename, inspect_file, extract_text, article, safe_name

RUNNING = {}
TASKS = {}
JOB_SLOTS = asyncio.Semaphore(2)


async def check_url(url):
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Invalid HTTP(S) URL")
    if os.environ.get("COLLECTOR_ALLOW_PRIVATE") == "1":
        return
    addresses = await asyncio.get_running_loop().getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
    def acceptable(value):
        address = ipaddress.ip_address(value)
        if address.is_global:
            return True
        # Explicit local configuration for Clash-style DNS fake-IP routing.
        if os.environ.get("COLLECTOR_ALLOW_FAKE_IP") == "1" and address.version == 4 and address in ipaddress.ip_network("198.18.0.0/15"):
            try:
                ipaddress.ip_address(p.hostname)
                return False
            except ValueError:
                return True
        return False
    if any(not acceptable(a[4][0]) for a in addresses):
        raise ValueError("Private/local network destination is disabled")


def enqueue(job):
    task = asyncio.create_task(run(job))
    TASKS[job["id"]] = task
    task.add_done_callback(lambda _: TASKS.pop(job["id"], None))


async def run(job):
    collector = Collector(job)
    RUNNING[job["id"]] = collector
    try:
        async with JOB_SLOTS:
            await collector.run()
            job["status"] = collector.final_status
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        collector.event("Task cancelled; completed files retained")
    except Exception as exc:
        job["status"] = "failed"
        collector.event(f"{type(exc).__name__}: {exc}")
    finally:
        store.save_job(job)
        RUNNING.pop(job["id"], None)


class Collector:
    def __init__(self, job):
        self.job = job
        self.spec = JobSpec(**job["spec"])
        self.folder = store.DATA / "jobs" / job["id"]
        self.folder.mkdir(parents=True, exist_ok=True)
        self.domains = set(self.spec.allowed_domains or [urlsplit(u).hostname for u in self.spec.urls])
        self.seen = {}
        self.pages = deque()
        self.page_count = 0
        self.resource_count = 0
        self.request_count = 0
        self.hashes = {}
        self.used = 0
        self.lock = asyncio.Lock()
        self.rate_lock = asyncio.Lock()
        self.next_request = 0
        self.browser_context = None
        self.browser_engine = None
        self.pw = None
        self.profile = store.ROOT / "profiles" / self.spec.profile if self.spec.profile else None

    def event(self, message):
        # Network library exceptions can contain signed query strings; store terse events.
        message = re.sub(r'https?://[^\s]+', '[URL]', str(message))
        self.job["events"] = (self.job["events"] + [{"time": store.now(), "message": message[:700]}])[-150:]
        self.job["pages"] = self.page_count
        self.job["bytes"] = self.used
        store.save_job(self.job)

    def item(self, url, source, role, order=0, label=""):
        if url in self.seen:
            item = self.seen[url]
            if source and source not in item["source_pages"]:
                item["source_pages"].append(source)
                store.save_item(item)
            return item, False
        item = dict(id=uuid.uuid4().hex, job_id=self.job["id"], url=url, source_pages=[source] if source else [], role=role, order=order, label=label, discovered=True, downloaded=False, extracted=False, classified=False, status="discovered", discovered_at=store.now())
        self.seen[url] = item
        store.save_item(item)
        return item, True

    def in_scope(self, url):
        p = urlsplit(url)
        return p.hostname in self.domains and p.path.startswith(self.spec.path_prefix)

    async def throttle(self):
        async with self.rate_lock:
            await asyncio.sleep(max(0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + self.spec.interval_seconds

    async def run(self):
        self.job["status"] = "running"
        self.event("Started")
        cookies = httpx.Cookies()
        if self.profile:
            state = self.profile / "storage-state.json"
            if state.exists():
                for c in json.loads(state.read_text(encoding="utf-8")).get("cookies", []):
                    cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
        self.client = httpx.AsyncClient(timeout=self.spec.timeout_seconds, cookies=cookies, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"}, follow_redirects=False, mounts={"all://127.0.0.1": httpx.AsyncHTTPTransport(), "all://localhost": httpx.AsyncHTTPTransport()})
        try:
            if self.spec.browser:
                from playwright.async_api import async_playwright
                self.pw = await async_playwright().start()
                self.browser_tmp = self.folder / "browser-tmp"
                self.browser_tmp.mkdir(exist_ok=True)
                self.browser_engine = await self.pw.chromium.launch(headless=True, downloads_path=str(self.browser_tmp))
                opts = {"accept_downloads": True, "viewport": {"width": 1365, "height": 900}}
                if self.profile and (self.profile / "storage-state.json").exists():
                    opts["storage_state"] = str(self.profile / "storage-state.json")
                self.browser_context = await self.browser_engine.new_context(**opts)
                checked = set()
                async def route_handler(route):
                    try:
                        u = route.request.url
                        if u.startswith(("blob:", "data:")):
                            await route.continue_()
                            return
                        host = urlsplit(u).netloc
                        if host not in checked:
                            await check_url(u)
                            checked.add(host)
                        await route.continue_()
                    except Exception:
                        await route.abort()
                await self.browser_context.route("**/*", route_handler)
            for u in self.spec.urls:
                item, fresh = self.item(u, "", "seed")
                if fresh:
                    self.pages.append((item, 0))
            while self.pages:
                item, depth = self.pages.popleft()
                await self.process(item, depth, seed=item["role"] == "seed")
            results = store.items(self.job["id"])
            successes = sum(r["downloaded"] for r in results)
            errors = sum(r["status"] in ("failed", "limited", "interrupted") for r in results)
            self.final_status = "completed_with_errors" if errors and successes else "failed" if errors else "completed"
            self.job["summary"] = dict(discovered=len(results), downloaded=successes, extracted=sum(r["extracted"] for r in results), classified=0, errors=errors)
            self.event("Finished")
        finally:
            if self.browser_context:
                await self.browser_context.close()
            if self.browser_engine:
                await self.browser_engine.close()
            if self.pw:
                await self.pw.stop()
                await asyncio.sleep(0.1)
            if hasattr(self, "browser_tmp"):
                for partial in self.browser_tmp.iterdir():
                    if partial.is_file():
                        partial.unlink(missing_ok=True)
            await self.client.aclose()

    async def request(self, method, url, headers=None):
        for _ in range(8):
            if self.request_count >= self.spec.max_requests:
                raise ValueError("Job exceeds max_requests")
            self.request_count += 1
            await check_url(url)
            await self.throttle()
            response = await self.client.send(self.client.build_request(method, url, headers=headers), stream=True)
            if response.is_redirect:
                target = urljoin(url, response.headers.get("location", ""))
                await response.aclose()
                url = target
                continue
            return response
        raise ValueError("Too many redirects")

    async def fetch(self, item, head_only=False):
        headers = {"Referer": item["source_pages"][0]} if item["source_pages"] else {}
        for attempt in range(self.spec.retries + 1):
            response = None
            temp = self.folder / (item["id"] + ".part")
            try:
                response = await self.request("HEAD" if head_only else "GET", item["url"], headers)
                response.raise_for_status()
                if head_only:
                    return None, dict(response.headers), str(response.url)
                length = int(response.headers.get("content-length", "0"))
                if length > self.spec.max_file_mb * 1024**2:
                    raise ValueError("File exceeds max_file_mb")
                size = 0
                digest = hashlib.sha256()
                with temp.open("wb") as out:
                    async for chunk in response.aiter_bytes(65536):
                        size += len(chunk)
                        if size > self.spec.max_file_mb * 1024**2:
                            raise ValueError("File exceeds max_file_mb")
                        async with self.lock:
                            if shutil.disk_usage(store.DATA).free < 512 * 1024**2:
                                raise ValueError("Disk reserve reached")
                            if self.used + len(chunk) > self.spec.max_total_mb * 1024**2:
                                raise ValueError("Job exceeds max_total_mb")
                            self.used += len(chunk)
                        out.write(chunk)
                        digest.update(chunk)
                item.update(size=size, sha256=digest.hexdigest(), fetched_at=store.now(), http_status=response.status_code)
                return temp, dict(response.headers), str(response.url)
            except (httpx.HTTPError, OSError) as exc:
                if temp.exists():
                    self.used -= temp.stat().st_size
                    temp.unlink()
                if attempt == self.spec.retries or isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in (408, 429, 500, 502, 503, 504):
                    raise
                await asyncio.sleep(0.5 * 2**attempt)
            finally:
                if response:
                    await response.aclose()
                # Temp files are retained only on successful GET, cleaned by caller otherwise.
        raise RuntimeError("Download did not finish")

    async def process(self, item, depth, seed=False, known_resource=False):
        temp = None
        try:
            if self.resource_count >= self.spec.max_resources:
                item.update(status="limited", error="max_resources reached")
                return
            item["status"] = "fetching"
            store.save_item(item)
            if not seed and not known_resource and (depth > self.spec.max_depth or not self.in_scope(item["url"])):
                try:
                    _, h, _ = await self.fetch(item, head_only=True)
                    if "html" in h.get("content-type", ""):
                        if re.search(r"\.(pdf|pptx?|docx?|xlsx?|zip|rar|mp4|mp3)(?:\?|$)", item["url"], re.I):
                            raise ValueError("Expected original file but received HTML (login/error/preview page)")
                        item.update(status="skipped", error="HTML outside crawl scope/depth")
                        return
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 405:
                        raise
            temp, headers, final_url = await self.fetch(item)
            mime = inspect_file(temp, headers.get("content-type", ""))
            item.update(content_type=mime, declared_type=headers.get("content-type", ""), final_url=final_url)
            is_html = mime in ("text/html", "application/xhtml+xml")
            if is_html and (known_resource or item["role"] == "attachment" or re.search(r"\.(pdf|pptx?|docx?|xlsx?|zip|rar|mp4|mp3)(?:\?|$)", item["url"], re.I)):
                raise ValueError("Expected original file but received HTML (login/error/preview page)")
            if is_html and (self.page_count >= self.spec.max_pages or not seed and (depth > self.spec.max_depth or not self.in_scope(final_url))):
                item.update(status="skipped", error="Page limit or crawl scope")
                return
            name = filename(final_url, headers, mime)
            if is_html and not name.lower().endswith((".html", ".htm")):
                name += ".html"
            destination = self.folder / (item["sha256"][:16] + "_" + name)
            if self.resource_count >= self.spec.max_resources:
                raise ValueError("Job exceeds max_resources")
            duplicate = self.hashes.get(item["sha256"])
            if duplicate:
                self.used -= temp.stat().st_size
                temp.unlink()
                destination = store.DATA / duplicate["path"]
                item["duplicate_of"] = duplicate["id"]
            else:
                temp.replace(destination)
            temp = None
            item.update(path=str(destination.relative_to(store.DATA)), filename=name, downloaded=True, status="downloaded", adapter="http")
            self.hashes[item["sha256"]] = item
            self.resource_count += 1
            store.save_item(item)
            if is_html:
                self.page_count += 1
                from charset_normalizer import from_bytes
                decoded = from_bytes(destination.read_bytes()).best()
                html = str(decoded) if decoded else destination.read_text(encoding="utf-8", errors="replace")
                refs = []
                if self.spec.browser:
                    try:
                        html, refs = await self.render(item, html)
                    except Exception as exc:
                        self.event(f"Browser: {type(exc).__name__}: {exc}")
                        item["browser_error"] = str(exc)[:200]
                text, meta = article(html, final_url)
                item.update(meta)
                if self.spec.extract:
                    self.save_text(item, text)
                _, dom_refs = discover(html, final_url, self.spec.include_assets)
                from .plugins import extra_links
                refs = dom_refs + refs + extra_links(html, final_url)
                if len(refs) > self.spec.max_links_per_page:
                    item["discovery_truncated"] = len(refs) - self.spec.max_links_per_page
                resources = []
                for ref in refs[:self.spec.max_links_per_page]:
                    child, fresh = self.item(ref["url"], item["url"], ref["role"], ref.get("order", 0), ref.get("label", ""))
                    if not fresh:
                        continue
                    if ref["role"] in ("resource", "attachment"):
                        resources.append(child)
                    elif depth < self.spec.max_depth and self.in_scope(child["url"]):
                        self.pages.append((child, depth+1))
                    else:
                        resources.append(child)
                semaphore = asyncio.Semaphore(self.spec.concurrency)
                async def download(child):
                    async with semaphore:
                        await self.process(child, depth+1, known_resource=child["role"] == "resource")
                await asyncio.gather(*(download(c) for c in resources))
                if self.spec.media:
                    await self.external(item, "yt_dlp")
                if self.spec.gallery:
                    await self.external(item, "gallery_dl")
            elif self.spec.extract and item["size"] <= 30 * 1024**2:
                try:
                    text, meta = await asyncio.to_thread(extract_text, destination, mime)
                    item["document_metadata"] = meta
                    if text:
                        self.save_text(item, text)
                    else:
                        item["extraction_status"] = "unsupported_or_no_text"
                except Exception as exc:
                    item["extraction_status"] = "failed"
                    item["extraction_error"] = type(exc).__name__ + ": " + str(exc)[:200]
            if not is_html and self.spec.media and ("mpegurl" in mime or "dash+xml" in mime or destination.suffix.lower() in (".m3u8", ".mpd")):
                await self.external(item, "yt_dlp")
        except asyncio.CancelledError:
            item.update(status="interrupted", error="Cancelled")
            raise
        except Exception as exc:
            item.update(status="limited" if "exceeds" in str(exc) else "failed", error=re.sub(r'https?://[^\s]+', '[URL]', f"{type(exc).__name__}: {exc}")[:400])
        finally:
            part = self.folder / (item["id"] + ".part")
            if part.exists():
                self.used -= part.stat().st_size
                part.unlink(missing_ok=True)
            store.save_item(item)
            self.job.update(bytes=self.used, pages=self.page_count)
            store.save_job(self.job)

    def save_text(self, item, text):
        path = self.folder / (item["id"] + ".md")
        encoded = text.encode("utf-8")
        if self.used + len(encoded) > self.spec.max_total_mb * 1024**2:
            item["extraction_status"] = "storage_limit"
            return
        path.write_bytes(encoded)
        self.used += len(encoded)
        item.update(extracted=True, extraction_status="completed", text_path=str(path.relative_to(store.DATA)))

    async def render(self, item, fallback):
        page = await self.browser_context.new_page()
        refs = []
        downloads = []
        def response_seen(response):
            ct = response.headers.get("content-type", "").split(";")[0]
            rt = response.request.resource_type
            if ct and "html" not in ct and (self.spec.include_assets or rt in ("image", "media") or "content-disposition" in response.headers or any(x in ct for x in ("pdf", "officedocument", "msword", "zip", "octet-stream"))):
                refs.append(dict(url=response.url, role="resource", order=len(refs)))
        page.on("response", response_seen)
        page.on("download", lambda download: downloads.append(download))
        try:
            response = await page.goto(item["final_url"], wait_until="domcontentloaded", timeout=self.spec.timeout_seconds * 1000)
            if response and response.status >= 400:
                raise ValueError(f"Browser HTTP {response.status}")
            await page.wait_for_timeout(self.spec.browser_wait_ms)
            for _ in range(self.spec.scroll_steps):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(250)
            for selector in self.spec.download_selectors:
                locator = page.locator(selector).first
                if await locator.count():
                    await locator.click(timeout=5000)
                    await page.wait_for_timeout(750)
            for c in await self.browser_context.cookies():
                self.client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
            for download in downloads:
                await self.browser_download(download, item)
            html = await page.content()
            p = self.folder / (item["id"] + ".rendered.html")
            encoded = html.encode("utf-8")
            if self.used + len(encoded) <= self.spec.max_total_mb * 1024**2:
                p.write_bytes(encoded)
                self.used += len(encoded)
                item["rendered_path"] = str(p.relative_to(store.DATA))
            item["adapter"] = "http+playwright"
            return html, refs
        finally:
            await page.close()

    async def browser_download(self, download, parent):
        """Save browser-owned downloads, including POST and blob downloads."""
        url = download.url if download.url.startswith(("http:", "https:")) else parent["url"] + "#download=" + safe_name(download.suggested_filename)
        item, fresh = self.item(url, parent["url"], "attachment", label=download.suggested_filename)
        if not fresh and item["downloaded"]:
            await download.cancel()
            return
        target = self.folder / (item["id"] + "_" + safe_name(download.suggested_filename))
        operation = asyncio.create_task(download.save_as(str(target)))
        deadline = time.monotonic() + self.spec.timeout_seconds
        try:
            while not operation.done():
                sizes = [p.stat().st_size for p in self.browser_tmp.iterdir() if p.is_file()]
                if sum(sizes) + self.used > self.spec.max_total_mb * 1024**2 or max(sizes, default=0) > self.spec.max_file_mb * 1024**2:
                    raise ValueError("Browser download exceeds storage limit")
                if time.monotonic() > deadline:
                    raise TimeoutError("Browser download timeout")
                await asyncio.sleep(.1)
            await operation
            if self.resource_count >= self.spec.max_resources or target.stat().st_size + self.used > self.spec.max_total_mb * 1024**2 or target.stat().st_size > self.spec.max_file_mb * 1024**2:
                raise ValueError("Browser download exceeds configured limits")
            mime = inspect_file(target)
            if mime == "text/html":
                raise ValueError("Browser download returned HTML instead of original file")
            with target.open("rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()
            item.update(downloaded=True, status="downloaded", filename=download.suggested_filename, size=target.stat().st_size, sha256=digest, path=str(target.relative_to(store.DATA)), content_type=mime, adapter="playwright-download", fetched_at=store.now())
            self.used += item["size"]
            self.resource_count += 1
            if self.spec.extract and item["size"] < 30*1024**2:
                try:
                    text, meta = await asyncio.to_thread(extract_text, target, mime)
                    item["document_metadata"] = meta
                    if text:
                        self.save_text(item, text)
                except Exception as exc:
                    item["extraction_error"] = type(exc).__name__
        except BaseException as exc:
            await download.cancel()
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            target.unlink(missing_ok=True)
            item.update(status="failed", error=str(exc)[:300])
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            store.save_item(item)

    async def external(self, parent, module):
        record, _ = self.item(parent["url"] + "#adapter=" + module, parent["url"], "media_job")
        folder = self.folder / record["id"]
        folder.mkdir(exist_ok=True)
        record.update(status="fetching", adapter=module)
        store.save_item(record)
        if module == "yt_dlp":
            fmt = "worst[ext=mp4]/worst" if self.spec.media_quality == "low" else "bestvideo*+bestaudio/best"
            args = ["--no-playlist", "--max-downloads", "1", "--abort-on-unavailable-fragment", "--socket-timeout", "20", "--retries", str(self.spec.retries), "--max-filesize", f"{self.spec.max_file_mb}M", "-f", fmt, "--write-subs", "--sub-langs", "zh.*,en", "--no-write-info-json", "--no-progress", "--no-warnings", "--restrict-filenames", "-o", str(folder / "%(id)s.%(ext)s")]
            if self.profile and (self.profile / "cookies.txt").exists():
                args += ["--cookies", str(self.profile / "cookies.txt")]
        else:
            args = ["--range", f"1-{min(self.spec.max_resources,100)}", "--dest", str(folder), "--no-input"]
            if self.profile and (self.profile / "cookies.txt").exists():
                args += ["--cookies", str(self.profile / "cookies.txt")]
        proc = None
        communication = None
        committed = set()
        try:
            await check_url(parent["url"])
            proc = await asyncio.create_subprocess_exec(sys.executable, "-m", module, *args, parent["url"], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            communication = asyncio.create_task(proc.communicate())
            deadline = time.monotonic() + 600
            while not communication.done():
                size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
                if self.used + size > self.spec.max_total_mb * 1024**2 or any(p.stat().st_size > self.spec.max_file_mb * 1024**2 for p in folder.rglob("*") if p.is_file()):
                    raise ValueError("Media exceeds storage limit")
                if time.monotonic() > deadline:
                    raise TimeoutError("Media timeout (600 seconds)")
                await asyncio.sleep(0.25)
            stdout, stderr = await communication
            if proc.returncode not in (0, 101):
                raise ValueError(stderr.decode("utf-8", errors="replace")[-500:])
            paths = [p for p in folder.rglob("*") if p.is_file() and p.suffix not in (".part", ".ytdl")]
            if not paths:
                raise ValueError("Extractor produced no complete file")
            for path in paths:
                if self.resource_count >= self.spec.max_resources:
                    path.unlink()
                    continue
                child, _ = self.item(parent["url"] + "#file=" + path.name, parent["url"], "resource")
                size = path.stat().st_size
                if self.used + size > self.spec.max_total_mb * 1024**2:
                    raise ValueError("Media exceeds storage limit")
                with path.open("rb") as file:
                    digest = hashlib.file_digest(file, "sha256").hexdigest()
                child.update(downloaded=True, status="downloaded", path=str(path.relative_to(store.DATA)), filename=path.name, size=size, sha256=digest, content_type=inspect_file(path), adapter=module, fetched_at=store.now())
                child["resource_url"] = None
                child["resource_url_note"] = "Extractor-managed media/segments; use source_pages to resolve again"
                store.save_item(child)
                committed.add(path)
                self.used += size
                self.resource_count += 1
            record.update(status="completed", adapter=module)
        except asyncio.CancelledError:
            record.update(status="interrupted", error="Cancelled")
            raise
        except Exception as exc:
            record.update(status="failed", error=re.sub(r'https?://[^\s]+', '[URL]', str(exc))[-500:])
        finally:
            if proc and proc.returncode is None:
                try:
                    parent_proc = psutil.Process(proc.pid)
                    for child_proc in parent_proc.children(recursive=True):
                        child_proc.kill()
                    parent_proc.kill()
                except psutil.NoSuchProcess:
                    pass
                await proc.wait()
            if communication:
                await asyncio.gather(communication, return_exceptions=True)
            # Failed media must not leave unaccounted partials behind.
            if record["status"] in ("failed", "interrupted"):
                for p in folder.rglob("*"):
                    if p.is_file() and p not in committed:
                        p.unlink(missing_ok=True)
            store.save_item(record)
