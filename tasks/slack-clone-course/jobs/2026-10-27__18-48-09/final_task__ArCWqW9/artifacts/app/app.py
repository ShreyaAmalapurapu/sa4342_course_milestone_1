import os,sys,json,re,sqlite3,hashlib,secrets,datetime,threading,base64,struct, socket
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,unquote
DB='/app/data/huddle.sqlite'; os.makedirs('/app/data',exist_ok=True)
lock=threading.RLock(); clients=[]

def now():
 return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=10,check_same_thread=False,isolation_level=None)
 c.row_factory=sqlite3.Row
 c.execute('pragma busy_timeout=10000')
 return c
def init():
 with lock:
  c=db()
  c.execute('pragma journal_mode=WAL')
  c.execute('pragma synchronous=NORMAL')
  c.execute('pragma busy_timeout=1000')
  c.executescript('''
  create table if not exists users(id integer primary key,username text unique,password text,display_name text,timezone text default 'UTC',avatar_url text default '',status_text text default '',status_emoji text default '');
  create table if not exists tokens(token text primary key,user_id integer);
  create table if not exists workspaces(id integer primary key,slug text unique,name text,owner_id integer,join_mode text default 'open');
  create table if not exists members(workspace_id integer,user_id integer,role text,primary key(workspace_id,user_id));
  create table if not exists channels(id integer primary key,workspace_id integer,name text,is_private integer default 0,is_dm integer default 0,topic text default '',is_archived integer default 0);
  create table if not exists channel_members(channel_id integer,user_id integer,primary key(channel_id,user_id));
  create table if not exists messages(id integer primary key,channel_id integer,author_id integer,body text,parent_id integer,created_at text,edited_at text,deleted integer default 0,mentions text default '[]');
  create table if not exists reactions(message_id integer,user_id integer,emoji text,primary key(message_id,user_id,emoji));
  create table if not exists pins(message_id integer primary key,pinned_by integer,pinned_at text);
  create table if not exists events(channel_id integer,event_id integer,kind text,data text,primary key(channel_id,event_id));
  create table if not exists reads(channel_id integer,user_id integer,last_read integer default 0);
  create table if not exists groups(id integer primary key,workspace_id integer,handle text,name text,creator_id integer,user_ids text);
  create table if not exists invitations(code text primary key,workspace_id integer,invited_user_id integer,expires integer,max_uses integer,uses integer default 0);
  ''')
  c.executescript('''create index if not exists ix_events_channel_event on events(channel_id,event_id);
  create index if not exists ix_messages_channel_parent on messages(channel_id,parent_id,deleted);
  create index if not exists ix_channel_members_user on channel_members(user_id,channel_id);
  create index if not exists ix_members_user on members(user_id,workspace_id);''')
  c.commit(); c.close()
