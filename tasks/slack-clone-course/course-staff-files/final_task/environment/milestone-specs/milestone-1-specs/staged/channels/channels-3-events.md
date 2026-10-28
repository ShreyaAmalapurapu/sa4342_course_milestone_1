# Channels.3: WebSocket events

Routes covered: `ws://127.0.0.1:<port>/api/ws?token=<bearer>`.
Check: `bash /spec/channels/checks/check_channels_3.sh`. The rule numbers below match its output.

Do not start this spec until `check_channels_2.sh` prints `RESULT: 24 passed, 0 failed`.

Your service is launched with `/app/start.sh`. The three HTTP nodes listen on
`127.0.0.1:8000`, `:8001`, and `:8002`. State written through one node must be visible through
the other two.

Conventions for every rule: JSON in, JSON out; `Authorization: Bearer <token>`; a missing or
invalid token is `401`; an unknown id is `404`; every request must answer within 5 seconds.

## Uses

Call these functions from earlier specs. Do not write a second version of any of them.

| Function | Used here for |
|---|---|
| `user_from_token` | Authenticates the WebSocket handshake |
| `post_message, join_channel, leave_channel, update_channel_topic, set_archived` | From Roles.1, Channels.1 and Channels.2: each now also calls `append_event` in the same step as its change |

## Adds

| Function | What it does |
|---|---|
| `append_event(channel_id, kind, payload)` | The one place that gives an event its `event_id`: a shared per-channel counter that is atomic across all three nodes |
| `events_since(channel_id, since_event_id)` | Stored events in order, for `resume` |
| `ws_endpoint(request)` | Upgrade, auth (reject a bad token), `subscribe`, `resume` |
| `broadcast(channel_id, event)` | Delivers an event to subscribers on every node |

## Rules (32-39, 61, 62)

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

61. **Leave is a no-op the second time.** Calling `POST /api/channels/{id}/leave` twice produces
    one `member.left` event, not two.

62. **Concurrent writes.** 30 messages posted at the same time through all three nodes (10 at
    once) all return `201`, and their `message.created` events carry 30 consecutive `event_id`s.

