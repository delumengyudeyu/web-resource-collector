"""Explicit provisioning only. Runtime processing never downloads models."""
import hashlib
import json
from pathlib import Path
import zipfile
import httpx

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / 'runtime' / 'models'

def main():
    MODELS.mkdir(parents=True, exist_ok=True)
    records = []
    with httpx.Client(follow_redirects=True, timeout=180) as c:
        index = c.get('https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json')
        index.raise_for_status()
        for pair in [('zh', 'en'), ('en', 'zh')]:
            package = next(p for p in index.json() if (p.get('from_code'), p.get('to_code')) == pair)
            archive = MODELS / (package['code'] + '.argosmodel')
            if not archive.exists():
                with c.stream('GET', package['links'][0]) as r:
                    r.raise_for_status()
                    with archive.with_suffix('.part').open('wb') as f:
                        for chunk in r.iter_bytes():
                            f.write(chunk)
                archive.with_suffix('.part').replace(archive)
            target = MODELS / ('-'.join(pair))
            target.mkdir(exist_ok=True)
            with zipfile.ZipFile(archive) as z:
                for name in z.namelist():
                    if not (target / name).resolve().is_relative_to(target.resolve()):
                        raise ValueError('Unsafe model archive')
                z.extractall(target)
            with archive.open('rb') as f:
                digest = hashlib.file_digest(f, 'sha256').hexdigest()
            records.append(dict(pair=pair, source=package['links'][0], sha256=digest, size=archive.stat().st_size))
            print('Translation model installed:', pair, flush=True)
        target = MODELS / 'whisper-tiny'
        target.mkdir(exist_ok=True)
        for name in ['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt']:
            path = target / name
            if not path.exists():
                with c.stream('GET', 'https://huggingface.co/Systran/faster-whisper-tiny/resolve/main/' + name) as r:
                    r.raise_for_status()
                    with path.with_suffix('.part').open('wb') as f:
                        for chunk in r.iter_bytes():
                            f.write(chunk)
                path.with_suffix('.part').replace(path)
        print('Local speech model installed', flush=True)
    (MODELS / 'manifest.json').write_text(json.dumps(records, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
