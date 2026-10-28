import os, re, json, time, uuid, sqlite3, hashlib
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

DB='/app/state.sqlite3'
PORT=int(os.environ.get('PORT','8000'))
NODE=(PORT-8000)//1_000 if False else PORT-8000
def db():
    c=sqlite3.connect(DB,timeout=20)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c
def init():
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT,avatar_url TEXT,status_text TEXT,status_emoji TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT);
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id INTEGER,name TEXT,is_private INTEGER,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,creator_id INTEGER,expires REAL,max_uses INTEGER,uses INTEGER DEFAULT 0,invited_user_id INTEGER,revoked INTEGER DEFAULT 0);
    '''); c.commit(); c.close()
def obj_user(r):
    return {'id':r['id'],'username':r['username'],'display_name':r['display_name'] or r['username'],'timezone':r['timezone'] or 'UTC','avatar_url':r['avatar_url'] or '','status_text':r['status_text'] or '','status_emoji':r['status_emoji'] or ''}
def obj_ws(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def obj_ch(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def sendj(self,status,data):
        b=json.dumps(data).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
    def auth(self, required=True):
        x=self.headers.get('Authorization','')
        if not x.startswith('Bearer '):
            if required: self.sendj(401,{'error':'unauthorized'})
            return None
        c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(x[7:],)).fetchone(); c.close()
        if not r and required: self.sendj(401,{'error':'unauthorized'})
        return r
    def route(self):
        p=unquote(urlparse(self.path).path); parts=p.strip('/').split('/')
        if p=='/api/health': return self.sendj(200,{'status':'ok','node_id':NODE})
        if p=='/api/ws' or p.startswith('/api/ws?'): return self.sendj(401,{'error':'websocket required'})
        if p=='/api/auth/register':
            try: d=self.body(); u=d.get('username',''); pw=d.get('password','')
            except: return self.sendj(400,{'error':'bad json'})
            if not re.fullmatch(r'[A-Za-z0-9_]+',u) or len(pw)<8: return self.sendj(400,{'error':'invalid registration'})
            c=db()
            try:
                cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(u,pw,d.get('display_name') or u)); uid=cur.lastrowid
                tok=uuid.uuid4().hex+uuid.uuid4().hex; c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid)); c.commit()
                r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close(); return self.sendj(201,{'user':obj_user(r),'token':tok})
            except sqlite3.IntegrityError: c.close(); return self.sendj(409,{'error':'duplicate'})
        if p=='/api/auth/login':
            d=self.body(); c=db(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),d.get('password'))).fetchone()
            if not r: c.close(); return self.sendj(401,{'error':'invalid credentials'})
            tok=uuid.uuid4().hex+uuid.uuid4().hex; c.execute('INSERT INTO tokens VALUES(?,?)',(tok,r['id'])); c.commit(); c.close(); return self.sendj(200,{'user':obj_user(r),'token':tok})
        if p=='/api/auth/me':
            r=self.auth(); return r and self.sendj(200,{'user':obj_user(r)})
        u=self.auth()
        if not u: return
        c=db()
        if p=='/api/workspaces':
            if self.command=='GET':
                rs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close(); return self.sendj(200,{'workspaces':[obj_ws(x) for x in rs]})
            d=self.body(); slug=d.get('slug','')
            if not re.fullmatch(r'[a-z0-9-]{2,32}',slug): c.close(); return self.sendj(400,{'error':'invalid slug'})
            try:
                cur=c.execute('INSERT INTO workspaces(slug,name,owner_id,join_mode) VALUES(?,?,?,"open")',(slug,d.get('name',''),u['id'])); wid=cur.lastrowid
                c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); ch=c.execute('INSERT INTO channels(workspace_id,name,is_private) VALUES(?, "general",0)',(wid,)).lastrowid; c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,u['id'])); c.commit()
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); q=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone(); c.close(); return self.sendj(201,{'workspace':obj_ws(w),'general_channel':obj_ch(q)})
            except sqlite3.IntegrityError: c.close(); return self.sendj(409,{'error':'duplicate'})
        if len(parts)>=3 and parts[0]=='api' and parts[1]=='workspaces':
            slug=parts[2]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w: c.close(); return self.sendj(404,{'error':'not found'})
            mem=c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if len(parts)==3 and self.command=='GET':
                chans=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0)',(w['id'],)).fetchall(); c.close(); return self.sendj(200,{'workspace':obj_ws(w),'channels':[obj_ch(x) for x in chans],'read_state':{}})
            if len(parts)==4 and parts[3]=='members' and self.command=='GET':
                if not mem: c.close(); return self.sendj(403,{'error':'forbidden'})
                rs=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall(); c.close(); return self.sendj(200,{'members':[dict(x) for x in rs]})
            if len(parts)==5 and parts[3]=='members' and self.command=='PATCH':
                if not mem or mem['role'] not in ('owner','admin'): c.close(); return self.sendj(403,{'error':'forbidden'})
                target=int(parts[4]); old=c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone(); role=self.body().get('role')
                if role=='owner' or not old: c.close(); return self.sendj(400,{'error':'invalid role'})
                if target==u['id'] or old['role']=='owner' or (mem['role']=='admin' and old['role']=='admin'): c.close(); return self.sendj(403,{'error':'forbidden'})
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target)); c.commit(); c.close(); return self.sendj(200,{'member':{'user_id':target,'role':role}})
            if len(parts)==4 and parts[3]=='transfer_ownership' and self.command=='POST':
                if not mem or mem['role']!='owner': c.close(); return self.sendj(403,{'error':'forbidden'})
                tid=self.body().get('new_owner_id'); t=c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w['id'],tid)).fetchone()
                if not t or t['role']!='admin': c.close(); return self.sendj(400,{'error':'target must be admin'})
                c.execute('UPDATE members SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])); c.execute('UPDATE members SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],tid)); c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(tid,w['id'])); c.commit(); c.close(); return self.sendj(200,{'workspace':obj_ws(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())}) if False else self.sendj(200,{'ok':True})
            if len(parts)==4 and parts[3]=='channels' and self.command=='POST':
                d=self.body(); role=mem['role'] if mem else None
                if role not in ('owner','admin','member') or (d.get('is_private',False) and role not in ('owner','admin')): c.close(); return self.sendj(403,{'error':'forbidden'})
                name=d.get('name','')
                if not re.fullmatch(r'[a-z0-9-]{1,32}',name): c.close(); return self.sendj(400,{'error':'invalid name'})
                try:
                    q=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,int(bool(d.get('is_private'))),d.get('topic',''))); cid=q.lastrowid; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id'])); c.commit(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.sendj(201,{'channel':obj_ch(r)})
                except sqlite3.IntegrityError: c.close(); return self.sendj(409,{'error':'duplicate'})
            if len(parts)>=4 and parts[3]=='invitations':
                if self.command=='POST':
                    if not mem or mem['role'] not in ('owner','admin'): c.close(); return self.sendj(403,{'error':'forbidden'})
                    d=self.body(); code=uuid.uuid4().hex[:20]; exp=time.time()+int(d.get('expires_in_seconds',30*86400)); mx=int(d.get('max_uses',1)); target=d.get('invited_user_id')
                    c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?,0)',(code,w['id'],u['id'],exp,mx,0,target)); c.commit(); c.close(); return self.sendj(201,{'invitation':{'code':code,'workspace_id':w['id'],'max_uses':mx}})
                if self.command=='GET':
                    if not mem or mem['role'] not in ('owner','admin'): c.close(); return self.sendj(403,{'error':'forbidden'})
                    rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND expires>? AND uses<max_uses',(w['id'],time.time())).fetchall(); c.close(); return self.sendj(200,{'invitations':[dict(x) for x in rs]})
                if self.command=='DELETE' and len(parts)==5:
                    if not mem or mem['role'] not in ('owner','admin'): c.close(); return self.sendj(403,{'error':'forbidden'})
                    c.execute('UPDATE invitations SET revoked=1 WHERE code=? AND workspace_id=?',(parts[4],w['id'])); c.commit(); c.close(); return self.sendj(200,{'deleted':True})
            if len(parts)==3 and self.command=='PATCH':
                if not mem or mem['role'] not in ('owner','admin'): c.close(); return self.sendj(403,{'error':'forbidden'})
                d=self.body()
                if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'): c.close(); return self.sendj(400,{'error':'invalid mode'})
                c.execute('UPDATE workspaces SET name=COALESCE(?,name),join_mode=COALESCE(?,join_mode) WHERE id=?',(d.get('name'),d.get('join_mode'),w['id'])); c.commit(); r=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone(); c.close(); return self.sendj(200,{'workspace':obj_ws(r)})
        if parts[:2]==['api','channels'] and len(parts)>=3 and parts[-1]=='join':
            cid=int(parts[2]); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch: c.close(); return self.sendj(404,{'error':'not found'})
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); mem=c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if ch['is_private'] and not mem: c.close(); return self.sendj(403,{'error':'forbidden'})
            if not mem and w['join_mode']=='invite_only': c.close(); return self.sendj(403,{'error':'invite required'})
            if mem and mem['role']=='guest': c.close(); return self.sendj(403,{'error':'guest'})
            if not mem: c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
            c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id'])); c.commit(); c.close(); return self.sendj(200,{'channel':obj_ch(ch)})
        if parts[:3]==['api','invitations',parts[2] if len(parts)>2 else ''] and len(parts)==4 and parts[3]=='accept':
            code=parts[2]; inv=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
            if not inv or inv['revoked'] or inv['expires']<time.time() or inv['uses']>=inv['max_uses']: c.close(); return self.sendj(404,{'error':'invalid invitation'})
            if inv['invited_user_id'] and inv['invited_user_id']!=u['id']: c.close(); return self.sendj(403,{'error':'invitation targeted to another user'})
            c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(inv['workspace_id'],u['id'],'member')); c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,)); c.commit(); c.close(); return self.sendj(200,{'ok':True})
        c.close(); return self.sendj(404,{'error':'not found'})
    def do_GET(self):
        try:self.route()
        except Exception as e:self.sendj(500,{'error':str(e)})
    def do_POST(self):
        try:self.route()
        except Exception as e:self.sendj(500,{'error':str(e)})
    def do_PATCH(self):
        try:self.route()
        except Exception as e:self.sendj(500,{'error':str(e)})
    def do_DELETE(self):
        try:self.route()
        except Exception as e:self.sendj(500,{'error':str(e)})
if __name__=='__main__':
    init(); ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
