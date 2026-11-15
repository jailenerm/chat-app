"""
server.py - the chat server.

Run:  python3 server.py

How it works:
  - Every client gets its own thread (handle_client).
  - The server keeps a registry: username -> that user's connection.
  - registry_lock makes sure only one thread changes the registry at a time.
  - Each connection has its own send_lock, so two threads can never write
    to the same client at the same moment (messages can't get mixed up).
  - The server fills in "from" itself, using the registry. It never trusts
    a sender name typed by the client.
  - The server adds a "time" to every chat message, so everyone sees the
    same clock (timestamps).
  - Every private message gets an "id". When the recipient's client shows
    it, it sends back {"type": "read", "id": ...}, and the server passes a
    read receipt to the original sender.
  - Offline messages: if you send a private message to someone who has been
    online before but isn't right now, the server saves it in their mailbox
    and delivers it the next time they join with that username.
  - Known users and mailboxes are saved to chat_data.json, so saved messages
    survive the server being stopped and restarted.
"""

import json
import os
import socket
import threading
from datetime import datetime

from protocol import MessageReader, send_message, MAX_MESSAGE_SIZE

HOST = "0.0.0.0"
PORT = 5050

registry = {}                       # username -> Connection
registry_lock = threading.Lock()

known_users = {}    
mailboxes = {}     
MAX_OFFLINE = 50    
DATA_FILE = "chat_data.json"   

pending_reads = {}
next_message_id = 1
messages_lock = threading.Lock()


def now():
    return datetime.now().strftime("%I:%M %p").lstrip("0")


def save_data():
    data = {"known_users": known_users, "mailboxes": mailboxes,
            "next_message_id": next_message_id}
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DATA_FILE)


def load_data():
    global next_message_id
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log(f"Could not read {DATA_FILE} ({e}). Starting with no saved data.")
        return
    known_users.update(data.get("known_users", {}))
    mailboxes.update(data.get("mailboxes", {}))
    next_message_id = max(next_message_id, data.get("next_message_id", 1))
    waiting = sum(len(box) for box in mailboxes.values())
    log(f"Loaded {len(known_users)} known users and {waiting} saved offline message(s).")


def new_message_id():
    global next_message_id
    with messages_lock:
        msg_id = next_message_id
        next_message_id += 1
    return msg_id


def await_read(msg_id, sender_name, recipient_name):
    with messages_lock:
        pending_reads[msg_id] = (sender_name, recipient_name)


class Connection:

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.username = None
        self.send_lock = threading.Lock()

    def send(self, msg):
        try:
            with self.send_lock:
                send_message(self.sock, msg)
            return True
        except OSError:
            return False


def log(text):
    print(text, flush=True)


def register(conn, username):
    
    if not isinstance(username, str) or not username.strip():
        return "invalid_name", []
    username = username.strip()
    if len(username) > 20 or " " in username:
        return "invalid_name", []
    with registry_lock:
        if username.lower() in (name.lower() for name in registry):
            return "name_taken", []
        registry[username] = conn
        conn.username = username
        known_users[username.lower()] = username
        waiting = mailboxes.pop(username.lower(), [])
        save_data()
    return None, waiting


def unregister(conn):
    with registry_lock:
        if conn.username and registry.get(conn.username) is conn:
            del registry[conn.username]
            return True
    return False


def online_users():
    with registry_lock:
        return sorted(registry, key=str.lower)


def find_user(name):
    with registry_lock:
        for username, conn in registry.items():
            if username.lower() == str(name).lower():
                return conn
    return None


def broadcast(msg, exclude=None):
    with registry_lock:
        targets = [c for c in registry.values() if c is not exclude]
    delivered = 0
    for c in targets:             
        if c.send(msg):            
            delivered += 1
    return delivered


def error(conn, code, message):
    conn.send({"type": "error", "code": code, "message": message})



