"""Submit bounded public-source probes through the real HTTP API; keep payloads local."""
import json
from pathlib import Path
import httpx

out = Path(__file__).resolve().parents[2] / "research" / "live-jobs.json"
client = httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, timeout=30)
cases = [
    ("中文绘本及原始图片/PDF", {"urls": ["https://www.storybookscanada.ca/stories/zh/0112/"], "max_resources": 150, "max_links_per_page": 180, "interval_seconds": .2, "concurrency": 5, "timeout_seconds": 20}),
    ("中文二年级课件PDF原件", {"urls": ["https://www.jsfzzy.com/storage/course/64dd92d7dad30.pdf"], "max_resources": 5}),
    ("中文一年级讲播视频", {"urls": ["https://edu.cctv.com/2020/02/17/VIDEVZmTlUifJLxCG2RSRY9b200217.shtml"], "media": True, "max_links_per_page": 5, "max_file_mb": 200, "max_total_mb": 300, "timeout_seconds": 20}),
    ("真实动态网页", {"urls": ["https://quotes.toscrape.com/js/"], "browser": True, "max_links_per_page": 5, "max_resources": 10, "timeout_seconds": 20}),
    ("课件站访问限制", {"urls": ["https://ziyk.szns.edu.cn/rc/res/show/id/425952"], "max_links_per_page": 5, "timeout_seconds": 15, "retries": 0}),
]
results = []
for label, spec in cases:
    r = client.post("/api/jobs", json=spec)
    r.raise_for_status()
    job = r.json()
    results.append({"case": label, "job_id": job["id"], "spec": spec})
    print(label, job["id"], flush=True)
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
