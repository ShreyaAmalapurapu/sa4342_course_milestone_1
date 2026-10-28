#!/usr/bin/env bash
# Self-check for component A. Prints PASS/FAIL per rule in /spec/roles/roles.md.
# Usage: bash /spec/roles/checks/check_roles.sh      Exit 0 only if every rule passes.
set -u
N0=http://127.0.0.1:8000; N1=http://127.0.0.1:8001; N2=http://127.0.0.1:8002
BODY=/tmp/check_roles_body.json
pass=0; fail=0
# AIDEV-NOTE: the agent's commands are cut off after 30s with the summary lost. After 22s every
# request fails at once (status 000) so the script still reaches its RESULT line in time. A signal
# trap does not work here: it fires inside a $(...) subshell and its output is swallowed.
T0=$SECONDS
LATE="/tmp/check_roles_late.$$"
trap 'rm -f "$LATE"' EXIT
PW="correct-horse-1"
RUN="$(printf '%x%x' "$(date +%s)" "$RANDOM")"

# AIDEV-NOTE: CHECK_RULES (for example "1-19,63") limits which rules are printed and counted. The
# staged layout uses it to run one stage; every other rule still runs silently as setup.
RULES="${CHECK_RULES:-all}"
want() {
  [ "$RULES" = all ] && return 0
  local t
  for t in ${RULES//,/ }; do
    case "$t" in
      *-*) [ "$1" -ge "${t%-*}" ] && [ "$1" -le "${t#*-}" ] && return 0 ;;
      *) [ "$1" = "$t" ] && return 0 ;;
    esac
  done
  return 1
}
# AIDEV-NOTE: ok and bad run in the main shell (not in a $(...) subshell), so this is the place to
# stop: once a request has hit the deadline, end with the normal RESULT line instead of running on.
late_exit() {
  echo "NOTE: the 22 second time budget ran out; this rule and every rule after it were not checked (the app is too slow, or hung)"
  echo "RESULT: $pass passed, $fail failed"
  exit 1
}
ok()  { want "$1" || return 0; echo "PASS $1 $2"; pass=$((pass+1)); [ -e "$LATE" ] && late_exit; return 0; }
bad() { [ "$1" = 0 ] || want "$1" || return 0; echo "FAIL $1 $2"; fail=$((fail+1)); [ -e "$LATE" ] && late_exit; return 0; }
# expect <rule> <description> <expected-status> <actual-status>
expect() { if [ "$4" = "$3" ]; then ok "$1" "$2"; else bad "$1" "$2: expected $3, got $4"; fi; }
# expect2 <rule> <description> <expected-1> <actual-1> <expected-2> <actual-2>
expect2() {
  if [ "$4" = "$3" ] && [ "$6" = "$5" ]; then ok "$1" "$2"
  else bad "$1" "$2: expected $3 then $5, got $4 then $6"; fi
}

# call <METHOD> <URL> [json-body] [token]  -> prints the HTTP status, saves the body in $BODY
call() {
  local left=$((22 - (SECONDS - T0)))
  if [ "$left" -le 0 ]; then : > "$LATE"; echo 000; return; fi
  local mt=4; [ "$left" -lt 4 ] && mt=$left
  local args=(-s -o "$BODY" -w '%{http_code}' --max-time "$mt" -X "$1" "$2")
  [ -n "${3:-}" ] && args+=(-H 'Content-Type: application/json' -d "$3")
  [ -n "${4:-}" ] && args+=(-H "Authorization: Bearer $4")
  curl "${args[@]}" 2>/dev/null || true   # status 000 means no answer within 5s
}
# field <dotted.path>  -> prints that field of the last response body, or nothing
field() {
  python3 -c 'import json,sys
try:
    v=json.load(open(sys.argv[1]))
    for k in sys.argv[2].split("."): v=v[k]
    print(json.dumps(v) if isinstance(v,(dict,list,bool)) or v is None else v)
except Exception: pass' "$BODY" "$1"
}
healthy() {
  for u in $N0 $N1 $N2; do curl -fs --max-time 2 "$u/api/health" >/dev/null 2>&1 || return 1; done
}

# AIDEV-NOTE: total wait is capped at 20s so the whole script fits the agent's 30s command limit.
if ! healthy; then
  (cd /app && nohup /app/start.sh >/tmp/start.log 2>&1 &)
  for _ in $(seq 1 20); do healthy && break; sleep 1; done
