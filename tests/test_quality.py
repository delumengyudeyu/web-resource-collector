import functools,json,subprocess,threading,time
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from collector.api import app
from collector.models import JobSpec
from collector import quality,network,store
from test_collector import wait,submit

def test_policy_compatibility_and_candidates():
    assert JobSpec(urls=['https://example.com'],media_quality='low').quality_policy=='data_saver'
    with pytest.raises(ValueError):JobSpec(urls=['https://example.com'],media_quality='low',quality_policy='best_available')
    formats=[dict(format_id=str(h),width=w,height=h,vcodec='h264',acodec='aac',protocol='https',filesize=100) for w,h in [(3840,2160),(1920,1080),(1280,720),(1080,1920)]]
    s=JobSpec(urls=['https://example.com'],quality_policy='best_available')
    choices,_=quality.candidates({'formats':formats},s,1000)
    assert choices[0]['height']==2160
    s.quality_policy='cap_1080'
    choices,_=quality.candidates({'formats':formats},s,1000)
    assert all(min(c['width'],c['height'])<=1080 for c in choices)
    assert any(c['height']==1920 for c in choices)
    assert quality.failure_reason('HTTP Error 429')=='network_temporary'
    assert quality.failure_reason('ProxyError connection refused')=='proxy_unavailable'

@pytest.fixture(scope='module')
def media_server(tmp_path_factory):
    root=tmp_path_factory.mktemp('media')
    for height in (2160,1080,720):
        folder=root/str(height);folder.mkdir()
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c=green:s={height*16//9}x{height}:r=2',
            '-f','lavfi','-i','sine=frequency=500:sample_rate=44100','-t','2','-c:v','libx264','-threads','2','-preset','ultrafast','-c:a','aac',
            '-f','hls','-hls_time','2','-hls_list_size','0',str(folder/'index.m3u8')],check=True,capture_output=True)
    (root/'broken').mkdir()
    (root/'dash').mkdir()
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=1920x1080:r=2',
        '-f','lavfi','-i','sine=frequency=600:sample_rate=44100','-t','2','-c:v','libx264','-threads','2','-preset','ultrafast',
        '-c:a','aac','-f','dash','-seg_duration','2','stream.mpd'],cwd=root/'dash',check=True,capture_output=True)
    (root/'broken/index.m3u8').write_text('#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:2,\nmissing.ts\n#EXT-X-ENDLIST\n')
    for name,top in [('good','2160'),('fallback','broken')]:
        (root/(name+'.m3u8')).write_text('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=3840x2160,CODECS="avc1.42e033,mp4a.40.2"\n'+top+'/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1920x1080,CODECS="avc1.42e033,mp4a.40.2"\n1080/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720,CODECS="avc1.42e033,mp4a.40.2"\n720/index.m3u8\n')
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*a):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(root)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close()

@pytest.mark.parametrize('route,policy,fallback,expected',[('good','best_available',True,2160),('good','cap_720',True,720),('fallback','best_available',True,1080),('fallback','best_available',False,None)])
def test_real_media_selection(media_server,route,policy,fallback,expected):
    with TestClient(app) as c:
        j,items=wait(c,submit(c,[media_server+'/'+route+'.m3u8'],media=True,network_mode='direct',quality_policy=policy,fallback_enabled=fallback,retries=0,media_timeout_seconds=60))
        media=[i for i in items if i.get('adapter')=='yt_dlp' and i.get('downloaded') and i.get('quality')]
        if expected is None:
            assert not media
            assert any(i.get('failure_reason') for i in items)
        else:
            assert media,items
            q=media[0]['quality']
            assert q['height']==expected and q['audio_codec']=='aac',q
            assert q['validation']['probe']=='passed'
            if route=='fallback':assert q['selection_state']=='fallback' and len(q['attempts'])==2
            assert c.get('/api/files/'+media[0]['id']).content

def test_settings_default_and_historical_retry():
    old=network.load()
    try:
        with TestClient(app) as c:
            c.patch('/api/settings',json={'default_strict':False,'storage_limit_mb':20000,'default_quality_policy':'cap_720','default_fallback_enabled':False})
            # Resolution occurs before the queued task starts.
            r=c.post('/api/jobs',json={'urls':['http://127.0.0.1:9/']}).json()
            assert r['spec']['quality_policy']=='cap_720' and not r['spec']['fallback_enabled']
            c.post('/api/jobs/'+r['id']+'/cancel')
            assert c.post('/api/jobs',json={'urls':['https://example.com'],'media_quality':'low','quality_policy':'best_available'}).status_code==422
    finally:network.save(old)

def test_separate_dash_tracks_are_merged(media_server):
    with TestClient(app) as c:
        j,items=wait(c,submit(c,[media_server+'/dash/stream.mpd'],media=True,network_mode='direct',quality_policy='best_available',media_timeout_seconds=60))
        saved=[i for i in items if i.get('quality') and i.get('adapter')=='yt_dlp' and i.get('downloaded')]
        assert len(saved)==1,[(i.get('failure_reason'),i.get('error'),i.get('quality')) for i in items]
        q=saved[0]['quality']
        assert q['height']==1080 and q['audio_codec']=='aac' and len(q['format_ids'])==2
        assert q['source_kind']=='source_streams_remux'

