# Channels.2: messages, paging, search, pins

Routes covered: `/api/channels/{id}/messages`, `/api/messages/{parent_id}/replies`, `/api/search`, `/api/channels/{id}/pins`, `/api/messages/{id}/pin`.
Check: `bash /spec/channels/checks/check_channels_2.sh`. The rule numbers below match its output.

Do not start this spec until `check_channels_1.sh` prints `RESULT: 15 passed, 0 failed`.

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
| `get_member` | From Roles.1: workspace membership for search and pin visibility |
| `require_writable` | From Channels.1: posting, replying and pinning check it |
| `visible_channels` | From Channels.1: search only covers channels the caller can access |

## Adds

| Function | What it does |
|---|---|
| `post_message(channel, author, body, parent_id)` | Validates the body and the parent; stores the message |
| `encode_cursor(position) / decode_cursor(text)` | One opaque format shared by every node; `decode_cursor` raises the `400` answer |
| `paginate(query, limit, cursor)` | One helper for messages, replies, search and pins: validates `limit`, returns the page and `next_cursor` |
| `list_messages(channel, limit, cursor)` | Newest first, top-level messages |
| `list_replies(parent_id, limit, cursor)` | Oldest first |
| `search_messages(user, q, workspace_id, limit, cursor)` | Newest first, only accessible channels |
| `pin_message(message_id, user) / unpin_message(message_id)` | Pinning is idempotent |
| `list_pins(channel, user, limit, cursor)` | Newest pinned first; private channels closed to outsiders |

## Rules (15-31, 52-55, 63-65)

Every list below returns `next_cursor`: an opaque string when more results exist, and `null` on the last page. Do not make clients build or read cursors. A cursor issued by one node must work on the other two.

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

63. **Archived means read-only for every write.** In an archived channel, a thread reply, a topic
    change, and pinning a message each return `423`.

64. **Body validation.** A `body` of only whitespace, and a request with no `body`, each return
    `400`.

65. **Parent validation.** A reply whose `parent_id` is an unknown message, or a message in a
    different channel, returns `400` or `404` (not `201`).

