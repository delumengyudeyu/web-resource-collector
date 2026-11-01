"""Durable derived-content jobs. One isolated worker at a time keeps the UI responsive."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import uuid
from typing import Literal
from pydantic import BaseModel, Field
from . import store, network

TASKS = {}
SLOT = asyncio.Semaphore(1)

class ProcessSpec(BaseModel):
    action: Literal['extract', 'ocr', 'transcribe', 'translate']
    source_language: Literal['zh', 'en'] = 'zh'
    target_language: Literal['zh', 'en'] = 'en'
    source_process_id: str | None = None
    max_pages: int = Field(default=100, ge=1, le=500)
    max_seconds: int = Field(default=3600, ge=10, le=14400)
    max_output_mb: int = Field(default=20, ge=1, le=100)

def init():
    with store.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS processes (id TEXT PRIMARY KEY, item_id TEXT NOT NULL, body TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS processes_item ON processes(item_id)')

def save(record):
    record['updated_at'] = store.now()
    with store.connection() as db:
        db.execute('INSERT INTO processes VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                   (record['id'],record['item_id'],json.dumps(record,ensure_ascii=False)))

def get(pid):
    with store.connection() as db:
        row = db.execute('SELECT body FROM processes WHERE id=?',(pid,)).fetchone()
    return json.loads(row[0]) if row else None

def listing(item_id=None):
    with store.connection() as db:
        rows = db.execute('SELECT body FROM processes' + (' WHERE item_id=?' if item_id else '') + ' ORDER BY rowid DESC',
                          (item_id,) if item_id else ()).fetchall()
    return [json.loads(r[0]) for r in rows]

def capabilities():
    models = store.ROOT / 'runtime' / 'models'
    return {'translation': {p: any((models/p).glob('**/model.bin')) for p in ['zh-en','en-zh']},
            'ocr': importlib.util.find_spec('rapidocr_onnxruntime') is not None,
            'speech': (models/'whisper-tiny'/'model.bin').is_file(),
            'external_services_enabled': False, 'quality': 'machine_output_requires_review'}

def create(item, spec):
    if not item.get('downloaded'):
        raise ValueError('Download the original resource first')
    if spec.source_process_id:
        source = get(spec.source_process_id)
        if not source or source['item_id'] != item['id'] or source['status'] not in ('completed','partial'):
            raise ValueError('Source processing result must belong to this file and be completed')
    caps = capabilities()
    if spec.action == 'translate' and spec.source_language != spec.target_language and not caps['translation'].get(spec.source_language+'-'+spec.target_language):
        raise ValueError('Local translation model is not installed')
    if spec.action == 'ocr' and not caps['ocr'] or spec.action == 'transcribe' and not caps['speech']:
        raise ValueError('Required local model is not installed')
    record = dict(id=uuid.uuid4().hex, item_id=item['id'], spec=spec.model_dump(), status='queued',
                  created_at=store.now(), progress=0, engine='local', needs_review=True)
    save(record)
    enqueue(record)
    return record

def enqueue(record):
    task = asyncio.create_task(run(record))
    TASKS[record['id']] = task
    task.add_done_callback(lambda _: TASKS.pop(record['id'],None))

async def run(record):
    proc = None
    comm = None
    try:
        async with SLOT:
            record['status'] = 'running'
            save(record)
            env = network.child_env(None)
            env.update(COLLECTOR_HOME=str(store.ROOT), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
            proc = await asyncio.create_subprocess_exec(sys.executable,'-m','collector.process_worker',record['id'],
                    cwd=str(Path(__file__).resolve().parents[1]),env=env,stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,creationflags=0x08000000 if os.name=='nt' else 0)
            comm = asyncio.create_task(proc.communicate())
            _, stderr = await asyncio.wait_for(asyncio.shield(comm), timeout=7200)
            if proc.returncode:
                record = get(record['id'])
                record.update(status='failed',error='Worker exited: '+network.scrub(stderr.decode(errors='replace')[-400:]))
                save(record)
    except asyncio.CancelledError:
        record = get(record['id'])
        record.update(status='cancelled')
        save(record)
        raise
    except Exception as exc:
        record = get(record['id'])
        record.update(status='failed',error=network.scrub(exc))
        save(record)
    finally:
        if proc and proc.returncode is None:
            proc.kill()
        if comm:
            await asyncio.gather(comm,return_exceptions=True)
        if get(record['id'])['status'] not in ('completed','partial'):
            folder = store.DATA/'derived'/record['id']
            if folder.exists():
                for p in folder.iterdir():
                    if p.is_file():
                        p.unlink()

def safe_path(relative):
    p = (store.DATA / relative).resolve()
    if not p.is_relative_to(store.DATA.resolve()) or not p.is_file():
        raise ValueError('Source file missing')
    return p
