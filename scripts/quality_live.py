"""Run bounded public-source acceptance. Payloads and evidence stay outside Git."""
import httpx,json,time
from pathlib import Path
root=Path(__file__).resolve().parents[2]
c=httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=60)
cases=[('hd',{'urls':['https://www.bilibili.com/video/BV1rG411r7BQ/'],'media':True,'max_file_mb':1000,'max_total_mb':2500,'max_links_per_page':1,'media_timeout_seconds':1800}),('book',{'urls':['https://www.storybookscanada.ca/stories/zh/0112/'],'max_links_per_page':180,'max_resources':150}),('lesson',{'urls':['https://www.jsfzzy.com/storage/course/64dd92d7dad30.pdf']}),('dynamic',{'urls':['https://quotes.toscrape.com/js/'],'browser':True,'max_links_per_page':3})]
ids={}
for name,spec in cases:
    spec.update(network_mode='strict',proxy_ids=['local-system'],quality_policy='best_available')
    r=c.post('/api/jobs',json=spec);r.raise_for_status();ids[name]=r.json()['id']
(root/'research/quality-acceptance-ids.json').write_text(json.dumps(ids))
print(ids,flush=True)
pending=dict(ids);deadline=time.monotonic()+2400
while pending and time.monotonic()<deadline:
    for name,job_id in list(pending.items()):
        j=c.get('/api/jobs/'+job_id).json()
        if j['status'] in ('queued','running'):continue
        items=c.get('/api/jobs/'+job_id+'/results').json()['items']
        (root/'research'/('quality-'+name+'.json')).write_text(json.dumps({'job':j,'items':items},ensure_ascii=False,indent=2),encoding='utf-8')
        print(name,j['status'],flush=True);pending.pop(name)
    if pending:time.sleep(2)
for job_id in pending.values():c.post('/api/jobs/'+job_id+'/cancel')
if pending:raise SystemExit('Acceptance deadline reached; pending jobs cancelled')
