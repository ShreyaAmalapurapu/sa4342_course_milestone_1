#!/usr/bin/env python3
"""Checks for component B (channels and message history). Prints PASS/FAIL per rule."""
import asyncio
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import websockets
except ImportError:  # the WebSocket rules then fail with a clear message
    websockets = None

N0, N1, N2 = "http://127.0.0.1:8000", "http://127.0.0.1:8001", "http://127.0.0.1:8002"
PW = "correct-horse-1"
RUN = "%x%x" % (int(time.time()), random.randrange(4096))
T0 = time.time()
# AIDEV-NOTE: the agent's commands are cut off after 30s; stop starting new WebSocket rules at 22s.
BUDGET = 18

passed = failed = 0
done = set()
# AIDEV-NOTE: CHECK_RULES (for example "1-14,66") limits which rules are printed and counted. The
# staged layout uses it to run one stage; every other rule still runs silently as setup.
RULES = os.environ.get("CHECK_RULES", "all")


def want(rule):
    if RULES == "all" or rule == 0:
        return True
    for tok in RULES.split(","):
        lo, _, hi = tok.partition("-")
        if int(lo) <= rule <= int(hi or lo):
            return True
    return False


# AIDEV-NOTE: the agent's commands are cut off after 30s with no output kept for the summary. This
# watchdog prints every rule that was not reached as FAIL, then RESULT, and exits at 22s.
TOTAL_RULES = 66


def _time_is_up():
    missing = [r for r in range(1, TOTAL_RULES + 1) if want(r) and r not in done]
    for r in missing:
        print("FAIL %s not reached: the check's 22 second time budget ran out (earlier rules were slow)" % r)
    print("RESULT: %d passed, %d failed" % (passed, failed + len(missing)), flush=True)
    os._exit(1)


def ok(rule, msg):
    global passed
    done.add(rule)
    if want(rule):
        passed += 1
        print("PASS %s %s" % (rule, msg), flush=True)


def bad(rule, msg):
    global failed
    done.add(rule)
    if want(rule):
        failed += 1
        print("FAIL %s %s" % (rule, msg), flush=True)


def check(rule, msg, cond, detail=""):
    if cond:
        ok(rule, msg)
    else:
        bad(rule, "%s: %s" % (msg, detail))


# AIDEV-NOTE: every request shares one deadline so a hung server cannot push the whole check past
# the agent's 30s command limit; after DEADLINE the remaining rules fail fast and RESULT still prints.
DEADLINE = 21


def call(method, url, body=None, token=None, timeout=5):
    """Returns (status, parsed-json-or-None). Status 0 means no answer."""
    left = DEADLINE - (time.time() - T0)
    if left <= 0:
        return 0, None
    timeout = min(timeout, max(left, 0.5))
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except Exception:
        return 0, None
    try:
        return status, (json.loads(raw) if raw else None)
    except Exception:
        return status, None


def dig(d, path):
    for k in path.split("."):
        if isinstance(d, dict) and k in d:
            d = d[k]
        else:
            return None
    return d


def healthy():
    for u in (N0, N1, N2):
        if call("GET", u + "/api/health", timeout=2)[0] != 200:
            return False
    return True


def boot():
    if not healthy():
        try:
            log = open("/tmp/start.log", "w")
            subprocess.Popen(["/app/start.sh"], cwd="/app", stdout=log, stderr=log, start_new_session=True)
        except OSError as e:
            print("FAIL 0 could not start /app/start.sh: %s" % e)
        for _ in range(20):
            if healthy():
                break
            time.sleep(1)
    if not healthy():
        print("FAIL 0 cluster is not answering /api/health on 8000, 8001, 8002 (see /tmp/start.log)")
        print("RESULT: 0 passed, 1 failed")
        sys.exit(1)


# ---- helpers ----
def reg(label):
    name = "%s%s" % (label, RUN)
    st, d = call("POST", N0 + "/api/auth/register", {"username": name, "password": PW})
    return {"name": name, "token": dig(d, "token"), "id": dig(d, "user.id")}


def mkws(slug, u):
    return call("POST", N0 + "/api/workspaces", {"slug": slug, "name": "Chan " + slug}, u["token"])


def mkch(slug, name, u, private=False, topic=None):
    body = {"name": name, "is_private": private}
    if topic is not None:
        body["topic"] = topic
    return call("POST", "%s/api/workspaces/%s/channels" % (N0, slug), body, u["token"])


def join(cid, u, node=N0):
    return call("POST", "%s/api/channels/%s/join" % (node, cid), {}, u["token"])[0]


def post(cid, u, body, parent=None, node=N0):
    payload = {"body": body}
    if parent is not None:
        payload["parent_id"] = parent
    return call("POST", "%s/api/channels/%s/messages" % (node, cid), payload, u["token"])


def messages(cid, u, qs="", node=N0):
    return call("GET", "%s/api/channels/%s/messages%s" % (node, cid, qs), None, u["token"])


def bodies(items):
    return [(m.get("body") if "body" in m else (m.get("message") or {}).get("body")) for m in (items or [])]


def q(cursor):
    return urllib.parse.quote(str(cursor), safe="")


def ids(items):
    return sorted(str(x) for x in items or [])


def newchan(slug, name, u, **kw):
    st, d = mkch(slug, name, u, **kw)
    return dig(d, "channel.id")


def read_state(cid, u, node=N0):
    return call("GET", "%s/api/channels/%s/read" % (node, cid), None, u["token"])


