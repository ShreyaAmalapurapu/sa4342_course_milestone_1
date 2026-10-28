#!/usr/bin/env python3
import json, os, re, sys, time, uuid, hashlib, fcntl, base64, struct, select
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote, parse_qs

STATE='/app/data/state.json'
os.makedirs('/app/data',exist_ok=True)
if not os.path.exists(STATE):
    open(STATE,'w').write(json.dumps({'seq':{'user':0,'workspace':0,'channel':0,'invite':0},'users':{},'tokens':{},'workspaces':{},'channels':{},'members':{},'invitations':{},'messages':{}}))

def locked():
    f=open(STATE,'r+'); fcntl.flock(f,fcntl.LOCK_EX)
    try:
        try: d=json.load(f)
        except: d={'seq':{'user':0,'workspace':0,'channel':0,'invite':0,'message':0},'users':{},'tokens':{},'workspaces':{},'channels':{},'members':{},'invitations':{},'messages':{}}
        d.setdefault('seq',{}).setdefault('message',0)
        d.setdefault('messages',{})
        d.setdefault('pins',{})
        d.setdefault('events',{})
        d.setdefault('seq',{}).setdefault('message',0)
        return f,d
    except:
        fcntl.flock(f,fcntl.LOCK_UN); f.close(); raise
def save(f,d):
    f.seek(0); f.truncate(); json.dump(d,f); f.flush(); os.fsync(f.fileno()); fcntl.flock(f,fcntl.LOCK_UN); f.close()
def close(f):
    fcntl.flock(f,fcntl.LOCK_UN); f.close()
def new(d,k):
    d['seq'][k]+=1; return d['seq'][k]
