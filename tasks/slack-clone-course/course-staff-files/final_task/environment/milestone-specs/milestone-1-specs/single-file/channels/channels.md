# Channels and message history

Routes covered: channel management (`/api/workspaces/{slug}`, `/api/workspaces/{slug}/channels`,
`/api/channels/{id}` and its `leave`, `members`, `archive`, `unarchive`), messages
(`/api/channels/{id}/messages`, `/api/messages/{id}/replies`, pins, search), mentions, read state
(`/api/channels/{id}/read`), and the durable WebSocket events (`/api/ws`).
Check: `bash /spec/channels/checks/check_channels.sh`. The rule numbers below match its output.

Your service is launched with `/app/start.sh`. The three HTTP nodes listen on
`127.0.0.1:8000`, `:8001`, and `:8002`. State written through one node must be visible through
the other two. Component A (auth, workspaces, roles, invitations) is already built; these rules
use its routes for setup.

Conventions for every rule: JSON in, JSON out; `Authorization: Bearer <token>`; a missing or
invalid token is `401`; an unknown id is `404`; every request must answer within 5 seconds.
Timestamps are UTC strings such as `2026-05-16T08:07:34.123Z`.

## A. Channel management (rules 1 to 14)

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

## B. Messages and paging (rules 15 to 31)

Every list below returns `next_cursor`: an opaque string when more results exist, and `null` on
the last page. Do not make clients build or read cursors. A cursor issued by one node must work
on the other two.

15. **Post.** `POST /api/channels/{id}/messages` with `{body}` returns `201` and `{message}`.
    `message` has `id`, `channel_id`, `author_id`, `author`, `body`, `parent_id`, `reply_count`,
    `created_at`, `edited_at`, `files`, `reactions`, and `mentions`. For a new top-level
    message, `parent_id` and `edited_at` are `null`, `reply_count` is `0`, and `files`,
    `reactions`, and `mentions` are empty lists. `author_id` is the caller's user id.
16. **Empty body.** A `body` of `""` returns `400`.
17. **Reply.** Posting with `parent_id` set to a message id returns `201`, and the parent's
    `reply_count` becomes `1` (visible in `GET /api/channels/{id}/messages`).
18. **List, newest first.** `GET /api/channels/{id}/messages` returns `200` with `messages` and
    `next_cursor`, newest first.
19. **Paging.** With 5 top-level messages and `limit=2`, the first page has the 2 newest and a
    non-empty `next_cursor`; following the cursor gives the next 2, then the last 1 with
    `next_cursor` `null`. Together the pages contain every message once, in order.
20. **Cursor on another node.** A cursor from a page read on port `8000` returns the correct
    next page when used on port `8002`.
21. **Stable cursor.** A new message posted between page 1 and page 2 does not appear in page 2
    and does not shift it: the cursor resumes from the same position.
22. **Malformed cursor.** `cursor=not-a-real-cursor` returns `400`.
23. **Limit range.** `limit` of `0`, `201`, `-1`, and `abc` each return `400`. `limit=200` returns
    `200`.
24. **Default limit.** With 55 messages in a channel, a request with no `limit` returns exactly
    50 messages and a non-null `next_cursor`.
25. **Replies.** `GET /api/messages/{parent_id}/replies` returns `200` with `replies` and
    `next_cursor`, oldest first. With 3 replies and `limit=2`, the first page has the 2 oldest
    and a cursor, and the next page has the last 1 and `next_cursor` `null`.
26. **Search.** `GET /api/search?q=<text>` returns `200` with `results` and `next_cursor`, newest
    first. It finds messages containing the text, and it does not return messages from private
    channels the caller is not in.
27. **Search by workspace.** `workspace_id=<id>` limits the results to that workspace.
28. **Pin.** `POST /api/messages/{id}/pin` returns `200` and `{pin: {message_id, pinned_by,
    pinned_at}}`. Pinning the same message again also returns `200`, and the pin list still
    holds it once.
29. **Pin list.** `GET /api/channels/{id}/pins` returns `200` with `pins` and `next_cursor`.
    Each pin has `message`, `pinned_by`, and `pinned_at`. The list is newest-pinned first, and
    `limit` and `cursor` page through it.
30. **Unpin.** `DELETE /api/messages/{id}/pin` returns `200` and `{"unpinned": true}`, and the
    message is no longer in the pin list.
31. **Private pin list.** A workspace member who is not in a private channel gets `403` on
    `GET /api/channels/{id}/pins`; a member of the channel gets `200`.

## C. WebSocket events (rules 32 to 39)

Connect to `ws://127.0.0.1:<port>/api/ws?token=<bearer>` on port `8000`, `8001`, or `8002`.
Frames are JSON, one per WebSocket message. Send `{"type": "subscribe", "channel_id": <id>}`.

32. **Bad token.** A connection with an invalid token is rejected: the handshake fails or the
    connection is closed, and no frame is delivered.
33. **Subscribe.** After `subscribe`, the server immediately sends
    `{"type": "subscribed", "channel_id": <id>, "head_event_id": <int>}`.
34. **Message event.** After a top-level message is posted, a subscriber receives
    `{"type": "message.created", "event_id": <int>, "channel_id": <id>, ...}` where `event_id`
    is exactly `head_event_id + 1`.
