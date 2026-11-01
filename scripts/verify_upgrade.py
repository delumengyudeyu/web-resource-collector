"""Live acceptance against the deployed API; evidence stays outside the repository."""
import json,time,hashlib,os
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[2]
c=httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=60)
def post(url,body):
    r=c.post(url,json=body);r.raise_for_status();return r.json()
def wait(url):
    for _ in range(1200):
        r=c.get(url).json()
        if r['status'] not in ('queued','running'):return r
        time.sleep(1)
    raise TimeoutError(url)
def main():
    post('/api/proxies',{'id':'local-system','url':'http://127.0.0.1:7897'})
    check=post('/api/proxies/local-system/check',{})
    assert check['reachable'],check
    cases=[('book','https://www.storybookscanada.ca/stories/zh/0112/',{}),
           ('lesson','https://www.jsfzzy.com/storage/course/64dd92d7dad30.pdf',{}),
           ('dynamic','https://quotes.toscrape.com/js/',{'browser':True}),
           ('video','https://edu.cctv.com/2020/02/17/VIDEVZmTlUifJLxCG2RSRY9b200217.shtml',{'media':True,'max_links_per_page':2})]
    report={'proxy':check,'jobs':[],'processing':[]}
    for name,url,opts in cases:
        spec=dict(urls=[url],network_mode='strict',proxy_ids=['local-system'],max_file_mb=200,max_total_mb=400,max_resources=150,timeout_seconds=20,retries=1)
        spec.update(opts)
        j=post('/api/jobs',spec);report['jobs'].append(dict(case=name,id=j['id']))
    for j in report['jobs']:
        job=wait('/api/jobs/'+j['id']);j.update(status=job['status'],summary=job.get('summary'),events=job['events'])
        items=c.get('/api/jobs/'+j['id']+'/results').json()['items']
        j['errors']=[i.get('error') for i in items if i.get('error')]
        j['files']=[{k:i.get(k) for k in ('id','filename','size','sha256','path','url')} for i in items if i.get('downloaded')]
        print(j['case'],j['status'],j.get('summary'),flush=True)
    old=c.get('/api/jobs/315b0e3bc28e44b09097f7c87cf667b1/results').json()['items']
    pdf=next(i for i in old if i.get('filename')=='zh-0112_body.pdf')
    requests=[(pdf['id'],{'action':'translate','source_language':'zh','target_language':'en'}),
              (pdf['id'],{'action':'ocr','max_pages':2})]
    videos=c.get('/api/jobs/529467a1b60747769bfda3e945e53614/results').json()['items']
    video=next(i for i in videos if i.get('filename','').endswith('.mp4'))
    requests.append((video['id'],{'action':'transcribe','max_seconds':45}))
    for item,spec in requests:
        p=post('/api/items/'+item+'/process',spec);p=wait('/api/processes/'+p['id']);report['processing'].append(p)
        print(spec['action'],p['status'],p.get('error'),flush=True)
    speech=report['processing'][-1]
    if speech['status'] in ('completed','partial'):
        p=post('/api/items/'+video['id']+'/process',{'action':'translate','source_process_id':speech['id']})
        report['processing'].append(wait('/api/processes/'+p['id']))
    (ROOT/'research/upgrade-evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    c.patch('/api/settings',json={'default_strict':True,'storage_limit_mb':20000}).raise_for_status()
if __name__=='__main__':main()