def test_budget_drm_protocol_and_language():
    formats=[dict(format_id='4k',width=3840,height=2160,filesize=10000,vcodec='h264',acodec='aac',protocol='https'),
             dict(format_id='720',width=1280,height=720,filesize=500,vcodec='h264',acodec='aac',protocol='https'),
             dict(format_id='drm',has_drm=True,protocol='https'),dict(format_id='rtmp',protocol='rtmp')]
    spec=JobSpec(urls=['https://example.com'],quality_policy='best_available')
    selected,excluded=quality.candidates({'formats':formats},spec,1000)
    assert selected[0]['height']==720
    assert {e['reason'] for e in excluded}=={'size_limit','access_restricted','protocol_disallowed'}

def test_image_candidates_keep_original_bytes_and_groups(media_server):
    from collector.content import discover
    _,refs=discover('<img src="small.jpg" srcset="small.jpg 100w, large.jpg 1600w"><picture><source srcset="crop.jpg 2000w"><img src="other.jpg"></picture>',media_server)
    assert refs[0]['url'].endswith('large.jpg')
    assert refs[0]['image_group']==refs[1]['image_group']
    assert refs[-1]['image_group']!=refs[0]['image_group']
    assert 'image_group' not in next(r for r in refs if r['url'].endswith('crop.jpg'))

def test_legacy_retry_preserves_policy():
    with TestClient(app) as c:
        spec=JobSpec(urls=['http://127.0.0.1:9/'],network_mode='direct',media_quality='low').model_dump()
        spec.pop('quality_policy');spec.pop('fallback_enabled')
        j=store.new_job(spec);j['status']='failed';store.save_job(j)
        r=c.post('/api/jobs/'+j['id']+'/retry')
        assert r.status_code==202 and r.json()['spec']['quality_policy']=='data_saver'
        c.post('/api/jobs/'+r.json()['id']+'/cancel')
        assert store.get_job(j['id'])['status']=='failed'

def test_corrupt_media_rejected(tmp_path):
    import asyncio
    from collector.engine import Collector
    from collector.media import probe,MediaError
    job=store.new_job(JobSpec(urls=['https://example.com'],network_mode='direct').model_dump())
    worker=Collector(job);worker.proxy=None
    path=tmp_path/'fake.mp4';path.write_bytes(b'not a video')
    with pytest.raises(MediaError):
        asyncio.run(probe(path,worker,tmp_path,time.monotonic()+10))

def test_cancel_media_child_cleanup(tmp_path):
    import asyncio,sys,psutil
    from collector.engine import Collector
    from collector.media import command
    job=store.new_job(JobSpec(urls=['https://example.com'],network_mode='direct').model_dump())
    worker=Collector(job);worker.proxy=None
    pidfile=tmp_path/'pid.txt'
    async def check():
        t=asyncio.create_task(command([sys.executable,'-c',"import os,time,pathlib;pathlib.Path("+repr(str(pidfile))+").write_text(str(os.getpid()));time.sleep(30)"],worker,tmp_path,time.monotonic()+40,'downloading_media'))
        for _ in range(100):
            if pidfile.exists():break
            await asyncio.sleep(.05)
        assert pidfile.exists()
        pid=int(pidfile.read_text());t.cancel()
        await asyncio.gather(t,return_exceptions=True)
        assert not psutil.pid_exists(pid)
    asyncio.run(check())

def test_media_proxy_failure_does_not_downgrade_or_connect_direct(media_server):
    from test_upgrade import Forward
    class BrokenMediaProxy(Forward):
        blocked=0
        def do_GET(self):
            if '.ts' in self.path:
                type(self).blocked+=1
                self.send_response(503);self.end_headers();return
            self.forward('GET')
    old=network.load();proxy=ThreadingHTTPServer(('127.0.0.1',0),BrokenMediaProxy)
    thread=threading.Thread(target=proxy.serve_forever,daemon=True);thread.start()
    try:
        with TestClient(app) as c:
            c.post('/api/proxies',json={'id':'media-break','url':f'http://127.0.0.1:{proxy.server_port}'})
            j,items=wait(c,submit(c,[media_server+'/good.m3u8'],media=True,network_mode='strict',proxy_ids=['media-break'],retries=0,quality_policy='best_available'))
            assert BrokenMediaProxy.blocked>0
            assert not any(i.get('adapter')=='yt_dlp' and i.get('downloaded') for i in items)
            record=next(i for i in items if i.get('role')=='media_job')
            assert record['failure_reason']=='network_temporary',record
            assert len(record['quality']['attempts'])==1
    finally:
        proxy.shutdown();proxy.server_close();network.save(old)