def handle_message(conn, msg):
    kind = msg.get("type")

    if kind == "public":
        text = msg.get("text")
        if not isinstance(text, str) or not text:
            error(conn, "malformed", "public messages need a 'text' field")
            return True
        delivered = broadcast({"type": "public", "from": conn.username,
                               "text": text, "time": now()})
        log(f"[public] {conn.username}: {text}  (delivered to {delivered})")

    elif kind == "private":
        to, text = msg.get("to"), msg.get("text")
        if not isinstance(to, str) or not isinstance(text, str) or not text:
            error(conn, "malformed", "private messages need 'to' and 'text' fields")
            return True
        msg_id = new_message_id()
        stored = None
        with registry_lock:
            target = next((c for n, c in registry.items() if n.lower() == to.lower()), None)
            if target is None and to.lower() in known_users:
                # They've been here before but are offline: save it for later
                box = mailboxes.setdefault(to.lower(), [])
                if len(box) < MAX_OFFLINE:
                    stored = {"type": "private", "id": msg_id, "from": conn.username,
                              "to": known_users[to.lower()], "text": text,
                              "time": now(), "offline": True}
                    box.append(stored)
                    save_data()
                else:
                    stored = "full"
        if target is None:
            if stored == "full":
                error(conn, "mailbox_full", f"{to}'s offline mailbox is full")
                log(f"[private #{msg_id}] {conn.username} -> {to}: FAILED (mailbox full)")
            elif stored:
                conn.send({"type": "stored", "id": msg_id, "to": stored["to"], "text": text})
                log(f"[private #{msg_id}] {conn.username} -> {stored['to']}: {text}  (saved, recipient offline)")
            else:
                error(conn, "unknown_recipient", f"No user named '{to}' has ever joined")
                log(f"[private] {conn.username} -> {to}: FAILED (unknown recipient)")
            return True
        if target is conn:
            error(conn, "malformed", "You can't send a private message to yourself")
            return True
        await_read(msg_id, conn.username, target.username)
        ok = target.send({"type": "private", "id": msg_id, "from": conn.username,
                          "to": target.username, "text": text, "time": now()})
        if ok:
            conn.send({"type": "delivered", "id": msg_id, "to": target.username,
                       "text": text, "time": now()})
            log(f"[private #{msg_id}] {conn.username} -> {target.username}: {text}  (delivered)")
        else:   # recipient left while we were sending
            with messages_lock:
                pending_reads.pop(msg_id, None)
            error(conn, "delivery_failed", f"'{to}' disconnected before the message arrived")
            log(f"[private #{msg_id}] {conn.username} -> {to}: FAILED (recipient disconnected)")

    elif kind == "read":
        msg_id = msg.get("id")
        with messages_lock:
            entry = pending_reads.get(msg_id)
            # only the real recipient can mark a message as read, and only once
            if entry and entry[1] == conn.username:
                del pending_reads[msg_id]
            else:
                entry = None
        if entry is None:
            error(conn, "unknown_message", f"No unread private message with id {msg_id}")
            return True
        sender_name = entry[0]
        sender = find_user(sender_name)
        if sender and sender.send({"type": "read_receipt", "id": msg_id,
                                   "by": conn.username, "time": now()}):
            log(f"[read #{msg_id}] {conn.username} read {sender_name}'s message")
        else:
            log(f"[read #{msg_id}] {conn.username} read {sender_name}'s message, but {sender_name} is offline")

    elif kind == "list":
        conn.send({"type": "users", "users": online_users()})

    elif kind == "quit":
        return False

    elif kind == "join":
        error(conn, "already_joined", "You have already joined")

    else:
        error(conn, "unknown_type", f"Unknown message type '{kind}'")

    return True


def handle_client(sock, addr):
    conn = Connection(sock, addr)
    reader = MessageReader(sock)
    log(f"[connect] {addr[0]}:{addr[1]}")
    try:
        while True:
            try:
                msg = reader.read_message()
            except ValueError as e:
                error(conn, "malformed", str(e))
                if len(reader.buffer) > MAX_MESSAGE_SIZE:
                    break          # someone is flooding us; drop them
                continue
            except OSError:
                break              # connection reset / broken
            if msg is None:
                break              # client closed the connection

            # Step 1: every client must join with a unique username first
            if conn.username is None:
                if msg.get("type") != "join":
                    error(conn, "not_joined", "Send a 'join' message with a username first")
                    continue
                problem, waiting = register(conn, msg.get("username"))
                if problem == "name_taken":
                    error(conn, "name_taken", f"'{msg.get('username')}' is already taken")
                elif problem == "invalid_name":
                    error(conn, "invalid_name", "Usernames must be 1-20 characters with no spaces")
                else:
                    conn.send({"type": "joined", "username": conn.username,
                               "users": online_users(), "waiting": len(waiting)})
                    broadcast({"type": "user_joined", "username": conn.username,
                               "time": now()}, exclude=conn)
                    log(f"[join] {conn.username} (online: {', '.join(online_users())})")
                    for saved in waiting:      # deliver offline messages
                        await_read(saved["id"], saved["from"], conn.username)
                        conn.send(saved)
                        log(f"[private #{saved['id']}] {saved['from']} -> {conn.username}: "
                            f"{saved['text']}  (delivered from mailbox)")
                continue

            # Step 2: normal chat messages
            if not handle_message(conn, msg):
                break
    finally:
        with messages_lock:  
            for mid in [m for m, (snd, rcp) in pending_reads.items()
                        if rcp == conn.username]:
                del pending_reads[mid]
        if unregister(conn):
            broadcast({"type": "user_left", "username": conn.username, "time": now()})
            log(f"[leave] {conn.username} (online: {', '.join(online_users()) or 'nobody'})")
        try:
            sock.close()
        except OSError:
            pass


def main():
    load_data()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()
    log(f"Chat server running on port {PORT}. Press Ctrl+C to stop.")
    try:
        while True:
            sock, addr = server.accept()
            threading.Thread(target=handle_client, args=(sock, addr), daemon=True).start()
    except KeyboardInterrupt:
        log("\nServer stopped.")
    finally:
        server.close()


if __name__ == "__main__":
    main()