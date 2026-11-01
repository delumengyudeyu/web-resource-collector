"""Candidate policy uses extractor order as a tie-breaker, never cross-codec bitrate alone."""
import re

SAFE_PROTOCOLS={'http','https','m3u8_native','http_dash_segments'}

def video(f):
    return f.get('vcodec') not in (None,'none') or bool(f.get('width') and f.get('height'))

def audio(f):
    return f.get('acodec') not in (None,'none')

def candidates(info,spec,budget):
    if info.get('is_live') or info.get('live_status')=='is_live':
        raise ValueError('Live streams need a bounded recording workflow')
    formats=info.get('formats') or [info]
    usable=[]; excluded=[]
    for index,f in enumerate(formats):
        fid=str(f.get('format_id',''))
        reason=None
        if not re.fullmatch(r'[\w.-]+',fid):reason='format_unavailable'
        elif f.get('has_drm'):reason='access_restricted'
        elif f.get('protocol') not in SAFE_PROTOCOLS:reason='protocol_disallowed'
        if reason:
            excluded.append(dict(format_id=fid,reason=reason));continue
        usable.append((index,f))
    audios=[(i,f) for i,f in usable if audio(f) and not video(f)]
    if spec.audio_language:
        audios=[(i,f) for i,f in audios if (f.get('language') or '').split('-')[0]==spec.audio_language.split('-')[0]]
    audios.sort(key=lambda x:(x[1].get('language_preference') or -1,x[0]),reverse=True)
    has_video=any(video(f) for _,f in usable)
    has_audio=any(audio(f) for _,f in usable)
    result=[]
    for index,f in usable:
        if has_video and not video(f):continue
        tracks=[f]
        if video(f) and not audio(f) and has_audio:
            if not audios:continue
            tracks.append(audios[0][1])
        if spec.audio_language and audio(f) and (f.get('language') or '').split('-')[0]!=spec.audio_language.split('-')[0]:
            excluded.append(dict(format_id=f['format_id'],reason='audio_language_unavailable'));continue
        dims=[f.get('width'),f.get('height')]
        short=min(dims) if all(dims) else None
        cap={'cap_1080':1080,'cap_720':720}.get(spec.quality_policy)
        if cap and video(f) and (short is None or short>cap):
            excluded.append(dict(format_id=f['format_id'],reason='resolution_cap'));continue
        sizes=[t.get('filesize') or t.get('filesize_approx') for t in tracks]
        estimated=sum(sizes) if all(sizes) else None
        row=dict(format_ids=[str(t['format_id']) for t in tracks],width=f.get('width'),height=f.get('height'),
                 fps=f.get('fps'),estimated_size_bytes=estimated,expected_audio=any(audio(t) for t in tracks),
                 expected_video=video(f),language=(tracks[-1].get('language')),dynamic_range=f.get('dynamic_range'),
                 source_kind='source_streams_remux' if len(tracks)>1 or 'm3u8' in f.get('protocol','') or 'dash' in f.get('protocol','') else 'source_file',
                 rank=(short or 0,f.get('width',0) or 0,f.get('fps') or 0,index))
        if estimated and estimated>budget:
            excluded.append(dict(format_id=f['format_id'],reason='size_limit',estimated_size_bytes=estimated,rank=row['rank']));continue
        result.append(row)
    result.sort(key=lambda f:f['rank'],reverse=spec.quality_policy!='data_saver')
    for e in excluded:
        if 'rank' in e:
            e['preferred_to_selected']=not result or (e['rank']<result[0]['rank'] if spec.quality_policy=='data_saver' else e['rank']>result[0]['rank'])
            e.pop('rank')
    for r in result:r.pop('rank')
    return result,excluded

def failure_reason(message):
    m=message.lower()
    if 'proxy' in m or 'connection refused' in m:return 'proxy_unavailable'
    if any(x in m for x in ('429','500','502','503','504','timed out','connection reset','temporary failure')):return 'network_temporary'
    if '401' in m or '403' in m or 'sign in' in m or 'login' in m:return 'access_restricted'
    if 'exceeds' in m or 'max-filesize' in m or 'larger than max' in m:return 'size_limit'
    if 'ffmpeg' in m or 'merge' in m:return 'merge_failed'
    return 'format_unavailable'