def userobj(u):
    return {k:u.get(k) for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_obj(w): return {k:w[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_obj(c): return {k:c[k] for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def member(d,wid,uid): return d['members'].get(f'{wid}:{uid}')
def auth(h,d):
    if not h.startswith('Bearer '): return None
    uid=d['tokens'].get(h[7:])
    return d['users'].get(str(uid)) if uid is not None else None
def js(handler,obj,status=200):
    raw=json.dumps(obj).encode(); handler.send_response(status); handler.send_header('Content-Type','application/json'); handler.send_header('Content-Length',str(len(raw))); handler.end_headers(); handler.wfile.write(raw)
def err(h,status,msg='error'): js(h,{'error':msg},status)
def body(h):
    n=int(h.headers.get('Content-Length','0') or 0)
    try: return json.loads(h.rfile.read(n) or b'{}')
    except: return None


def append_event(d,cid,kind,payload):
    c=d['channels'].get(str(cid))
    if not c: return None
    c.setdefault('event_seq',0); c['event_seq']+=1
    e={'type':kind,'event_id':c['event_seq'],'channel_id':cid}
    e.update(payload or {})
    d.setdefault('events',{}).setdefault(str(cid),[]).append(e)
    return e



def resolve_mentions(d,c,author,text):
    out=[]
    if re.search(r'(?<![\w@.])@channel\b',text):
        for k,m in d['members'].items():
            if k.startswith(str(c['workspace_id'])+':') and c['id'] in m.get('channel_ids',[]) and m['user_id']!=author['id']:
                out.append(m['user_id'])
    for hit in re.finditer(r'(?<![\w@.])@([A-Za-z0-9_]+)(?!\.[A-Za-z])',text):
        name=hit.group(1)
        q=next((z for z in d['users'].values() if z.get('username')==name),None)
        if q and q['id']!=author['id'] and member(d,c['workspace_id'],q['id']) and q['id'] not in out:
            out.append(q['id'])
    return list(dict.fromkeys(out))

def read_state(d,c,user):
    mm=member(d,c['workspace_id'],user['id'])
    reads=(mm or {}).setdefault('reads',{})
    last=int(reads.get(str(c['id']),0))
    evs=d.get('events',{}).get(str(c['id']),[])
    unread=0; mentions=0
    for e in evs:
        if e['event_id']<=last: continue
        m=e.get('message') or {}
        if m.get('author_id')!=user['id']: unread+=1
        if user['id'] in (m.get('mentions') or []): mentions+=1
    return {'channel_id':c['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':mentions}

def workspace_read(d,w,user):
    states=[read_state(d,c,user) for c in d['channels'].values() if c['workspace_id']==w['id']]
    return {'last_read_event_id':max([x['last_read_event_id'] for x in states] or [0]),
            'unread_count':sum(x['unread_count'] for x in states),
            'mention_count':sum(x['mention_count'] for x in states)}

def message_obj(d,m):
    a=d['users'].get(str(m['author_id']),{})
    z=dict(m)
    z['author']=userobj(a)
    return z

def cursor_pack(items, pos):
    import base64
    return base64.urlsafe_b64encode(json.dumps({'ids':[x['id'] for x in items],'p':pos},separators=(',',':')).encode()).decode().rstrip('=')

def cursor_unpack(text):
    import base64
    try:
        raw=base64.urlsafe_b64decode(text+'='*((4-len(text)%4)%4))
        z=json.loads(raw)
        if not isinstance(z,dict) or not isinstance(z.get('ids'),list) or not isinstance(z.get('p'),int): raise ValueError()
        return z
    except Exception: raise ValueError()

def page(items, qs):
    try:
        lim=int(qs.get('limit',['50'])[0])
    except: raise ValueError()
    if lim<1 or lim>200: raise ValueError()
    if qs.get('cursor'):
        z=cursor_unpack(qs['cursor'][0])
        ids=[x['id'] for x in items]
        if ids != z['ids']: items=[next((x for x in items if x['id']==i),None) for i in z['ids']]
        pos=z['p']
    else: pos=0
    items=[x for x in items if x is not None]
    out=items[pos:pos+lim]; nxt=cursor_pack(items,pos+lim) if pos+lim<len(items) else None
    return out,nxt

class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*a): pass
    def do_GET(self): self.route('GET')
    def do_POST(self): self.route('POST')
    def do_PATCH(self): self.route('PATCH')
    def do_DELETE(self): self.route('DELETE')
    def _ws_send(self,obj):
        raw=json.dumps(obj,separators=(',',':')).encode()
        self.wfile.write(bytes([0x81])+((len(raw) if len(raw)<126 else 126)).to_bytes(1,'big')+(struct.pack('!H',len(raw)) if len(raw)>=126 else b'')+raw)
        self.wfile.flush()

    def _ws_recv(self):
        h=self.rfile.read(2)
        if len(h)<2: return None
        n=h[1]&127; masked=h[1]&128
        if n==126: n=struct.unpack('!H',self.rfile.read(2))[0]
        elif n==127: n=struct.unpack('!Q',self.rfile.read(8))[0]
        key=self.rfile.read(4) if masked else b''
        data=self.rfile.read(n)
        if masked: data=bytes(data[i]^key[i%4] for i in range(n))
        if (h[0]&15)==8: return None
        try: return json.loads(data.decode())
        except: return {}

    def websocket(self):
        qs=parse_qs(urlparse(self.path).query)
        f,d=locked()
        tok=qs.get('token',[''])[0]; uid=d['tokens'].get(tok)
        close(f)
        if uid is None:
            self.send_response(401); self.send_header('Content-Length','0'); self.send_header('Connection','close'); self.end_headers(); self.close_connection=True; return
        key=self.headers.get('Sec-WebSocket-Key')
        if not key:
            self.send_response(400); self.send_header('Content-Length','0'); self.send_header('Connection','close'); self.end_headers(); self.close_connection=True; return
        import hashlib as _h
        accept=base64.b64encode(_h.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101,'Switching Protocols')
        self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
        self.connection.settimeout(0.15); subs={}
        while True:
            try:
                r,_,_=select.select([self.connection],[],[],0.1)
                if r:
                    msg=self._ws_recv()
                    if msg is None: return
                    typ=msg.get('type'); cid=int(msg.get('channel_id',0))
                    f,d=locked(); c=d['channels'].get(str(cid)); head=(c or {}).get('event_seq',0)
                    if typ=='subscribe' and c:
                        subs[cid]=head; close(f); self._ws_send({'type':'subscribed','channel_id':cid,'head_event_id':head})
                    elif typ=='resume' and c:
                        since=int(msg.get('since_event_id',0)); evs=[e for e in d.get('events',{}).get(str(cid),[]) if e['event_id']>since]
                        close(f)
                        for e in evs: self._ws_send(e)
                        subs[cid]=head; self._ws_send({'type':'resumed','channel_id':cid,'head_event_id':head})
                    else: close(f)
                f,d=locked()
                for cid,last in list(subs.items()):
                    evs=[e for e in d.get('events',{}).get(str(cid),[]) if e['event_id']>last]
                    for e in evs: self._ws_send(e); subs[cid]=e['event_id']
                close(f)
            except (socket.timeout,TimeoutError): pass
            except Exception: return

    def route(self,method):
        p=unquote(urlparse(self.path).path); parts=p.strip('/').split('/')
        if p=='/api/health':
            js(self,{'status':'ok','node_id':int(self.server.server_port)-8000}); return
        if p.startswith('/api/ws'):
            return self.websocket()

        f,d=locked()
        try:
            u=auth(self.headers.get('Authorization',''),d)
            # auth
            if p=='/api/auth/register' and method=='POST':
                x=body(self)
                if not x or not x.get('password') or len(x['password'])<8 or not re.fullmatch(r'[A-Za-z0-9_]+',x.get('username','')): close(f); err(self,400); return
                if any(v['username']==x['username'] for v in d['users'].values()): close(f); err(self,409); return
                i=new(d,'user'); uid=str(i); uo={'id':i,'username':x['username'],'display_name':x.get('display_name') or x['username'],'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':'','password':hashlib.sha256(x['password'].encode()).hexdigest()}
                d['users'][uid]=uo; tok=uuid.uuid4().hex+uuid.uuid4().hex; d['tokens'][tok]=i; save(f,d); js(self,{'user':userobj(uo),'token':tok},201); return
            if p=='/api/auth/login' and method=='POST':
                x=body(self) or {}; found=next((v for v in d['users'].values() if v['username']==x.get('username')),None)
                if not found or found['password']!=hashlib.sha256(str(x.get('password','')).encode()).hexdigest(): close(f); err(self,401); return
                tok=uuid.uuid4().hex+uuid.uuid4().hex; d['tokens'][tok]=found['id']; save(f,d); js(self,{'user':userobj(found),'token':tok}); return
            if p=='/api/auth/me' and method=='GET':
                if not u: close(f); err(self,401); return
                close(f); js(self,{'user':userobj(u)}); return
            if not u: close(f); err(self,401); return
            if len(parts)==4 and parts[:2]==['api','channels'] and parts[3]=='messages':
                c=d['channels'].get(parts[2])
                if not c: close(f); err(self,404); return
                w=d['workspaces'].get(str(c['workspace_id'])); mm=member(d,w['id'],u['id']) if w else None
                if method=='POST':
                    if c['is_archived']: close(f); err(self,423); return
                    if c['id'] not in (mm or {}).get('channel_ids',[]) and (not mm or mm['role']!='owner'): close(f); err(self,403); return
                    x=body(self) or {}; text=x.get('body')
                    if not isinstance(text,str) or not text.strip(): close(f); err(self,400); return
                    parent=x.get('parent_id')
                    if parent is not None:
                        pm=d.get('messages',{}).get(str(parent))
                        if not pm or pm['channel_id']!=c['id'] or pm.get('parent_id') is not None: close(f); err(self,400); return
                    mid=new(d,'message')
                    msg={'id':mid,'channel_id':c['id'],'author_id':u['id'],'body':text,'parent_id':parent,'reply_count':0,'created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'edited_at':None,'files':[],'reactions':[],'mentions':resolve_mentions(d,c,u,text)}
                    d.setdefault('messages',{})[str(mid)]=msg
                    if parent is not None: d['messages'][str(parent)]['reply_count']+=1
                    append_event(d,c['id'],'message.reply' if parent is not None else 'message.created',{'message':message_obj(d,msg)})
                    save(f,d); js(self,{'message':message_obj(d,msg)},201); return
                if method=='GET':
                    if not mm or (c['is_private'] and c['id'] not in mm.get('channel_ids',[])): close(f); err(self,403); return
                    qs=parse_qs(urlparse(self.path).query)
                    arr=[message_obj(d,m) for m in d.get('messages',{}).values() if m['channel_id']==c['id'] and m.get('parent_id') is None]
                    arr.sort(key=lambda x:x['id'],reverse=True)
                    try: out,nxt=page(arr,qs)
                    except ValueError: close(f); err(self,400); return
                    close(f); js(self,{'messages':out,'next_cursor':nxt}); return
            # workspaces create/list/detail/members/patch
            if p=='/api/workspaces' and method=='GET':
                out=[workspace_obj(w) for w in d['workspaces'].values() if member(d,w['id'],u['id'])]
                close(f); js(self,{'workspaces':out}); return
            if p=='/api/workspaces' and method=='POST':
                x=body(self) or {}; slug=x.get('slug','')
                if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not x.get('name'): close(f); err(self,400); return
                if any(w['slug']==slug for w in d['workspaces'].values()): close(f); err(self,409); return
                wid=new(d,'workspace'); cid=new(d,'channel'); w={'id':wid,'slug':slug,'name':x['name'],'owner_id':u['id'],'join_mode':'open'}
                c={'id':cid,'workspace_id':wid,'name':'general','is_private':False,'is_dm':False,'topic':'','is_archived':False}
                d['workspaces'][str(wid)]=w; d['channels'][str(cid)]=c; d['members'][f'{wid}:{u["id"]}']={'user_id':u['id'],'role':'owner','channel_ids':[cid]}
                save(f,d); js(self,{'workspace':workspace_obj(w),'general_channel':channel_obj(c)},201); return
            if len(parts)>=3 and parts[0:2]==['api','workspaces']:
                slug=parts[2]; w=next((z for z in d['workspaces'].values() if z['slug']==slug),None)
                if not w: close(f); err(self,404); return
                mm=member(d,w['id'],u['id'])
                if len(parts)==3 and method=='GET':
                    inc=urlparse(self.path).query.lower() in ('include_archived=1','include_archived=true')
                    cs=[channel_obj(c) for c in d['channels'].values() if c['workspace_id']==w['id'] and (inc or not c['is_archived']) and (not c['is_private'] or c['id'] in d['members'].get(f'{w["id"]}:{u["id"]}',{}).get('channel_ids',[]))]
                    close(f); js(self,{'workspace':workspace_obj(w),'channels':cs,'read_state':workspace_read(d,w,u)}); return
                if len(parts)==4 and parts[3]=='members' and method=='GET':
                    if not mm: close(f); err(self,403); return
                    out=[x for k,x in d['members'].items() if k.startswith(str(w['id'])+':')]
                    close(f); js(self,{'members':out}); return
                if len(parts)==4 and parts[3]=='channels' and method=='POST':
                    x=body(self) or {}; priv=bool(x.get('is_private',False))
                    if not mm or mm['role'] not in ('owner','admin','member') or (priv and mm['role'] not in ('owner','admin')): close(f); err(self,403); return
                    name=x.get('name','')
                    if not re.fullmatch(r'[a-z0-9-]{1,32}',name): close(f); err(self,400); return
                    topic=x.get('topic','')
                    if not isinstance(topic,str) or len(topic)>250: close(f); err(self,400); return
                    if any(c['workspace_id']==w['id'] and c['name']==name for c in d['channels'].values()): close(f); err(self,409); return
                    cid=new(d,'channel'); c={'id':cid,'workspace_id':w['id'],'name':name,'is_private':priv,'is_dm':False,'topic':topic,'is_archived':False}; d['channels'][str(cid)]=c; d['members'][f'{w["id"]}:{u["id"]}'].setdefault('channel_ids',[]).append(cid); save(f,d); js(self,{'channel':channel_obj(c)},201); return
                if len(parts)==4 and parts[3]=='invitations':
                    if method=='GET':
                        if not mm or mm['role'] not in ('owner','admin'): close(f); err(self,403); return
                        now=time.time(); out=[v for v in d['invitations'].values() if v['workspace_id']==w['id'] and not v['revoked'] and v['expires_at']>now and v['uses']<v['max_uses']]
                        close(f); js(self,{'invitations':out}); return
                    if method=='POST':
                        if not mm or mm['role'] not in ('owner','admin'): close(f); err(self,403); return
                        x=body(self) or {}; mx=x.get('max_uses',1)
                        if not isinstance(mx,int) or mx<1: close(f); err(self,400); return
                        target=x.get('invited_user_id')
                        if x.get('invited_username'):
                            q=next((z for z in d['users'].values() if z['username']==x['invited_username']),None); target=q['id'] if q else None
                        code=uuid.uuid4().hex[:16]; inv={'code':code,'workspace_id':w['id'],'created_by':u['id'],'max_uses':1 if target else mx,'uses':0,'expires_at':time.time()+x.get('expires_in_seconds',86400),'invited_user_id':target,'revoked':False}; d['invitations'][code]=inv; save(f,d); js(self,{'invitation':inv},201); return
                if len(parts)==5 and parts[3]=='invitations' and method=='DELETE':
                    if not mm or mm['role'] not in ('owner','admin'): close(f); err(self,403); return
                    inv=d['invitations'].get(parts[4])
                    if not inv or inv['workspace_id']!=w['id']: close(f); err(self,404); return
                    inv['revoked']=True; save(f,d); js(self,{'deleted':True}); return
                if len(parts)==3 and method=='PATCH':
                    if not mm or mm['role'] not in ('owner','admin'): close(f); err(self,403); return
                    x=body(self) or {}
                    if 'join_mode' in x and x['join_mode'] not in ('open','invite_only'): close(f); err(self,400); return
                    w.update({k:x[k] for k in ('name','join_mode') if k in x}); save(f,d); js(self,{'workspace':workspace_obj(w)}); return
                if len(parts)==4 and parts[3]=='transfer_ownership' and method=='POST':
                    if not mm or mm['role']!='owner': close(f); err(self,403); return
                    target=member(d,w['id'],(body(self) or {}).get('new_owner_id'))
                    if not target or target['role']!='admin': close(f); err(self,400); return
                    old=u['id']; target['role']='owner'; member(d,w['id'],old)['role']='admin'; w['owner_id']=target['user_id']; save(f,d); js(self,{'workspace':workspace_obj(w)}); return
                if len(parts)==5 and parts[3]=='members' and method=='PATCH':
                    tid=int(parts[4]); x=body(self) or {}; role=x.get('role')
                    if role not in ('owner','admin','member','guest'): close(f); err(self,400); return
                    target=member(d,w['id'],tid)
                    if not target: close(f); err(self,404); return
                    if role=='owner': close(f); err(self,400); return
                    if not mm or mm['role'] not in ('owner','admin') or tid==u['id'] or (mm['role']=='admin' and target['role'] in ('owner','admin')): close(f); err(self,403); return
                    target['role']=role; save(f,d); js(self,{'member':target}); return
            if len(parts)==4 and parts[:2]==['api','channels'] and parts[3]=='read':
                c=d['channels'].get(parts[2])
                if not c: close(f); err(self,404); return
                if not member(d,c['workspace_id'],u['id']): close(f); err(self,403); return
                mm=member(d,c['workspace_id'],u['id']); mm.setdefault('reads',{})
                if method=='POST':
                    x=body(self) or {}
                    try: val=int(x.get('last_read_event_id'))
                    except: close(f); err(self,400); return
                    cur=int(mm['reads'].get(str(c['id']),0))
                    if val>cur: mm['reads'][str(c['id'])]=val
                st=read_state(d,c,u); save(f,d); js(self,{'read_state':st}); return
            # replies, search, and pins
            if len(parts)==4 and parts[:2]==['api','messages'] and parts[3]=='replies':
                pm=d.get('messages',{}).get(parts[2])
                if not pm: close(f); err(self,404); return
                cc=d['channels'].get(str(pm['channel_id'])); mm=member(d,cc['workspace_id'],u['id'])
                if not mm or (cc['is_private'] and cc['id'] not in mm.get('channel_ids',[])): close(f); err(self,403); return
                qs=parse_qs(urlparse(self.path).query); arr=[message_obj(d,m) for m in d.get('messages',{}).values() if m.get('parent_id')==pm['id']]
                arr.sort(key=lambda x:x['id'])
                try: out,nxt=page(arr,qs)
                except ValueError: close(f); err(self,400); return
                close(f); js(self,{'replies':out,'next_cursor':nxt}); return
            if parts==['api','search'] and method=='GET':
                qs=parse_qs(urlparse(self.path).query); q=qs.get('q',[''])[0]
                try: int(qs.get('limit',['50'])[0]); page([],qs)
                except ValueError: close(f); err(self,400); return
                wid=qs.get('workspace_id',[None])[0]; arr=[]
                for m in d.get('messages',{}).values():
                    cc=d['channels'].get(str(m['channel_id'])); mm=member(d,cc['workspace_id'],u['id']) if cc else None
                    if cc and mm and (not cc['is_private'] or cc['id'] in mm.get('channel_ids',[])) and q.lower() in m['body'].lower() and (wid is None or str(cc['workspace_id'])==wid):
                        arr.append(message_obj(d,m))
                arr.sort(key=lambda x:x['id'],reverse=True)
                try: out,nxt=page(arr,qs)
                except ValueError: close(f); err(self,400); return
                close(f); js(self,{'results':out,'next_cursor':nxt}); return
            if len(parts)==4 and parts[:2]==['api','channels'] and parts[3]=='pins':
                cc=d['channels'].get(parts[2])
                if not cc: close(f); err(self,404); return
                mm=member(d,cc['workspace_id'],u['id'])
                if not mm or (cc['is_private'] and cc['id'] not in mm.get('channel_ids',[])): close(f); err(self,403); return
                qs=parse_qs(urlparse(self.path).query); arr=[]
                for z in d.get('pins',{}).values():
                    m=d.get('messages',{}).get(str(z['message_id']))
                    if m and m['channel_id']==cc['id']: arr.append({'id':m['id'],'message':message_obj(d,m),'pinned_by':z['pinned_by'],'pinned_at':z['pinned_at']})
                arr.sort(key=lambda x:x['pinned_at'],reverse=True)
                try: out,nxt=page(arr,qs)
                except ValueError: close(f); err(self,400); return
                for z in out: z.pop('id',None)
                close(f); js(self,{'pins':out,'next_cursor':nxt}); return
            if len(parts)==4 and parts[:2]==['api','messages'] and parts[3]=='pin' and method=='POST':
                m=d.get('messages',{}).get(parts[2])
                if not m: close(f); err(self,404); return
                cc=d['channels'].get(str(m['channel_id'])); mm=member(d,cc['workspace_id'],u['id'])
                if cc['is_archived']: close(f); err(self,423); return
                if not mm or (cc['is_private'] and cc['id'] not in mm.get('channel_ids',[])): close(f); err(self,403); return
                d.setdefault('pins',{}); z=d['pins'].get(str(m['id']))
                if not z:
                    z={'message_id':m['id'],'pinned_by':u['id'],'pinned_at':time.strftime('%Y-%m-%dT%H:%M:%S.',time.gmtime())+('%03d'%((m['id'])%1000))+'Z'}; d['pins'][str(m['id'])]=z
                save(f,d); js(self,{'pin':z}); return
            if len(parts)==4 and parts[:2]==['api','messages'] and parts[3]=='pin' and method=='DELETE':
                d.setdefault('pins',{}).pop(parts[2],None); save(f,d); js(self,{'unpinned':True}); return
            # channel management
            if len(parts)>=3 and parts[0:2]==['api','channels']:
                c=d['channels'].get(parts[2])
                if not c: close(f); err(self,404); return
                w=d['workspaces'].get(str(c['workspace_id']))
                mm=member(d,w['id'],u['id']) if w else None
                if len(parts)==4 and parts[3]=='members' and method=='GET':
                    if c['is_private'] and c['id'] not in (mm or {}).get('channel_ids',[]): close(f); err(self,403); return
                    out=[userobj(d['users'][str(m['user_id'])]) for k,m in d['members'].items() if k.startswith(str(w['id'])+':') and c['id'] in m.get('channel_ids',[])]
                    close(f); js(self,{'members':out}); return
                if len(parts)==4 and parts[3]=='leave' and method=='POST':
                    key=f'{w["id"]}:{u["id"]}'
                    if c['is_private']:
                        cm=d['members'].get(key,{}).get('channel_ids',[])
                        if c['id'] in cm: cm.remove(c['id'])
                    elif key in d['members']:
                        d['members'][key].setdefault('channel_ids',[])
                        removed=False
                        if c['id'] in d['members'][key]['channel_ids']: d['members'][key]['channel_ids'].remove(c['id']); removed=True
                    if removed: append_event(d,c['id'],'member.left',{'user_id':u['id']})
                    save(f,d); js(self,{'channel':channel_obj(c)}); return
                if len(parts)==3 and method=='PATCH':
                    if c['is_archived']: close(f); err(self,423); return
                    if not mm or (mm['role']!='owner' and c['id'] not in mm.get('channel_ids',[])): close(f); err(self,403); return
                    x=body(self) or {}
                    if 'topic' not in x or not isinstance(x['topic'],str) or len(x['topic'])>250: close(f); err(self,400); return
                    c['topic']=x['topic']; append_event(d,c['id'],'channel.updated',{'channel':channel_obj(c)}); save(f,d); js(self,{'channel':channel_obj(c)}); return
                if len(parts)==4 and parts[3] in ('archive','unarchive') and method=='POST':
                    if not mm or mm['role']!='owner': close(f); err(self,403); return
                    c['is_archived']=parts[3]=='archive'; append_event(d,c['id'],'channel.archived' if c['is_archived'] else 'channel.unarchived',{'channel':channel_obj(c)}); save(f,d); js(self,{'channel':channel_obj(c)}); return
            # accept invitation
            if len(parts)==4 and parts[:2]==['api','invitations'] and parts[3]=='accept' and method=='POST':
                code=parts[2]; inv=d['invitations'].get(code)
                if not inv or inv['revoked'] or inv['expires_at']<=time.time() or inv['uses']>=inv['max_uses']: close(f); err(self,404); return
                if inv['invited_user_id'] is not None and inv['invited_user_id']!=u['id']: close(f); err(self,403); return
                if not member(d,inv['workspace_id'],u['id']): d['members'][f'{inv["workspace_id"]}:{u["id"]}']={'user_id':u['id'],'role':'member'}
                inv['uses']+=1; save(f,d); js(self,{'workspace':workspace_obj(d['workspaces'][str(inv['workspace_id'])])}); return
            # channels join
            if len(parts)==4 and parts[:2]==['api','channels'] and parts[3]=='join' and method=='POST':
                c=d['channels'].get(parts[2])
                if not c: close(f); err(self,404); return
                w=d['workspaces'][str(c['workspace_id'])]; mm=member(d,w['id'],u['id'])
                if c['is_private'] and (not mm): close(f); err(self,403); return
                if mm and mm['role']=='guest': close(f); err(self,403); return
                if not mm and w['join_mode']=='invite_only': close(f); err(self,403); return
                joined=False
                if not mm:
                    d['members'][f'{w["id"]}:{u["id"]}']={'user_id':u['id'],'role':'member','channel_ids':[c['id']]}
                    joined=True
                else:
                    mm.setdefault('channel_ids',[])
                    if c['id'] not in mm['channel_ids']:
                        mm['channel_ids'].append(c['id'])
                        joined=True
                if joined: append_event(d,c['id'],'member.joined',{'user_id':u['id']})
                save(f,d); js(self,{'channel':channel_obj(c)}); return
            close(f); err(self,404)
        finally:
            try: f.close()
            except: pass

if __name__=='__main__':
    port=int(sys.argv[1]); ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
