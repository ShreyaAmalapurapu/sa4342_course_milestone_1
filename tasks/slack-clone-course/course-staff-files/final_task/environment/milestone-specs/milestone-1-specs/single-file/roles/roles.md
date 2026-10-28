# Roles and invitations: auth, workspaces, roles, invitations, join mode

Routes covered: `/api/auth/*`, `/api/workspaces` (create, list, members, PATCH, role changes,
`transfer_ownership`, invitations), `POST /api/invitations/{code}/accept`,
`POST /api/channels/{id}/join`, and `POST /api/workspaces/{slug}/channels` (only for the role
rules).
Check: `bash /spec/roles/checks/check_roles.sh`. The rule numbers below match its output.

Your service is launched with `/app/start.sh`. The three HTTP nodes listen on
`127.0.0.1:8000`, `:8001`, and `:8002`. State written through one node must be visible through
the other two.

Terms: **owner**, **admin**, **member**, **guest** are the four workspace roles. "Non-member"
means a registered user who is not in the workspace.

Conventions for every rule: JSON in, JSON out; `Authorization: Bearer <token>`; a missing or
invalid token is `401`; every request must answer within 5 seconds.

## A. Foundations: auth, workspaces, joining (rules 1 to 19)

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

## B. Roles (rules 20 to 40)

Role changes use `PATCH /api/workspaces/{slug}/members/{user_id}` with `{role}` and return
`200` and `{member: {user_id, role, ...}}`.

20. **Owner promotes.** The owner sets a member to `admin`: `200`, and `member.role` is
    `"admin"`.
21. **Owner demotes.** The owner sets an admin to `member`: `200`, and `member.role` is
    `"member"`. The owner sets a member to `guest`: `200`, and `member.role` is `"guest"`.
22. **Change persists.** After rules 20 and 21, `GET /api/workspaces/{slug}/members` read from
    another node shows the new roles.
23. **Admin promotes.** An admin sets a member to `admin`: `200`.
24. **Admin cannot modify an admin.** An admin changing another admin's role returns `403`.
25. **Admin cannot modify the owner.** An admin changing the owner's role returns `403`.
26. **Owner's own role.** The owner changing their own role (to anything) returns `403`.
27. **No owner via PATCH.** Setting `role` to `"owner"` with PATCH returns `400`.
28. **Member cannot change roles.** A `member` calling the role PATCH returns `403`.
29. **Guest cannot change roles.** A `guest` calling the role PATCH returns `403`.
30. **Transfer, admin caller.** `POST /api/workspaces/{slug}/transfer_ownership` with
    `{new_owner_id}` called by an admin returns `403` (owner-only).
31. **Transfer, target not admin.** The owner transferring to a `member` returns `400`.
32. **Transfer.** The owner transferring to an `admin` returns `200`.
33. **Transfer result.** Afterwards the target's role is `"owner"`, the previous owner's role
    is `"admin"`, and `workspace.owner_id` (from `GET /api/workspaces/{slug}`) is the new
    owner's id.
34. **Old owner cannot transfer again.** The previous owner (now `admin`) calling
    `transfer_ownership` returns `403`.
35. **Guest cannot join.** A `guest` calling `POST /api/channels/{id}/join` on a public channel
    returns `403`.
36. **Guest cannot create.** A `guest` calling `POST /api/workspaces/{slug}/channels` returns
    `403`.
37. **Member creates public.** A `member` creating a public channel
    (`{name}`) returns `201`.
38. **Member cannot create private.** A `member` creating `{name, is_private: true}` returns
    `403`.
39. **Admin creates private.** An `admin` creating a private channel returns `201`.
40. **Owner creates private.** The owner creating a private channel returns `201`.

## C. Invitations (rules 41 to 55)

`POST /api/workspaces/{slug}/invitations` takes
`{expires_in_seconds?, max_uses?, invited_username?, invited_user_id?}` and returns
`201 {invitation}` where `invitation.code` is a non-empty string.