fi
healthy || { echo "FAIL 0 cluster is not answering /api/health on 8000, 8001, 8002 (see /tmp/start.log)"; echo "RESULT: 0 passed, 1 failed"; exit 1; }

# ---- helpers: users are referred to by a short label ----
tk()  { local v="T_$1"; printf '%s' "${!v}"; }     # token of user <label>
uid() { local v="I_$1"; printf '%s' "${!v}"; }     # id of user <label>
jid() { if [[ "$1" =~ ^[0-9]+$ ]]; then printf '%s' "$1"; else printf '"%s"' "$1"; fi; }
# reg <label>  -> registers user "<label><RUN>" on node 8000; sets REG_S REG_U T_<label> I_<label>
reg() {
  local l="$1" u="$1$RUN"
  REG_S=$(call POST $N0/api/auth/register "{\"username\":\"$u\",\"password\":\"$PW\"}")
  REG_U=$(field user.username)
  printf -v "T_$l" '%s' "$(field token)"; printf -v "I_$l" '%s' "$(field user.id)"
}
# mkws <slug> <label>  -> creates a workspace; sets WS_S (status) and GEN (id of #general)
mkws() { WS_S=$(call POST $N0/api/workspaces "{\"slug\":\"$1\",\"name\":\"Roles $1\"}" "$(tk "$2")"); GEN=$(field general_channel.id); }
# mkch <slug> <name> <private:true|false> <label>  -> status; read the new id afterwards with: field channel.id
mkch() { call POST $N0/api/workspaces/$1/channels "{\"name\":\"$2\",\"is_private\":$3}" "$(tk "$4")"; }
# jn <channel-id> <label> [node]  -> join status
jn() { call POST "${3:-$N0}"/api/channels/$1/join '{}' "$(tk "$2")"; }
# setrole <slug> <target-label> <role> <actor-label>  -> status
setrole() { call PATCH $N0/api/workspaces/$1/members/$(uid "$2") "{\"role\":\"$3\"}" "$(tk "$4")"; }
# role_of <slug> <label> <reader-label>  -> role of the user, read through node 8002
role_of() {
  call GET $N2/api/workspaces/$1/members "" "$(tk "$3")" >/dev/null
  python3 -c 'import json,sys
try:
    for m in json.load(open(sys.argv[1]))["members"]:
        if str(m.get("user_id"))==sys.argv[2]: print(m.get("role")); break
except Exception: pass' "$BODY" "$(uid "$2")"
}
# mkinv <slug> <label> <json-body>  -> status; read the code afterwards with: field invitation.code
mkinv() { call POST $N0/api/workspaces/$1/invitations "$3" "$(tk "$2")"; }
# accept <code> <label> [node]  -> status
accept() { call POST "${3:-$N0}"/api/invitations/$1/accept '{}' "$(tk "$2")"; }

# ---- setup: register every user ----
LABELS="o ad m g x m2 a3 m3 i1 i2 i3 i4 i5 i6 i7 i8 j1 j2 y z k1 k2 k3 k4"
for l in $LABELS; do
  reg $l
  if [ "$l" = o ]; then O_REG_S=$REG_S; O_REG_U=$REG_U; fi
done
[ -n "$(tk o)" ] || { bad 0 "setup: could not register users (rule 1 must work first)"; echo "RESULT: $pass passed, $fail failed"; exit 1; }

# ===== A. Foundations =====
if [ "$O_REG_S" = 201 ] && [ "$O_REG_U" = "o$RUN" ] && [ "${#T_o}" -ge 16 ]; then ok 1 "register returns 201 with user and token"
else bad 1 "register: expected 201, username o$RUN and a token of 16+ chars, got status $O_REG_S, username '$O_REG_U', token length ${#T_o}"; fi

s=$(call POST $N0/api/auth/register "{\"username\":\"o$RUN\",\"password\":\"$PW\"}"); expect 2 "duplicate username" 409 "$s"
s=$(call POST $N0/api/auth/register "{\"username\":\"bad name $RUN\",\"password\":\"$PW\"}"); expect 3 "username with a space" 400 "$s"
s=$(call POST $N0/api/auth/register "{\"username\":\"sp$RUN\",\"password\":\"short77\"}"); expect 4 "7 character password" 400 "$s"

