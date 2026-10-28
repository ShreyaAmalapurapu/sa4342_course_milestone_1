import os, re, json, time, uuid, sqlite3, hashlib, base64, struct, socket
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

DB='/app/state.sqlite3'
PORT=int(os.environ.get('PORT','8000'))
NODE=(PORT-8000)//1_000 if False else PORT-8000
def db():
    c=sqlite3.connect(DB,timeout=20)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=20000')
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
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,event_id INTEGER);
    CREATE TABLE IF NOT EXISTS channel_events(channel_id INTEGER,event_id INTEGER,type TEXT,message_id INTEGER,created_at TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS reads(channel_id INTEGER,user_id INTEGER,last_read_event_id INTEGER DEFAULT 0, mention_count INTEGER DEFAULT 0,PRIMARY KEY(channel_id,user_id));
    '''); c.commit(); c.close()
def obj_user(r):
    return {'id':r['id'],'username':r['username'],'display_name':r['display_name'] or r['username'],'timezone':r['timezone'] or 'UTC','avatar_url':r['avatar_url'] or '','status_text':r['status_text'] or '','status_emoji':r['status_emoji'] or ''}
def obj_ws(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def obj_ch(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
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
        def now(): return time.strftime('%Y-%m-%dT%H:%M:%S.000Z',time.gmtime())
        def msgobj(r):
            a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
            reps=c.execute('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
            return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':obj_user(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':reps,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[],'mentions':mention_ids(r['channel_id'],r['author_id'],r['body'])}
        def nextid(cid,typ,mid=None):
            q=c.execute('SELECT COALESCE(MAX(event_id),0)+1 n FROM channel_events WHERE channel_id=?',(cid,)).fetchone()['n']
            c.execute('INSERT INTO channel_events VALUES(?,?,?,?,?)',(cid,q,typ,mid,now())); return q
        def mention_ids(cid, author, body):
            chx=c.execute('SELECT workspace_id FROM channels WHERE id=?',(cid,)).fetchone()
            if not chx: return []
            ids=[]
            if re.search(r'(?<![\w@])@channel\b',body):
                ids=[x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(cid,author)).fetchall()]
            for m in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)\b',body):
                x=c.execute('SELECT id FROM users WHERE username=?',(m,)).fetchone()
                if x and x['id']!=author and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,x['id'])).fetchone(): ids.append(x['id'])
            return list(dict.fromkeys(ids))
        def accessible(ch):
            return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone() is not None
        # channel operations
        if parts[:3]==['api','workspaces',parts[2] if len(parts)>2 else ''] and len(parts)>=4 and parts[3]=='channels':
            slug=parts[2]; w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
            if not w: c.close(); return self.sendj(404,{'error':'not found'})
            mem=c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if self.command=='POST' and len(parts)==4:
                d=self.body(); name=d.get('name',''); topic=d.get('topic','')
                if not re.fullmatch(r'[a-z0-9-]{1,32}',name) or len(topic)>250: c.close(); return self.sendj(400,{'error':'invalid channel'})
                if not mem or mem['role'] not in ('owner','admin','member') or (d.get('is_private') and mem['role'] not in ('owner','admin')): c.close(); return self.sendj(403,{'error':'forbidden'})
                try:
                    if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone(): c.close(); return self.sendj(409,{'error':'duplicate'})
                    q=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,int(bool(d.get('is_private'))),topic)); c.execute('INSERT INTO channel_members VALUES(?,?)',(q.lastrowid,u['id'])); c.commit(); r=c.execute('SELECT * FROM channels WHERE id=?',(q.lastrowid,)).fetchone(); c.close(); return self.sendj(201,{'channel':obj_ch(r)})
                except sqlite3.IntegrityError: c.rollback(); c.close(); return self.sendj(409,{'error':'duplicate'})
        if parts[:2]==['api','channels'] and len(parts)>=3:
            try: cid=int(parts[2])
            except: cid=-1
            ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch: c.close(); return self.sendj(404,{'error':'not found'})
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); wm=c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone(); joined=accessible(ch)
            if parts[-1]=='members' and self.command=='GET':
                if ch['is_private'] and not joined: c.close(); return self.sendj(403,{'error':'forbidden'})
                rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(cid,)).fetchall(); c.close(); return self.sendj(200,{'members':[obj_user(x) for x in rs]})
            if parts[-1]=='join' and self.command=='POST':
                if ch['is_private'] and not joined: c.close(); return self.sendj(403,{'error':'forbidden'})
                if wm and wm['role']=='guest': c.close(); return self.sendj(403,{'error':'forbidden'})
                if not wm and w['join_mode']=='invite_only': c.close(); return self.sendj(403,{'error':'forbidden'})
                if not wm: c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
                c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id'])); nextid(cid,'member.joined'); c.commit(); c.close(); return self.sendj(200,{'channel':obj_ch(ch)})
            if parts[-1]=='leave' and self.command=='POST':
                c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])); nextid(cid,'member.left'); c.commit(); c.close(); return self.sendj(200,{'channel':obj_ch(ch)})
            if parts[-1] in ('archive','unarchive') and self.command=='POST':
                if not wm or wm['role']!='owner': c.close(); return self.sendj(403,{'error':'forbidden'})
                val=1 if parts[-1]=='archive' else 0; typ='channel.archived' if val else 'channel.unarchived'; eid=nextid(cid,typ); c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid)); c.commit(); c.close(); return self.sendj(200,{'channel':dict(obj_ch(ch),is_archived=bool(val))})
            if len(parts)==3 and self.command=='PATCH':
                if not joined and (not wm or wm['role']!='owner'): c.close(); return self.sendj(403,{'error':'forbidden'})
                d=self.body()
                if len(d.get('topic',''))>250: c.close(); return self.sendj(400,{'error':'topic too long'})
                c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',ch['topic']),cid)); nextid(cid,'channel.updated'); c.commit(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.sendj(200,{'channel':obj_ch(r)})
            if len(parts)>=4 and parts[3]=='messages':
                if not joined: c.close(); return self.sendj(403,{'error':'forbidden'})
                if self.command=='POST':
                    if ch['is_archived']: c.close(); return self.sendj(423,{'error':'archived'})
                    d=self.body(); body=d.get('body','')
                    if not body: c.close(); return self.sendj(400,{'error':'empty'})
                    par=d.get('parent_id'); t=now(); q=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],body,par,t)); mid=q.lastrowid; eid=nextid(cid,'message.reply' if par else 'message.created',mid); c.execute('UPDATE messages SET event_id=? WHERE id=?',(eid,mid)); c.execute('INSERT OR IGNORE INTO reads(channel_id,user_id,last_read_event_id,mention_count) VALUES(?,?,0,0)',(cid,u['id']));
                    for target in mention_ids(cid,u['id'],body):
                        c.execute('INSERT OR IGNORE INTO reads(channel_id,user_id,last_read_event_id,mention_count) VALUES(?,?,0,0)',(cid,target))
                        c.execute('UPDATE reads SET mention_count=COALESCE(mention_count,0)+1 WHERE channel_id=? AND user_id=?',(cid,target))
                    c.commit(); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); out=msgobj(r); c.close(); return self.sendj(201,{'message':out})
                if self.command=='GET':
                    qs=urlparse(self.path).query
                    z=dict(x.split('=',1) for x in qs.split('&') if '=' in x)
                    try:
                        lim=int(z.get('limit','50'))
                    except:
                        c.close(); return self.sendj(400,{'error':'limit'})
                    if lim<1 or lim>200: c.close(); return self.sendj(400,{'error':'limit'})
                    cur=z.get('cursor'); args=[cid]; where='channel_id=? AND parent_id IS NULL AND deleted=0'
                    if cur:
                        try: int(cur)
                        except: c.close(); return self.sendj(400,{'error':'cursor'})
                        where+=' AND id<?'; args.append(int(cur))
                    rs=c.execute('SELECT * FROM messages WHERE '+where+' ORDER BY id DESC LIMIT ?',args+[lim+1]).fetchall(); more=len(rs)>lim; rs=rs[:lim]; out=[msgobj(x) for x in rs]; nxt=str(rs[-1]['id']) if more and rs else None; c.close(); return self.sendj(200,{'messages':out,'next_cursor':nxt})
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
                include=urlparse(self.path).query.lower(); inc=('include_archived=1' in include or 'include_archived=true' in include); chans=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (? OR is_archived=0) AND (is_private=0 OR id IN (SELECT channel_id FROM channel_members WHERE user_id=?))',(w['id'],int(inc),u['id'])).fetchall(); c.close(); return self.sendj(200,{'workspace':obj_ws(w),'channels':[obj_ch(x) for x in chans],'read_state':{'last_read_event_id':0,'unread_count':0,'mention_count':0}})
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
        # remaining channel history endpoints
        if parts[:3]==['api','messages',parts[2] if len(parts)>2 else ''] and len(parts)>=4:
            try: mid=int(parts[2])
            except: mid=-1
            mr=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
            if not mr: c.close(); return self.sendj(404,{'error':'not found'})
            ch=c.execute('SELECT * FROM channels WHERE id=?',(mr['channel_id'],)).fetchone()
            if not accessible(ch): c.close(); return self.sendj(403,{'error':'forbidden'})
            if parts[3]=='replies' and self.command=='GET':
                q=urlparse(self.path).query; z=dict(x.split('=',1) for x in q.split('&') if '=' in x)
                try: lim=int(z.get('limit','50'))
                except: c.close(); return self.sendj(400,{'error':'limit'})
                cur=z.get('cursor'); args=[mid]; where='parent_id=? AND deleted=0'
                if cur:
                    try: args.append(int(cur))
                    except: c.close(); return self.sendj(400,{'error':'cursor'})
                    where+=' AND id>?'
                rs=c.execute('SELECT * FROM messages WHERE '+where+' ORDER BY id LIMIT ?',args+[lim+1]).fetchall(); more=len(rs)>lim; rs=rs[:lim]; out=[msgobj(x) for x in rs]; nxt=str(rs[-1]['id']) if more and rs else None; c.close(); return self.sendj(200,{'replies':out,'next_cursor':nxt})
            if parts[3]=='pin':
                if self.command=='POST':
                    t=now(); c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],t)); c.commit(); x=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone(); c.close(); return self.sendj(200,{'pin':{'message_id':mid,'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']}})
                if self.command=='DELETE':
                    c.execute('DELETE FROM pins WHERE message_id=?',(mid,)); c.commit(); c.close(); return self.sendj(200,{'unpinned':True})
        if parts[:3]==['api','channels',parts[2] if len(parts)>2 else ''] and len(parts)>=4 and parts[3]=='pins' and self.command=='GET':
            try: cid=int(parts[2])
            except: cid=-1
            ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch: c.close(); return self.sendj(404,{'error':'not found'})
            if not accessible(ch): c.close(); return self.sendj(403,{'error':'forbidden'})
            q=urlparse(self.path).query; z=dict(x.split('=',1) for x in q.split('&') if '=' in x)
            try: lim=int(z.get('limit','50'))
            except: c.close(); return self.sendj(400,{'error':'limit'})
            cur=z.get('cursor'); args=[cid]; where='m.channel_id=? AND p.message_id=m.id'
            if cur:
                try: args.append(int(cur))
                except: c.close(); return self.sendj(400,{'error':'cursor'})
                where+=' AND p.rowid<?'
            rs=c.execute('SELECT p.rowid AS pin_rowid,p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE '+where+' ORDER BY p.rowid DESC LIMIT ?',args+[lim+1]).fetchall(); more=len(rs)>lim; rs=rs[:lim]
            out=[{'message':msgobj(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs]; nxt=str(rs[-1]['pin_rowid']) if more and rs else None; c.close(); return self.sendj(200,{'pins':out,'next_cursor':nxt})
        if p=='/api/search' and self.command=='GET':
            q=urlparse(self.path).query; z=dict(x.split('=',1) for x in q.split('&') if '=' in x); term=unquote(z.get('q','')); wid=z.get('workspace_id')
            sql="SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.deleted=0 AND m.body LIKE ? AND ((ch.is_private=0 AND EXISTS (SELECT 1 FROM members wm WHERE wm.workspace_id=ch.workspace_id AND wm.user_id=?)) OR EXISTS (SELECT 1 FROM channel_members cm WHERE cm.channel_id=m.channel_id AND cm.user_id=?))"
            args=['%'+term+'%',u['id'],u['id']]
            if wid:
                sql+=" AND ch.workspace_id=?"; args.append(int(wid))
            sql+=" ORDER BY m.id DESC LIMIT 200"
            rs=c.execute(sql,args).fetchall(); out=[msgobj(x) for x in rs]; c.close(); return self.sendj(200,{'results':out,'next_cursor':None})
        if parts[:3]==['api','channels',parts[2] if len(parts)>2 else ''] and len(parts)>=4 and parts[3]=='read':
            try: cid=int(parts[2])
            except: cid=-1
            ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch or not accessible(ch): c.close(); return self.sendj(404 if not ch else 403,{'error':'not found'})
            r=c.execute('SELECT * FROM reads WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()
            last=r['last_read_event_id'] if r else 0
            head=c.execute('SELECT COALESCE(MAX(event_id),0) n FROM channel_events WHERE channel_id=?',(cid,)).fetchone()['n']
            unread=max(0,head-last); state={'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':(r['mention_count'] if r and 'mention_count' in r.keys() else 0)}
            if self.command=='POST':
                d=self.body(); x=int(d.get('last_read_event_id',0)); last=max(last,x); c.execute('INSERT OR REPLACE INTO reads(channel_id,user_id,last_read_event_id,mention_count) VALUES(?,?,?,COALESCE((SELECT mention_count FROM reads WHERE channel_id=? AND user_id=?),0))',(cid,u['id'],last,cid,u['id'])); c.commit(); state['last_read_event_id']=last; state['unread_count']=max(0,head-last)
            c.close(); return self.sendj(200,{'read_state':state})
        c.close(); return self.sendj(404,{'error':'not found'})
    def websocket(self):
        q=urlparse(self.path).query
        z=dict(x.split('=',1) for x in q.split('&') if '=' in x)
        tok=unquote(z.get('token',''))
        c=db(); user=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(tok,)).fetchone(); c.close()
        if not user:
            b=b'{"error":"unauthorized"}'; self.send_response(401); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.send_header('Connection','close'); self.end_headers(); self.wfile.write(b); return
        key=self.headers.get('Sec-WebSocket-Key')
        if not key:
            self.send_response(400); self.end_headers(); return
        import hashlib as _h
        accept=base64.b64encode(_h.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101,'Switching Protocols'); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
        sock=self.connection; sock.settimeout(0.5); subs={}; sent={}
        def frame(obj):
            b=json.dumps(obj,separators=(',',':')).encode(); n=len(b)
            if n<126: h=bytes([0x81,n])
            elif n<65536: h=bytes([0x81,126])+struct.pack('>H',n)
            else: h=bytes([0x81,127])+struct.pack('>Q',n)
            sock.sendall(h+b)
        def recv():
            h=sock.recv(2)
            if not h: return None
            n=h[1]&127
            if n==126: n=struct.unpack('>H',sock.recv(2))[0]
            elif n==127: n=struct.unpack('>Q',sock.recv(8))[0]
            mask=sock.recv(4); b=sock.recv(n)
            return bytes(b[i]^mask[i%4] for i in range(n))
        try:
            while True:
                try:
                    raw=recv()
                    if raw is None: break
                    if raw and (raw[0] if isinstance(raw,bytes) else 0)==8: break
                    d=json.loads(raw.decode())
                    cid=int(d.get('channel_id'))
                    c=db(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                    allowed=ch and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,user['id'])).fetchone()
                    if not allowed: c.close(); continue
                    if d.get('type')=='subscribe':
                        head=c.execute('SELECT COALESCE(MAX(event_id),0) n FROM channel_events WHERE channel_id=?',(cid,)).fetchone()['n']; subs[cid]=head; sent[cid]=head; frame({'type':'subscribed','channel_id':cid,'head_event_id':head})
                    elif d.get('type')=='resume':
                        since=int(d.get('since_event_id',0)); rows=c.execute('SELECT * FROM channel_events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall()
                        for e in rows:
                            ev={'type':e['type'],'event_id':e['event_id'],'channel_id':cid}
                            if e['message_id']:
                                mr=c.execute('SELECT * FROM messages WHERE id=?',(e['message_id'],)).fetchone()
                                if mr: ev['message']= {'id':mr['id'],'body':mr['body'],'author_id':mr['author_id'],'parent_id':mr['parent_id']}
                            frame(ev); sent[cid]=e['event_id']
                        head=c.execute('SELECT COALESCE(MAX(event_id),0) n FROM channel_events WHERE channel_id=?',(cid,)).fetchone()['n']; frame({'type':'resumed','channel_id':cid,'head_event_id':head}); sent[cid]=head
                    c.close()
                except socket.timeout: pass
                except (ConnectionResetError,ValueError,json.JSONDecodeError): break
                if subs:
                    c=db()
                    for cid,last in list(sent.items()):
                        rows=c.execute('SELECT * FROM channel_events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall()
                        for e in rows:
                            ev={'type':e['type'],'event_id':e['event_id'],'channel_id':cid}
                            if e['message_id']:
                                mr=c.execute('SELECT * FROM messages WHERE id=?',(e['message_id'],)).fetchone()
                                if mr: ev['message']={'id':mr['id'],'body':mr['body'],'author_id':mr['author_id'],'parent_id':mr['parent_id']}
                            frame(ev); sent[cid]=e['event_id']
                    c.close()
        except Exception: pass
    def do_GET(self):
        if self.path.startswith('/api/ws') and self.headers.get('Upgrade','').lower()=='websocket':
            return self.websocket()
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
