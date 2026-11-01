"""Fault-path verification; all state uses tests/conftest.py's disposable home."""
import asyncio,json,sys,time,threading,subprocess,socket,os
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest,httpx
from fastapi.testclient import TestClient
from collector import media,store,network,quality,processing
from collector.api import app
from collector.models import JobSpec
from collector.engine import Collector
from test_collector import submit,wait
from test_quality import media_server

def worker(**kwargs):
    store.init()
    w=Collector(store.new_job(JobSpec(urls=['http://example.invalid/media'],network_mode='direct',quality_policy='best_available',fallback_enabled=True,**kwargs).model_dump()))
    w.job["status"]="failed";store.save_job(w.job)
    w.proxy=None;w.strict=False
    return w

@pytest.mark.parametrize('limit',['file','total'])
def test_unknown_length_child_hits_budget(tmp_path,limit):
    w=worker(max_file_mb=1 if limit=='file' else 10,max_total_mb=1 if limit=='total' else 10)
    p=tmp_path/'stream.part'
    code="import pathlib,time;p=pathlib.Path("+repr(str(p))+");f=p.open('wb');"+'\nfor i in range(100):\n f.write(b"x"*65536);f.flush();time.sleep(.03)'
    with pytest.raises(media.MediaError,match='storage limit'):
        asyncio.run(media.command([sys.executable,'-c',code],w,tmp_path,time.monotonic()+10,'downloading_media'))
    assert p.stat().st_size<3*1024**2

@pytest.mark.parametrize('fault',['subtitles','video_only','audio_only','truncated','missing_probe','merge'])
def test_incomplete_media_never_registered(tmp_path,monkeypatch,fault):
    w=worker();parent,_=w.item(w.spec.urls[0],'','seed')
    original_probe=media.probe
    async def allowed(*a):pass
    monkeypatch.setattr(network,'check_target',allowed)
    async def fake_command(args,collector,folder,deadline,phase):
        if phase=='probing_quality':return json.dumps({'formats':[{'format_id':'one','protocol':'https','vcodec':'h264','acodec':'aac','width':128,'height':72}],'duration':2}).encode()
        if phase=='downloading_media':
            out=folder/'0';out.mkdir(exist_ok=True)
            if fault=='merge':raise media.MediaError('merge_failed','Fixture merge failed')
            if fault=='subtitles':(out/'media.vtt').write_text('WEBVTT\n');return b''
            if fault=='truncated':(out/'media.mp4').write_bytes(b'\x00\x00\x00\x18ftypmp42');return b''
            args=['ffmpeg','-v','error','-y']
            if fault=='audio_only':args+=['-f','lavfi','-i','sine=frequency=500','-t','2','-c:a','aac',str(out/'media.m4a')]
            else:args+=['-f','lavfi','-i','color=s=128x72:r=2','-t','2','-c:v','libx264',str(out/'media.mp4')]
            subprocess.run(args,check=True,capture_output=True);return b''
        # Real ffprobe for generated files, except the missing-tool condition.
        p=subprocess.run(args,capture_output=True)
        if p.returncode:raise media.MediaError('invalid_media','ffprobe rejected malformed container')
        return p.stdout
    monkeypatch.setattr(media,'command',fake_command)
    if fault=='missing_probe':monkeypatch.setattr(media.shutil,'which',lambda name:None)
    asyncio.run(media.collect(w,parent))
    records=store.items(w.job['id'])
    assert not any(i.get('downloaded') for i in records)
    job=next(i for i in records if i['role']=='media_job')
    assert job['status']=='failed'
    assert not list((w.folder/job['id']).rglob('*.*'))


def test_disk_reserve_stops_child(tmp_path,monkeypatch):
    from collector import limits
    from collections import namedtuple
    w=worker();usage=namedtuple('usage','total used free')
    monkeypatch.setattr(limits.shutil,'disk_usage',lambda p:usage(1000,999,1))
    with pytest.raises(ValueError,match='Disk reserve'):
        asyncio.run(media.command([sys.executable,'-c','import time;time.sleep(30)'],w,tmp_path,time.monotonic()+5,'downloading_media'))

@pytest.mark.parametrize('status',[401,403])
def test_auth_denial_is_not_original_file(status):
    class H(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):self.send_response(status);self.end_headers();self.wfile.write(b'Login required')
    s=ThreadingHTTPServer(('127.0.0.1',0),H);threading.Thread(target=s.serve_forever,daemon=True).start()
    try:
        with TestClient(app) as c:
            j,items=wait(c,submit(c,[f'http://127.0.0.1:{s.server_port}/lesson.pdf'],network_mode='direct'))
            assert j['status']=='failed' and not items[0]['downloaded']
            assert items[0]['access_status']=='authorization_required_or_denied'
    finally:s.shutdown();s.server_close()

