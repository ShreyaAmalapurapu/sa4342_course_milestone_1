# Channels.1: channel management

Routes covered: `POST /api/workspaces/{slug}/channels`, `GET /api/workspaces/{slug}`, `PATCH /api/channels/{id}`, `leave`, `members`, `archive`, `unarchive`.
Check: `bash /spec/channels/checks/check_channels_1.sh`. The rule numbers below match its output.

Your service is launched with `/app/start.sh`. The three HTTP nodes listen on
`127.0.0.1:8000`, `:8001`, and `:8002`. State written through one node must be visible through
the other two.

Conventions for every rule: JSON in, JSON out; `Authorization: Bearer <token>`; a missing or
invalid token is `401`; an unknown id is `404`; every request must answer within 5 seconds.

## Uses

Call these functions from earlier specs. Do not write a second version of any of them.

| Function | Used here for |
|---|---|
| `user_from_token` | From Roles.1 |
| `get_member` | From Roles.1: the workspace owner check |
| `create_channel` | From Roles.1: extended with name, topic and duplicate checks |
| `join_channel` | From Roles.1 |

## Adds

| Function | What it does |
|---|---|
| `visible_channels(workspace, user, include_archived)` | Public channels, private ones the user is in, archived ones only on request |
| `update_channel_topic(channel, actor, topic)` | Channel member or workspace owner; 250 character limit |
| `leave_channel(channel, user)` | Idempotent |
| `list_channel_members(channel)` | Full user objects |
| `set_archived(channel, actor, flag)` | Workspace owner only |
| `require_writable(channel)` | Raises the `423` answer for an archived channel; every later write calls it |

## Rules (1-14, 66)

1. **Create.** `POST /api/workspaces/{slug}/channels` with `{name}` returns `201` and
   `{channel}`. `channel` has `id`, `workspace_id`, `name`, `is_private`, `is_dm`, `topic`, and
   `is_archived`. A new channel has `topic` `""`, `is_private` false, `is_dm` false, and
   `is_archived` false.

2. **Topic length at create.** A `topic` of 250 characters returns `201`; 251 characters
   returns `400`.

3. **Invalid names.** A name with uppercase letters, a name with a space, and a 33-character
   name each return `400`.

4. **Duplicate name.** A name already used in the workspace returns `409`. The same name in a
   different workspace returns `201`.

5. **Workspace detail.** `GET /api/workspaces/{slug}` returns `200` with `workspace`,
   `channels`, and `read_state`. `channels` includes `general`.

6. **Private channels are hidden.** A private channel does not appear in `channels` for a
   workspace member who is not in it, and does appear for a member who is.

7. **Archived channels.** An archived channel is left out of `channels` by default. With
   `?include_archived=1` it is included. `?include_archived=true` also includes it.

8. **Update topic.** `PATCH /api/channels/{id}` with `{topic}` returns `200` and `{channel}`
   with the new topic when the caller is a channel member, and also when the caller is the
   workspace owner without being a channel member. Any other workspace member gets `403`. A
   topic over 250 characters returns `400`.

9. **Leave.** `POST /api/channels/{id}/leave` returns `200` and `{channel}`. Calling it a
   second time also returns `200`.

10. **Leaving removes the member.** After leaving, the user is no longer listed by
    `GET /api/channels/{id}/members`.

11. **Members.** `GET /api/channels/{id}/members` returns `200` and `{members}`. Each member
    has `id`, `username`, `display_name`, `timezone`, `avatar_url`, `status_text`, and
    `status_emoji`.

12. **Archive is owner-only.** `POST /api/channels/{id}/archive` by the workspace owner returns
    `200` with `channel.is_archived` true. By another member it returns `403`, and so does
    `unarchive`.

13. **Archived channels are read-only.** Posting a message to an archived channel returns
    `423`. After the owner calls `POST /api/channels/{id}/unarchive` (`200`), posting returns
    `201` again.

14. **Unknown channel and auth.** An unknown channel id returns `404` on `members`, `archive`,
    and `PATCH`. `members` with no token returns `401`.

66. **No description.** `POST /api/workspaces/{slug}/channels` with an extra `description` field
    returns `201` and `topic` stays `""`.

