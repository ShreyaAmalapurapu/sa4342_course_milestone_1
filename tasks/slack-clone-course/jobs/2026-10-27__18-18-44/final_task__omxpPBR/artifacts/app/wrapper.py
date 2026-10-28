import importlib.machinery, importlib.util, threading, time, re, json
spec=importlib.util.spec_from_file_location('huddle_core','/app/__pycache__/app.cpython-312.pyc')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
_db=m.db(); _db.execute('CREATE UNIQUE INDEX IF NOT EXISTS reads_unique ON reads(channel_id,user_id)'); _db.close()
_orig_event=m.ws_event
def safe_event(c,cid,typ,payload):
    for _ in range(30):
        try:
            with m.LOCK:
                x=c.execute('SELECT COALESCE(MAX(event_id),0)+1 n FROM events WHERE channel_id=?',(cid,)).fetchone()['n']
                c.execute('INSERT INTO events(channel_id,event_id,type,payload) VALUES(?,?,?,?)',(cid,x,typ,json.dumps(payload)))
                return x
        except Exception as e:
            if 'UNIQUE' not in str(e): raise
            time.sleep(0.003)
    return _orig_event(c,cid,typ,payload)
m.ws_event=safe_event
_orig_get=m.H.do_GET; _orig_post=m.H.do_POST; _orig_patch=m.H.do_PATCH
def _get(self):
    p,q=self.route(); u=m.auth(self.headers.get('Authorization'))
    if u and re.match(r'^/api/channels/\d+/read$',p):
        c=m.db(); cid=int(p.split('/')[3]); ch=m.channel(c,cid)
        if not ch:return m.error(self,404)
        z=c.execute('SELECT last_event FROM reads WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()
        last=z['last_event'] if z else 0
        unread=c.execute('SELECT count(*) n FROM messages WHERE channel_id=? AND deleted=0 AND COALESCE(event_id,0)>? AND author_id!=?',(cid,last,u['id'])).fetchone()['n']
        mention=c.execute("SELECT count(*) n FROM messages WHERE channel_id=? AND deleted=0 AND event_id>? AND author_id!=? AND mentions LIKE ?",(cid,last,u['id'],'%'+str(u['id'])+'%')).fetchone()['n']
        return m.js(self,200,{'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mention}})
    if u and re.match(r'^/api/workspaces/[^/]+$',p):
        c=m.db(); slug=p.split('/')[3]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
        if not w:return m.error(self,404)
        include=q.get('include_archived',['0'])[0] in ('1','true')
        chans=list(c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],include)))
        chans=[x for x in chans if not x['is_private'] or c.execute('SELECT 1 FROM cm WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone()]
        rs=[]
        for x in chans:
            z=c.execute('SELECT last_event FROM reads WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone(); last=z['last_event'] if z else 0
            unread=c.execute('SELECT count(*) n FROM messages WHERE channel_id=? AND deleted=0 AND COALESCE(event_id,0)>? AND author_id!=?',(x['id'],last,u['id'])).fetchone()['n']
            mention=c.execute("SELECT count(*) n FROM messages WHERE channel_id=? AND deleted=0 AND COALESCE(event_id,0)>? AND author_id!=? AND mentions LIKE ?",(x['id'],last,u['id'],'%'+str(u['id'])+'%')).fetchone()['n']
            rs.append({'channel_id':x['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':mention})
        return m.js(self,200,{'workspace':dict(w),'channels':[dict(x) for x in chans],'read_state':rs})
    return _orig_get(self)
def _post(self):
    p,q=self.route(); u=m.auth(self.headers.get('Authorization'))
    if u and re.match(r'^/api/channels/\d+/read$',p):
        d=self.body(); c=m.db(); cid=int(p.split('/')[3]); ch=m.channel(c,cid)
        if not ch:return m.error(self,404)
        old=c.execute('SELECT last_event FROM reads WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()
        val=max(int(d.get('last_read_event_id',0)), old['last_event'] if old else 0)
        row=c.execute('SELECT 1 FROM reads WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()
        if row:c.execute('UPDATE reads SET last_event=? WHERE channel_id=? AND user_id=?',(val,cid,u['id']))
        else:c.execute('INSERT INTO reads(channel_id,user_id,last_event) VALUES(?,?,?)',(cid,u['id'],val))
        return m.js(self,200,{'read_state':{'channel_id':cid,'last_read_event_id':val,'unread_count':0,'mention_count':0}})
    was_join=False
    if u and re.match(r'^/api/channels/\d+/join$',p):
        try:
            cid=int(p.split('/')[3]); c0=m.db()
            was_join=bool(c0.execute('SELECT 1 FROM cm WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone())
        except Exception: pass
    with m.LOCK:
        result=_orig_post(self)
    if u and re.match(r'^/api/channels/\d+/join$',p) and not was_join:
        try:
            c1=m.db()
            if c1.execute('SELECT 1 FROM cm WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():
                m.ws_event(c1,cid,'member.joined',{'user_id':u['id']})
        except Exception: pass
    return result
def _patch(self):
    p,q=self.route(); u=m.auth(self.headers.get('Authorization'))
    if u and re.match(r'^/api/channels/\d+$',p):
        cid=int(p.split('/')[3]); c=m.db(); ch=m.channel(c,cid)
        if ch and ch['is_archived']: return m.error(self,423)
    return _orig_patch(self)

def ws_frame(obj):
    b=json.dumps(obj).encode(); n=len(b)
    if n<126: return bytes([0x81,n])+b
    if n<65536: return bytes([0x81,126])+n.to_bytes(2,'big')+b
    return bytes([0x81,127])+n.to_bytes(8,'big')+b
def ws_read(sock):
    h=sock.recv(2)
    if len(h)<2:return None
    ln=h[1]&127
    if ln==126:ln=int.from_bytes(sock.recv(2),'big')
    elif ln==127:ln=int.from_bytes(sock.recv(8),'big')
    mask=sock.recv(4); b=sock.recv(ln)
    if h[0]&15==8:return None
    if h[0]&15!=1:return {}
    return json.loads(bytes(b[i]^mask[i%4] for i in range(ln)))
def ws_handler(self):
    import base64,hashlib,select
    key=self.headers.get('Sec-WebSocket-Key'); q=__import__('urllib').parse.urlsplit(self.path).query
    tok=__import__('urllib').parse.parse_qs(q).get('token',[''])[0]
    if not key or not m.auth('Bearer '+tok):
        self.send_response(401);self.end_headers();return
    ac=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
    self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',ac);self.end_headers()
    sock=self.connection; sock.setblocking(False); subs={}
    def send(x):
        sock.sendall(ws_frame(x))
    def poll():
        c=m.db()
        for cid,last in list(subs.items()):
            for e in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)):
                z=json.loads(e['payload']); z.update(type=e['type'],event_id=e['event_id'],channel_id=cid); send(z); subs[cid]=e['event_id']
    try:
        while True:
            poll()
            ready,_,_=select.select([sock],[],[],0.15)
            if not ready: continue
            x=ws_read(sock)
            if x is None: break
            typ=x.get('type'); cid=int(x.get('channel_id',0))
            c=m.db()
            if typ=='subscribe':
                head=c.execute('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,)).fetchone()['n']; subs[cid]=head
                send({'type':'subscribed','channel_id':cid,'head_event_id':head})
            elif typ=='resume':
                since=int(x.get('since_event_id',0))
                for e in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)):
                    z=json.loads(e['payload']);z.update(type=e['type'],event_id=e['event_id'],channel_id=cid);send(z);subs[cid]=e['event_id']
                head=c.execute('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel_id=?',(cid,)).fetchone()['n'];send({'type':'resumed','channel_id':cid,'head_event_id':head});subs[cid]=head
    except Exception: pass
m.H.do_WS=ws_handler
m.H.do_GET=_get; m.H.do_POST=_post; m.H.do_PATCH=_patch
def run(port): m.ThreadingHTTPServer(('127.0.0.1',port),m.H).serve_forever()
if __name__=='__main__':
    for p in (8000,8001,8002): threading.Thread(target=run,args=(p,),daemon=True).start()
    while True: time.sleep(3600)