s=$(call POST $N0/api/auth/login "{\"username\":\"o$RUN\",\"password\":\"$PW\"}"); lt=$(field token)
if [ "$s" = 200 ] && [ -n "$lt" ]; then ok 5 "login returns 200 with user and token"; else bad 5 "login: expected 200 with a token, got status $s"; fi
s=$(call POST $N0/api/auth/login "{\"username\":\"o$RUN\",\"password\":\"WRONG12345\"}"); expect 6 "login with wrong password" 401 "$s"

s=$(call GET $N0/api/auth/me "" "$(tk o)"); un=$(field user.username)
if [ "$s" = 200 ] && [ "$un" = "o$RUN" ]; then ok 7 "me returns 200 and the same username"; else bad 7 "me: expected 200 and o$RUN, got $s and '$un'"; fi
s1=$(call GET $N0/api/auth/me); s2=$(call GET $N0/api/auth/me "" "not-a-real-token-000000")
expect2 8 "me without a valid token" 401 "$s1" 401 "$s2"
a=$(call POST $N2/api/auth/login "{\"username\":\"o$RUN\",\"password\":\"$PW\"}"); b=$(call GET $N1/api/auth/me "" "$(tk o)")
expect2 9 "login on 8002 and me on 8001 after register on 8000" 200 "$a" 200 "$b"

S1="r1-$RUN"
mkws $S1 o; GEN1=$GEN
W_ID=$(field workspace.id); W_SLUG=$(field workspace.slug); W_NAME=$(field workspace.name); W_JM=$(field workspace.join_mode); W_OWN=$(field workspace.owner_id); GN=$(field general_channel.name)
if [ "$WS_S" = 201 ] && [ -n "$W_ID" ] && [ "$W_SLUG" = "$S1" ] && [ -n "$W_NAME" ] && [ -n "$GEN1" ] && [ "$GN" = general ]; then ok 10 "create workspace returns 201 with workspace and #general"
else bad 10 "create workspace: expected 201, workspace{id,slug,name} and general_channel named general, got status $WS_S, slug '$W_SLUG', general '$GN'"; fi
if [ "$W_OWN" = "$(uid o)" ] && [ "$W_JM" = open ]; then ok 11 "owner_id is the creator and join_mode is open"
else bad 11 "owner_id/join_mode: expected $(uid o) and open, got '$W_OWN' and '$W_JM'"; fi
s=$(call POST $N0/api/workspaces "{\"slug\":\"$S1\",\"name\":\"Dup\"}" "$(tk m)"); expect 12 "duplicate slug" 409 "$s"
bads=""
for sl in Acme a "has space" "$(head -c 33 /dev/zero | tr '\0' a)"; do
  s=$(call POST $N0/api/workspaces "{\"slug\":\"$sl\",\"name\":\"Bad\"}" "$(tk m)"); [ "$s" = 400 ] || bads="$bads [${sl:0:8}->$s]"
done
if [ -z "$bads" ]; then ok 13 "invalid slugs return 400"; else bad 13 "invalid slugs: expected 400 for each, got$bads"; fi
s=$(call GET $N0/api/workspaces "" "$(tk o)"); has=$(grep -c "$S1" "$BODY")
s2=$(call GET $N0/api/workspaces "" "$(tk y)"); hasy=$(grep -c "$S1" "$BODY")
if [ "$s" = 200 ] && [ "$has" -ge 1 ] && [ "$s2" = 200 ] && [ "$hasy" = 0 ]; then ok 14 "list shows my workspace and hides others"
else bad 14 "list workspaces: expected 200, own slug present and absent for non-member, got $s/$has and $s2/$hasy"; fi
r=$(role_of $S1 o o)
if [ "$r" = owner ]; then ok 15 "members lists the creator as owner"; else bad 15 "members: expected creator role owner, got '$r'"; fi
s1=$(call GET $N0/api/workspaces); s2=$(call GET $N0/api/workspaces/$S1/members)
expect2 16 "workspace routes without a token" 401 "$s1" 401 "$s2"
s=$(jn 99999999 o); expect 17 "join unknown channel" 404 "$s"
s=$(jn $GEN1 x); r=$(role_of $S1 x o)
if [ "$s" = 200 ] && [ "$r" = member ]; then ok 18 "open workspace: join returns 200 and adds a member"
else bad 18 "open join: expected 200 and role member, got $s and '$r'"; fi
s=$(mkch $S1 secret true o); SEC=$(field channel.id); s=$(jn $SEC y); expect 19 "join a private channel without invite" 403 "$s"

