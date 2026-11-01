import httpx,json
from pathlib import Path
root=Path(__file__).resolve().parents[2]/'research/upgrade'
root.mkdir(exist_ok=True)
out=[]
with httpx.Client(timeout=30,follow_redirects=True) as c:
    for repo in ['D4Vinci/Scrapling','apify/crawlee-python','firecrawl/firecrawl','LibreTranslate/LibreTranslate']:
        r=c.get('https://api.github.com/repos/'+repo);r.raise_for_status();d=r.json()
        out.append({k:d.get(k) for k in ['full_name','pushed_at','license','default_branch']})
        for file in ['README.md','pyproject.toml','LICENSE']:
            s=c.get('https://raw.githubusercontent.com/'+repo+'/'+d['default_branch']+'/'+file)
            if s.status_code==200:
                (root/(repo.replace('/','_')+'_'+file)).write_text(s.text,encoding='utf-8')
        tree=c.get('https://api.github.com/repos/'+repo+'/git/trees/'+d['default_branch'],params={'recursive':1}).json()
        names=[t['path'] for t in tree.get('tree',[]) if t['path'].endswith(('.py','.ts')) and any(x in t['path'] for x in ('proxy_configuration','proxy.py','translate.py','fetchers/requests'))][:2]
        for name in names:
            s=c.get('https://raw.githubusercontent.com/'+repo+'/'+d['default_branch']+'/'+name)
            if s.status_code==200:(root/(repo.replace('/','_')+'_'+name.replace('/','_'))).write_text(s.text,encoding='utf-8')
        print(repo,names,flush=True)
(root/'metadata.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