# ---- WebSocket helpers ----
async def ws_open(port, token):
    return await websockets.connect("ws://127.0.0.1:%d/api/ws?token=%s" % (port, token), open_timeout=3)


async def wait_for(ws, pred, timeout, seen=None):
    end = time.time() + timeout
    while True:
        left = end - time.time()
        if left <= 0:
            return None
        try:
            f = json.loads(await asyncio.wait_for(ws.recv(), left))
        except Exception:
            return None
        if seen is not None:
            seen.append(f)
        if pred(f):
            return f


async def subscribe(ws, cid, timeout=3):
    await ws.send(json.dumps({"type": "subscribe", "channel_id": cid}))
    return await wait_for(ws, lambda f: f.get("type") == "subscribed" and str(f.get("channel_id")) == str(cid), timeout)


async def collect(ws, cid, want, timeout, kinds=None):
    """Collect durable frames of this channel until `want` are seen or the timeout ends."""
    got = []
    end = time.time() + timeout
    while len(got) < want and end > time.time():
        f = await wait_for(ws, lambda f: str(f.get("channel_id")) == str(cid) and "event_id" in f
                           and (kinds is None or f.get("type") in kinds), end - time.time())
        if f is None:
            break
        got.append(f)
    return got


def head_of(cid, token):
    async def go():
        ws = await ws_open(8000, token)
        try:
            f = await subscribe(ws, cid)
            return f.get("head_event_id") if f else None
        finally:
            await ws.close()
    try:
        return asyncio.run(asyncio.wait_for(go(), 6))
    except Exception:
        return None


async def guarded(rules, fn, limit=9):
    # AIDEV-NOTE: the WebSocket rules set up nothing that later rules use, so a staged slice that does
    # not include them skips them entirely instead of spending its time budget on them.
    if not any(want(r) for r in rules):
        return
    if websockets is None:
        for r in rules:
            bad(r, "the python `websockets` package is not available to the check")
        return
    if time.time() - T0 > BUDGET:
        for r in rules:
            bad(r, "skipped: the check's time budget is used up (earlier rules were slow)")
        return
    try:
        await asyncio.wait_for(fn(), max(1, min(limit, DEADLINE + 2 - (time.time() - T0))))
    except Exception as e:
        for r in rules:
            if r not in done:
                bad(r, "error or timeout (%s %s)" % (type(e).__name__, e))
    for r in rules:
        if r not in done:
            bad(r, "no result")


