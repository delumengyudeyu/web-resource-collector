"""Collect installed package license notices for audit; does not copy package source."""
from importlib.metadata import distributions
from pathlib import Path
import json

root = Path(__file__).resolve().parents[1] / "third_party"
root.mkdir(exist_ok=True)
records = []
for dist in distributions():
    name = dist.metadata["Name"]
    license_id = dist.metadata.get("License-Expression") or dist.metadata.get("License", "See bundled notice")
    records.append({"name": name, "version": dist.version, "license": license_id[:300], "homepage": dist.metadata.get("Home-page", ""), "project_urls": dist.metadata.get_all("Project-URL", [])})
    for f in dist.files or []:
        if f.suffix.lower() in ('.py','.pyc','.pyo'):
            continue
        if ".dist-info/" in str(f) and ("license" in str(f).lower() or f.name.upper().startswith(("NOTICE", "COPYING"))):
            source = Path(dist.locate_file(f))
            if source.is_file() and source.stat().st_size < 1_000_000:
                target = root / name / f.name
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(source.read_bytes())
(root / "dependencies.json").write_text(json.dumps(sorted(records, key=lambda x: x["name"].lower()), indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Recorded {len(records)} dependencies")