35. **Reply event.** A thread reply produces `message.reply`, and its `event_id` is exactly one
    more than the previous event.
36. **Dense ids across nodes.** Twelve messages posted one after another, rotating through ports
    `8000`, `8001`, and `8002`, produce twelve `message.created` frames whose `event_id`s are
    consecutive: no gaps, no duplicates. A subscriber on `8001` sees all of them.
37. **Fan-out.** A subscriber connected to port `8002` receives a `message.created` frame within
    5 seconds of a message posted through port `8000`.
38. **Resume.** A client that was subscribed, disconnected at `head_event_id` H, and misses 3
    messages can connect to a different node and send
    `{"type": "resume", "channel_id": <id>, "since_event_id": H}`. It receives the 3 missed
    events in order (`event_id` H+1, H+2, H+3), then
    `{"type": "resumed", "channel_id": <id>, "head_event_id": H+3}`.
39. **Other durable kinds.** In one channel, in this order: a member joins, the topic changes,
    the member leaves, the channel is archived, the channel is unarchived. A subscriber receives
    `member.joined`, `channel.updated`, `member.left`, `channel.archived`, and
    `channel.unarchived`, and their `event_id`s are consecutive, each one more than the last.

## D. Mentions and read state (rules 40 to 51)

`mentions` on a message is the list of user ids that `@username` resolves to.

40. **Mention.** A message containing `@<username>` of a workspace member has that user's id in
    `mentions`.
41. **No in-word or email matches.** `hello@<username>` and `write to bob@<username>.com` give
    an empty `mentions`.
42. **No self-mention.** A message that mentions its own author gives an empty `mentions`.
43. **No duplicates.** Mentioning the same user three times lists the id once.
44. **Outsiders excluded.** Mentioning a user who is not a member of the channel's workspace
    gives an empty `mentions`.
45. **`@channel`.** A message containing `@channel` has `mentions` equal to every channel member
    except the author.
46. **Read state.** `GET /api/channels/{id}/read` returns `200` and
    `{read_state: {channel_id, last_read_event_id, unread_count, mention_count}}`.
47. **Unread count.** After another user posts 3 messages, the reader's `unread_count` is 3
    higher than before.
48. **Mention count.** After another user mentions the reader, the reader's `mention_count` is 1
    higher. After a user mentions themselves, their own `mention_count` does not change.
49. **Mark read.** `POST /api/channels/{id}/read` with `{last_read_event_id: X}` returns `200`
    and the updated `{read_state}` with `last_read_event_id` X, and a later `GET` shows X.
50. **Never backwards.** Posting a `last_read_event_id` lower than the current one returns the
    unchanged, higher value.
51. **Workspace detail.** `GET /api/workspaces/{slug}` includes a `read_state` that carries
    `last_read_event_id`, `unread_count`, and `mention_count`.

## E. Stricter edge cases (rules 52 to 66)

52. **Limit on every list.** `limit=0` and `limit=201` return `400` on
    `GET /api/messages/{parent_id}/replies`, `GET /api/channels/{id}/pins`, and `GET /api/search`,
    just as they do on the message list.
53. **Cursor on every list.** A malformed `cursor` (for example `!!!`) returns `400` on those
    same three routes.
54. **Search pages.** With 5 matching messages and `limit=2`, `GET /api/search` returns pages of
    2, 2, and 1 results (newest first), with a `next_cursor` on the first two and `null` on the
    last.
55. **Public pin list.** A workspace member who has not joined a public channel gets `200` and
    `pins` on `GET /api/channels/{id}/pins`. (Only private channels are closed to outsiders.)
56. **Mention without channel membership.** `@username` of a workspace member who is not in the
    channel is still listed in `mentions`. Only users outside the workspace are excluded.
57. **Mentions are fixed at post time.** `mentions` is computed when the message is posted. If a
    user joins the channel later, the message's `mentions` as returned by
    `GET /api/channels/{id}/messages` does not change.
58. **Own messages are read.** The author's own message does not increase the author's
    `unread_count`.
59. **Read clears unread.** After `POST /api/channels/{id}/read` with the head `last_read_event_id`,
    `unread_count` is `0`; one new message from another user makes it `1`.
60. **Workspace detail has real counts.** In `GET /api/workspaces/{slug}`, `read_state` reflects
    the caller's actual `unread_count` (not zeros) when the caller has unread messages.
61. **Leave is a no-op the second time.** Calling `POST /api/channels/{id}/leave` twice produces
    one `member.left` event, not two.
62. **Concurrent writes.** 30 messages posted at the same time through all three nodes (10 at
    once) all return `201`, and their `message.created` events carry 30 consecutive `event_id`s.
63. **Archived means read-only for every write.** In an archived channel, a thread reply, a topic
    change, and pinning a message each return `423`.
64. **Body validation.** A `body` of only whitespace, and a request with no `body`, each return
    `400`.
65. **Parent validation.** A reply whose `parent_id` is an unknown message, or a message in a
    different channel, returns `400` or `404` (not `201`).
66. **No description.** `POST /api/workspaces/{slug}/channels` with an extra `description` field
    returns `201` and `topic` stays `""`.
