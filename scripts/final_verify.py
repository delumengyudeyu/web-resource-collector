import json,hashlib,subprocess,time,shutil
from pathlib import Path
import httpx
from PIL import Image
from pypdf import PdfReader
root=Path(__file__).resolve().parents[2]
c=httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=60)
report=json.loads((root/'research/upgrade-evidence.json').read_text(encoding='utf-8'))
def wait(pid):
    for _ in range(600):
        r=c.get('/api/processes/'+pid).json()
        if r['status'] not in ('queued','running'):return r
        time.sleep(.5)
    raise TimeoutError(pid)
speech=c.get('/api/processes/'+(root/'research/speech-retry-id.txt').read_text()).json()
assert speech['status']=='partial',speech
report['speech_fixed']=speech
r=c.post('/api/items/'+speech['item_id']+'/process',json={'action':'translate','source_process_id':speech['id']});r.raise_for_status()
r=wait(r.json()['id']);assert r['status']=='partial',r
assert 'bilingual_srt' in r['files'];report['subtitle_translation']=r
for j in report['jobs']:
    items=c.get('/api/jobs/'+j['id']+'/results').json()['items']
    for i in items:
        if i.get('downloaded'):
            p=root/'data'/i['path']
            with p.open('rb') as f: assert hashlib.file_digest(f,'sha256').hexdigest()==i['sha256']
    if j['case']=='book':
        pdf=next(i for i in items if i.get('filename')=='zh-0112_body.pdf')
        assert len(PdfReader(root/'data'/pdf['path']).pages)==10
        images=sorted([i for i in items if '/images/0112/' in i['url']],key=lambda i:i['order'])
        assert [i['url'].rsplit('/',1)[1] for i in images]==[f'{n:02}.jpg' for n in range(1,10)]
        for i in images:Image.open(root/'data'/i['path']).verify()
        assert hashlib.sha256(c.get('/api/files/'+pdf['id']).content).hexdigest()==pdf['sha256']
    if j['case']=='lesson':assert len(PdfReader(root/'data'/items[0]['path']).pages)==29
    if j['case']=='video':
        v=next(i for i in items if i.get('filename','').endswith('.mp4'))
        ff=shutil.which('ffmpeg') or 'C:/Users/62541/.dsh/tools/ffmpeg/ffmpeg.exe'
        decoded=subprocess.run([ff,'-v','error','-i',str(root/'data'/v['path']),'-f','null','NUL'],capture_output=True)
        assert decoded.returncode==0 and not decoded.stderr
        report['full_video_decode']='passed'
report['verified_at']=time.strftime('%Y-%m-%dT%H:%M:%S')
(root/'research/upgrade-evidence-final.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('Original hashes, PDF pages, image sequence, complete video decode, API downloads and bilingual timed subtitles passed')
print('Translation sample:',c.get('/api/processes/'+report['processing'][0]['id']+'/files/json').json()['segments'][:2])
print('Speech sample:',c.get('/api/processes/'+speech['id']+'/files/json').json()['segments'][:2])
