# Roles.2: roles, ownership transfer, role-based permissions

Routes covered: `PATCH /api/workspaces/{slug}/members/{user_id}`, `POST /api/workspaces/{slug}/transfer_ownership`, and the guest/member rules on `POST /api/channels/{id}/join` and `POST /api/workspaces/{slug}/channels`.
Check: `bash /spec/roles/checks/check_roles_2.sh`. The rule numbers below match its output.

Do not start this spec until `check_roles_1.sh` prints `RESULT: 21 passed, 0 failed`.

Your service is launched with `/app/start.sh`. The three HTTP nodes listen on
`127.0.0.1:8000`, `:8001`, and `:8002`. State written through one node must be visible through
the other two.

Conventions for every rule: JSON in, JSON out; `Authorization: Bearer <token>`; a missing or
invalid token is `401`; an unknown id is `404`; every request must answer within 5 seconds.

## Uses

Call these functions from earlier specs. Do not write a second version of any of them.

| Function | Used here for |
|---|---|
| `user_from_token` | Every route here needs the caller |
| `get_member` | The actor's and the target's roles |
| `list_members` | Reading roles back, including from another node |
| `create_channel` | Extended with the guest/member/private permission rules |
| `join_channel` | Extended with the guest rule |

## Adds

| Function | What it does |
|---|---|
| `set_member_role(workspace, actor, target_id, role)` | Applies the role rules (who may change whom, no `owner` via PATCH) |
| `transfer_ownership(workspace, actor, new_owner_id)` | Owner-only; the target must be an `admin`; swaps `owner` and `admin` |
| `may_join_public_channel(member)` | False for a `guest` |
| `may_create_channel(member, is_private)` | Guests never; members only public; admins and owner both |

## Rules (20-40, 64, 65)

Role changes use `PATCH /api/workspaces/{slug}/members/{user_id}` with `{role}` and return `200` and `{member: {user_id, role, ...}}`.

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

64. **Unknown role.** `PATCH /api/workspaces/{slug}/members/{user_id}` with a `role` that is not
    one of `owner`, `admin`, `member`, `guest` (for example `"superuser"`) returns `400`.

65. **Unknown member.** The same PATCH for a user id that is not a member of the workspace
    returns `404`.