# ===== B. Roles =====
for l in ad m g m2 a3 m3; do jn $GEN1 $l >/dev/null; done
s=$(setrole $S1 ad admin o); r=$(field member.role)
if [ "$s" = 200 ] && [ "$r" = admin ]; then ok 20 "owner promotes member to admin"; else bad 20 "owner promote: expected 200 and admin, got $s and '$r'"; fi
setrole $S1 a3 admin o >/dev/null
s=$(setrole $S1 a3 member o); r=$(field member.role); s2=$(setrole $S1 m3 guest o); r2=$(field member.role)
if [ "$s" = 200 ] && [ "$r" = member ] && [ "$s2" = 200 ] && [ "$r2" = guest ]; then ok 21 "owner demotes admin to member and member to guest"
else bad 21 "owner demote: expected 200/member and 200/guest, got $s/'$r' and $s2/'$r2'"; fi
setrole $S1 g guest o >/dev/null
ra=$(role_of $S1 ad o); rb=$(role_of $S1 a3 o); rc=$(role_of $S1 m3 o); rd=$(role_of $S1 g o)
if [ "$ra" = admin ] && [ "$rb" = member ] && [ "$rc" = guest ] && [ "$rd" = guest ]; then ok 22 "role changes persist and are visible on another node"
else bad 22 "persist: expected admin/member/guest/guest, got $ra/$rb/$rc/$rd"; fi
s=$(setrole $S1 m2 admin ad); expect 23 "admin promotes a member to admin" 200 "$s"
s=$(setrole $S1 m2 member ad); expect 24 "admin cannot modify another admin" 403 "$s"
s=$(setrole $S1 o member ad); expect 25 "admin cannot modify the owner" 403 "$s"
s=$(setrole $S1 o admin o); expect 26 "owner cannot change own role" 403 "$s"
s=$(setrole $S1 m owner o); expect 27 "role owner via PATCH is rejected" 400 "$s"
s=$(setrole $S1 g member m); expect 28 "member cannot change roles" 403 "$s"
s=$(setrole $S1 m admin g); expect 29 "guest cannot change roles" 403 "$s"

S2="r2-$RUN"
mkws $S2 o; GEN2=$GEN
for l in ad m; do jn $GEN2 $l >/dev/null; done
setrole $S2 ad admin o >/dev/null
tr_() { call POST $N0/api/workspaces/$1/transfer_ownership "{\"new_owner_id\":$(jid "$(uid "$2")")}" "$(tk "$3")"; }
s=$(tr_ $S2 ad ad); expect 30 "admin cannot transfer ownership" 403 "$s"
s=$(tr_ $S2 m o); expect 31 "transfer to a member is rejected" 400 "$s"
s=$(tr_ $S2 ad o); expect 32 "owner transfers to an admin" 200 "$s"
r1=$(role_of $S2 ad ad); r2=$(role_of $S2 o ad); call GET $N1/api/workspaces/$S2 "" "$(tk ad)" >/dev/null; own=$(field workspace.owner_id)
if [ "$r1" = owner ] && [ "$r2" = admin ] && [ "$own" = "$(uid ad)" ]; then ok 33 "after transfer: new owner, old owner is admin, owner_id updated"
else bad 33 "transfer result: expected owner/admin and owner_id $(uid ad), got $r1/$r2 and '$own'"; fi
s=$(tr_ $S2 ad o); expect 34 "previous owner cannot transfer again" 403 "$s"

s=$(mkch $S1 lobby false o); LOBBY=$(field channel.id)
s=$(jn $LOBBY g); expect 35 "guest cannot join a public channel" 403 "$s"
s=$(mkch $S1 guest-chan false g); expect 36 "guest cannot create a channel" 403 "$s"
s=$(mkch $S1 m-pub false m); expect 37 "member creates a public channel" 201 "$s"
s=$(mkch $S1 m-priv true m); expect 38 "member cannot create a private channel" 403 "$s"
s=$(mkch $S1 ad-priv true ad); expect 39 "admin creates a private channel" 201 "$s"
s=$(mkch $S1 o-priv true o); expect 40 "owner creates a private channel" 201 "$s"

