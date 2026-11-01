import json,hashlib,subprocess,httpx
from pathlib import Path
from PIL import Image
import pypdfium2 as pdf
root=Path('D:/WebCollector');evidence={};c=httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=90)
for name in ('hd','book','lesson','dynamic'):
 d=json.loads((root/'research'/('quality-'+name+'.json')).read_text(encoding='utf-8'));rows=[]
 for i in d['items']:
  if not i.get('downloaded'):continue
  p=root/'data'/i['path'];r={'id':i['id'],'filename':i['filename'],'mime':i.get('content_type'),'sha256':i.get('sha256')}
  assert hashlib.sha256(p.read_bytes()).hexdigest()==i['sha256']
  if i.get('content_type')=='application/pdf':
   doc=pdf.PdfDocument(p);r['pages']=len(doc);bitmap=doc[0].render();r['render']='passed';bitmap.close();doc.close()
  if i.get('content_type','').startswith('image/'):
   try:
    with Image.open(p) as im:im.load();r['pixels']=list(im.size)
   except Exception:r['image_validation']='unsupported_or_failed'
  if i.get('adapter')=='yt_dlp':
   v=subprocess.run(['ffmpeg','-v','error','-xerror','-protocol_whitelist','file,pipe','-i',str(p),'-map','0:v?','-map','0:a?','-f','null','-'],capture_output=True,timeout=300)
   r['full_decode']='passed' if v.returncode==0 else 'failed';r['quality']=i['quality'];r['decode_errors']=v.stderr.decode(errors='replace')[:1000]
   response=c.get('/api/files/'+i['id']);r['api_hash_matches']=hashlib.sha256(response.content).hexdigest()==i['sha256']
  rows.append(r)
 evidence[name]={'job_id':d['job']['id'],'status':d['job']['status'],'files':rows,'summary':d['job'].get('summary')}
(root/'research/quality-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
for name,d in evidence.items():
 print(name,d['status'],len(d['files']),[(x['filename'],x.get('pages'),x.get('full_decode'),x.get('quality',{}).get('duration_seconds')) for x in d['files'] if x.get('pages') or x.get('full_decode')])