init()
def row(c,q,a=()): return c.execute(q,a).fetchone()
def userobj(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def channel(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def ws(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def token_user(h):
 if not h or not h.startswith('Bearer '): return None
 with lock:
  c=db(); r=row(c,'select u.* from tokens t join users u on u.id=t.user_id where t.token=?',(h[7:],)); c.close(); return r
def member(c,w,u): return row(c,'select * from members where workspace_id=? and user_id=?',(w,u))
def accessible(c,ch,u,write=False):
 uid=u['id'] if isinstance(u,sqlite3.Row) or isinstance(u,dict) else int(u)
 m=member(c,ch['workspace_id'],uid)
 cm=row(c,'select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],uid))
 if not m: return False
 if ch['is_private'] and not cm: return False
 return bool(cm or (not write and not ch['is_private']))

def emit(c,cid,kind,data):
 payload=json.dumps(data)
 for attempt in range(20):
  try:
   c.execute('BEGIN IMMEDIATE')
   n=c.execute('select coalesce(max(event_id),0)+1 from events where channel_id=?',(cid,)).fetchone()[0]
   c.execute('insert into events(channel_id,event_id,kind,data) values(?,?,?,?)',(cid,n,kind,payload))
   c.execute('COMMIT')
   return n
  except sqlite3.IntegrityError:
   try: c.execute('ROLLBACK')
   except Exception: pass
   continue
  except sqlite3.OperationalError:
   try: c.execute('ROLLBACK')
   except Exception: pass
   __import__('time').sleep(0.01*(attempt+1))
 raise sqlite3.OperationalError('event allocation timeout')

def msgobj(c,r):
 au=row(c,'select * from users where id=?',(r['author_id'],)); reps=row(c,'select count(*) n from messages where parent_id=? and deleted=0',(r['id'],))['n']
 rr=[]
 for x in c.execute('select emoji,count(*) n from reactions where message_id=? group by emoji',(r['id'],)):
  rr.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[z['user_id'] for z in c.execute('select user_id from reactions where message_id=? and emoji=?',(r['id'],x['emoji']))]})
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(au),'body':r['body'],'parent_id':r['parent_id'],'reply_count':reps,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':rr,'mentions':json.loads(r['mentions'] or '[]')}
def paginate(items,limit,cursor):
 if limit is None: limit=50
 try: limit=int(limit)
 except: raise ValueError()
 if limit<1 or limit>200: raise ValueError()
 def key(x):
  return x.get('id') if isinstance(x,dict) and x.get('id') is not None else x.get('message',{}).get('id')
 if cursor:
  try:
   z=json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
   ids=z['ids']; start=int(z['offset'])
   if not isinstance(ids,list) or start<0: raise ValueError()
   lookup={key(x):x for x in items}; snap=[lookup[i] for i in ids if i in lookup]
  except Exception: raise ValueError()
 else:
  start=0; snap=list(items); ids=[key(x) for x in snap]
 out=snap[start:start+limit]
 nxt=base64.urlsafe_b64encode(json.dumps({'ids':ids,'offset':start+limit},separators=(',',':')).encode()).decode() if start+limit<len(snap) else None
 return out,nxt
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*x): pass
 def sendj(self,status,obj):
  b=json.dumps(obj,separators=(',',':')).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
 def auth(self):
  u=token_user(self.headers.get('Authorization')); 
  if not u: self.sendj(401,{'error':'unauthorized'})
  return u
 def request_path(self): return unquote(urlparse(self.path).path),parse_qs(urlparse(self.path).query)
 def do_GET(self):
  p,q=self.request_path()
  if p=='/api/health': return self.sendj(200,{'status':'ok','node_id':int(os.environ.get('NODE_ID','0'))})
  if p=='/': return self.html()
  if p=='/api/ws': return self.ws(q)
  u=self.auth()
  if not u:return
  with lock:
   c=db()
   try:
    if p=='/api/auth/me': return self.sendj(200,{'user':userobj(u)})
    if p.startswith('/api/workspaces/') and p.endswith('/invitations'):
     mm=re.match(r'/api/workspaces/([^/]+)/invitations$',p); w=row(c,'select * from workspaces where slug=?',(mm[1],)) if mm else None
     if not w:return self.sendj(404,{})
     a=member(c,w['id'],u['id'])
     if not a or a['role'] not in ('owner','admin'):return self.sendj(403,{})
     t=int(__import__('time').time())
     vals=[dict(x) for x in c.execute('select * from invitations where workspace_id=? and (expires=0 or expires>?) and uses<max_uses',(w['id'],t))]
     return self.sendj(200,{'invitations':vals})
    if p.startswith('/api/invitations/') and p.endswith('/accept'):
     code=p.split('/')[3]; inv=row(c,'select * from invitations where code=?',(code,))
     if not inv or (inv['expires'] and inv['expires']<=int(__import__('time').time())) or inv['uses']>=inv['max_uses']:return self.sendj(404,{})
     if inv['invited_user_id'] and inv['invited_user_id']!=u['id']:return self.sendj(403,{})
     c.execute('insert or ignore into members values(?,?,?)',(inv['workspace_id'],u['id'],'member'))
     if inv['invited_user_id']: c.execute('update invitations set uses=max_uses where code=?',(code,))
     else: c.execute('update invitations set uses=uses+1 where code=?',(code,))
     c.commit()
     return self.sendj(200,{'workspace':ws(row(c,'select * from workspaces where id=?',(inv['workspace_id'],)))})
    if p=='/api/workspaces':
     a=[ws(x) for x in c.execute('select w.* from workspaces w join members m on w.id=m.workspace_id where m.user_id=?',(u['id'],))]; return self.sendj(200,{'workspaces':a})
    m=re.match(r'/api/workspaces/([^/]+)(?:/(.*))?$',p)
    if m:
     w=row(c,'select * from workspaces where slug=?',(m[1],))
     if not w:return self.sendj(404,{})
     tail=m[2]
     if tail=='members':
      if not member(c,w['id'],u['id']):return self.sendj(403,{})
      return self.sendj(200,{'members':[dict(x) for x in c.execute('select user_id,role from members where workspace_id=?',(w['id'],))]})
     if tail=='groups': return self.sendj(200,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['user_ids'])} for x in c.execute('select * from groups where workspace_id=?',(w['id'],))]})
     if tail=='invitations':
      a=member(c,w['id'],u['id'])
      if not a or a['role'] not in ('owner','admin'): return self.sendj(403,{})
      return self.sendj(200,{'invitations':[dict(x) for x in c.execute('select * from invitations where workspace_id=? and (expires=0 or expires>?) and uses<max_uses',(w['id'],int(__import__('time').time())))]})
     chans=[]
     for x in c.execute('select * from channels where workspace_id=? and (is_archived=0 or ?)',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'))):
      if not x['is_private'] or row(c,'select 1 from channel_members where channel_id=? and user_id=?',(x['id'],u['id'])): chans.append(channel(x))
     rs=self.readstate(c,w['id'],u['id']); return self.sendj(200,{'workspace':ws(w),'channels':chans,'read_state':rs})
    m=re.match(r'/api/channels/(\d+)(?:/(.*))?$',p)
    if m:
     ch=row(c,'select * from channels where id=?',(m[1],))
     if not ch:return self.sendj(404,{})
     tail=m[2] or ''
     if tail=='members': return self.sendj(200,{'members':[userobj(row(c,'select * from users where id=?',(x['user_id'],))) for x in c.execute('select * from channel_members where channel_id=?',(ch['id'],))]})
     if tail=='read': return self.sendj(200,{'read_state':self.readstate(c,ch['workspace_id'],u['id'],ch['id'])})
     if tail=='pins':
      if ch['is_private'] and not row(c,'select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],u['id'])):return self.sendj(403,{})
      arr=[{'message':msgobj(c,row(c,'select * from messages where id=?',(x['message_id'],))),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in c.execute('select * from pins where message_id in (select id from messages where channel_id=?) order by pinned_at desc',(ch['id'],))]
      a,n=paginate(arr,q.get('limit',[None])[0],q.get('cursor',[None])[0]); return self.sendj(200,{'pins':a,'next_cursor':n})
     if tail=='messages':
      arr=[msgobj(c,x) for x in c.execute('select * from messages where channel_id=? and parent_id is null and deleted=0 order by id desc',(ch['id'],))]; a,n=paginate(arr,q.get('limit',[None])[0],q.get('cursor',[None])[0]); return self.sendj(200,{'messages':a,'next_cursor':n})
    m=re.match(r'/api/messages/(\d+)/replies',p)
    if m:
     arr=[msgobj(c,x) for x in c.execute('select * from messages where parent_id=? and deleted=0 order by id',(m[1],))]; a,n=paginate(arr,q.get('limit',[None])[0],q.get('cursor',[None])[0]); return self.sendj(200,{'replies':a,'next_cursor':n})
    if p=='/api/search':
     text=q.get('q',[''])[0]; wid=q.get('workspace_id',[None])[0]
     arr=[msgobj(c,x) for x in c.execute("select m.* from messages m join channels ch on ch.id=m.channel_id where m.deleted=0 and m.body like ? and (? is null or ch.workspace_id=?) order by m.id desc",('%'+text+'%',wid,wid)) if accessible(c,row(c,'select * from channels where id=?',(x['channel_id'],)),u)]
     a,n=paginate(arr,q.get('limit',[None])[0],q.get('cursor',[None])[0]); return self.sendj(200,{'results':a,'next_cursor':n})
    return self.sendj(404,{})
   except ValueError:return self.sendj(400,{'error':'bad pagination'})
   finally:c.close()
 def readstate(self,c,wid,uid,cid=None):
  if cid is not None:
   chans=[cid]
  else:
   chans=[x['channel_id'] for x in c.execute('select distinct cm.channel_id from channel_members cm join channels ch on ch.id=cm.channel_id where cm.user_id=? and ch.workspace_id=?',(uid,wid))]
  last=0; unread=mentions=0
  for x in chans:
   r=row(c,'select last_read from reads where channel_id=? and user_id=?',(x,uid)); z=int(r['last_read']) if r else 0; last=max(last,z)
   for e in c.execute("select data from events where channel_id=? and event_id>? and kind like 'message.%'",(x,z)):
    try:
     mid=json.loads(e['data']).get('message',{}).get('id')
     mm=row(c,'select author_id,mentions from messages where id=? and deleted=0',(mid,))
     if mm and mm['author_id']!=uid:
      unread+=1
      mentions += uid in json.loads(mm['mentions'] or '[]')
    except Exception: pass
  return {'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mentions}
 def do_POST(self):
  p,q=self.request_path()
  if p=='/api/auth/register' or p=='/api/auth/login':
   try:b=self.body()
   except:return self.sendj(400,{})
   with lock:
    c=db()
    if p.endswith('register'):
     if not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or len(b.get('password',''))<8:return self.sendj(400,{})
     if row(c,'select 1 from users where username=?',(b['username'],)):return self.sendj(409,{})
     c.execute('insert into users(username,password,display_name) values(?,?,?)',(b['username'],b['password'],b.get('display_name') or b['username'])); uid=c.execute('select last_insert_rowid()').fetchone()[0]
    else:
     x=row(c,'select * from users where username=?',(b.get('username'),))
     if not x or x['password']!=b.get('password'):return self.sendj(401,{})
     uid=x['id']
    t=secrets.token_urlsafe(24); c.execute('insert into tokens values(?,?)',(t,uid)); c.commit(); x=row(c,'select * from users where id=?',(uid,)); return self.sendj(201 if p.endswith('register') else 200,{'user':userobj(x),'token':t})
  u=self.auth()
  if not u:return
  with lock:
   c=db()
   try:
    b=self.body(); m=re.match(r'/api/workspaces/([^/]+)(?:/(.*))?$',p)
    if m and m[2]=='invitations':
     w=row(c,'select * from workspaces where slug=?',(m[1],))
     if not w:return self.sendj(404,{})
     a=member(c,w['id'],u['id'])
     if not a or a['role'] not in ('owner','admin'):return self.sendj(403,{})
     try: mx=int(b.get('max_uses',1)); ex=int(b.get('expires_in_seconds',0))
     except: return self.sendj(400,{})
     if mx<1:return self.sendj(400,{})
     code=secrets.token_urlsafe(16); exp=int(__import__('time').time())+ex if ex else 0
     target=b.get('invited_user_id')
     if target is None and b.get('invited_username'):
      z=row(c,'select id from users where username=?',(b['invited_username'],)); target=z['id'] if z else None
     c.execute('insert into invitations values(?,?,?,?,?,0)',(code,w['id'],target,exp,mx));c.commit()
     return self.sendj(201,{'invitation':dict(row(c,'select * from invitations where code=?',(code,)))})
    if p.startswith('/api/invitations/') and p.endswith('/accept'):
     code=p.split('/')[3]; inv=row(c,'select * from invitations where code=?',(code,))
     if not inv or (inv['expires'] and inv['expires']<=int(__import__('time').time())) or inv['uses']>=inv['max_uses']:return self.sendj(404,{})
     if inv['invited_user_id'] and inv['invited_user_id']!=u['id']:return self.sendj(403,{})
     c.execute('insert or ignore into members values(?,?,?)',(inv['workspace_id'],u['id'],'member'))
     if inv['invited_user_id']: c.execute('update invitations set uses=max_uses where code=?',(code,))
     else: c.execute('update invitations set uses=uses+1 where code=?',(code,))
     c.commit()
     return self.sendj(200,{'workspace':ws(row(c,'select * from workspaces where id=?',(inv['workspace_id'],)))})
    if p=='/api/workspaces':
     if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return self.sendj(400,{})
     if row(c,'select 1 from workspaces where slug=?',(b['slug'],)):return self.sendj(409,{})
     c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(b['slug'],b.get('name',''),u['id'])); wid=c.execute('select last_insert_rowid()').fetchone()[0]; c.execute('insert into members values(?,?,?)',(wid,u['id'],'owner')); c.execute('insert into channels(workspace_id,name) values(?,?)',(wid,'general')); cid=c.execute('select last_insert_rowid()').fetchone()[0]; c.execute('insert into channel_members values(?,?)',(cid,u['id'])); c.commit(); return self.sendj(201,{'workspace':ws(row(c,'select * from workspaces where id=?',(wid,))),'general_channel':channel(row(c,'select * from channels where id=?',(cid,)))})
    if m and m[2]=='transfer_ownership':
     w=row(c,'select * from workspaces where slug=?',(m[1],))
     if not w:return self.sendj(404,{})
     actor=member(c,w['id'],u['id']); target=member(c,w['id'],b.get('new_owner_id'))
     if not actor or actor['role']!='owner':return self.sendj(403,{})
     if not target or target['role']!='admin':return self.sendj(400,{})
     c.execute('update members set role=? where workspace_id=? and user_id=?',('admin',w['id'],u['id']))
     c.execute('update members set role=? where workspace_id=? and user_id=?',('owner',w['id'],b.get('new_owner_id')))
     c.execute('update workspaces set owner_id=? where id=?',(b.get('new_owner_id'),w['id'])); c.commit()
     return self.sendj(200,{'workspace':ws(row(c,'select * from workspaces where id=?',(w['id'],)))})
    if m and m[2] and m[2]=='channels':
     w=row(c,'select * from workspaces where slug=?',(m[1],)); mem=member(c,w['id'],u['id']) if w else None
     if not w:return self.sendj(404,{})
     if not mem or mem['role']=='guest' or (b.get('is_private',False) and mem['role'] not in ('owner','admin')):return self.sendj(403,{})
     if not re.fullmatch(r'[a-z0-9-]{1,32}',b.get('name','')) or len(b.get('topic',''))>250:return self.sendj(400,{})
     if row(c,'select 1 from channels where workspace_id=? and name=?',(w['id'],b['name'])):return self.sendj(409,{})
     c.execute('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic',''))); cid=c.execute('select last_insert_rowid()').fetchone()[0]; c.execute('insert into channel_members values(?,?)',(cid,u['id'])); c.commit(); return self.sendj(201,{'channel':channel(row(c,'select * from channels where id=?',(cid,)))})
    m=re.match(r'/api/channels/(\d+)/(join|leave|archive|unarchive|read)$',p)
    if m:
     ch=row(c,'select * from channels where id=?',(m[1],)); wid=ch['workspace_id'] if ch else None
     if not ch:return self.sendj(404,{})
     mem=member(c,wid,u['id']); own=row(c,'select owner_id from workspaces where id=?',(wid,))['owner_id']
     if m[2]=='read':
      if not mem or (ch['is_private'] and not row(c,'select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],u['id']))): return self.sendj(403,{})
      old=row(c,'select last_read from reads where channel_id=? and user_id=?',(ch['id'],u['id']))
      try: requested=int(b.get('last_read_event_id',0))
      except: return self.sendj(400,{})
      value=max(requested, int(old['last_read']) if old else 0)
      c.execute('insert or replace into reads(channel_id,user_id,last_read) values(?,?,?)',(ch['id'],u['id'],value)); c.commit()
      return self.sendj(200,{'read_state':self.readstate(c,wid,u['id'],ch['id'])})
     if m[2]=='join':
      if ch['is_private'] and not mem:return self.sendj(403,{})
      if mem and mem['role']=='guest':return self.sendj(403,{})
      if not mem:
       wmode=row(c,'select join_mode from workspaces where id=?',(wid,))['join_mode']
       if wmode=='invite_only': return self.sendj(403,{})
       c.execute('insert into members values(?,?,?)',(wid,u['id'],'member'))
      before=row(c,'select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],u['id']))
      c.execute('insert or ignore into channel_members values(?,?)',(ch['id'],u['id']))
      if not before: emit(c,ch['id'],'member.joined',{'user_id':u['id']})
      c.commit(); return self.sendj(200,{'channel':channel(ch)})
     if m[2]=='leave':
      before=row(c,'select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],u['id']))
      c.execute('delete from channel_members where channel_id=? and user_id=?',(ch['id'],u['id']))
      if before: emit(c,ch['id'],'member.left',{'user_id':u['id']})
      c.commit(); return self.sendj(200,{'channel':channel(ch)})
     if u['id']!=own:return self.sendj(403,{})
     archived=int(m[2]=='archive')
     if archived and ch['is_archived']: return self.sendj(200,{'channel':channel(ch)})
     if not archived and not ch['is_archived']: return self.sendj(200,{'channel':channel(ch)})
     c.execute('update channels set is_archived=? where id=?',(archived,ch['id']))
     emit(c,ch['id'],'channel.archived' if archived else 'channel.unarchived',{'channel':channel(row(c,'select * from channels where id=?',(ch['id'],)))})
     c.commit(); return self.sendj(200,{'channel':channel(row(c,'select * from channels where id=?',(ch['id'],)))})
    m=re.match(r'/api/channels/(\d+)/messages$',p)
    if m:
     ch=row(c,'select * from channels where id=?',(m[1],))
     if not ch:return self.sendj(404,{})
     if ch['is_archived']:return self.sendj(423,{})
     if not accessible(c,ch,u['id'],True):return self.sendj(403,{})
     body=b.get('body'); parent=b.get('parent_id')
     if not isinstance(body,str) or not body.strip():return self.sendj(400,{})
     if body.startswith('/') and not body.startswith('//') and body.split()[0] not in ('/me','/shrug'):return self.sendj(400,{})
     if body.startswith('/shrug'):body=body[6:].strip()+' ¯\\_(ツ)_/¯'
     if parent:
      pr=row(c,'select * from messages where id=? and deleted=0',(parent,))
      if not pr:return self.sendj(404,{})
      if pr['channel_id']!=ch['id']:return self.sendj(400,{})
     memids=[x['user_id'] for x in c.execute('select user_id from members where workspace_id=?',(ch['workspace_id'],))]
     mentions=[]
     if '@channel' in body: mentions=[x['user_id'] for x in c.execute('select user_id from channel_members where channel_id=?',(ch['id'],)) if x['user_id']!=u['id']]
     for z in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body):
      x=row(c,'select id from users where username=?',(z,))
      if x and x['id'] in memids and x['id']!=u['id'] and x['id'] not in mentions:mentions.append(x['id'])
     c.execute('insert into messages(channel_id,author_id,body,parent_id,created_at,mentions) values(?,?,?,?,?,?)',(ch['id'],u['id'],body,parent,now(),json.dumps(mentions))); mid=c.execute('select last_insert_rowid()').fetchone()[0]; mo=msgobj(c,row(c,'select * from messages where id=?',(mid,))); kind='message.reply' if parent else 'message.created'; emit(c,ch['id'],kind,{'message':mo}); c.commit(); return self.sendj(201,{'message':mo})
    m=re.match(r'/api/messages/(\d+)/pin$',p)
    if m:
     mm=row(c,'select m.*,ch.is_archived from messages m join channels ch on ch.id=m.channel_id where m.id=?',(m[1],))
     if not mm:return self.sendj(404,{})
     if mm['is_archived']:return self.sendj(423,{})
     c.execute('insert or ignore into pins values(?,?,?)',(mm['id'],u['id'],now())); c.commit(); x=row(c,'select * from pins where message_id=?',(mm['id'],)); return self.sendj(200,{'pin':dict(x)})
    m=re.match(r'/api/channels/(\d+)/read$',p)
    if m:
     ch=row(c,'select * from channels where id=?',(m[1],)); old=row(c,'select last_read from reads where channel_id=? and user_id=?',(m[1],u['id'])); x=int(b.get('last_read_event_id',0)); x=max(x,old['last_read'] if old else 0); c.execute('insert or replace into reads values(?,?,?)',(m[1],u['id'],x)); c.commit(); return self.sendj(200,{'read_state':self.readstate(c,ch['workspace_id'],u['id'],ch['id'])})
    return self.sendj(404,{})
   finally:c.close()
 def do_PATCH(self):
  p,q=self.request_path(); u=self.auth()
  if not u:return
  with lock:
   c=db(); b=self.body()
   m=re.match(r'/api/workspaces/([^/]+)$',p)
   if m:
    w=row(c,'select * from workspaces where slug=?',(m[1],)); a=member(c,w['id'],u['id']) if w else None
    if not w:return self.sendj(404,{})
    if not a or a['role'] not in ('owner','admin'):return self.sendj(403,{})
    if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return self.sendj(400,{})
    c.execute('update workspaces set name=coalesce(?,name),join_mode=coalesce(?,join_mode) where id=?',(b.get('name'),b.get('join_mode'),w['id']));c.commit()
    return self.sendj(200,{'workspace':ws(row(c,'select * from workspaces where id=?',(w['id'],)))})
   m=re.match(r'/api/channels/(\d+)$',p)
   if m:
    ch=row(c,'select * from channels where id=?',(m[1],))
    if not ch:return self.sendj(404,{})
    w=row(c,'select * from workspaces where id=?',(ch['workspace_id'],))
    cm=row(c,'select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],u['id']))
    if not cm and w['owner_id']!=u['id']:return self.sendj(403,{})
    if ch['is_archived']:return self.sendj(423,{})
    if len(b.get('topic',''))>250:return self.sendj(400,{})
    c.execute('update channels set topic=? where id=?',(b.get('topic',''),ch['id']))
    emit(c,ch['id'],'channel.updated',{'channel':channel(row(c,'select * from channels where id=?',(ch['id'],)))})
    c.commit(); return self.sendj(200,{'channel':channel(row(c,'select * from channels where id=?',(ch['id'],)))})
   m=re.match(r'/api/workspaces/([^/]+)/members/(\d+)$',p)
   if m:
    w=row(c,'select * from workspaces where slug=?',(m[1],)); actor=member(c,w['id'],u['id']); target=member(c,w['id'],m[2]); role=b.get('role')
    if not target:return self.sendj(404,{})
    if role not in ('admin','member','guest'):return self.sendj(400,{})
    if int(m[2])==u['id'] or target['role']=='owner':return self.sendj(403,{})
    if not actor or actor['role'] not in ('owner','admin'):return self.sendj(403,{})
    if actor['role']=='admin' and target['role'] in ('owner','admin'):return self.sendj(403,{})
    c.execute('update members set role=? where workspace_id=? and user_id=?',(role,w['id'],m[2]));c.commit();return self.sendj(200,{'member':{'user_id':int(m[2]),'role':role}})
   return self.sendj(404,{})
 def do_DELETE(self):
  p,q=self.request_path();u=self.auth()
  if not u:return
  with lock:
   c=db();m=re.match(r'/api/workspaces/([^/]+)/invitations/([^/]+)$',p)
   if m:
    w=row(c,'select * from workspaces where slug=?',(m[1],)); a=member(c,w['id'],u['id']) if w else None
    if not w:return self.sendj(404,{})
    if not a or a['role'] not in ('owner','admin'):return self.sendj(403,{})
    c.execute('delete from invitations where code=? and workspace_id=?',(m[2],w['id']));c.commit();return self.sendj(200,{'deleted':True})
   m=re.match(r'/api/messages/(\d+)/pin$',p)
   if m:c.execute('delete from pins where message_id=?',(m[1],));c.commit();return self.sendj(200,{'unpinned':True})
   return self.sendj(404,{})
 def html(self):
  b=b'''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>body{font:15px Arial;margin:0;background:#f8f8f8}button{border:0;padding:9px 14px;color:#fff;background:#007a5a}#app{padding:30px}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}</style></head><body><div id=app><h1>Huddle</h1><div data-testid=auth-form id=auth-modal><input name=username placeholder=username><input name=password type=password placeholder=password><button data-testid=auth-submit data-button-role=primary>Login</button><button data-testid=auth-toggle>Sign up</button></div><div data-testid=channel-list></div><div data-testid=message-list></div></div></body></html>'''
  self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def ws(self,q):
  u=token_user('Bearer '+q.get('token',[''])[0])
  if not u:return self.close()
  key=self.headers.get('Sec-WebSocket-Key')
  if not key:return self.close()
  import hashlib
  accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  def send(o):
   b=json.dumps(o,separators=(',',':')).encode(); n=len(b)
   if n<126: hdr=bytes([0x81,n])
   elif n<65536: hdr=bytes([0x81,126])+struct.pack('!H',n)
   else: hdr=bytes([0x81,127])+struct.pack('!Q',n)
   self.wfile.write(hdr+b); self.wfile.flush()
  try:
   while True:
    h=self.rfile.read(2)
    if not h:break
    n=h[1]&127
    if n==126: n=struct.unpack('!H',self.rfile.read(2))[0]
    elif n==127: n=struct.unpack('!Q',self.rfile.read(8))[0]
    mask=self.rfile.read(4); data=self.rfile.read(n); data=bytes(x^mask[i%4] for i,x in enumerate(data))
    opcode=h[0]&15
    if opcode==8:
     try: self.wfile.write(b'\x88\x00'); self.wfile.flush()
     except Exception: pass
     for ent in list(clients):
      if ent.get('send') is send:
       ent['active']=False
       try: clients.remove(ent)
       except ValueError: pass
     try: self.connection.shutdown(socket.SHUT_RDWR)
     except Exception: pass
     break
    if opcode==9:
     try: self.wfile.write(b'\x8a'+bytes([len(data)])+data); self.wfile.flush()
     except Exception: pass
     continue
    z=json.loads(data)
    cid=int(z.get('channel_id')); 
    if z['type']=='subscribe':
     with lock:c=db(); head=row(c,'select coalesce(max(event_id),0) n from events where channel_id=?',(cid,))['n'];c.close()
     send({'type':'subscribed','channel_id':cid,'head_event_id':head})
     ent={'channel':cid,'send':send,'last':head,'active':True}; clients.append(ent)
     def watch(e=ent):
      cc=None
      try:
       cc=db()
       while e.get('active'):
        rows=list(cc.execute('select channel_id,event_id,kind,data from events where channel_id=? and event_id>? order by event_id',(e['channel'],e['last'])))
        for ev in rows:
         d=json.loads(ev['data']); d.update(type=ev['kind'],event_id=ev['event_id'],channel_id=ev['channel_id'])
         e['send'](d); e['last']=ev['event_id']
        __import__('time').sleep(0.1)
      except Exception:
       e['active']=False
      finally:
       if cc: cc.close()
     threading.Thread(target=watch,daemon=True).start()
    elif z['type']=='resume':
     with lock:
      c=db()
      for e in c.execute('select channel_id,event_id,kind,data from events where channel_id=? and event_id>? order by event_id',(cid,int(z.get('since_event_id',0)))):
       d=json.loads(e['data']); d.update(type=e['kind'],event_id=e['event_id'],channel_id=cid);send(d)
      head=row(c,'select coalesce(max(event_id),0) n from events where channel_id=?',(cid,))['n'];c.close()
     send({'type':'resumed','channel_id':cid,'head_event_id':head})
  except:pass
  finally:
   for e in list(clients):
    if e.get('send') is send: e['active']=False
 def close(self):
  self.send_response(401);self.end_headers()
def run(port,node):
 os.environ['NODE_ID']=str(node)
 class Server(ThreadingHTTPServer):
  daemon_threads=True
  allow_reuse_address=True
 Server(('127.0.0.1',port),H).serve_forever()
if __name__=='__main__':run(int(sys.argv[1]),int(sys.argv[2]))
