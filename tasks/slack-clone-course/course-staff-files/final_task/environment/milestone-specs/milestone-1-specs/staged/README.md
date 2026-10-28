# Read this first: work order and rules for this run

This run is about **two components in one run**: roles and invitations (A) and channels and message history (B).
Before you run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`, you MUST have run `check_roles_all.sh` and `check_channels_all.sh` and seen **all 66** channel rules pass and **all 70** roles rules pass. Do not submit unless every one of them passes.

The task description you were given is still the grading contract. The files in `/spec` do
not change it; they tell you what to work on now and how to prove it works.

## Files in /spec

| File | What it is |
|---|---|
| `README.md` | This file |
| `roles/roles-1-foundations.md` | Roles.1: auth, workspaces, joining channels (rules 1-19, 63, 70) |
| `roles/roles-2-roles.md` | Roles.2: roles, ownership transfer, role-based permissions (rules 20-40, 64, 65) |
| `roles/roles-3-invitations.md` | Roles.3: invitations and join mode (rules 41-62, 66-69) |
| `roles/checks/check_roles_1.sh`, `_2.sh`, `_3.sh` | PASS or FAIL per rule of one spec |
| `roles/checks/check_roles_all.sh` | All 70 roles rules, one total |
| `roles/checks/check_roles_core.sh` | The rules themselves; called by the others, do not edit |
| `channels/channels-1-management.md` | Channels.1: channel management (rules 1-14, 66) |
| `channels/channels-2-messages.md` | Channels.2: messages, paging, search, pins (rules 15-31, 52-55, 63-65) |
| `channels/channels-3-events.md` | Channels.3: WebSocket events (rules 32-39, 61, 62) |
| `channels/channels-4-mentions-read.md` | Channels.4: mentions and read state (rules 40-51, 56-60) |
| `channels/checks/check_channels_1.sh` to `_4.sh` | PASS or FAIL per rule of one spec |
| `channels/checks/check_channels_all.sh` | All 66 channel rules, one total |
| `channels/checks/check_channels_core.py` | The rules themselves; called by the others, do not edit |

Each spec has three parts: **Uses** (functions from earlier specs that you must call, not rewrite), **Adds** (functions this spec introduces, with the names to use), and **Rules** (what must be true over HTTP or WebSocket).

## Scope

Work only on the routes in the roles and channels specs. Do NOT start on files, DMs, reactions, edit/delete, groups, slash commands, IRC, or the UI in this run.

If `/app` already has code, improve it, do not start over; read it first. If `/app` is empty, build the smallest cluster that answers `GET /api/health` on ports `8000`, `8001`, and `8002`, then add what the rules need. State written through one node must be visible through the other two (store it in one shared place, not in per-process memory). Event ids must come from one shared per-channel counter, not from a counter per process, and must stay consecutive when several nodes write at the same time.

## Do not break the baseline

These four conditions must still hold when you submit:

1. `GET /api/health` on `127.0.0.1:8000` returns `200` with `{"status": "ok", "node_id": 0}`.
2. Ports `8000`, `8001`, `8002` report `node_id` `0`, `1`, `2`.
3. A WebSocket to `ws://127.0.0.1:8000/api/ws?token=<invalid>` is rejected.
4. If one HTTP node is killed (for example by PID), the other two keep answering
   `GET /api/health` with `200`.

`/app/start.sh` must stay in the foreground and keep the cluster alive. List every Python
dependency in `/app/requirements.txt`. The check scripts use their own Python (`/opt/test-venv`),
so they do not depend on your packages.

## Protect your code

You edit `/app` with commands, and a bad edit script can wipe a file. A previous run lost 500
lines of `app.py` this way and spent twenty steps rebuilding it from compiled bytecode.

- Keep the source as plain `.py` files in `/app`. Do not run from, or depend on, `__pycache__` or
  any compiled file: only the source is kept.
- Before every scripted edit (a `python3 - <<` patch, `sed -i`, a heredoc that overwrites a file),
  copy the file: `cp /app/app.py /tmp/app.py.bak`.
- After the edit, run `python3 -m py_compile` and `wc -l` on the file. If it is far shorter than
  before or fails to compile, restore the backup instead of patching on top of the damage.
- Prefer several smaller files (one per area) over one very large file.

## Order of work

1. Read `/spec/roles/roles-1-foundations.md`. Build it using the functions from earlier specs. Run `check_roles_1.sh` until all of its rules pass.
2. Read `/spec/roles/roles-2-roles.md`. Build it using the functions from earlier specs. Run `check_roles_2.sh` until all of its rules pass.
3. Read `/spec/roles/roles-3-invitations.md`. Build it using the functions from earlier specs. Run `check_roles_3.sh` until all of its rules pass.
4. Read `/spec/channels/channels-1-management.md`. Build it using the functions from earlier specs. Run `check_channels_1.sh` until all of its rules pass.
5. Read `/spec/channels/channels-2-messages.md`. Build it using the functions from earlier specs. Run `check_channels_2.sh` until all of its rules pass.
6. Read `/spec/channels/channels-3-events.md`. Build it using the functions from earlier specs. Run `check_channels_3.sh` until all of its rules pass.
7. Read `/spec/channels/channels-4-mentions-read.md`. Build it using the functions from earlier specs. Run `check_channels_4.sh` until all of its rules pass.
8. After each step, run the matching `_all.sh` check. If a rule from an earlier spec starts failing, undo your last change before doing anything else.

Do not start a spec while a rule from an earlier spec is failing. A spec does not need to be built twice: `Uses` tells you what already exists, and `Adds` what to write now.

Time guidance (50 minutes in total): finish component A within about 20 minutes, rules 1 to 31 of component B by minute 35, the WebSocket rules by minute 43. Do not spend more than 5 minutes on one failing rule; move on and come back. Leave the last 5 minutes for the baseline conditions.

## How to verify

Run each as a single command:

```bash
bash /spec/roles/checks/check_roles_all.sh
bash /spec/channels/checks/check_channels_all.sh
# one stage at a time: check_roles_1.sh ... check_roles_3.sh, check_channels_1.sh ... check_channels_4.sh
```

Each prints one line per rule and a final summary:

```text
PASS 1 create returns 201 and a full channel with defaults
FAIL 19 limit=2 pages through 5 messages ...: pages [...]
RESULT: 65 passed, 1 failed
```

- Run each check as a command of its own. Do not chain a restart, a `sleep`, and a check in one
  command: every command is cut off after 30 seconds, and a check stops itself at 22 seconds.
- A check starts `/app/start.sh` itself if the cluster is not answering, and finishes in under 30
  seconds. Every user, workspace, and channel it creates has a random name, so you can run it as
  often as you like.
- After you change code, restart the cluster before checking again: find the process that owns
  each port with `ss -ltnp` and stop it by PID. Never stop processes by name.

## Before you submit

You may submit only when all of these are true, from output you actually saw:

- the last line of `check_roles_all.sh` is `RESULT: 70 passed, 0 failed`;
- the last line of `check_channels_all.sh` is `RESULT: 66 passed, 0 failed`;
- `GET /api/health` returns `200` on ports `8000`, `8001`, and `8002`;
- you confirmed baseline conditions 3 and 4 above.

These rules are necessary, not sufficient. The full task in the grading contract still applies.
