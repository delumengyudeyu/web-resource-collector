"""Bounded media discovery, candidate attempts and post-download validation."""
import asyncio,hashlib,json,os,shutil,sys,time
from pathlib import Path
import psutil
from . import store,network,quality
from .limits import ensure_storage
from .content import inspect_file

class MediaError(Exception):
    def __init__(self,reason,message):
        self.reason=reason
        super().__init__(message)

async def command(args,collector,folder,deadline,phase):
    collector.job['phase']=phase;store.save_job(collector.job)
    proc=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
        env=network.child_env(collector.proxy) if collector.spec.network_mode!='system' else None,
        creationflags=0x08000000 if os.name=='nt' else 0)
    async def bounded(stream,watch=False):
        data=bytearray()
        while chunk:=await stream.read(65536):
            data.extend(chunk)
            if watch and b'[Merger]' in chunk:
                collector.job['phase']='merging';store.save_job(collector.job)
            if len(data)>16*1024**2:raise MediaError('output_limit','Media metadata exceeds output limit')
        return bytes(data)
    stdout=asyncio.create_task(bounded(proc.stdout,phase=='downloading_media'));stderr=asyncio.create_task(bounded(proc.stderr))
    try:
        started=time.monotonic()
        while proc.returncode is None:
            for t in (stdout,stderr):
                if t.done() and t.exception():raise t.exception()
            if time.monotonic()>deadline:raise MediaError('timeout','Media task deadline reached')
            ensure_storage()
            sizes=[p.stat().st_size for p in folder.rglob('*') if p.is_file()]
            used=sum(sizes) if phase!='verifying' else 0
            if phase!='verifying' and (used+collector.used>collector.spec.max_total_mb*1024**2 or max(sizes,default=0)>collector.spec.max_file_mb*1024**2):
                raise MediaError('size_limit','Media exceeds configured storage limit')
            collector.job.update(bytes=collector.used+used,bytes_per_second=round(used/max(1,time.monotonic()-started)))
            store.save_job(collector.job)
            await asyncio.sleep(.2)
        out,err=await asyncio.gather(stdout,stderr)
        if proc.returncode:
            message=network.scrub(err.decode('utf-8',errors='replace')[-1500:])
            raise MediaError(quality.failure_reason(message),message)
        return out
    finally:
        if proc.returncode is None:
            try:
                p=psutil.Process(proc.pid)
                children=p.children(recursive=True)
                for child in children:
                    try:child.kill()
                    except psutil.NoSuchProcess:pass
                p.kill()
                await asyncio.to_thread(psutil.wait_procs,children+[p],timeout=5)
            except psutil.NoSuchProcess:pass
            await proc.wait()
        await asyncio.gather(stdout,stderr,return_exceptions=True)

