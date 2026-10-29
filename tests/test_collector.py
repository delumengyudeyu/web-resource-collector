import os
import tempfile
os.environ["COLLECTOR_HOME"] = tempfile.mkdtemp(prefix="webcollector-tests-")
os.environ["COLLECTOR_ALLOW_PRIVATE"] = "1"
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from collector.api import app
from collector import store

ASSETS = Path(os.environ["COLLECTOR_HOME"]) / "fixtures"
ASSETS.mkdir()
deck = Presentation()
slide = deck.slides.add_slide(deck.slide_layouts[0])
slide.shapes.title.text = "一年级：春夏秋冬"
deck.save(ASSETS / "lesson.pptx")
RAW = b"arbitrary original format\x00\xff" * 100


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        code, ct, body = 200, "text/html; charset=utf-8", b""
        if self.path == "/":
            body = '<html><title>资源测试</title><h1>真实 HTTP 文件下载</h1><a href="/download">课件下载</a><a href="/file.odd">任意格式</a><a href="/fake.pdf">错误的 PDF</a><a href="/child">下一页</a></html>'.encode()
        elif self.path == "/download":
            ct = "application/octet-stream"
            body = (ASSETS / "lesson.pptx").read_bytes()
        elif self.path in ("/file.odd", "/same.odd"):
            ct, body = "application/octet-stream", RAW
        elif self.path == "/fake.pdf":
            body = b"<!doctype html><html><h1>Please log in</h1></html>"
        elif self.path == "/child":
            body = b'<html><title>Child</title><a href="/grandchild">more</a></html>'
        elif self.path == "/grandchild":
            body = b"<html><title>Grandchild</title></html>"
        elif self.path == "/dynamic":
            body = b'<html><title>Dynamic</title><body><script>setTimeout(()=>{document.body.innerHTML=`<h1>JavaScript loaded</h1><a download href="/file.odd">download</a>`},100)</script></body></html>'
        elif self.path == "/blob":
            body = b'''<html><title>Browser download</title><body><button id="download" onclick="let a=document.createElement('a'); a.href=window.URL.createObjectURL(new Blob(['browser original file'],{type:'application/octet-stream'})); a.download='lesson.custom'; a.click()">Download</button></body></html>'''
        elif self.path == "/large":
            ct, body = "application/octet-stream", b"x" * (2 * 1024**2)
        elif self.path == "/slow":
            ct, body = "application/octet-stream", b"x" * (4 * 1024**2)
        else:
            code = 404
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        if self.path == "/download":
            self.send_header("Content-Disposition", "attachment; filename=lesson.pptx")
        self.end_headers()
        if not head:
            try:
                if self.path == "/slow":
                    for offset in range(0, len(body), 32768):
                        self.wfile.write(body[offset:offset+32768])
                        self.wfile.flush()
                        time.sleep(.05)
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def wait(client, job_id):
    for _ in range(300):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job, client.get(f"/api/jobs/{job_id}/results").json()["items"]
        time.sleep(.1)
    raise AssertionError("Job timeout")


def submit(client, urls, **kwargs):
    response = client.post("/api/jobs", json={"urls": urls, "interval_seconds": .1, **kwargs})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_original_files_false_pdf_scope_and_hash(server):
    with TestClient(app) as c:
        job, items = wait(c, submit(c, [server + "/"]))
        ppt = next(i for i in items if i["url"].endswith("/download"))
        assert ppt["downloaded"] and ppt["extracted"]
        assert "presentationml" in ppt["content_type"]
        assert ppt["document_metadata"]["slides"] == 1
        data = c.get("/api/files/" + ppt["id"]).content
        assert data == (ASSETS / "lesson.pptx").read_bytes()
        odd = next(i for i in items if i["url"].endswith("/file.odd"))
        assert odd["sha256"] == hashlib.sha256(RAW).hexdigest()
        fake = next(i for i in items if i["url"].endswith("/fake.pdf"))
        assert not fake["downloaded"] and fake["status"] == "failed"
        assert "HTML" in fake["error"]
        assert job["status"] == "completed_with_errors"
        assert not any(i["url"].endswith("/grandchild") for i in items)


def test_depth_and_url_dedup(server):
    with TestClient(app) as c:
        job, items = wait(c, submit(c, [server + "/child", server + "/child"], max_depth=1))
        assert job["status"] == "completed"
        assert sum(i["downloaded"] for i in items) == 2
        assert len({i["url"] for i in items}) == len(items)


def test_size_limit_and_no_partials(server):
    with TestClient(app) as c:
        job, items = wait(c, submit(c, [server + "/large"], max_file_mb=1))
        assert job["status"] == "failed"
        assert items[0]["status"] == "limited" and not items[0]["downloaded"]
        assert not list((store.DATA / "jobs" / job["id"]).glob("*.part"))


def test_cancel_and_retry(server):
    with TestClient(app) as c:
        job_id = submit(c, [server + "/slow"])
        time.sleep(.4)
        cancelled = c.post(f"/api/jobs/{job_id}/cancel").json()
        assert cancelled["status"] == "cancelled"
        assert not list((store.DATA / "jobs" / job_id).glob("*.part"))
        retry = c.post(f"/api/jobs/{job_id}/retry")
        assert retry.status_code == 202 and retry.json()["id"] != job_id
        c.post('/api/jobs/' + retry.json()["id"] + '/cancel')


def test_dynamic_browser(server):
    with TestClient(app) as c:
        job, items = wait(c, submit(c, [server + "/dynamic"], browser=True, browser_wait_ms=300, scroll_steps=0))
        assert job["status"] == "completed", job
        assert any(i["downloaded"] and i["url"].endswith("/file.odd") for i in items)
        page = items[0]
        assert page["adapter"] == "http+playwright"
        assert "JavaScript loaded" in c.get('/api/files/' + page["id"] + '?text=true').text


def test_api_boundary():
    with TestClient(app) as c:
        assert c.post('/api/jobs', json={"urls": ["file:///C:/Windows/win.ini"]}).status_code == 422
        assert c.post('/api/jobs', json={"urls": ["https://example.com"], "profile": "../secret"}).status_code == 422
        assert c.post('/api/jobs', headers={"Origin": "https://evil.example"}, json={"urls": ["https://example.com"]}).status_code == 403
        assert c.get('/api/files/missing').status_code == 404


def test_blob_download_button(server):
    with TestClient(app) as c:
        job, items = wait(c, submit(c, [server + '/blob'], browser=True, download_selectors=['#download'], scroll_steps=0, browser_wait_ms=0))
        result = next(i for i in items if i.get('adapter') == 'playwright-download')
        assert result['downloaded'], result
        assert c.get('/api/files/' + result['id']).content == b'browser original file'


def test_content_dedup_and_resource_limit(server):
    with TestClient(app) as c:
        job, items = wait(c, submit(c, [server + '/file.odd', server + '/same.odd']))
        assert items[1]['duplicate_of'] == items[0]['id']
        assert items[0]['path'] == items[1]['path']
        assert job['bytes'] == len(RAW)
        job, items = wait(c, submit(c, [server + '/', server + '/same.odd'], max_resources=1))
        assert sum(i['downloaded'] for i in items) == 1