41. **Owner creates.** The owner creating an invitation returns `201` with a `code`.
42. **Admin creates.** An admin creating an invitation returns `201`.
43. **Others cannot create.** A `member` and a `guest` creating an invitation each return
    `403`.
44. **Default is one use.** With no `max_uses`, the first `POST /api/invitations/{code}/accept`
    by a non-member returns `200`, and the second accept by a different non-member returns
    `404`.
45. **max_uses.** An invitation created with `max_uses: 2` can be accepted by two different
    non-members (`200` each); a third gets `404`.
46. **Accept adds a member.** After a successful accept, the user appears in
    `GET /api/workspaces/{slug}/members` with `role` `"member"`.
47. **Unknown code.** Accepting a code that does not exist returns `404`.
48. **Expired.** An invitation created with `expires_in_seconds: 1` returns `404` when accepted
    after 2 seconds.
49. **Revoke.** `DELETE /api/workspaces/{slug}/invitations/{code}` by the owner returns `200`
    and `{"deleted": true}`. Accepting the revoked code afterwards returns `404`.
50. **Targeted.** For an invitation created with `invited_user_id` set to user P, accepting it
    as a different user Q returns `403`; accepting it as P returns `200`.
51. **Targeted is single-use.** After P has accepted, accepting again returns `404`.
52. **List.** `GET /api/workspaces/{slug}/invitations` by the owner or an admin returns `200`
    and `invitations`. It contains an active code and does not contain a revoked code.
53. **List is admin-only.** The same request by a `member` returns `403`.
54. **Revoke is admin-only.** `DELETE .../invitations/{code}` by a `member` returns `403`.
55. **Accept needs auth.** Accepting with no token returns `401`.

## D. Join mode (rules 56 to 62)

56. **Set join mode.** `PATCH /api/workspaces/{slug}` with `{join_mode: "invite_only"}` by the
    owner returns `200` and `workspace.join_mode` is `"invite_only"`.
57. **Rename.** `PATCH /api/workspaces/{slug}` with `{name}` by an admin returns `200` and
    `workspace.name` is the new name.
58. **PATCH is admin-only.** The same PATCH by a `member` returns `403`.
59. **Members still join.** In an `invite_only` workspace, an existing member calling
    `POST /api/channels/{id}/join` on another public channel returns `200`.
60. **Non-members blocked.** In an `invite_only` workspace, a non-member calling
    `POST /api/channels/{id}/join` on a public channel returns `403`, and is not added to the
    members list.
61. **Redeem then join.** After that user accepts a valid invitation, the same join returns
    `200`.
62. **Back to open.** After `join_mode` is set back to `"open"`, a different non-member joining
    a public channel returns `200` and appears in the members list as `"member"`.

## E. Cluster baseline

63. **Every node reports its own id.** `GET /api/health` returns `200` on ports `8000`, `8001`,
    and `8002`, with `node_id` `0`, `1`, and `2`.

## F. Stricter edge cases (rules 64 to 70)

64. **Unknown role.** `PATCH /api/workspaces/{slug}/members/{user_id}` with a `role` that is not
    one of `owner`, `admin`, `member`, `guest` (for example `"superuser"`) returns `400`.
65. **Unknown member.** The same PATCH for a user id that is not a member of the workspace
    returns `404`.
66. **Invite by username.** An invitation created with `invited_username` set to user P's
    username can only be redeemed by P: another user gets `403`, P gets `200`.
67. **Targeted means single-use.** An invitation with `invited_user_id` set stays single-use even
    when `max_uses` is `5`: after the target accepts (`200`), accepting again returns `404`.
68. **max_uses validation.** `max_uses` of `0` and `max_uses` of `"abc"` each return `400` (never
    `500`).
69. **join_mode validation.** `PATCH /api/workspaces/{slug}` with `{join_mode: "closed"}` returns
    `400`.
70. **Auth validation.** Registering without a `password`, and registering with `{}`, each return
    `400`. Logging in as a username that does not exist returns `401`.
