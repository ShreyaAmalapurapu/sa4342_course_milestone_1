# Roles.3: invitations and join mode

Routes covered: `/api/workspaces/{slug}/invitations`, `POST /api/invitations/{code}/accept`, `PATCH /api/workspaces/{slug}`, and the `join_mode` behaviour of `POST /api/channels/{id}/join`.
Check: `bash /spec/roles/checks/check_roles_3.sh`. The rule numbers below match its output.

Do not start this spec until `check_roles_2.sh` prints `RESULT: 23 passed, 0 failed`.

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
| `get_member` | Admin/owner checks and membership after accept |
| `list_members` | Confirming a redeemed invitation made a `member` |
| `join_channel` | Extended: an `invite_only` workspace refuses non-members |

## Adds

| Function | What it does |
|---|---|
| `create_invitation(workspace, actor, options)` | Admin/owner only; validates `max_uses`; targeted invitations are single-use |
| `accept_invitation(code, user)` | Consumes one use; 404 for unknown, expired, exhausted, revoked; 403 for the wrong target |
| `revoke_invitation(workspace, actor, code)` | Admin/owner only; the code then returns 404 |
| `list_invitations(workspace, actor)` | Active invitations for admins and the owner |
| `update_workspace(workspace, actor, name, join_mode)` | Admin/owner only; `join_mode` is `open` or `invite_only` |

## Rules (41-62, 66-69)

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

66. **Invite by username.** An invitation created with `invited_username` set to user P's
    username can only be redeemed by P: another user gets `403`, P gets `200`.

67. **Targeted means single-use.** An invitation with `invited_user_id` set stays single-use even
    when `max_uses` is `5`: after the target accepts (`200`), accepting again returns `404`.

68. **max_uses validation.** `max_uses` of `0` and `max_uses` of `"abc"` each return `400` (never
    `500`).

69. **join_mode validation.** `PATCH /api/workspaces/{slug}` with `{join_mode: "closed"}` returns
    `400`.

