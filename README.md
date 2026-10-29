# Real-Time Multi-User Chat App

A command-line chat application in Python using TCP sockets. Many users connect to one server and can send **public messages** to everyone or **private messages** to one user.

## Features

- Multiple users connected at the same time (one thread per client)
- Public messages to everyone
- Private messages to one user, with a delivery confirmation
- **Timestamps** on every message, added by the server
- **Read receipts** for private messages
- **Offline messages:** private messages to someone who is offline are saved and delivered when they come back
- Unique usernames (duplicates are rejected, case-insensitive)
- Online user list (`/list`)
- Join and leave notifications
- Safe handling of abrupt disconnects: the online list always stays correct
- Clear errors for unknown recipients and malformed messages

## Files

| File | What it does |
|---|---|
| `server.py` | The chat server. Tracks users and routes every message |
| `client.py` | The chat program each user runs |
| `protocol.py` | Shared message format used by both server and client |
| `test_protocol.py` | Tests the message framing (partial reads, combined messages, bad JSON) |
| `test_server.py` | Automated three-client test of the whole system |

## How to run

Requires Python 3 (tested on Python 3.13). No extra packages are needed.

1. Start the server:
   ```
   python3 server.py
   ```
2. In separate terminals, start each client:
   ```
   python3 client.py
   ```
   To connect from another computer on the same network:
   ```
   python3 client.py <server-ip-address>
   ```
   On a Mac, find the server's address with `ipconfig getifaddr en0`.
3. Pick a username and start chatting.

The server uses **port 5050**. Port 5000 is often taken by AirPlay Receiver on macOS.

## Commands

| Command | What it does |
|---|---|
| *(any text)* | Send a public message to everyone |
| `/msg <name> <text>` | Send a private message to one user |
| `/list` | Show who is online |
| `/help` | Show the commands |
| `/quit` | Leave the chat |

## Protocol

Every message is a JSON object with a `type` field, and **each message ends with a newline** (newline-delimited JSON). The receiver buffers incoming data, so partial reads and multiple messages arriving in one read are handled correctly.

**Client → Server**
```json
{"type": "join", "username": "Jay"}
{"type": "public", "text": "Hi everyone!"}
{"type": "private", "to": "Isha", "text": "Ready to present?"}
{"type": "read", "id": 7}
{"type": "list"}
{"type": "quit"}
```

**Server → Client**
```json
{"type": "joined", "username": "Isha", "users": ["Isha", "Jay"], "waiting": 1}
{"type": "public", "from": "Jay", "text": "Hi everyone!", "time": "2:15 PM"}
{"type": "private", "id": 7, "from": "Jay", "to": "Isha", "text": "Ready?", "time": "2:16 PM"}
{"type": "delivered", "id": 7, "to": "Isha", "text": "Ready?"}
{"type": "stored", "id": 9, "to": "Isha", "text": "Call me"}
{"type": "read_receipt", "id": 7, "by": "Isha", "time": "2:16 PM"}
{"type": "users", "users": ["Isha", "Jay", "Sam"]}
{"type": "user_joined", "username": "Sam", "time": "2:17 PM"}
{"type": "user_left", "username": "Sam", "time": "2:20 PM"}
{"type": "error", "code": "unknown_recipient", "message": "No user named 'Bob' has ever joined"}
```

**The server sets the `from` field** using its own record of which connection belongs to which user. Any sender name supplied by a client is ignored, so users cannot impersonate each other.

## How the newer features work

**Timestamps:** the server adds a `time` field to every message just before forwarding it, so everyone sees one consistent clock.

**Read receipts:** the server gives each private message a unique `id` and remembers who sent it. When the recipient's client shows the message, it automatically sends `{"type": "read", "id": 7}`. The server checks that this user really is the recipient and that the message hasn't already been marked read, then sends a `read_receipt` to the sender.

**Offline messages:** the server remembers every username that has ever joined. A private message to one of those users while they're offline is saved in their mailbox, and the sender gets `stored`. When someone joins with that name again, the saved messages are delivered right away, marked `"offline": true`. A name that has never joined still returns `unknown_recipient`, so typos are reported. Each mailbox holds at most 50 messages.

## Error handling

| Situation | Server response |
|---|---|
| Username already taken | `name_taken`, and the client is asked to pick another |
| Invalid username (empty, spaces, over 20 characters) | `invalid_name` |
| Private message to a name that has never joined | `unknown_recipient` |
| Private message to a known user who is offline | saved, and the sender gets `stored` |
| Offline mailbox is full (50 messages) | `mailbox_full` |
| Recipient disconnects while a message is being sent | `delivery_failed` |
| Read receipt from someone who isn't the recipient, or sent twice | `unknown_message` |
| Message isn't valid JSON or is missing fields | `malformed`, and the server keeps running |
| Unknown message type | `unknown_type` |
| Chat message sent before joining | `not_joined` |

## Concurrency

- Each client is handled by its **own thread**, so the server can receive from many users at once.
- The shared user registry is protected by a **lock** (`registry_lock`), so two threads can never change it at the same time. This is also how two users choosing the same name at the same moment is resolved: only the first one succeeds.
- Each connection has its **own send lock**, so if two threads send to the same user at once, their messages are written one after another and never mixed together.
- A separate **message lock** protects the message-id counter and the table of messages waiting for read receipts.
- Checking whether a user is online and **saving to their offline mailbox happen together under the registry lock**, so a user can't come online in between the check and the save.
- The **client** also uses a send lock, because it sends from two threads: what the user types, and automatic read receipts.
- Messages are sent **outside** the registry lock, so one slow client cannot freeze the server.
- A `finally` block removes a user however they leave (`/quit`, closed window, crash, or lost network), so the online list stays consistent.

## Testing

With the server running, run:
```
python3 test_server.py
```
It connects three clients and checks:

- three users can join, and duplicate usernames are rejected
- a public message reaches all three clients
- a private message reaches only its recipient, **and the third client does not receive it**
- the sender gets a delivery confirmation
- messages carry a server timestamp
- read receipts reach the sender, only the real recipient can send one, and only once
- a client cannot fake the sender name
- unknown recipients and malformed messages return errors
- 100 rapid messages arrive complete and in order
- an abrupt disconnect keeps the online list correct
- messages to an offline user are saved, delivered when they rejoin, and delivered only once
- response time (private message → delivery confirmation)

**Result:** 20 of 20 tests passed. Average response time was about 0.17 ms on localhost.

To save the server's delivery log as a record:
```
python3 server.py | tee server_log.txt
```

## Limitations

- **Not encrypted.** Messages travel as plain text, so private messages are hidden from other chat users but not from someone watching the network.
- **Usernames are labels, not accounts.** There's no password, so anyone who joins with a username receives that username's offline messages.
- **Offline mailboxes are kept in memory.** Saved messages are lost if the server stops or restarts.
- Runs on a **local network**. Using it over the internet would need a public server or port forwarding.

## Planned extensions

- Simple passwords, so only the real owner of a username gets its offline messages
- Saving mailboxes to a file, so they survive a server restart
- TLS encryption using Python's `ssl` module

## Team

BY: Isha Khan and Jailene Rodriguez Medina