def test_http_retry_after_contract():
    times=[]
    class H(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            times.append(time.monotonic());self.send_response(429 if len(times)==1 else 200)
            self.send_header('Retry-After','2');self.send_header('Content-Type','application/octet-stream');self.end_headers()
            self.wfile.write(b'resource')
    s=ThreadingHTTPServer(('127.0.0.1',0),H);threading.Thread(target=s.serve_forever,daemon=True).start()
    try:
        with TestClient(app) as c:
            j,items=wait(c,submit(c,[f'http://127.0.0.1:{s.server_port}/file'],network_mode='direct',retries=1))
            assert items[0]['downloaded']
            assert times[1]-times[0]>=1.9, times[1]-times[0]
    finally:s.shutdown();s.server_close()


def test_restart_preserves_originals_and_queued_work():
    store.init();processing.init()
    done=store.new_job(JobSpec(urls=['http://127.0.0.1:9/'],network_mode='direct').model_dump());done['status']='completed';store.save_job(done)
    running=store.new_job(done['spec']);running['status']='running';store.save_job(running)
    queued=store.new_job(done['spec'])
    marker=store.DATA/'preserved.bin';marker.write_bytes(b'original evidence')
    env=os.environ.copy();env['COLLECTOR_HOME']=str(store.ROOT)
    result=subprocess.run([sys.executable,'-c',"from fastapi.testclient import TestClient;from collector.api import app;import time\nwith TestClient(app) as c:\n time.sleep(1)"],env=env,capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert store.get_job(running['id'])['status']=='interrupted'
    assert store.get_job(done['id'])['status']=='completed'
    assert store.get_job(queued['id'])['status'] not in ('queued','running')
    assert marker.read_bytes()==b'original evidence'


def test_audio_language_beats_extractor_quality():
    formats=[dict(format_id='v',vcodec='h264',acodec='none',width=1920,height=1080,protocol='https'),dict(format_id='zh',vcodec='none',acodec='aac',language='zh',protocol='https'),dict(format_id='en',vcodec='none',acodec='aac',language='en',protocol='https')]
    choices,_=quality.candidates({'formats':formats},JobSpec(urls=['https://example.com'],quality_policy='best_available',audio_language='zh'),100000)
    assert choices[0]['format_ids']==['v','zh']

@pytest.mark.parametrize('mode',['http','browser','media'])
def test_proxy_resolves_host_despite_direct_bypass_environment(media_server,monkeypatch,mode):
    from urllib.parse import urlsplit
    observed=[]
    class P(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_HEAD(self):self.forward('HEAD')
        def do_GET(self):self.forward('GET')
        def forward(self,method):
            observed.append(self.path)
            target=media_server+urlsplit(self.path).path
            r=httpx.request(method,target,trust_env=False)
            self.send_response(r.status_code)
            for k in ('content-type','content-length'):
                if k in r.headers:self.send_header(k,r.headers[k])
            self.end_headers()
            if method!='HEAD':self.wfile.write(r.content)
    s=ThreadingHTTPServer(('127.0.0.1',0),P);threading.Thread(target=s.serve_forever,daemon=True).start();old=network.load()
    monkeypatch.setenv('NO_PROXY','*');monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:9')
    try:
        with TestClient(app) as c:
            c.post('/api/proxies',json={'id':'dns-test','url':f'http://127.0.0.1:{s.server_port}'})
            url='http://collector-unresolvable.invalid/'+('good.m3u8' if mode=='media' else '')
            j,items=wait(c,submit(c,[url],network_mode='strict',proxy_ids=['dns-test'],browser=mode=='browser',media=mode=='media',max_links_per_page=1,retries=0))
            assert observed and items[0]['downloaded']
            if mode=='browser':assert items[0].get('rendered_path') and not items[0].get('browser_error')
            if mode=='media':assert any(i.get('adapter')=='yt_dlp' and i.get('downloaded') for i in items),items
            assert all('collector-unresolvable.invalid' in u for u in observed)
    finally:s.shutdown();s.server_close();network.save(old)


def test_media_midstream_proxy_disconnect(media_server):
    from test_upgrade import Forward
    class P(Forward):
        stopped=False
        def do_GET(self):
            if '.ts' in self.path:
                type(self).stopped=True
                threading.Thread(target=lambda:(proxy.shutdown(),proxy.server_close()),daemon=True).start()
                self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
            self.forward('GET')
    proxy=ThreadingHTTPServer(('127.0.0.1',0),P);threading.Thread(target=proxy.serve_forever,daemon=True).start();old=network.load()
    try:
        with TestClient(app) as c:
            c.post('/api/proxies',json={'id':'midstream','url':f'http://127.0.0.1:{proxy.server_port}'})
            j,items=wait(c,submit(c,[media_server+'/good.m3u8'],network_mode='strict',proxy_ids=['midstream'],media=True,retries=0,timeout_seconds=5))
            assert P.stopped and not any(i.get('adapter')=='yt_dlp' and i.get('downloaded') for i in items)
            record=next(i for i in items if i['role']=='media_job')
            assert len(record['quality']['attempts'])==1,record
            # Origin is still healthy; a direct fallback could have succeeded.
            assert httpx.get(media_server+'/2160/index.m3u8',trust_env=False).status_code==200
    finally:proxy.shutdown();proxy.server_close();network.save(old)

@pytest.mark.xfail(strict=True,reason='yt-dlp fragment retries do not honour Retry-After exactly')
def test_media_retry_after_contract(media_server):
    from test_upgrade import Forward
    times=[]
    class P(Forward):
        def do_GET(self):
            if '.ts' in self.path:
                times.append(time.monotonic())
                if len(times)==1:
                    self.send_response(503);self.send_header('Retry-After','3');self.end_headers();return
            self.forward('GET')
    proxy=ThreadingHTTPServer(('127.0.0.1',0),P);threading.Thread(target=proxy.serve_forever,daemon=True).start();old=network.load()
    try:
        with TestClient(app) as c:
            c.post('/api/proxies',json={'id':'rate','url':f'http://127.0.0.1:{proxy.server_port}'})
            j,items=wait(c,submit(c,[media_server+'/good.m3u8'],network_mode='strict',proxy_ids=['rate'],media=True,retries=1))
            assert any(i.get('adapter')=='yt_dlp' and i.get('downloaded') for i in items)
            assert times[1]-times[0]>=2.9,times[1]-times[0]
    finally:proxy.shutdown();proxy.server_close();network.save(old)


def test_webrtc_udp_control_and_strict_policy():
    from playwright.async_api import async_playwright
    udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('127.0.0.1',0));udp.settimeout(.1)
    packets=[];stop=threading.Event()
    def listen():
        while not stop.is_set():
            try:data,_=udp.recvfrom(4096);packets.append(data)
            except socket.timeout:pass
    thread=threading.Thread(target=listen,daemon=True);thread.start()
    async def exercise():
        async with async_playwright() as p:
            async def gather(options):
                b=await p.chromium.launch(headless=True,**options)
                page=await b.new_page()
                await page.evaluate('''async port=>{const pc=new RTCPeerConnection({iceServers:[{urls:'stun:127.0.0.1:'+port}]});pc.createDataChannel('test');await pc.setLocalDescription(await pc.createOffer());await new Promise(r=>setTimeout(r,2500));pc.close()}''',udp.getsockname()[1])
                await b.close()
            await gather({})
            assert packets,'Positive control must send STUN before judging strict policy'
            packets.clear()
            await gather(network.browser_options(network.ProxyConfig(id='closed',url='http://127.0.0.1:9')))
            assert not packets,'Strict browser emitted non-proxied UDP'
    try:asyncio.run(exercise())
    finally:stop.set();thread.join(1);udp.close()


@pytest.mark.parametrize('formula',['3 + 2 = 5','6 ÷ 2 = 3','2 × 3 = 6','9 − 4 = 5'])
def test_pure_formula_never_sent_to_translation_model(formula):
    from collector.process_worker import Translator
    t=Translator.__new__(Translator)
    assert t.translate(formula)==formula

@pytest.mark.parametrize('host',['redirect.invalid','[2001:db8::123]'])
def test_redirect_and_ipv6_destination_use_proxy(host):
    seen=[]
    class P(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_HEAD(self):self.do_GET(head=True)
        def do_GET(self,head=False):
            seen.append(self.path)
            if self.path.endswith('/first'):
                self.send_response(302);self.send_header('Location','http://second.invalid/final');self.end_headers()
            else:
                self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.end_headers()
                if not head:self.wfile.write(b'proxied original')
    proxy=ThreadingHTTPServer(('127.0.0.1',0),P);threading.Thread(target=proxy.serve_forever,daemon=True).start();old=network.load()
    try:
        with TestClient(app) as c:
            c.post('/api/proxies',json={'id':'redirect','url':f'http://127.0.0.1:{proxy.server_port}'})
            j,items=wait(c,submit(c,['http://'+host+'/first'],network_mode='strict',proxy_ids=['redirect'],retries=0))
            assert items[0]['downloaded']
            assert any(host.strip('[]') in u for u in seen) and any('second.invalid/final' in u for u in seen),seen
    finally:proxy.shutdown();proxy.server_close();network.save(old)

@pytest.mark.parametrize('long_wait',[False,True])
def test_http_retry_after_date_or_excess_budget(long_wait):
    from email.utils import formatdate
    times=[]
    class H(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            times.append(time.monotonic());self.send_response(503 if len(times)==1 else 200)
            self.send_header('Retry-After','600' if long_wait else formatdate(time.time()+3,usegmt=True));self.end_headers();self.wfile.write(b'original')
    s=ThreadingHTTPServer(('127.0.0.1',0),H);threading.Thread(target=s.serve_forever,daemon=True).start()
    try:
        with TestClient(app) as c:
            j,items=wait(c,submit(c,[f'http://127.0.0.1:{s.server_port}/file'],network_mode='direct',timeout_seconds=5,retries=1))
            if long_wait:
                assert len(times)==1 and j['status']=='failed'
                assert 'Retry-After' in items[0]['error']
            else:assert items[0]['downloaded'] and times[1]-times[0]>=1.9
    finally:s.shutdown();s.server_close()
