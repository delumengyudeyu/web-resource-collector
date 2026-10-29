import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("COLLECTOR_HOME", Path(__file__).resolve().parents[2]))
DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "collector.sqlite3"


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    db = sqlite3.connect(DB, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    try:
        with db:
            yield db
    finally:
        db.close()


def init():
    with connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, body TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS items_job ON items(job_id)")


def save_job(job):
    job["updated_at"] = now()
    with connection() as db:
        db.execute("INSERT INTO jobs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body", (job["id"], json.dumps(job, ensure_ascii=False)))


def get_job(job_id):
    with connection() as db:
        row = db.execute("SELECT body FROM jobs WHERE id=?", (job_id,)).fetchone()
    return json.loads(row[0]) if row else None


def list_jobs():
    with connection() as db:
        return [json.loads(r[0]) for r in db.execute("SELECT body FROM jobs ORDER BY rowid DESC LIMIT 200")]


def new_job(spec):
    job = dict(id=uuid.uuid4().hex, spec=spec, status="queued", created_at=now(), events=[], pages=0, bytes=0)
    save_job(job)
    return job


def save_item(item):
    with connection() as db:
        db.execute("INSERT INTO items VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body", (item["id"], item["job_id"], json.dumps(item, ensure_ascii=False)))


def items(job_id):
    with connection() as db:
        return [json.loads(r[0]) for r in db.execute("SELECT body FROM items WHERE job_id=? ORDER BY rowid", (job_id,))]


def get_item(item_id):
    with connection() as db:
        r = db.execute("SELECT body FROM items WHERE id=?", (item_id,)).fetchone()
    return json.loads(r[0]) if r else None