def main():
    wd = threading.Timer(22, _time_is_up)
    wd.daemon = True
    wd.start()
    boot()
    o, m, x, y = reg("o"), reg("m"), reg("x"), reg("y")
    if not o["token"]:
        print("FAIL 0 setup: could not register users (component A must work first)")
        print("RESULT: 0 passed, 1 failed")
        sys.exit(1)
    S = "c1-" + RUN
    st, d = mkws(S, o)
    WS1 = dig(d, "workspace.id")
    GEN = dig(d, "general_channel.id")
    for u in (m, x):
        join(GEN, u)  # open workspace: becomes a member

    # ===== A. Channel management =====
    st, d = mkch(S, "alpha", o)
    ch = dig(d, "channel") or {}
    keys = ["id", "workspace_id", "name", "is_private", "is_dm", "topic", "is_archived"]
    ALPHA = ch.get("id")
    check(1, "create returns 201 and a full channel with defaults",
          st == 201 and all(k in ch for k in keys) and ch.get("topic") == "" and ch.get("is_private") is False
          and ch.get("is_dm") is False and ch.get("is_archived") is False,
          "status %s, channel %s" % (st, json.dumps(ch)[:200]))
    s1, d1 = mkch(S, "topic250", o, topic="t" * 250)
    s2, _ = mkch(S, "topic251", o, topic="t" * 251)
    check(2, "topic of 250 chars is accepted, 251 is rejected", s1 == 201 and len(dig(d1, "channel.topic") or "") == 250 and s2 == 400,
          "250 -> %s, 251 -> %s" % (s1, s2))
    got = [mkch(S, n, o)[0] for n in ("Upper", "has space", "a" * 33)]
    check(3, "invalid channel names return 400", got == [400, 400, 400], "got %s" % got)
    st, d = mkws("c1b-" + RUN, o)
    s1 = mkch(S, "alpha", o)[0]
    s2 = mkch("c1b-" + RUN, "alpha", o)[0]
    check(4, "duplicate name 409, same name in another workspace 201", s1 == 409 and s2 == 201, "got %s and %s" % (s1, s2))
    st, d = call("GET", "%s/api/workspaces/%s" % (N0, S), None, o["token"])
    names = [c.get("name") for c in (d or {}).get("channels", [])]
    check(5, "workspace detail has workspace, channels, read_state and general",
          st == 200 and d and "workspace" in d and "read_state" in d and "general" in names, "status %s, channels %s" % (st, names))
    SEC = newchan(S, "secret", o, private=True)
    names_x = [c.get("name") for c in (call("GET", "%s/api/workspaces/%s" % (N0, S), None, x["token"])[1] or {}).get("channels", [])]
    names_o = [c.get("name") for c in (call("GET", "%s/api/workspaces/%s" % (N0, S), None, o["token"])[1] or {}).get("channels", [])]
    check(6, "private channel is hidden from non-members and shown to members", "secret" not in names_x and "secret" in names_o,
          "x sees %s, o sees %s" % (names_x, names_o))
    OLD = newchan(S, "old", o)
    call("POST", "%s/api/channels/%s/archive" % (N0, OLD), {}, o["token"])
    def listed(suffix):
        return "old" in [c.get("name") for c in (call("GET", "%s/api/workspaces/%s%s" % (N0, S, suffix), None, o["token"])[1] or {}).get("channels", [])]
    a, b, c = listed(""), listed("?include_archived=1"), listed("?include_archived=true")
    check(7, "archived channels are hidden by default and shown with include_archived", (a, b, c) == (False, True, True),
          "default %s, =1 %s, =true %s" % (a, b, c))
    join(ALPHA, m)
    MCH = newchan(S, "mchan", m)
    s1, d1 = call("PATCH", "%s/api/channels/%s" % (N0, ALPHA), {"topic": "from member"}, m["token"])
    s2, d2 = call("PATCH", "%s/api/channels/%s" % (N0, MCH), {"topic": "from owner"}, o["token"])
    s3, _ = call("PATCH", "%s/api/channels/%s" % (N0, ALPHA), {"topic": "from x"}, x["token"])
    s4, _ = call("PATCH", "%s/api/channels/%s" % (N0, ALPHA), {"topic": "z" * 251}, o["token"])
    check(8, "topic update: member 200, owner 200, other member 403, too long 400",
          (s1, dig(d1, "channel.topic"), s2, dig(d2, "channel.topic"), s3, s4) == (200, "from member", 200, "from owner", 403, 400),
          "got %s/%s/%s/%s/%s/%s" % (s1, dig(d1, "channel.topic"), s2, dig(d2, "channel.topic"), s3, s4))
    s1, d1 = call("POST", "%s/api/channels/%s/leave" % (N0, ALPHA), {}, m["token"])
    s2, _ = call("POST", "%s/api/channels/%s/leave" % (N1, ALPHA), {}, m["token"])
    check(9, "leave returns 200 with the channel, twice", s1 == 200 and "channel" in (d1 or {}) and s2 == 200, "got %s and %s" % (s1, s2))
    st, d = call("GET", "%s/api/channels/%s/members" % (N2, ALPHA), None, o["token"])
    mem = (d or {}).get("members", [])
    check(10, "a user who left is no longer listed", st == 200 and m["name"] not in [u.get("username") for u in mem],
          "status %s, members %s" % (st, [u.get("username") for u in mem]))
    ufields = ["id", "username", "display_name", "timezone", "avatar_url", "status_text", "status_emoji"]
    check(11, "members returns full user objects", st == 200 and len(mem) >= 1 and all(all(k in u for k in ufields) for u in mem),
          "status %s, first member %s" % (st, json.dumps(mem[:1])[:200]))
    ARCH = newchan(S, "arch", o)
    a1 = call("POST", "%s/api/channels/%s/archive" % (N0, ARCH), {}, x["token"])[0]
    s, d = call("POST", "%s/api/channels/%s/archive" % (N0, ARCH), {}, o["token"])
    a3 = call("POST", "%s/api/channels/%s/unarchive" % (N0, ARCH), {}, x["token"])[0]
    check(12, "archive/unarchive are owner-only", a1 == 403 and s == 200 and dig(d, "channel.is_archived") is True and a3 == 403,
          "x archive %s, owner archive %s/%s, x unarchive %s" % (a1, s, dig(d, "channel.is_archived"), a3))
    p1 = post(ARCH, o, "while archived")[0]
    u1 = call("POST", "%s/api/channels/%s/unarchive" % (N0, ARCH), {}, o["token"])[0]
    p2 = post(ARCH, o, "after unarchive")[0]
    check(13, "archived channel rejects posts with 423, unarchive restores", (p1, u1, p2) == (423, 200, 201), "got %s/%s/%s" % (p1, u1, p2))
    BAD = 99999999
    r = [call("GET", "%s/api/channels/%s/members" % (N0, BAD), None, o["token"])[0],
         call("POST", "%s/api/channels/%s/archive" % (N0, BAD), {}, o["token"])[0],
         call("PATCH", "%s/api/channels/%s" % (N0, BAD), {"topic": "t"}, o["token"])[0],
         call("GET", "%s/api/channels/%s/members" % (N0, ALPHA))[0]]
    check(14, "unknown channel 404 and no token 401", r == [404, 404, 404, 401], "got %s" % r)

    # ===== B. Messages and paging =====
    st, d = post(ALPHA, o, "hello")
    msg = dig(d, "message") or {}
    mk = ["id", "channel_id", "author_id", "author", "body", "parent_id", "reply_count", "created_at", "edited_at", "files", "reactions", "mentions"]
    ts = re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|\+00:00)$", str(msg.get("created_at")))
    check(15, "post returns 201 with a full message", st == 201 and all(k in msg for k in mk) and msg.get("body") == "hello"
          and msg.get("parent_id") is None and msg.get("reply_count") == 0 and msg.get("edited_at") is None
          and msg.get("files") == [] and msg.get("reactions") == [] and msg.get("mentions") == []
          and str(msg.get("author_id")) == str(o["id"]) and ts,
          "status %s, message %s" % (st, json.dumps(msg)[:250]))
    check(16, "empty body returns 400", post(ALPHA, o, "")[0] == 400, "")
    THR = newchan(S, "thr", o)
    st, d = post(THR, o, "parent")
    PARENT = dig(d, "message.id")
    s1, _ = post(THR, o, "r1", parent=PARENT)
    lst = messages(THR, o)[1] or {}
    rc = [mm.get("reply_count") for mm in lst.get("messages", []) if mm.get("id") == PARENT]
    check(17, "reply returns 201 and the parent's reply_count is 1", s1 == 201 and rc == [1], "reply status %s, reply_count %s" % (s1, rc))
    PG = newchan(S, "pg", o)
    for i in range(1, 6):
        post(PG, o, "m%d" % i)
    st, d = messages(PG, o)
    check(18, "list is newest first", st == 200 and bodies((d or {}).get("messages")) == ["m5", "m4", "m3", "m2", "m1"],
          "status %s, bodies %s" % (st, bodies((d or {}).get("messages"))))
    st1, p1 = messages(PG, o, "?limit=2")
    c1 = (p1 or {}).get("next_cursor")
    p2 = messages(PG, o, "?limit=2&cursor=" + q(c1))[1] if c1 else {}
    p3 = messages(PG, o, "?limit=2&cursor=" + q((p2 or {}).get("next_cursor")))[1] if (p2 or {}).get("next_cursor") else {}
    seq = [bodies((p or {}).get("messages")) for p in (p1, p2, p3)]
    check(19, "limit=2 pages through 5 messages and ends with a null cursor",
          seq == [["m5", "m4"], ["m3", "m2"], ["m1"]] and isinstance(c1, str) and c1 and (p3 or {}).get("next_cursor") is None,
          "pages %s, first cursor %r, last cursor %r" % (seq, c1, (p3 or {}).get("next_cursor")))
    st, d = messages(PG, o, "?limit=2", node=N0)
    cur = (d or {}).get("next_cursor")
    st, d2 = messages(PG, o, "?limit=2&cursor=" + q(cur), node=N2) if cur else (0, None)
    check(20, "a cursor from port 8000 works on port 8002", st == 200 and bodies((d2 or {}).get("messages")) == ["m3", "m2"],
          "status %s, bodies %s" % (st, bodies((d2 or {}).get("messages"))))
    st, d = messages(PG, o, "?limit=2", node=N0)
    cur = (d or {}).get("next_cursor")
    post(PG, o, "m6")
    st, d2 = messages(PG, o, "?limit=2&cursor=" + q(cur), node=N1) if cur else (0, None)
    check(21, "a message posted between pages does not shift page 2", st == 200 and bodies((d2 or {}).get("messages")) == ["m3", "m2"],
          "status %s, bodies %s" % (st, bodies((d2 or {}).get("messages"))))
    check(22, "malformed cursor returns 400", messages(PG, o, "?cursor=not-a-real-cursor")[0] == 400, "")
    got = [messages(PG, o, "?limit=%s" % v)[0] for v in ("0", "201", "-1", "abc", "200")]
    check(23, "limit out of range returns 400, limit=200 is accepted", got == [400, 400, 400, 400, 200], "got %s" % got)
    if want(24):  # 55 posts: only worth the time when this rule is being checked
        BIG = newchan(S, "big", o)
        for i in range(55):
            post(BIG, o, "b%d" % i)
        st, d = messages(BIG, o)
        check(24, "default limit is 50", st == 200 and len((d or {}).get("messages", [])) == 50 and (d or {}).get("next_cursor"),
              "status %s, count %s, cursor %r" % (st, len((d or {}).get("messages", [])), (d or {}).get("next_cursor")))
    post(THR, o, "r2", parent=PARENT)
    post(THR, o, "r3", parent=PARENT)
    base = "%s/api/messages/%s/replies" % (N0, PARENT)
    st, d = call("GET", base, None, o["token"])
    b_all = bodies((d or {}).get("replies"))
    st1, d1 = call("GET", base + "?limit=2", None, o["token"])
    cr = (d1 or {}).get("next_cursor")
    d2 = call("GET", base + "?limit=2&cursor=" + q(cr), None, o["token"])[1] if cr else {}
    check(25, "replies are oldest first and page correctly", st == 200 and b_all == ["r1", "r2", "r3"]
          and bodies((d1 or {}).get("replies")) == ["r1", "r2"] and bodies((d2 or {}).get("replies")) == ["r3"] and (d2 or {}).get("next_cursor") is None,
          "all %s, page1 %s, page2 %s" % (b_all, bodies((d1 or {}).get("replies")), bodies((d2 or {}).get("replies"))))
    TOK = "zq" + RUN
    post(ALPHA, o, "first " + TOK)
    time.sleep(0.05)
    post(ALPHA, o, "second " + TOK)
    post(SEC, o, "hidden " + TOK)
    so, do_ = call("GET", "%s/api/search?q=%s" % (N0, TOK), None, o["token"])
    sx, dx = call("GET", "%s/api/search?q=%s" % (N0, TOK), None, x["token"])
    bo, bx = bodies((do_ or {}).get("results")), bodies((dx or {}).get("results"))
    check(26, "search finds matches newest first and hides private channels", so == 200 and sx == 200
          and bo[:3] == ["hidden " + TOK, "second " + TOK, "first " + TOK] and bx == ["second " + TOK, "first " + TOK],
          "owner got %s, other member got %s" % (bo, bx))
    S2 = "c1c-" + RUN
    st, d = mkws(S2, o)
    WS2, GEN2 = dig(d, "workspace.id"), dig(d, "general_channel.id")
    post(GEN2, o, "ws2 " + TOK)
    b1 = bodies((call("GET", "%s/api/search?q=%s&workspace_id=%s" % (N0, TOK, WS1), None, o["token"])[1] or {}).get("results"))
    b2 = bodies((call("GET", "%s/api/search?q=%s&workspace_id=%s" % (N0, TOK, WS2), None, o["token"])[1] or {}).get("results"))
    check(27, "workspace_id limits search to one workspace", ("ws2 " + TOK) not in b1 and b1 and b2 == ["ws2 " + TOK],
          "workspace 1 results %s, workspace 2 results %s" % (b1, b2))
    PIN = newchan(S, "pins", o)
    pm = {}
    for n in ("p1", "p2", "p3"):
        pm[n] = dig(post(PIN, o, n)[1], "message.id")
    s1, d1 = call("POST", "%s/api/messages/%s/pin" % (N0, pm["p1"]), {}, o["token"])
    s2, _ = call("POST", "%s/api/messages/%s/pin" % (N1, pm["p1"]), {}, o["token"])
    lst = (call("GET", "%s/api/channels/%s/pins" % (N0, PIN), None, o["token"])[1] or {}).get("pins", [])
    pin = dig(d1, "pin") or {}
    check(28, "pin returns 200 with a pin object and is idempotent", s1 == 200 and s2 == 200 and all(k in pin for k in ("message_id", "pinned_by", "pinned_at"))
          and len(lst) == 1, "status %s/%s, pin %s, list length %s" % (s1, s2, json.dumps(pin)[:150], len(lst)))
    for n in ("p2", "p3"):
        time.sleep(0.05)
        call("POST", "%s/api/messages/%s/pin" % (N0, pm[n]), {}, o["token"])
    base = "%s/api/channels/%s/pins" % (N0, PIN)
    st, d = call("GET", base, None, o["token"])
    names = [(p.get("message") or {}).get("body") for p in (d or {}).get("pins", [])]
    st1, d1 = call("GET", base + "?limit=2", None, o["token"])
    cp = (d1 or {}).get("next_cursor")
    d2 = call("GET", base + "?limit=2&cursor=" + q(cp), None, o["token"])[1] if cp else {}
    n1 = [(p.get("message") or {}).get("body") for p in (d1 or {}).get("pins", [])]
    n2 = [(p.get("message") or {}).get("body") for p in (d2 or {}).get("pins", [])]
    pfields = all(all(k in p for k in ("message", "pinned_by", "pinned_at")) for p in (d or {}).get("pins", []))
    check(29, "pin list is newest-pinned first, has the right fields and pages", st == 200 and names == ["p3", "p2", "p1"] and pfields
          and n1 == ["p3", "p2"] and n2 == ["p1"] and (d2 or {}).get("next_cursor") is None,
          "list %s, page1 %s, page2 %s" % (names, n1, n2))
    s, d = call("DELETE", "%s/api/messages/%s/pin" % (N0, pm["p1"]), None, o["token"])
    after = [(p.get("message") or {}).get("body") for p in (call("GET", base, None, o["token"])[1] or {}).get("pins", [])]
    check(30, "unpin returns {unpinned: true} and removes the pin", s == 200 and d == {"unpinned": True} and after == ["p3", "p2"],
          "status %s, body %s, list %s" % (s, d, after))
    post(SEC, o, "pinme")
    sx = call("GET", "%s/api/channels/%s/pins" % (N0, SEC), None, x["token"])[0]
    so = call("GET", "%s/api/channels/%s/pins" % (N0, SEC), None, o["token"])[0]
    check(31, "private channel pin list: outsider 403, member 200", (sx, so) == (403, 200), "got %s and %s" % (sx, so))

    # ===== C. WebSocket events =====
    EVT = newchan(S, "evt", o)
    join(EVT, m)

    async def r32():
        try:
            ws = await ws_open(8000, "bogus-token-" + RUN)
        except Exception:
            ok(32, "a bad token is rejected at the handshake")
            return
        try:
            await asyncio.wait_for(ws.recv(), 2)
            bad(32, "a bad token was accepted and a frame was delivered")
        except websockets.exceptions.ConnectionClosed:
            ok(32, "a bad token closes the connection")
        except asyncio.TimeoutError:
            bad(32, "a bad token connection stayed open")

    async def r33_35():
        ws = await ws_open(8000, o["token"])
        try:
            sub = await subscribe(ws, EVT)
            head = (sub or {}).get("head_event_id")
            check(33, "subscribe is answered with subscribed and head_event_id", isinstance(head, int), "got %s" % sub)
            if not isinstance(head, int):
                bad(34, "no head_event_id to compare with")
                bad(35, "no head_event_id to compare with")
                return
            st, d = post(EVT, o, "ws one")
            f = await wait_for(ws, lambda f: f.get("type") == "message.created" and str(f.get("channel_id")) == str(EVT), 3)
            check(34, "message.created with event_id head+1", f is not None and f.get("event_id") == head + 1,
                  "head %s, frame %s" % (head, json.dumps(f)[:200]))
            post(EVT, o, "ws reply", parent=dig(d, "message.id"))
            f2 = await wait_for(ws, lambda f: f.get("type") == "message.reply" and str(f.get("channel_id")) == str(EVT), 3)
            check(35, "message.reply with the next event_id", f2 is not None and f is not None and f2.get("event_id") == f.get("event_id") + 1,
                  "frame %s" % json.dumps(f2)[:200])
        finally:
            await ws.close()

    async def r36():
        ws = await ws_open(8001, o["token"])
        try:
            sub = await subscribe(ws, EVT)
            head = (sub or {}).get("head_event_id")
            if not isinstance(head, int):
                bad(36, "subscribe on 8001 gave no head_event_id")
                return
            for i in range(12):
                post(EVT, o, "dense %d" % i, node=(N0, N1, N2)[i % 3])
            got = await collect(ws, EVT, 12, 4, kinds=("message.created",))
            evs = [f["event_id"] for f in got]
            check(36, "12 posts across 3 nodes give 12 consecutive event_ids", evs == list(range(head + 1, head + 13)),
                  "head %s, event_ids seen %s" % (head, evs))
        finally:
            await ws.close()

    async def r37():
        ws = await ws_open(8002, o["token"])
        try:
            if not await subscribe(ws, EVT):
                bad(37, "subscribe on 8002 got no subscribed frame")
                return
            t = time.time()
            post(EVT, o, "fanout", node=N0)
            f = await wait_for(ws, lambda f: f.get("type") == "message.created", 5)
            check(37, "an event posted via 8000 reaches a subscriber on 8002 within 5s", f is not None, "no frame within 5s")
        finally:
            await ws.close()

    async def r38():
        a = await ws_open(8000, o["token"])
        sub = await subscribe(a, EVT)
        await a.close()
        head = (sub or {}).get("head_event_id")
        if not isinstance(head, int):
            bad(38, "subscribe gave no head_event_id")
            return
        for i in range(3):
            post(EVT, o, "missed %d" % i, node=N1)
        b = await ws_open(8002, o["token"])
        try:
            await b.send(json.dumps({"type": "resume", "channel_id": EVT, "since_event_id": head}))
            seen = []
            end = await wait_for(b, lambda f: f.get("type") == "resumed", 4, seen)
            evs = [f.get("event_id") for f in seen if "event_id" in f and f.get("type") != "resumed"]
            check(38, "resume on another node replays the missed events then resumed",
                  end is not None and evs == [head + 1, head + 2, head + 3] and end.get("head_event_id") == head + 3,
                  "head %s, replayed %s, resumed %s" % (head, evs, json.dumps(end)[:150]))
        finally:
            await b.close()

    async def r39():
        KC = newchan(S, "kinds", o)
        ws = await ws_open(8000, o["token"])
        try:
            sub = await subscribe(ws, KC)
            head = (sub or {}).get("head_event_id")
            if not isinstance(head, int):
                bad(39, "subscribe gave no head_event_id")
                return
            join(KC, m)
            call("PATCH", "%s/api/channels/%s" % (N0, KC), {"topic": "kinds"}, o["token"])
            call("POST", "%s/api/channels/%s/leave" % (N0, KC), {}, m["token"])
            call("POST", "%s/api/channels/%s/archive" % (N0, KC), {}, o["token"])
            call("POST", "%s/api/channels/%s/unarchive" % (N0, KC), {}, o["token"])
            got = await collect(ws, KC, 5, 4)
            kinds = [f.get("type") for f in got]
            evs = [f.get("event_id") for f in got]
            want = ["member.joined", "channel.updated", "member.left", "channel.archived", "channel.unarchived"]
            check(39, "join, topic, leave, archive, unarchive give the five durable kinds with consecutive ids",
                  kinds == want and evs == list(range(head + 1, head + 6)), "head %s, kinds %s, ids %s" % (head, kinds, evs))
        finally:
            await ws.close()

    async def r61():
        LV = newchan(S, "leave2", o)
        join(LV, m)
        ws = await ws_open(8000, o["token"])
        try:
            sub = await subscribe(ws, LV)
            head = (sub or {}).get("head_event_id")
            if not isinstance(head, int):
                bad(61, "subscribe gave no head_event_id")
                return
            call("POST", "%s/api/channels/%s/leave" % (N0, LV), {}, m["token"])
            call("POST", "%s/api/channels/%s/leave" % (N1, LV), {}, m["token"])
            got = await collect(ws, LV, 3, 1.5)
            kinds, evs = [f.get("type") for f in got], [f.get("event_id") for f in got]
            check(61, "leaving twice emits exactly one member.left", kinds == ["member.left"] and evs == [head + 1],
                  "head %s, kinds %s, ids %s" % (head, kinds, evs))
        finally:
            await ws.close()

    async def r62():
        CC = newchan(S, "conc", o)
        ws = await ws_open(8000, o["token"])
        try:
            sub = await subscribe(ws, CC)
            head = (sub or {}).get("head_event_id")
            if not isinstance(head, int):
                bad(62, "subscribe gave no head_event_id")
                return
            nodes = (N0, N1, N2)

            def burst():
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(10) as ex:
                    return list(ex.map(lambda i: post(CC, o, "c%d" % i, node=nodes[i % 3])[0], range(30)))

            statuses = await asyncio.get_running_loop().run_in_executor(None, burst)
            got = await collect(ws, CC, 30, 4, kinds=("message.created",))
            evs = sorted(f["event_id"] for f in got)
            check(62, "30 concurrent posts across 3 nodes all succeed with 30 consecutive event_ids",
                  statuses.count(201) == 30 and evs == list(range(head + 1, head + 31)),
                  "statuses %s, head %s, ids %s" % (sorted(set(statuses)), head, evs[:6]))
        finally:
            await ws.close()

    async def ws_rules():
        await guarded([32], r32)
        await guarded([33, 34, 35], r33_35)
        await guarded([36], r36)
        await guarded([37], r37)
        await guarded([38], r38)
        await guarded([39], r39)
        await guarded([61], r61)
        await guarded([62], r62, limit=12)

    asyncio.run(ws_rules())

    # ===== D. Mentions and read state =====
    MEN = newchan(S, "ment", o)
    join(MEN, m)
    join(MEN, x)
    def mentions(text, who=o):
        st, d = post(MEN, who, text)
        return st, [str(i) for i in (dig(d, "message.mentions") or [])]
    st, got = mentions("hi @%s" % m["name"])
    check(40, "@username fills mentions with the user's id", st == 201 and got == [str(m["id"])], "status %s, mentions %s" % (st, got))
    (s1, g1), (s2, g2) = mentions("hello@%s there" % m["name"]), mentions("write to bob@%s.com" % m["name"])
    check(41, "in-word and email-shaped mentions do not match", (s1, s2) == (201, 201) and g1 == [] and g2 == [],
          "statuses %s and %s, mentions %s and %s" % (s1, s2, g1, g2))
    st, got = mentions("note to self @%s" % o["name"])
    check(42, "a self-mention is dropped", st == 201 and got == [], "status %s, mentions %s" % (st, got))
    st, got = mentions("@%s @%s and @%s" % ((m["name"],) * 3))
    check(43, "mentions are deduplicated", st == 201 and got == [str(m["id"])], "status %s, mentions %s" % (st, got))
    st, got = mentions("hey @%s" % y["name"])
    check(44, "a user outside the workspace is excluded", st == 201 and got == [], "status %s, mentions %s" % (st, got))
    st, got = mentions("@channel heads up")
    expected = sorted([str(m["id"]), str(x["id"])])
    check(45, "@channel mentions every channel member except the author", st == 201 and sorted(got) == expected,
          "status %s, mentions %s, expected %s" % (st, sorted(got), expected))

    RS = newchan(S, "rs", o)
    join(RS, m)
    st, d = read_state(RS, m)
    rs = dig(d, "read_state") or {}
    check(46, "read state has channel_id, last_read_event_id, unread_count, mention_count",
          st == 200 and all(k in rs for k in ("channel_id", "last_read_event_id", "unread_count", "mention_count")) and str(rs.get("channel_id")) == str(RS),
          "status %s, read_state %s" % (st, json.dumps(rs)[:200]))
    u0 = rs.get("unread_count") if isinstance(rs.get("unread_count"), int) else 0
    for i in range(3):
        post(RS, o, "unread %d" % i)
    u1 = (dig(read_state(RS, m, node=N2)[1], "read_state") or {}).get("unread_count")
    check(47, "unread_count rises by 3 after 3 posts", u1 == u0 + 3, "before %s, after %s" % (u0, u1))
    m0 = (dig(read_state(RS, m)[1], "read_state") or {}).get("mention_count")
    o0 = (dig(read_state(RS, o)[1], "read_state") or {}).get("mention_count")
    post(RS, o, "@%s ping" % m["name"])
    post(RS, o, "@%s me" % o["name"])
    m1 = (dig(read_state(RS, m)[1], "read_state") or {}).get("mention_count")
    o1 = (dig(read_state(RS, o)[1], "read_state") or {}).get("mention_count")
    check(48, "mention_count rises for the mentioned user and not for a self-mention",
          isinstance(m0, int) and m1 == m0 + 1 and o1 == o0, "mentioned user %s -> %s, self-mentioner %s -> %s" % (m0, m1, o0, o1))
    head = head_of(RS, m["token"])
    if isinstance(head, int) and head >= 3:
        st, d = call("POST", "%s/api/channels/%s/read" % (N0, RS), {"last_read_event_id": head}, m["token"])
        now = (dig(read_state(RS, m, node=N1)[1], "read_state") or {}).get("last_read_event_id")
        check(49, "marking read returns the updated read_state and persists", st == 200 and dig(d, "read_state.last_read_event_id") == head and now == head,
              "status %s, posted %s, returned %s, later %s" % (st, head, dig(d, "read_state.last_read_event_id"), now))
        st, d = call("POST", "%s/api/channels/%s/read" % (N0, RS), {"last_read_event_id": head - 2}, m["token"])
        check(50, "the read cursor never moves backwards", st == 200 and dig(d, "read_state.last_read_event_id") == head,
              "status %s, posted %s then %s, got %s" % (st, head, head - 2, dig(d, "read_state.last_read_event_id")))
    else:
        bad(49, "setup: could not read head_event_id over WebSocket (rules 33 to 35 first)")
        bad(50, "setup: could not read head_event_id over WebSocket (rules 33 to 35 first)")
    st, d = call("GET", "%s/api/workspaces/%s" % (N0, S), None, m["token"])
    emb = json.dumps((d or {}).get("read_state"))
    check(51, "workspace detail embeds read_state with the same fields", st == 200 and all(k in emb for k in ("last_read_event_id", "unread_count", "mention_count")),
          "status %s, read_state %s" % (st, emb[:200]))

    # ===== E. Stricter edge cases =====
    base_r = "%s/api/messages/%s/replies" % (N0, PARENT)
    base_p = "%s/api/channels/%s/pins" % (N0, PIN)
    base_s = "%s/api/search?q=%s" % (N0, TOK)
    bads = []
    for name, url in (("replies", base_r + "?"), ("pins", base_p + "?"), ("search", base_s + "&")):
        for v in ("0", "201"):
            s = call("GET", url + "limit=" + v, None, o["token"])[0]
            if s != 400:
                bads.append("%s limit=%s->%s" % (name, v, s))
    check(52, "limit 0 and 201 return 400 on replies, pins and search", not bads, "; ".join(bads))
    bads = []
    for name, url in (("replies", base_r + "?"), ("pins", base_p + "?"), ("search", base_s + "&")):
        s = call("GET", url + "cursor=%21%21%21", None, o["token"])[0]
        if s != 400:
            bads.append("%s->%s" % (name, s))
    check(53, "a malformed cursor returns 400 on replies, pins and search", not bads, "; ".join(bads))
    SP = newchan(S, "sp", o)
    TOK2 = "sp" + RUN
    for i in range(1, 6):
        post(SP, o, "s%d %s" % (i, TOK2))
    url = "%s/api/search?q=%s&limit=2" % (N0, TOK2)
    s1, d1 = call("GET", url, None, o["token"])
    c1 = (d1 or {}).get("next_cursor")
    d2 = call("GET", url + "&cursor=" + q(c1), None, o["token"])[1] if c1 else {}
    c2 = (d2 or {}).get("next_cursor")
    d3 = call("GET", url + "&cursor=" + q(c2), None, o["token"])[1] if c2 else {}
    seq = [[b.split(" ")[0] for b in bodies((d or {}).get("results"))] for d in (d1, d2, d3)]
    check(54, "search pages with limit and cursor and ends with a null cursor",
          seq == [["s5", "s4"], ["s3", "s2"], ["s1"]] and (d3 or {}).get("next_cursor") is None, "pages %s" % seq)
    sx = call("GET", base_p, None, x["token"])
    check(55, "a workspace member who has not joined a public channel can read its pin list",
          sx[0] == 200 and isinstance((sx[1] or {}).get("pins"), list), "status %s" % sx[0])
    M3 = newchan(S, "men3", o)
    st, d = post(M3, o, "psst @%s" % x["name"])
    got = [str(i) for i in (dig(d, "message.mentions") or [])]
    check(56, "a workspace member who is not in the channel is still mentioned", st == 201 and got == [str(x["id"])],
          "status %s, mentions %s" % (st, got))
    M2 = newchan(S, "men2", o)
    join(M2, m)
    st, d = post(M2, o, "@channel notice")
    mid = dig(d, "message.id")
    join(M2, x)
    lst = (messages(M2, o)[1] or {}).get("messages", [])
    later = [[str(i) for i in mm.get("mentions", [])] for mm in lst if mm.get("id") == mid]
    check(57, "mentions are fixed when the message is posted", st == 201 and later == [[str(m["id"])]],
          "status %s, mentions when listed later %s" % (st, later))
    ARCH2 = newchan(S, "arch2", o)
    st, d = post(ARCH2, o, "parent")
    pid = dig(d, "message.id")
    call("POST", "%s/api/channels/%s/archive" % (N0, ARCH2), {}, o["token"])
    r1 = post(ARCH2, o, "reply", parent=pid)[0]
    r2 = call("PATCH", "%s/api/channels/%s" % (N0, ARCH2), {"topic": "new"}, o["token"])[0]
    r3 = call("POST", "%s/api/messages/%s/pin" % (N0, pid), {}, o["token"])[0]
    check(63, "reply, topic change and pin in an archived channel return 423", [r1, r2, r3] == [423, 423, 423],
          "reply %s, topic %s, pin %s" % (r1, r2, r3))
    w1 = post(PG, o, "   ")[0]
    w2 = call("POST", "%s/api/channels/%s/messages" % (N0, PG), {}, o["token"])[0]
    check(64, "a whitespace-only body and a missing body return 400", (w1, w2) == (400, 400), "got %s and %s" % (w1, w2))
    p1 = post(PG, o, "orphan", parent=99999999)[0]
    p2 = post(PG, o, "cross", parent=PARENT)[0]
    check(65, "a reply to an unknown parent or to a parent in another channel is rejected (400 or 404)",
          p1 in (400, 404) and p2 in (400, 404), "unknown parent %s, parent in other channel %s" % (p1, p2))
    st, d = call("POST", "%s/api/workspaces/%s/channels" % (N0, S), {"name": "desc-chan", "description": "hello"}, o["token"])
    check(66, "a description in the create body does not fill topic", st == 201 and dig(d, "channel.topic") == "",
          "status %s, topic %r" % (st, dig(d, "channel.topic")))
    RS2 = newchan(S, "rs2", o)
    join(RS2, m)
    own0 = (dig(read_state(RS2, o)[1], "read_state") or {}).get("unread_count")
    post(RS2, o, "my own message")
    own1 = (dig(read_state(RS2, o)[1], "read_state") or {}).get("unread_count")
    check(58, "the author's own message does not raise their unread_count", isinstance(own0, int) and own0 == own1,
          "before %s, after %s" % (own0, own1))
    h2 = head_of(RS2, m["token"])
    if isinstance(h2, int):
        call("POST", "%s/api/channels/%s/read" % (N0, RS2), {"last_read_event_id": h2}, m["token"])
        z0 = (dig(read_state(RS2, m)[1], "read_state") or {}).get("unread_count")
        post(RS2, o, "after read")
        z1 = (dig(read_state(RS2, m)[1], "read_state") or {}).get("unread_count")
        check(59, "marking read to the head makes unread_count 0, and one new message makes it 1", (z0, z1) == (0, 1),
              "after marking read %s, after one new message %s" % (z0, z1))
    else:
        bad(59, "setup: could not read head_event_id over WebSocket")
    emb = json.dumps((call("GET", "%s/api/workspaces/%s" % (N0, S), None, m["token"])[1] or {}).get("read_state"))
    check(60, "workspace detail read_state carries real counts, not zeros", re.search(r'"unread_count": *[1-9]', emb) is not None,
          "read_state %s" % emb[:200])

    print("RESULT: %d passed, %d failed" % (passed, failed))
    sys.exit(0 if failed == 0 else 1)


main()
