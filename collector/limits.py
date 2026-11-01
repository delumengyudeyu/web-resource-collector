import shutil
import time
from . import store, network

_usage = (0,0)

def disk_used():
    global _usage
    if time.monotonic()-_usage[0]>1:
        _usage=(time.monotonic(),sum(p.stat().st_size for p in store.DATA.rglob('*') if p.is_file()))
    return _usage[1]

def ensure_storage(extra=0):
    if disk_used()+extra>network.load().storage_limit_mb*1024**2:
        raise ValueError('Global storage limit reached')
    if shutil.disk_usage(store.DATA).free-extra<512*1024**2:
        raise ValueError('Disk reserve reached')
