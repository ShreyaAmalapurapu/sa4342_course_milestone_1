# Roles.1: auth, workspaces, joining channels

Routes covered: `/api/auth/*`, `POST /api/workspaces`, `GET /api/workspaces`, `GET /api/workspaces/{slug}/members`, `POST /api/channels/{id}/join`, `POST /api/workspaces/{slug}/channels` (bare creation), `GET /api/health`.
Check: `bash /spec/roles/checks/check_roles_1.sh`. The rule numbers below match its output.

Your service is launched with `/app/start.sh`. The three HTTP nodes listen on
`127.0.0.1:8000`, `:8001`, and `:8002`. State written through one node must be visible through
the other two.

Conventions for every rule: JSON in, JSON out; `Authorization: Bearer <token>`; a missing or
invalid token is `401`; an unknown id is `404`; every request must answer within 5 seconds.

## Uses

Call these functions from earlier specs. Do not write a second version of any of them.

Nothing: this is the first spec of the component.

## Adds

| Function | What it does |
|---|---|
| `create_user(username, password, display_name)` | Validates and stores a user; 400 or 409 on bad input |
| `issue_token(user_id)` | A bearer token of 16+ characters, valid on every node |
| `user_from_token(header)` | The user for `Authorization: Bearer ...`, or `None` (the caller answers 401) |
| `create_workspace(owner, slug, name)` | Creates the workspace, its `#general` channel, and the owner membership |
| `get_workspace(slug)` | The workspace or `None` |
| `get_member(workspace_id, user_id)` | The membership with its role, or `None` |
| `list_members(workspace_id)` | Every member with `user_id` and `role` |
| `create_channel(workspace, actor, name, is_private)` | Bare channel creation: id, name, privacy. Permission rules arrive in Roles.2 |
| `join_channel(channel_id, user)` | Joins a public channel; in an open workspace a non-member becomes a `member` |

## Rules (1-19, 63, 70)

1. **Register.** `POST /api/auth/register` with `{username, password}` (valid username,
   password of 8+ characters) returns `201` and JSON with `user` and `token`. `user.username`
   is the username sent. `token` is at least 16 characters.

2. **Duplicate username.** Registering the same username again returns `409`.

3. **Invalid username.** A username containing a space returns `400`.

4. **Short password.** A 7-character password returns `400`.

5. **Login.** `POST /api/auth/login` with the right password returns `200` and JSON with
   `user` and `token`.

6. **Wrong password.** Login with a wrong password returns `401`.

7. **Me.** `GET /api/auth/me` with a valid bearer token returns `200` and `user` with the
   registered username.

8. **Me without a valid token.** No `Authorization` header returns `401`; so does a made-up
   token.

9. **Every node.** A user registered on port `8000` can log in on `8002`, and a token issued on
   `8000` is accepted by `GET /api/auth/me` on `8001`.

10. **Create workspace.** `POST /api/workspaces` with `{slug, name}` returns `201` with
    `workspace` (containing `id`, `slug`, `name`, `owner_id`, `join_mode`) and
    `general_channel` (containing `id` and `name` equal to `general`).

11. **Owner and default mode.** `workspace.owner_id` is the creator's user id and
    `workspace.join_mode` is `"open"`.

12. **Duplicate slug.** Creating a workspace with a slug that is taken returns `409`.

13. **Invalid slug.** Each of these slugs returns `400`: `Acme` (uppercase), `a` (1 character),
    `has space`, and a 33-character slug. (Valid slugs match `^[a-z0-9-]{2,32}$`.)

14. **List workspaces.** `GET /api/workspaces` returns `200` and `workspaces`. It contains the
    caller's workspace and does not contain a workspace the caller is not in.

15. **List members.** `GET /api/workspaces/{slug}/members` returns `200` and `members`, a list
    of objects with `user_id` and `role`. The creator is listed with `role` `"owner"`.

16. **Auth required.** `GET /api/workspaces` and `GET /api/workspaces/{slug}/members` with no
    token return `401`.

17. **Join, unknown channel.** `POST /api/channels/{id}/join` with an id that does not exist
    returns `404`.

18. **Join, open workspace.** In an `open` workspace, a non-member who calls
    `POST /api/channels/{id}/join` on a public channel gets `200` and is then listed in
    `GET /api/workspaces/{slug}/members` with `role` `"member"`.

19. **Join, private channel.** A non-member joining a private channel without an invitation
    gets `403`.

63. **Every node reports its own id.** `GET /api/health` returns `200` on ports `8000`, `8001`,
    and `8002`, with `node_id` `0`, `1`, and `2`.

70. **Auth validation.** Registering without a `password`, and registering with `{}`, each return
    `400`. Logging in as a username that does not exist returns `401`.

