"""Explicit per-job proxy policy; secrets stay outside the repository and job manifests."""
import asyncio
import hashlib
import ipaddress
import json
import os
import re
from urllib.parse import urlsplit, unquote
import httpx
from pydantic import BaseModel, Field, field_validator
from . import store

class ProxyConfig(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    url: str

    @field_validator('url')
    @classmethod
    def validate_url(cls, value):
        p = urlsplit(value)
        if p.scheme != 'http' or not p.hostname or not p.port or p.query or p.fragment or p.path not in ('', '/'):
            raise ValueError('Use an HTTP CONNECT proxy with an explicit port')
        # Numeric proxy addresses avoid a local DNS request for the proxy itself.
        try:
            ipaddress.ip_address(p.hostname)
        except ValueError:
            raise ValueError('Proxy endpoint must use a numeric IP address')
        return value.rstrip('/')

class Settings(BaseModel):
    proxies: list[ProxyConfig] = Field(default_factory=list, max_length=30)
    default_strict: bool = False
    storage_limit_mb: int = Field(default=20000, ge=500, le=1000000)

def path():
    return store.ROOT / 'network-settings.json'

def load():
    return Settings.model_validate_json(path().read_text(encoding='utf-8')) if path().exists() else Settings()

def save(value):
    if len({p.id for p in value.proxies}) != len(value.proxies):
        raise ValueError('Duplicate proxy ID')
    # A masked entry means retain the existing secret, never replace it with asterisks.
    temp = path().with_suffix('.tmp')
    temp.write_text(value.model_dump_json(indent=2), encoding='utf-8')
    temp.replace(path())

def public():
    s = load()
    return dict(default_strict=s.default_strict, storage_limit_mb=s.storage_limit_mb,
                proxies=[{'id': p.id, 'configured': True} for p in s.proxies],
                protection='application_proxy_only',
                limitations=['Not an OS firewall or VPN kill switch', 'The destination sees the final proxy exit',
                             'External translation APIs are disabled; processing is local'])

def scrub(value):
    return re.sub(r'(?:https?|socks5h?)://[^\s]+', '[URL]', str(value))[:700]

def candidates(spec):
    entries = load().proxies
    ids = spec.proxy_ids
    if ids:
        by_id = {p.id: p for p in entries}
        if any(i not in by_id for i in ids):
            raise ValueError('Proxy ID is not configured')
        entries = [by_id[i] for i in ids]
    if spec.network_mode == 'strict' and not entries:
        raise ValueError('Strict proxy mode requires a configured proxy')
    if spec.network_mode != 'strict':
        return []
    if spec.profile:
        p = store.ROOT / 'profiles' / spec.profile / 'network.json'
        if p.exists():
            pinned = json.loads(p.read_text())['proxy_id']
            entries = [v for v in entries if v.id == pinned]
            if not entries:
                raise ValueError('Login profile requires its pinned proxy')
        # Profiles never silently switch endpoints.
        entries = entries[:1]
    elif len(entries) > 1:
        n = int(hashlib.sha256(spec.urls[0].encode()).hexdigest()[:8], 16) % len(entries)
        entries = entries[n:] + entries[:n]
    return entries

async def select(spec):
    entries = candidates(spec)
    if not entries:
        return None
    # Establish an actual proxied TLS request, not merely a listening TCP port.
    for p in entries:
        try:
            async with httpx.AsyncClient(proxy=p.url, trust_env=False, timeout=12) as c:
                r = await c.head(spec.urls[0])
                if r.status_code != 407:
                    return p
        except (httpx.HTTPError, OSError):
            pass
    raise ValueError('All selected proxies failed; direct fallback is disabled')

def browser_options(proxy):
    if not proxy:
        return {}
    u = urlsplit(proxy.url)
    server = f'http://{u.hostname}:{u.port}' if ':' not in u.hostname else f'http://[{u.hostname}]:{u.port}'
    options = {'server': server, 'bypass': '<-loopback>'}
    if u.username:
        options.update(username=unquote(u.username), password=unquote(u.password or ''))
    return dict(proxy=options, args=['--disable-quic', '--disable-background-networking',
        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
        '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE ' + u.hostname])

def child_env(proxy):
    env = {k:v for k,v in os.environ.items() if k.lower() not in
           ('http_proxy','https_proxy','all_proxy','no_proxy')}
    if proxy:
        env.update(HTTP_PROXY=proxy.url, HTTPS_PROXY=proxy.url, ALL_PROXY=proxy.url, NO_PROXY='')
    return env

async def check_target(url, strict=False):
    p = urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('Invalid HTTP(S) URL')
    if not strict:
        from .engine import check_url
        return await check_url(url)
    if os.environ.get('COLLECTOR_ALLOW_PRIVATE') == '1':
        return
    if p.hostname.lower() in ('localhost',) or p.hostname.endswith(('.localhost','.local','.internal')) or '.' not in p.hostname:
        raise ValueError('Local destination is disabled')
    try:
        address = ipaddress.ip_address(p.hostname)
    except ValueError:
        # Resolve the target at the proxy, not locally. Proxy must restrict private destinations.
        return
    if not address.is_global:
        raise ValueError('Private/literal Fake-IP destination is disabled')