async def probe(path,collector,folder,deadline,candidate=None,duration=None):
    exe=shutil.which('ffprobe')
    if not exe:raise MediaError('validation_unavailable','ffprobe is required to validate media')
    raw=await command([exe,'-v','error','-protocol_whitelist','file,pipe','-show_streams','-show_format','-of','json',str(path)],collector,folder,min(deadline,time.monotonic()+60),'verifying')
    d=json.loads(raw);streams=d.get('streams',[])
    v=next((s for s in streams if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic')),None)
    a=next((s for s in streams if s['codec_type']=='audio'),None)
    if not (v or a):raise MediaError('invalid_media','No media streams')
    if candidate and (candidate['expected_video'] and not v or candidate['expected_audio'] and not a):
        raise MediaError('incomplete_media','Expected video/audio track missing')
    seconds=float(d.get('format',{}).get('duration') or 0)
    if seconds<=0:raise MediaError('incomplete_media','No finite media duration')
    if duration and abs(seconds-duration)>max(3,duration*.02):
        raise MediaError('incomplete_media','Duration differs from source metadata')
    if v and candidate and candidate.get('height') and candidate.get('width') and (v.get('height'),v.get('width'))!=(candidate['height'],candidate['width']):
        raise MediaError('quality_mismatch','Actual dimensions differ from selected format')
    fps=None
    if v:
        n,den=v.get('avg_frame_rate','0/1').split('/');fps=round(float(n)/float(den),3) if float(den) else None
    return dict(width=v.get('width') if v else None,height=v.get('height') if v else None,fps=fps,
                video_codec=v.get('codec_name') if v else None,audio_codec=a.get('codec_name') if a else None,
                duration_seconds=seconds,actual_size_bytes=path.stat().st_size,
                validation={'probe':'passed','full_decode':'not_run','checked_at':store.now()})

async def collect(collector,parent):
    spec=collector.spec
    record,_=collector.item(parent['url']+'#adapter=yt_dlp',parent['url'],'media_job')
    folder=collector.folder/record['id'];folder.mkdir(exist_ok=True)
    record.update(status='fetching',adapter='yt_dlp',quality={'requested_policy':spec.quality_policy,'attempts':[]})
    store.save_item(record)
    deadline=time.monotonic()+spec.media_timeout_seconds
    common=[sys.executable,'-m','yt_dlp','--ignore-config','--no-playlist','--no-warnings','--no-progress','--socket-timeout',str(spec.timeout_seconds),'--retries',str(spec.retries),'--fragment-retries',str(spec.retries),'--retry-sleep','http:exp=1:20','--abort-on-unavailable-fragment']
    common+=['--match-filter',"protocol ~= '^(https?|m3u8_native|http_dash_segments)(\\+(https?|m3u8_native|http_dash_segments))*$'",'--sleep-requests',str(spec.interval_seconds)]
    if collector.proxy:common+=['--proxy',collector.proxy.url]
    elif spec.network_mode=='direct':common+=['--proxy','']
    if collector.profile and (collector.profile/'cookies.txt').is_file():common+=['--cookies',str(collector.profile/'cookies.txt')]
    committed=set()
    try:
        await network.check_target(parent['url'],collector.strict)
        await collector.throttle()
        info=json.loads(await command(common+['--dump-single-json','--skip-download',parent['url']],collector,folder,deadline,'probing_quality'))
        if info.get('_type') in ('playlist','multi_video'):raise MediaError('unsupported_collection','Submit an individual media URL')
        budget=min(spec.max_file_mb*1024**2,spec.max_total_mb*1024**2-collector.used)
        choices,excluded=quality.candidates(info,spec,budget)
        record['quality'].update(candidates=choices,excluded=excluded)
        store.save_item(record)
        if not choices:raise MediaError('no_eligible_format','No eligible format under current permissions, protocol, language and budget')
        budget_fallback=any(e.get('preferred_to_selected') for e in excluded)
        if not spec.fallback_enabled and budget_fallback:
            raise MediaError('size_limit','A format exceeds budget and fallback is disabled')
        for index,candidate in enumerate(choices[:spec.max_format_attempts]):
            attempt=dict(format_ids=candidate['format_ids'],status='running');record['quality']['attempts'].append(attempt);store.save_item(record)
            attempt_folder=folder/str(index);attempt_folder.mkdir(exist_ok=True)
            try:
                # Numeric format IDs were validated before becoming selector expressions.
                args=common+['--downloader','native','--hls-prefer-native','--max-filesize',str(budget),'-f','+'.join(candidate['format_ids']),
                    '--merge-output-format','mkv','--write-subs','--sub-langs','zh.*,en','-o',str(attempt_folder/'media.%(ext)s'),parent['url']]
                await command(args,collector,folder,deadline,'downloading_media')
                sizes=[p.stat().st_size for p in folder.rglob('*') if p.is_file()]
                if sum(sizes)+collector.used>spec.max_total_mb*1024**2 or max(sizes,default=0)>spec.max_file_mb*1024**2:
                    raise MediaError('size_limit','Completed media exceeds configured storage limit')
                paths=[p for p in attempt_folder.iterdir() if p.is_file() and p.suffix.lower() not in ('.part','.ytdl','.vtt','.srt','.ass','.ttml','.json')]
                if len(paths)!=1:raise MediaError('incomplete_media','Expected one complete media file, not subtitles or separate tracks')
                path=paths[0]
                metadata=await probe(path,collector,folder,deadline,candidate,info.get('duration'))
                changed=index>0 or budget_fallback
                metadata.update(requested_policy=spec.quality_policy,selection_state='fallback' if changed else 'best_discovered' if candidate.get('height') or not candidate['expected_video'] else 'unknown',
                    selection_basis='extractor_candidates_and_post_download_probe',format_ids=candidate['format_ids'],
                    source_kind=candidate['source_kind'],estimated_size_bytes=candidate['estimated_size_bytes'],
                    fallback_reason=record['quality']['attempts'][-2].get('reason') if index else 'size_limit' if changed else None,
                    candidates=choices,excluded=excluded,attempts=record['quality']['attempts'])
                if candidate.get('height') and min(candidate['height'],candidate['width'])<720:
                    metadata['quality_note']='no_hd_source' if not changed else 'lower_resolution_fallback'
                for output in [path]+[p for p in attempt_folder.iterdir() if p.suffix.lower() in ('.vtt','.srt','.ass','.ttml')]:
                    if collector.resource_count>=spec.max_resources:raise MediaError('resource_limit','Resource count limit reached')
                    child,_=collector.item(parent['url']+'#media='+record['id']+'/'+output.name,parent['url'],'resource')
                    with output.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
                    child.update(downloaded=True,status='downloaded',filename=output.name,path=str(output.relative_to(store.DATA)),size=output.stat().st_size,sha256=digest,
                        content_type=inspect_file(output),adapter='yt_dlp',fetched_at=store.now(),resource_url=None,
                        resource_url_note='Extractor-managed media; temporary signed URLs are not retained')
                    if output==path:child['quality']=metadata
                    store.save_item(child);committed.add(output);collector.used+=output.stat().st_size;collector.resource_count+=1
                attempt['status']='completed';record.update(status='completed');record['quality'].update(metadata)
                # Persist final attempt status in media resource as well.
                for i in store.items(collector.job['id']):
                    if i.get('path')==str(path.relative_to(store.DATA)):
                        i['quality']=metadata;store.save_item(i)
                return
            except MediaError as exc:
                attempt.update(status='failed',reason=exc.reason,error=str(exc));store.save_item(record)
                for p in attempt_folder.iterdir():
                    if p.is_file() and p not in committed:p.unlink()
                if not spec.fallback_enabled or exc.reason in ('proxy_unavailable','network_temporary','timeout','validation_unavailable','resource_limit'):
                    raise
                if collector.proxy:
                    # Probe the SAME endpoint before any lower-quality retry; never rotate a login session.
                    import httpx
                    try:
                        async with httpx.AsyncClient(proxy=collector.proxy.url,trust_env=False,timeout=10) as c:await c.head(parent['url'])
                    except httpx.HTTPError:raise MediaError('proxy_unavailable','Proxy failed; no direct fallback')
        raise MediaError('attempts_exhausted','No candidate completed within the attempt limit')
    except asyncio.CancelledError:
        record.update(status='interrupted',error='Cancelled');raise
    except Exception as exc:
        record.update(status='failed',error=network.scrub(exc),failure_reason=getattr(exc,'reason','media_error'))
    finally:
        for p in folder.rglob('*'):
            if p.is_file() and p not in committed:p.unlink()
        store.save_item(record)
