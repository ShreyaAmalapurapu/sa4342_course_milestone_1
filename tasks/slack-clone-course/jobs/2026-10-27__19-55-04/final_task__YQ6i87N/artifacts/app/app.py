#!/usr/bin/env python3
import json, os, re, sys, time, uuid, hashlib, fcntl
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

STATE='/app/data/state.json'
os.makedirs('/app/data',exist_ok=True)
if not os.path.exists(STATE):
    open(STATE,'w').write(json.dumps({'seq':{'user':0,'workspace':0,'channel':0,'invite':0},'users':{},'tokens':{},'workspaces':{},'channels':{},'members':{},'invitations':{}}))

def locked():
    f=open(STATE,'r+'); fcntl.flock(f,fcntl.LOCK_EX)
    try:
        try: d=json.load(f)
        except: d={'seq':{'user':0,'workspace':0,'channel':0,'invite':0},'users':{},'tokens':{},'workspaces':{},'channels':{},'members':{},'invitations':{}}
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

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self): self.route('GET')
    def do_POST(self): self.route('POST')
    def do_PATCH(self): self.route('PATCH')
    def do_DELETE(self): self.route('DELETE')
    def route(self,method):
        p=unquote(urlparse(self.path).path); parts=p.strip('/').split('/')
        if p=='/api/health':
            js(self,{'status':'ok','node_id':int(self.server.server_port)-8000}); return
        if p.startswith('/api/ws'):
            err(self,401); return
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
                d['workspaces'][str(wid)]=w; d['channels'][str(cid)]=c; d['members'][f'{wid}:{u["id"]}']={'user_id':u['id'],'role':'owner'}
                save(f,d); js(self,{'workspace':workspace_obj(w),'general_channel':channel_obj(c)},201); return
            if len(parts)>=3 and parts[0:2]==['api','workspaces']:
                slug=parts[2]; w=next((z for z in d['workspaces'].values() if z['slug']==slug),None)
                if not w: close(f); err(self,404); return
                mm=member(d,w['id'],u['id'])
                if len(parts)==3 and method=='GET':
                    cs=[channel_obj(c) for c in d['channels'].values() if c['workspace_id']==w['id'] and (not c['is_private'] or member(d,w['id'],u['id']))]
                    close(f); js(self,{'workspace':workspace_obj(w),'channels':cs,'read_state':{}}); return
                if len(parts)==4 and parts[3]=='members' and method=='GET':
                    if not mm: close(f); err(self,403); return
                    out=[x for k,x in d['members'].items() if k.startswith(str(w['id'])+':')]
                    close(f); js(self,{'members':out}); return
                if len(parts)==4 and parts[3]=='channels' and method=='POST':
                    x=body(self) or {}; priv=bool(x.get('is_private',False))
                    if not mm or mm['role'] not in ('owner','admin','member') or (priv and mm['role'] not in ('owner','admin')): close(f); err(self,403); return
                    name=x.get('name','')
                    if not re.fullmatch(r'[a-z0-9-]{1,32}',name): close(f); err(self,400); return
                    if any(c['workspace_id']==w['id'] and c['name']==name for c in d['channels'].values()): close(f); err(self,409); return
                    cid=new(d,'channel'); c={'id':cid,'workspace_id':w['id'],'name':name,'is_private':priv,'is_dm':False,'topic':x.get('topic',''),'is_archived':False}; d['channels'][str(cid)]=c; save(f,d); js(self,{'channel':channel_obj(c)},201); return
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
                if not mm: d['members'][f'{w["id"]}:{u["id"]}']={'user_id':u['id'],'role':'member'}
                save(f,d); js(self,{'channel':channel_obj(c)}); return
            close(f); err(self,404)
        finally:
            try: f.close()
            except: pass

if __name__=='__main__':
    port=int(sys.argv[1]); ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