# ===== C. Invitations =====
S3="r3-$RUN"
mkws $S3 o; GEN3=$GEN
for l in ad m g; do jn $GEN3 $l >/dev/null; done
setrole $S3 ad admin o >/dev/null; setrole $S3 g guest o >/dev/null
s=$(mkinv $S3 o '{}'); C1=$(field invitation.code)
if [ "$s" = 201 ] && [ -n "$C1" ]; then ok 41 "owner creates an invitation with a code"; else bad 41 "owner invitation: expected 201 and a code, got $s and '$C1'"; fi
s=$(mkinv $S3 ad '{}'); expect 42 "admin creates an invitation" 201 "$s"
s1=$(mkinv $S3 m '{}'); s2=$(mkinv $S3 g '{}'); expect2 43 "member and guest cannot create invitations" 403 "$s1" 403 "$s2"
sa=$(accept $C1 i1); sb=$(accept $C1 i2 $N1); expect2 44 "default max_uses is 1 (accept, then accept again)" 200 "$sa" 404 "$sb"
s=$(mkinv $S3 o '{"max_uses":2}'); C2=$(field invitation.code)
sa=$(accept $C2 i3); sb=$(accept $C2 i4 $N2); sc=$(accept $C2 i5)
if [ "$sa" = 200 ] && [ "$sb" = 200 ] && [ "$sc" = 404 ]; then ok 45 "max_uses 2 allows two accepts then 404"
else bad 45 "max_uses 2: expected 200, 200, 404, got $sa, $sb, $sc"; fi
r=$(role_of $S3 i1 o)
if [ "$r" = member ]; then ok 46 "accepting an invitation adds a member"; else bad 46 "accept: expected i1 listed as member, got '$r'"; fi
s=$(accept "nosuchcode$RUN" i6); expect 47 "unknown code" 404 "$s"
s=$(mkinv $S3 o '{"expires_in_seconds":1}'); CEXP=$(field invitation.code)
s=$(mkinv $S3 o '{"max_uses":5}'); CREV=$(field invitation.code)
s=$(mkinv $S3 o '{"max_uses":5}'); CA=$(field invitation.code)
sleep 2
s=$(accept $CEXP i6); expect 48 "expired invitation" 404 "$s"
s=$(call DELETE $N0/api/workspaces/$S3/invitations/$CREV "" "$(tk o)")
if [ "$s" = 200 ] && grep -Eq '"deleted": *true' "$BODY"; then dl=ok; else dl=no; fi
sb=$(accept $CREV i6)
if [ "$dl" = ok ] && [ "$sb" = 404 ]; then ok 49 "revoke returns {deleted:true} and the code then returns 404"
else bad 49 "revoke: expected 200 with deleted true then 404, got $s ($dl) then $sb"; fi
s=$(mkinv $S3 o "{\"invited_user_id\":$(jid "$(uid i7)")}"); CT=$(field invitation.code)
sa=$(accept $CT i8); sb=$(accept $CT i7)
expect2 50 "targeted invitation: other user then target user" 403 "$sa" 200 "$sb"
sc=$(accept $CT i7); expect 51 "targeted invitation is single-use" 404 "$sc"
s=$(call GET $N0/api/workspaces/$S3/invitations "" "$(tk o)"); a1=$(grep -c "$CA" "$BODY"); a2=$(grep -c "$CREV" "$BODY")
s2=$(call GET $N1/api/workspaces/$S3/invitations "" "$(tk ad)")
if [ "$s" = 200 ] && [ "$s2" = 200 ] && [ "$a1" -ge 1 ] && [ "$a2" = 0 ]; then ok 52 "list shows active codes and hides revoked ones"
else bad 52 "list invitations: expected 200/200, active present, revoked absent, got $s/$s2, $a1, $a2"; fi
s=$(call GET $N0/api/workspaces/$S3/invitations "" "$(tk m)"); expect 53 "member cannot list invitations" 403 "$s"
s=$(call DELETE $N0/api/workspaces/$S3/invitations/$CA "" "$(tk m)"); expect 54 "member cannot revoke an invitation" 403 "$s"
s=$(call POST $N0/api/invitations/$CA/accept '{}'); expect 55 "accept without a token" 401 "$s"

