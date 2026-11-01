import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import httpx
import pytest
from fastapi.testclient import TestClient
from collector.api import app
from collector import network,store
from test_collector import server,submit,wait

class Forward(BaseHTTPRequestHandler):
    count=0
    def log_message(self,*args):pass
    def do_HEAD(self):self.forward('HEAD')
    def do_GET(self):self.forward('GET')
    def forward(self,method):
        type(self).count+=1
        r=httpx.request(method,self.path,trust_env=False)
        self.send_response(r.status_code)
        for k in ('content-type','content-length','content-disposition'):
            if k in r.headers:self.send_header(k,r.headers[k])
        self.end_headers()
        if method!='HEAD':self.wfile.write(r.content)

@pytest.mark.parametrize('route,browser',[('/download',False),('/dynamic',True)])
def test_strict_proxy_forward_and_dead_proxy(server,route,browser):
    old=network.load()
    proxy=ThreadingHTTPServer(('127.0.0.1',0),Forward)
    thread=threading.Thread(target=proxy.serve_forever,daemon=True);thread.start()
    try:
        with TestClient(app) as c:
            assert c.post('/api/proxies',json={'id':'test','url':f'http://127.0.0.1:{proxy.server_port}'}).status_code==200
            j,items=wait(c,submit(c,[server+route],network_mode='strict',proxy_ids=['test'],browser=browser,scroll_steps=0))
            assert j['status']=='completed' and items[0]['downloaded'] and Forward.count>=2
            # Stop the actual proxy; a direct fallback would still reach the healthy origin.
            proxy.shutdown();proxy.server_close()
            j,items=wait(c,submit(c,[server+route],network_mode='strict',proxy_ids=['test'],retries=0,browser=browser))
            assert j['status']=='failed' and not any(i['downloaded'] for i in items)
            assert 'direct fallback' in j['events'][-1]['message']
    finally:network.save(old)

def test_settings_secrets_library_tags_and_processing(server):
    old=network.load()
    try:
        with TestClient(app) as c:
            c.post('/api/proxies',json={'id':'secret','url':'http://example-user:example-password@127.0.0.1:9'})
            assert 'example-password' not in c.get('/api/settings').text
            job,items=wait(c,submit(c,[server+'/download']))
            item=items[0]
            tagged=c.patch('/api/items/'+item['id']+'/tags',json={'tags':['一年级','课件']}).json()
            assert tagged['classified']
            assert c.get('/api/library',params={'q':'一年级'}).json()['total']>=1
            p=c.post('/api/items/'+item['id']+'/process',json={'action':'extract'}).json()
            import time
            for _ in range(100):
                p=c.get('/api/processes/'+p['id']).json()
                if p['status'] not in ('queued','running'):break
                time.sleep(.1)
            assert p['status']=='completed',p
            out=c.get('/api/processes/'+p['id']+'/files/json').json()
            assert out['segments'][0]['page']==1 and '春夏秋冬' in out['segments'][0]['text']
            assert c.get('/api/files/'+item['id']).content.startswith(b'PK')
    finally:network.save(old)
