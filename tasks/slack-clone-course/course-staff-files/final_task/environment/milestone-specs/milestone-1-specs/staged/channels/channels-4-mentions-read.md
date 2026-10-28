# Channels.4: mentions and read state

Routes covered: `POST /api/channels/{id}/messages` (the `mentions` field), `GET` and `POST /api/channels/{id}/read`, `GET /api/workspaces/{slug}` (`read_state`).
Check: `bash /spec/channels/checks/check_channels_4.sh`. The rule numbers below match its output.

Do not start this spec until `check_channels_3.sh` prints `RESULT: 10 passed, 0 failed`.

Your service is launched with `/app/start.sh`. The three HTTP nodes listen on
`127.0.0.1:8000`, `:8001`, and `:8002`. State written through one node must be visible through
the other two.

Conventions for every rule: JSON in, JSON out; `Authorization: Bearer <token>`; a missing or
invalid token is `401`; an unknown id is `404`; every request must answer within 5 seconds.

## Uses

Call these functions from earlier specs. Do not write a second version of any of them.

| Function | Used here for |
|---|---|
| `post_message` | From Channels.2: extended to store `mentions` when the message is created |
| `append_event / events_since` | From Channels.3: `last_read_event_id` and `unread_count` are measured in event ids |
| `list_channel_members` | From Channels.1: `@channel` |
| `user_from_token, get_member` | From Roles.1 |

## Adds

| Function | What it does |
|---|---|
| `resolve_mentions(channel, author, body)` | Usernames in the workspace, `@channel`; no in-word matches, no self, no duplicates |
| `get_read_state(channel, user)` | `last_read_event_id`, `unread_count`, `mention_count` |
| `mark_read(channel, user, last_read_event_id)` | Never moves backwards |
| `workspace_read_state(workspace, user)` | The same fields for the workspace detail |

## Rules (40-51, 56-60)

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

