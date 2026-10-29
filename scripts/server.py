"""Launch independently from a terminal and write the actual server PID."""
import json
import os
from pathlib import Path
import sys

app_root = Path(__file__).resolve().parents[1]
root = app_root.parent
sys.path.insert(0, str(app_root))
os.chdir(app_root)
os.environ["COLLECTOR_HOME"] = str(root)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root / "runtime" / "browsers")
config_file = root / "local-config.json"
if config_file.exists() and json.loads(config_file.read_text(encoding="utf-8-sig")).get("allow_fake_ip"):
    os.environ["COLLECTOR_ALLOW_FAKE_IP"] = "1"
(root / "logs").mkdir(exist_ok=True)
sys.stdout = (root / "logs" / "server.out.log").open("a", encoding="utf-8", buffering=1)
sys.stderr = (root / "logs" / "server.err.log").open("a", encoding="utf-8", buffering=1)
(root / "logs" / "server.pid").write_text(str(os.getpid()))
import uvicorn
from collector.api import app
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765))
app.state.shutdown = lambda: setattr(server, "should_exit", True)
server.run()
