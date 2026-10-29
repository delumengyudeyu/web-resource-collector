import sys
import psutil
p = psutil.Process(int(sys.argv[1]))
if "collector.api:app" not in " ".join(p.cmdline()) and "scripts\\server.py" not in " ".join(p.cmdline()):
    raise SystemExit("Process identity mismatch")
children = p.children(recursive=True)
for child in reversed(children):
    try:
        child.terminate()
    except psutil.NoSuchProcess:
        pass
p.terminate()
psutil.wait_procs(children + [p], timeout=5)
