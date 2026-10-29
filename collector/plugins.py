"""Installed entry points can discover extra resources without replacing the API.

Entry-point group: webcollector.discovery. A synchronous callable receives
(html: str, url: str) and returns dictionaries with url, role, label, order.
Use role='attachment' for actual original-file download links, not previews.
Only install trusted plugins: these run as local Python code.
"""
from importlib.metadata import entry_points


def extra_links(html, url):
    refs = []
    for ep in entry_points(group="webcollector.discovery"):
        refs.extend(ep.load()(html, url))
    return refs