# ===== D. Join mode =====
S4="r4-$RUN"
mkws $S4 o; GEN4=$GEN
for l in ad m; do jn $GEN4 $l >/dev/null; done
setrole $S4 ad admin o >/dev/null
s=$(mkch $S4 lobby false o); LB4=$(field channel.id); s=$(mkch $S4 lobby2 false o); LB42=$(field channel.id)
s=$(call PATCH $N0/api/workspaces/$S4 '{"join_mode":"invite_only"}' "$(tk o)"); jm=$(field workspace.join_mode)
if [ "$s" = 200 ] && [ "$jm" = invite_only ]; then ok 56 "owner sets join_mode to invite_only"; else bad 56 "set join_mode: expected 200 and invite_only, got $s and '$jm'"; fi
s=$(call PATCH $N0/api/workspaces/$S4 '{"name":"Renamed"}' "$(tk ad)"); nm=$(field workspace.name)
if [ "$s" = 200 ] && [ "$nm" = Renamed ]; then ok 57 "admin renames the workspace"; else bad 57 "rename: expected 200 and Renamed, got $s and '$nm'"; fi
s=$(call PATCH $N0/api/workspaces/$S4 '{"name":"Nope"}' "$(tk m)"); expect 58 "member cannot PATCH the workspace" 403 "$s"
s=$(jn $LB42 m); expect 59 "invite_only: existing member can still join a public channel" 200 "$s"
s=$(jn $LB4 x); r=$(role_of $S4 x o)
if [ "$s" = 403 ] && [ -z "$r" ]; then ok 60 "invite_only: non-member gets 403 and is not added"; else bad 60 "invite_only join: expected 403 and no membership, got $s and role '$r'"; fi
s=$(mkinv $S4 o '{}'); CI=$(field invitation.code); sa=$(accept $CI x); sb=$(jn $LB4 x)
expect2 61 "invite_only: redeem an invitation, then join" 200 "$sa" 200 "$sb"
call PATCH $N0/api/workspaces/$S4 '{"join_mode":"open"}' "$(tk o)" >/dev/null
s=$(jn $LB4 y); r=$(role_of $S4 y o)
if [ "$s" = 200 ] && [ "$r" = member ]; then ok 62 "back to open: non-member joins and becomes a member"; else bad 62 "open again: expected 200 and member, got $s and '$r'"; fi

# ===== E. Cluster baseline =====
ids=""
for p in 8000 8001 8002; do s=$(call GET http://127.0.0.1:$p/api/health); ids="$ids $s/$(field node_id)"; done
if [ "$ids" = " 200/0 200/1 200/2" ]; then ok 63 "every node reports its own node_id"; else bad 63 "health: expected 200/0 200/1 200/2, got$ids"; fi

# ===== F. Stricter edge cases =====
s=$(setrole $S1 m superuser o); expect 64 "an unknown role value returns 400" 400 "$s"
s=$(setrole $S1 k1 member o); expect 65 "role change for a user who is not in the workspace returns 404" 404 "$s"
s=$(mkinv $S3 o "{\"invited_username\":\"k2$RUN\"}"); CU=$(field invitation.code)
sa=$(accept $CU k3); sb=$(accept $CU k2)
expect2 66 "invited_username invitation: other user then named user" 403 "$sa" 200 "$sb"
s=$(mkinv $S3 o "{\"invited_user_id\":$(jid "$(uid k4)"),\"max_uses\":5}"); CM=$(field invitation.code)
sa=$(accept $CM k4); sb=$(accept $CM k4)
expect2 67 "targeted invitation is single-use even with max_uses 5" 200 "$sa" 404 "$sb"
s1=$(mkinv $S3 o '{"max_uses":0}'); s2=$(mkinv $S3 o '{"max_uses":"abc"}')
expect2 68 "max_uses of 0 and of a non-number return 400" 400 "$s1" 400 "$s2"
s=$(call PATCH $N0/api/workspaces/$S3 '{"join_mode":"closed"}' "$(tk o)"); expect 69 "an unknown join_mode returns 400" 400 "$s"
s1=$(call POST $N0/api/auth/register "{\"username\":\"np$RUN\"}"); s2=$(call POST $N0/api/auth/register '{}')
s3=$(call POST $N0/api/auth/login "{\"username\":\"nobody$RUN\",\"password\":\"$PW\"}")
if [ "$s1" = 400 ] && [ "$s2" = 400 ] && [ "$s3" = 401 ]; then ok 70 "register without a password or with {} returns 400, login for an unknown user 401"
else bad 70 "auth edge cases: expected 400, 400, 401, got $s1, $s2, $s3"; fi

echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
