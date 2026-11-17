import http.client
import json
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from hotel.server import App
from hotel.store import Store

class Handler(App):
    def log_message(self,*args):pass

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.store=Store(Path(cls.tmp.name)/'api.sqlite3')
        cls.store.bootstrap_admin('admin','passphrase-123')
        cls.handler=type('HandlerForTest',(Handler,),{'store':cls.store})
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),cls.handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join();cls.server.server_close();cls.tmp.cleanup()

    def request(self,path,data=None,cookie=None):
        con=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        headers={'Content-Type':'application/json'}
        if cookie:headers['Cookie']=cookie
        con.request('POST' if data is not None else 'GET',path,body=json.dumps(data) if data is not None else None,headers=headers)
        res=con.getresponse();headers=dict(res.getheaders());body=res.read();status=res.status;con.close()
        return status, json.loads(body) if path.startswith('/api/') else body, headers

    def auth(self):
        status, data, headers=self.request('/api/login',{'username':'admin','password':'passphrase-123'})
        self.assertEqual(status,200)
        return headers['Set-Cookie'].split(';')[0]

    def test_no_access_without_session(self):
        status,body,_=self.request('/api/rooms')
        self.assertEqual(status,401)

    def test_login_and_me(self):
        cookie=self.auth()
        status,body,_=self.request('/api/me',cookie=cookie)
        self.assertEqual(status,200)
        self.assertEqual(body['role'],'admin')

    def test_http_booking_conflict_and_cancel(self):
        cookie=self.auth()
        code,r,_=self.request('/api/rooms',{'number':'201','room_type':'Standard','nightly_rate_cents':12000},cookie)
        self.assertEqual(code,200)
        room=r['id']
        code,g,_=self.request('/api/guests',{'full_name':'Web Guest'},cookie)
        self.assertEqual(code,200)
        start=date.today().isoformat();end=(date.today()+timedelta(days=2)).isoformat()
        data={'room_id':room,'guest_id':g['id'],'check_in':start,'check_out':end}
        code,r,_=self.request('/api/reservations',data,cookie)
        self.assertEqual(code,200)
        code,_,_=self.request('/api/reservations',data,cookie)
        self.assertEqual(code,409)
        code,_,_=self.request(f'/api/reservations/{r["id"]}/cancel',{},cookie)
        self.assertEqual(code,200)
        code,_,_=self.request('/api/reservations',data,cookie)
        self.assertEqual(code,200)

    def test_static_client(self):
        code,body,headers=self.request('/')
        self.assertEqual(code,200)
        self.assertIn(b'Innkeeper',body)

if __name__=='__main__':unittest.main()
