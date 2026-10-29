"""Check the Git index before publishing. Reports paths, never matched secrets."""
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
files = subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
blocked = []
patterns = [r'AKIA[0-9A-Z]{16}', r'gh[pousr]_[A-Za-z0-9]{30,}', r'sk-[A-Za-z0-9_-]{30,}', '-----BEGIN ' + r'(?:RSA |EC |OPENSSH )?PRIVATE KEY-----']
for name in filter(None, files):
    path = Path(name)
    if any(part in {'data','profiles','runtime','research','.venv'} for part in path.parts) or path.suffix.lower() in {'.sqlite3','.db','.pdf','.ppt','.pptx','.mp4','.mp3','.zip','.env','.log'}:
        blocked.append(name)
        continue
    content = subprocess.check_output(['git','show',':'+name],cwd=root).decode('utf-8',errors='replace')
    if any(re.search(p,content) for p in patterns):
        blocked.append(name)
if blocked:
    raise SystemExit('Review required for staged paths: ' + ', '.join(blocked))
print(f'Checked {len(list(filter(None,files)))} staged files: no captured payloads, credentials files or checked secret patterns')
