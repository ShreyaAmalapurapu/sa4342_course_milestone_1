import os,sys,json,re,sqlite3,secrets,hashlib,base64,struct,threading,time,datetime,urllib.parse, mimetypes,socket
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/data/huddle.db'; os.makedirs('/app/data',exist_ok=True)
LOCK=threading.RLock()
def db():
 for _ in range(30):
  try:
   c=sqlite3.connect(DB,timeout=10,isolation_level=None)
   c.row_factory=sqlite3.Row
   c.execute('PRAGMA busy_timeout=10000')
   c.execute('PRAGMA journal_mode=WAL')
   return c
  except sqlite3.OperationalError:
   time.sleep(.1)
 raise RuntimeError('database unavailable')
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init():
 c=db()
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(ws INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(ws,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,ws INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cmembers(channel INTEGER,user_id INTEGER,PRIMARY KEY(channel,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel INTEGER,author INTEGER,body TEXT,parent INTEGER,created TEXT,edited TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS events(channel INTEGER,event_id INTEGER,type TEXT,payload TEXT,PRIMARY KEY(channel,event_id));
CREATE TABLE IF NOT EXISTS pins(message INTEGER PRIMARY KEY,user_id INTEGER,pinned TEXT);
CREATE TABLE IF NOT EXISTS reactions(message INTEGER, user_id INTEGER, emoji TEXT,PRIMARY KEY(message,user_id,emoji));
CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel INTEGER,last_event INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel));
CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,ws INTEGER,creator INTEGER,expires INTEGER,max_uses INTEGER,uses INTEGER DEFAULT 0,target INTEGER,targetname TEXT);
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,ws INTEGER,handle TEXT,name TEXT,creator INTEGER,users TEXT,UNIQUE(ws,handle));''')
 c.commit(); c.close()
init()
clients=[]; clients_lock=threading.Lock()
def userobj(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def getuser(c,uid): return c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
def token_user(c,h):
 if not h or not h.startswith('Bearer '): return None
 r=c.execute('SELECT user_id FROM tokens WHERE token=?',(h[7:],)).fetchone()
 return getuser(c,r[0]) if r else None
def out(h,status,obj=None,extra=''):
 b=json.dumps(obj if obj is not None else {}).encode(); h.send_response(status); h.send_header('Content-Type','application/json'); h.send_header('Content-Length',str(len(b))); h.end_headers(); h.wfile.write(b)
def fail(h,s,msg='error'): out(h,s,{'error':msg})
def parse(h):
 n=int(h.headers.get('Content-Length','0')); 
 try:return json.loads(h.rfile.read(n) or b'{}')
 except:return {}
def pag(qs):
 try: lim=int(qs.get('limit',['50'])[0])
 except: raise ValueError()
 if lim<1 or lim>200: raise ValueError()
 cur=qs.get('cursor',[None])[0]
 if cur is not None and (not cur.isdigit()): raise ValueError()
 return lim,int(cur) if cur else None
def cursor(i): return str(i)
def wsrow(c,slug): return c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
def channel(c,cid): return c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
def member(c,ch,uid): return c.execute('SELECT 1 FROM cmembers WHERE channel=? AND user_id=?',(ch,uid)).fetchone()
def wsmember(c,ws,uid): return c.execute('SELECT role FROM members WHERE ws=? AND user_id=?',(ws,uid)).fetchone()
def chanobj(r): return {'id':r['id'],'workspace_id':r['ws'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['archived'])}
def add_event(c,ch,typ,payload):
 for _ in range(40):
  try:
   c.execute('BEGIN IMMEDIATE')
   n=c.execute('SELECT COALESCE(MAX(event_id),0)+1 n FROM events WHERE channel=?',(ch,)).fetchone()['n']
   c.execute('INSERT INTO events(channel,event_id,type,payload) VALUES(?,?,?,?)',(ch,n,typ,json.dumps(payload)))
   c.execute('COMMIT')
   return n
  except sqlite3.IntegrityError:
   try: c.execute('ROLLBACK')
   except: pass
   time.sleep(.01)
  except sqlite3.OperationalError:
   try: c.execute('ROLLBACK')
   except: pass
   time.sleep(.02)
 raise sqlite3.OperationalError('event allocation failed')
def msgobj(c,r):
 au=getuser(c,r['author']); rc=c.execute('SELECT count(*) n FROM messages WHERE parent=? AND deleted=0',(r['id'],)).fetchone()['n']
 rr=[]
 for x in c.execute('SELECT emoji,count(*) n FROM reactions WHERE message=? GROUP BY emoji',(r['id'],)):
  rr.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[str(z[0]) for z in c.execute('SELECT user_id FROM reactions WHERE message=? AND emoji=?',(r['id'],x['emoji']))]})
 return {'id':r['id'],'channel_id':r['channel'],'author_id':r['author'],'author':userobj(au),'body':r['body'],'parent_id':r['parent'],'reply_count':rc,'created_at':r['created'],'edited_at':r['edited'],'files':[],'reactions':rr,'mentions':json.loads(r['mentions'] or '[]')}
def broadcast(ev):
 # WebSocket handlers poll the shared durable event log. Keeping this local
 # broadcast disabled avoids duplicate delivery when a node also observes its
 # own committed event through the poller.
 return
def ws_send(s,x):
 b=x.encode(); n=len(b)
 if n<126: head=bytes([0x81,n])
 elif n<65536: head=bytes([0x81,126])+struct.pack('>H',n)
 else: head=bytes([0x81,127])+struct.pack('>Q',n)
 s.sendall(head+b)
def _recv_exact(s,n):
 b=b''
 while len(b)<n:
  x=s.recv(n-len(b))
  if not x: return None
  b+=x
 return b
def ws_read(s):
 h=_recv_exact(s,2)
 if not h:return None
 n=h[1]&127
 if n==126:
  z=_recv_exact(s,2)
  if not z:return None
  n=struct.unpack('>H',z)[0]
 elif n==127:
  z=_recv_exact(s,8)
  if not z:return None
  n=struct.unpack('>Q',z)[0]
 mask=_recv_exact(s,4); data=_recv_exact(s,n)
 if not mask or data is None:return None
 return bytes(data[i]^mask[i%4] for i in range(n)).decode()
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a):pass
 def route(self):
  p=urllib.parse.urlsplit(self.path); return p.path,urllib.parse.parse_qs(p.query)
 def auth(self,c,required=True):
  u=token_user(c,self.headers.get('Authorization'))
  if required and not u: fail(self,401); return None
  return u
 def do_GET(self):
  if self.path.startswith('/api/ws'): return self._do_GET()
  with LOCK: return self._do_GET()
 def _do_GET(self):
  p,q=self.route(); c=db()
  if p=='/api/health': out(self,200,{'status':'ok','node_id':self.server.server_port-8000}); return
  if p=='/api/ws': self.websocket(c); return
  u=self.auth(c)
  if not u:return
  if p=='/api/auth/me': out(self,200,{'user':userobj(u)});return
  if p=='/api/workspaces':
   arr=[] 
   for r in c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.ws=w.id WHERE m.user_id=?',(u['id'],)): arr.append({'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']})
   out(self,200,{'workspaces':arr});return
  m=re.match(r'/api/users/(\d+)$',p)
  if m:
   r=getuser(c,int(m[1])); out(self,200,{'user':userobj(r)}) if r else fail(self,404);return
  m=re.match(r'/api/workspaces/([^/]+)(?:/(.*))?$',p)
  if m:
   w=wsrow(c,m[1]); 
   if not w:fail(self,404);return
   mem=wsmember(c,w['id'],u['id'])
   sub=m[2]
   if sub=='members':
    out(self,200,{'members':[{'user_id':r['user_id'],'role':r['role'],'username':getuser(c,r['user_id'])['username']} for r in c.execute('SELECT * FROM members WHERE ws=?',(w['id'],))]});return
   if sub=='groups': out(self,200,{'groups':[{'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['users'])} for r in c.execute('SELECT * FROM groups_ WHERE ws=?',(w['id'],))]});return
   if sub=='invitations':
    if not mem or mem['role'] not in ('owner','admin'): fail(self,403); return
    out(self,200,{'invitations':[dict(r) for r in c.execute('SELECT code,ws,creator,expires,max_uses,uses,target,targetname FROM invites WHERE ws=? AND expires>? AND uses<max_uses',(w['id'],int(time.time()))) ]}); return
   chans=[]
   for r in c.execute('SELECT * FROM channels WHERE ws=? AND (archived=0 OR ?)',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'))):
    if not r['is_private'] or member(c,r['id'],u['id']): chans.append(chanobj(r))
   out(self,200,{'workspace':{'id':w['id'],'slug':w['slug'],'name':w['name'],'owner_id':w['owner_id'],'join_mode':w['join_mode']},'channels':chans,'read_state':readstate(c,u['id'],chans)});return
  m=re.match(r'/api/channels/(\d+)(?:/(.*))?$',p)
  if m:
   ch=channel(c,int(m[1])); sub=m[2] or ''
   if not ch:fail(self,404);return
   if sub=='members':
    out(self,200,{'members':[userobj(getuser(c,x['user_id'])) for x in c.execute('SELECT user_id FROM cmembers WHERE channel=?',(ch['id'],))]});return
   if sub=='messages':
    try: lim,cur=pag(q)
    except:fail(self,400);return
    base='SELECT * FROM messages WHERE channel=? AND deleted=0 AND parent IS NULL'; args=[ch['id']]
    if cur is not None: base+=' AND id<?';args.append(cur)
    rows=c.execute(base+' ORDER BY id DESC LIMIT ?',args+[lim+1]).fetchall(); nxt=cursor(rows[lim-1]['id']) if len(rows)>lim else None
    out(self,200,{'messages':[msgobj(c,r) for r in rows[:lim]],'next_cursor':nxt});return
   if sub=='pins':
    if ch['is_private'] and not member(c,ch['id'],u['id']): fail(self,403); return
    try:lim,cur=pag(q)
    except:fail(self,400);return
    sql='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message WHERE m.channel=?'
    args=[ch['id']]
    if cur is not None: sql+=' AND p.message<?'; args.append(cur)
    rows=c.execute(sql+' ORDER BY p.message DESC LIMIT ?', args+[lim+1]).fetchall()
    out(self,200,{'pins':[{'message':msgobj(c,r),'pinned_by':r['user_id'],'pinned_at':r['pinned']} for r in rows[:lim]],'next_cursor':cursor(rows[lim-1]['message']) if len(rows)>lim else None});return
   if sub=='read': out(self,200,{'read_state':readstate(c,u['id'],[ch])});return
  m=re.match(r'/api/messages/(\d+)/replies$',p)
  if m:
   parent=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(m[1]),)).fetchone()
   if not parent: fail(self,404); return
   try: lim,cur=pag(q)
   except: fail(self,400); return
   sql='SELECT * FROM messages WHERE parent=? AND deleted=0'; args=[parent['id']]
   if cur is not None: sql+=' AND id>?'; args.append(cur)
   rows=c.execute(sql+' ORDER BY id ASC LIMIT ?',args+[lim+1]).fetchall()
   out(self,200,{'replies':[msgobj(c,r) for r in rows[:lim]],'next_cursor':str(rows[lim-1]['id']) if len(rows)>lim else None}); return
  if p=='/api/search':
   try:lim,cur=pag(q)
   except:fail(self,400);return
   text=q.get('q',[''])[0]; args=['%'+text+'%',u['id']]
   sql="SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel WHERE m.body LIKE ? AND m.deleted=0 AND (ch.is_private=0 OR EXISTS (SELECT 1 FROM cmembers cm WHERE cm.channel=ch.id AND cm.user_id=?))"
   if q.get('workspace_id'):
    sql+=' AND ch.ws=?'; args.append(int(q['workspace_id'][0]))
   if cur is not None: sql+=' AND m.id<?'; args.append(cur)
   sql+=' ORDER BY m.id DESC LIMIT ?'; args.append(lim+1)
   rows=c.execute(sql,args).fetchall()
   out(self,200,{'results':[msgobj(c,r) for r in rows[:lim]],'next_cursor':str(rows[lim-1]['id']) if len(rows)>lim else None});return
  fail(self,404)
 def do_POST(self):
  with LOCK: return self._do_POST()
 def _do_POST(self):
  p,q=self.route(); c=db()
  if p=='/api/auth/register':
   d=parse(self)
   if not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or len(d.get('password',''))<8:fail(self,400);return
   try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],d['password'],d.get('display_name') or d['username']));c.commit()
   except:fail(self,409);return
   u=getuser(c,c.execute('SELECT last_insert_rowid() x').fetchone()['x']); t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id']));c.commit();out(self,201,{'user':userobj(u),'token':t});return
  if p=='/api/auth/login':
   d=parse(self);u=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),d.get('password'))).fetchone()
   if not u:fail(self,401);return
   t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id']));c.commit();out(self,200,{'user':userobj(u),'token':t});return
  u=self.auth(c)
  if not u:return
  if p=='/api/workspaces':
   d=parse(self)
   if not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')):fail(self,400);return
   try:
    c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d.get('name',''),u['id'])); wid=c.execute('SELECT last_insert_rowid() x').fetchone()['x'];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(ws,name) VALUES(?,?)',(wid,'general'));gid=c.execute('SELECT last_insert_rowid() x').fetchone()['x'];c.execute('INSERT INTO cmembers VALUES(?,?)',(gid,u['id']));c.commit()
   except:fail(self,409);return
   w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();out(self,201,{'workspace':{'id':wid,'slug':w['slug'],'name':w['name'],'owner_id':u['id'],'join_mode':'open'},'general_channel':chanobj(c.execute('SELECT * FROM channels WHERE id=?',(gid,)).fetchone())});return
  m=re.match(r'/api/workspaces/([^/]+)/transfer_ownership$',p)
  if m:
   w=wsrow(c,m[1]); actor=wsmember(c,w['id'],u['id']); d=parse(self); target=int(d.get('new_owner_id',0)); tr=wsmember(c,w['id'],target)
   if not actor or actor['role']!='owner': fail(self,403); return
   if not tr or tr['role']!='admin': fail(self,400); return
   c.execute('UPDATE members SET role=? WHERE ws=? AND user_id=?',('admin',w['id'],u['id']))
   c.execute('UPDATE members SET role=? WHERE ws=? AND user_id=?',('owner',w['id'],target))
   c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id'])); c.commit()
   w=wsrow(c,m[1]); out(self,200,{'workspace':{'id':w['id'],'slug':w['slug'],'name':w['name'],'owner_id':w['owner_id'],'join_mode':w['join_mode']}}); return
  m=re.match(r'/api/channels/(\d+)/(leave|archive|unarchive)$',p)
  if m:
   ch=channel(c,int(m[1]))
   if not ch: fail(self,404); return
   w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['ws'],)).fetchone()
   role=wsmember(c,ch['ws'],u['id'])
   action=m[2]
   if action=='leave':
    if member(c,ch['id'],u['id']):
     c.execute('DELETE FROM cmembers WHERE channel=? AND user_id=?',(ch['id'],u['id']))
     ev=add_event(c,ch['id'],'member.left',{'user_id':u['id']}); c.commit()
     broadcast({'type':'member.left','event_id':ev,'channel_id':ch['id'],'user_id':u['id']})
    out(self,200,{'channel':chanobj(ch)}); return
   if not role or role['role']!='owner': fail(self,403); return
   archived=1 if action=='archive' else 0
   c.execute('UPDATE channels SET archived=? WHERE id=?',(archived,ch['id']))
   typ='channel.archived' if archived else 'channel.unarchived'
   ev=add_event(c,ch['id'],typ,{}); c.commit()
   broadcast({'type':typ,'event_id':ev,'channel_id':ch['id']})
   out(self,200,{'channel':chanobj(channel(c,ch['id']))}); return
  m=re.match(r'/api/channels/(\d+)/join$',p)
  if m:
   ch=channel(c,int(m[1]));
   if not ch:fail(self,404);return
   w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['ws'],)).fetchone();wm=wsmember(c,w['id'],u['id'])
   if ch['is_private'] and not wm:fail(self,403);return
   if wm and wm['role']=='guest' and not member(c,ch['id'],u['id']): fail(self,403); return
   if not wm:
    if w['join_mode']!='open':fail(self,403);return
    c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
   if not member(c,ch['id'],u['id']):
    c.execute('INSERT INTO cmembers VALUES(?,?)',(ch['id'],u['id'])); ev=add_event(c,ch['id'],'member.joined',{'user_id':u['id']}); c.commit(); broadcast({'type':'member.joined','event_id':ev,'channel_id':ch['id'],'user_id':u['id']})
   out(self,200,{'channel':chanobj(ch)});return
  m=re.match(r'/api/workspaces/([^/]+)/channels$',p)
  if m:
   w=wsrow(c,m[1]);d=parse(self);r=wsmember(c,w['id'],u['id']) if w else None
   if not w or not r:fail(self,404);return
   if r['role']=='guest' or (d.get('is_private') and r['role'] not in ('owner','admin')):fail(self,403);return
   if not re.fullmatch(r'[a-z0-9-]{1,32}',d.get('name','')) or len(d.get('topic',''))>250:fail(self,400);return
   if c.execute('SELECT 1 FROM channels WHERE ws=? AND name=?',(w['id'],d['name'])).fetchone():fail(self,409);return
   c.execute('INSERT INTO channels(ws,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic',''))); cid=c.execute('SELECT last_insert_rowid() x').fetchone()['x'];c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit();out(self,201,{'channel':chanobj(channel(c,cid))});return
  m=re.match(r'/api/channels/(\d+)/messages$',p)
  if m:
   ch=channel(c,int(m[1]));d=parse(self)
   if not ch:fail(self,404);return
   if ch['archived']:fail(self,423);return
   if not d.get('body') or not d['body'].strip():fail(self,400);return
   if ch['is_private'] and not member(c,ch['id'],u['id']):fail(self,403);return
   par=d.get('parent_id')
   if par and not c.execute('SELECT 1 FROM messages WHERE id=? AND channel=? AND deleted=0',(par,ch['id'])).fetchone():fail(self,400);return
   ids=[]; txt=d['body']
   for x in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)\b',txt):
    z=c.execute('SELECT id FROM users WHERE username=?',(x,)).fetchone()
    if z and z['id']!=u['id'] and (member(c,ch['id'],z['id']) or c.execute('SELECT 1 FROM members WHERE ws=? AND user_id=?',(ch['ws'],z['id'])).fetchone()):ids.append(str(z['id']))
   if '@channel' in txt: ids += [str(x['user_id']) for x in c.execute('SELECT user_id FROM cmembers WHERE channel=? AND user_id!=?',(ch['id'],u['id']))]
   ids=list(dict.fromkeys(ids)); t=now();c.execute('INSERT INTO messages(channel,author,body,parent,created,mentions) VALUES(?,?,?,?,?,?)',(ch['id'],u['id'],txt,par,t,json.dumps(ids)));mid=c.execute('SELECT last_insert_rowid() x').fetchone()['x']; typ='message.reply' if par else 'message.created'; ev=add_event(c,ch['id'],typ,{'message_id':mid});c.commit(); mo=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone());broadcast({'type':typ,'event_id':ev,'channel_id':ch['id'],'message':mo});out(self,201,{'message':mo});return
  m=re.match(r'/api/messages/(\d+)/pin$',p)
  if m:
   r=c.execute('SELECT m.*,ch.ws FROM messages m JOIN channels ch ON ch.id=m.channel WHERE m.id=?',(int(m[1]),)).fetchone()
   if not r:fail(self,404);return
   chx=channel(c,r['channel'])
   if chx['archived']: fail(self,423); return
   if chx['is_private'] and not member(c,chx['id'],u['id']): fail(self,403); return
   c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(r['id'],u['id'],now()));c.commit();pinned=c.execute('SELECT * FROM pins WHERE message=?',(r['id'],)).fetchone();out(self,200,{'pin':{'message_id':r['id'],'pinned_by':pinned['user_id'],'pinned_at':pinned['pinned']}});return
  m=re.match(r'/api/channels/(\d+)/read$',p)
  if m:
   ch=channel(c,int(m[1]));d=parse(self); old=c.execute('SELECT last_event FROM reads WHERE user_id=? AND channel=?',(u['id'],ch['id'])).fetchone(); ev=max(int(d.get('last_read_event_id',0)),old['last_event'] if old else 0);c.execute('INSERT OR REPLACE INTO reads VALUES(?,?,?)',(u['id'],ch['id'],ev));c.commit();out(self,200,{'read_state':readstate(c,u['id'],[ch])});return
  m=re.match(r'/api/workspaces/([^/]+)/invitations$',p)
  if m:
   w=wsrow(c,m[1]);r=wsmember(c,w['id'],u['id']) if w else None
   if not r or r['role'] not in ('owner','admin'):fail(self,403);return
   d=parse(self)
   try: maxuses=int(d.get('max_uses',1))
   except: fail(self,400); return
   if maxuses<1: fail(self,400); return
   target=d.get('invited_user_id')
   if d.get('invited_username'):
    tu=c.execute('SELECT id FROM users WHERE username=?',(d['invited_username'],)).fetchone()
    if not tu: fail(self,400); return
    target=tu['id']
   code=secrets.token_urlsafe(10)
   if target: maxuses=1
   c.execute('INSERT INTO invites VALUES(?,?,?,?,?,?,?,?)',(code,w['id'],u['id'],int(time.time())+int(d.get('expires_in_seconds',86400)),maxuses,0,target,d.get('invited_username')));c.commit();out(self,201,{'invitation':{'code':code,'workspace_id':w['id'],'max_uses':maxuses}});return
  m=re.match(r'/api/invitations/([^/]+)/accept$',p)
  if m:
   r=c.execute('SELECT * FROM invites WHERE code=?',(m[1],)).fetchone()
   if not r or r['uses']>=r['max_uses'] or r['expires']<time.time() or (r['target'] and r['target']!=u['id']):fail(self,404 if not r or not r['target'] or r['target']==u['id'] else 403);return
   c.execute('INSERT OR REPLACE INTO members VALUES(?,?,?)',(r['ws'],u['id'],'member'));c.execute('UPDATE invites SET uses=uses+1 WHERE code=?',(m[1],));c.commit();out(self,200,{'accepted':True});return
  m=re.match(r'/api/workspaces/([^/]+)/invitations/([^/]+)$',p)
  if m:
   w=wsrow(c,m[1]); r=wsmember(c,w['id'],u['id']) if w else None
   if not r or r['role'] not in ('owner','admin'): fail(self,403); return
   inv=c.execute('SELECT 1 FROM invites WHERE ws=? AND code=?',(w['id'],m[2])).fetchone()
   if not inv: fail(self,404); return
   c.execute('DELETE FROM invites WHERE ws=? AND code=?',(w['id'],m[2])); c.commit(); out(self,200,{'deleted':True}); return
  fail(self,404)
 def do_PATCH(self):
  with LOCK: return self._do_PATCH()
 def _do_PATCH(self):
  p,q=self.route();c=db();u=self.auth(c)
  if not u:return
  m=re.match(r'/api/workspaces/([^/]+)/members/(\d+)$',p)
  if m:
   w=wsrow(c,m[1]); actor=wsmember(c,w['id'],u['id']); target=int(m[2]); d=parse(self); role=d.get('role')
   old=wsmember(c,w['id'],target)
   if role not in ('admin','member','guest'):fail(self,400);return
   if not old: fail(self,404); return
   if not actor or actor['role'] not in ('owner','admin') or target==w['owner_id'] or (actor['role']=='admin' and old['role']=='admin'):fail(self,403);return
   c.execute('UPDATE members SET role=? WHERE ws=? AND user_id=?',(role,w['id'],target));c.commit();out(self,200,{'member':{'user_id':target,'role':role}});return
  m=re.match(r'/api/workspaces/([^/]+)$',p)
  if m:
   w=wsrow(c,m[1]);r=wsmember(c,w['id'],u['id']);d=parse(self)
   if d.get('join_mode') is not None and d['join_mode'] not in ('open','invite_only'): fail(self,400); return
   if not r or r['role'] not in ('owner','admin'):fail(self,403);return
   c.execute('UPDATE workspaces SET name=COALESCE(?,name),join_mode=COALESCE(?,join_mode) WHERE id=?',(d.get('name'),d.get('join_mode'),w['id']));c.commit();w=wsrow(c,m[1]);out(self,200,{'workspace':dict(w)});return
  m=re.match(r'/api/channels/(\d+)$',p)
  if m:
   ch=channel(c,int(m[1]))
   if not ch: fail(self,404); return
   r=wsmember(c,ch['ws'],u['id']);d=parse(self)
   if not r or (not member(c,ch['id'],u['id']) and r['role']!='owner'):fail(self,403);return
   if len(d.get('topic',''))>250:fail(self,400);return
   if ch['archived']:fail(self,423);return
   c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),ch['id']));ev=add_event(c,ch['id'],'channel.updated',{});c.commit();broadcast({'type':'channel.updated','event_id':ev,'channel_id':ch['id']});out(self,200,{'channel':chanobj(channel(c,ch['id']))});return
  fail(self,404)
 def do_DELETE(self):
  with LOCK: return self._do_DELETE()
 def _do_DELETE(self):
  p,q=self.route();c=db();u=self.auth(c)
  if not u:return
  m=re.match(r'/api/workspaces/([^/]+)/invitations/([^/]+)$',p)
  if m:
   w=wsrow(c,m[1]); r=wsmember(c,w['id'],u['id']) if w else None
   if not r or r['role'] not in ('owner','admin'): fail(self,403); return
   inv=c.execute('SELECT 1 FROM invites WHERE ws=? AND code=?',(w['id'],m[2])).fetchone()
   if not inv: fail(self,404); return
   c.execute('DELETE FROM invites WHERE ws=? AND code=?',(w['id'],m[2])); c.commit(); out(self,200,{'deleted':True}); return
  m=re.match(r'/api/messages/(\d+)/pin$',p)
  if m:c.execute('DELETE FROM pins WHERE message=?',(int(m[1]),));c.commit();out(self,200,{'unpinned':True});return
  fail(self,404)
 def websocket(self,c):
  if urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('token',[''])[0] not in [r['token'] for r in c.execute('SELECT token FROM tokens')]:
   self.send_error(401); return
  key=self.headers.get('Sec-WebSocket-Key')
  acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101); self.send_header('Upgrade','WebSocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',acc); self.end_headers()
  self.close_connection=True
  sock=self.connection; sock.settimeout(.2); chids=set(); sent={}
  item=(sock,chids)
  with clients_lock: clients.append(item)
  def emit(cid,after):
   cc=db()
   for e in cc.execute('SELECT * FROM events WHERE channel=? AND event_id>? ORDER BY event_id',(cid,after)):
    ev=json.loads(e['payload']); ev.update(type=e['type'],event_id=e['event_id'],channel_id=cid); ws_send(sock,json.dumps(ev)); sent[cid]=e['event_id']
   cc.close()
  try:
   while True:
    try:
     raw=ws_read(sock)
    except socket.timeout:
     raw=None
    if raw:
     d=json.loads(raw); cid=int(d.get('channel_id',0)); chids.add(cid)
     cc=db(); head=cc.execute('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel=?',(cid,)).fetchone()['n']; cc.close()
     if d.get('type')=='subscribe':
      sent[cid]=head; ws_send(sock,json.dumps({'type':'subscribed','channel_id':cid,'head_event_id':head}))
     elif d.get('type')=='resume':
      sent[cid]=int(d.get('since_event_id',0)); emit(cid,sent[cid]); cc=db(); head=cc.execute('SELECT COALESCE(MAX(event_id),0) n FROM events WHERE channel=?',(cid,)).fetchone()['n']; cc.close(); ws_send(sock,json.dumps({'type':'resumed','channel_id':cid,'head_event_id':head})); sent[cid]=head
    for cid in list(chids):
     if cid in sent: emit(cid,sent[cid])
  except:
   pass
  finally:
   with clients_lock:
    if item in clients: clients.remove(item)

def readstate(c,uid,chans):
 outa=[]
 for ch in chans:
  last=c.execute('SELECT COALESCE((SELECT last_event FROM reads WHERE user_id=? AND channel=?),0) x',(uid,ch['id'])).fetchone()['x']; head=c.execute('SELECT COALESCE(MAX(event_id),0) x FROM events WHERE channel=?',(ch['id'],)).fetchone()['x']
  unread=c.execute("SELECT count(*) n FROM events e JOIN messages m ON m.id=json_extract(e.payload,'$.message_id') WHERE e.channel=? AND e.event_id>? AND e.type IN ('message.created','message.reply') AND m.author!=?",(ch['id'],last,uid)).fetchone()['n']
  mention_count=c.execute("SELECT count(*) n FROM messages WHERE channel=? AND author!=? AND mentions LIKE ?",(ch['id'],uid,'%"'+str(uid)+'"%')).fetchone()['n']
  outa.append({'channel_id':ch['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':mention_count})
 return outa[0] if len(outa)==1 else outa
if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='cluster':
  servers=[]
  for port in (8000,8001,8002):
   os.environ['NODE_ID']=str(port-8000)
   srv=ThreadingHTTPServer(('127.0.0.1',port),H); servers.append(srv)
   threading.Thread(target=srv.serve_forever,daemon=True).start()
  while True: time.sleep(3600)
 else:
  port=int(sys.argv[1]);os.environ['NODE_ID']=str(port-8000);ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
