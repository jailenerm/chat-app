"""
client.py - the chat client (what each user runs).

Run:  python3 client.py                  (server on this same computer)
      python3 client.py 192.168.1.20     (server on another computer)

Type a message and press Enter to send it to everyone.
Commands:
  /msg <name> <text>   private message to one person
  /list                see who is online
  /help                show the commands
  /quit                leave the chat
"""

import socket
import sys
import threading

from protocol import MessageReader, send_message as _send_message

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = 5050

# Two threads send on this socket (typing, and automatic read receipts),
# so a lock makes sure their messages never get mixed together.
send_lock = threading.Lock()


def send_message(sock, msg):
    with send_lock:
        _send_message(sock, msg)


HELP = """Commands:
  /msg <name> <text>   private message to one person
  /list                see who is online
  /help                show this help
  /quit                leave the chat
Anything else you type is sent to everyone."""


def show(msg, sock):
    """Turn a message from the server into a line on the screen."""
    kind = msg.get("type")
    t = msg.get("time", "")
    if kind == "public":
        print(f"[{t}] {msg['from']}: {msg['text']}")
    elif kind == "private":
        note = ", sent while you were offline" if msg.get("offline") else ""
        print(f"[{t}] (private{note}) {msg['from']} -> you: {msg['text']}")
        # We just showed it on screen, so tell the sender it was read
        send_message(sock, {"type": "read", "id": msg["id"]})
    elif kind == "delivered":
        print(f"  [delivered to {msg['to']}]")
    elif kind == "stored":
        print(f"  [{msg['to']} is offline. Saved, and will be delivered when they come back]")
    elif kind == "read_receipt":
        print(f"  [read by {msg['by']} at {t}]")
    elif kind == "users":
        print(f"Online ({len(msg['users'])}): {', '.join(msg['users'])}")
    elif kind == "user_joined":
        print(f"[{t}] *** {msg['username']} joined the chat ***")
    elif kind == "user_left":
        print(f"[{t}] *** {msg['username']} left the chat ***")
    elif kind == "error":
        print(f"! {msg['message']}")
    else:
        print(f"(unknown message from server: {msg})")


def receive_loop(reader, sock):
    """Runs in its own thread: prints messages as soon as they arrive."""
    while True:
        try:
            msg = reader.read_message()
        except (ValueError, OSError):
            msg = None
        if msg is None:
            print("Disconnected from the server. Press Enter to exit.")
            return
        try:
            show(msg, sock)
        except OSError:
            return


def join(sock, reader):
    """Keep asking for a username until the server accepts one."""
    while True:
        name = input("Pick a username: ").strip()
        send_message(sock, {"type": "join", "username": name})
        reply = reader.read_message()
        if reply is None:
            return None
        if reply["type"] == "joined":
            return reply
        print(f"! {reply.get('message', 'Try again')}")


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((HOST, PORT))
    except OSError as e:
        print(f"Could not connect to {HOST}:{PORT} ({e}). Is the server running?")
        return
    reader = MessageReader(sock)

    try:
        joined = join(sock, reader)
    except (KeyboardInterrupt, EOFError):
        sock.close()
        return
    if joined is None:
        print("The server closed the connection.")
        return
    print(f"Welcome, {joined['username']}! Online: {', '.join(joined['users'])}")
    if joined.get("waiting"):
        print(f"You have {joined['waiting']} message(s) from while you were away:")
    print("Type /help for commands.")

    receiver = threading.Thread(target=receive_loop, args=(reader, sock), daemon=True)
    receiver.start()

    try:
        while receiver.is_alive():
            text = input().strip()
            if not receiver.is_alive():
                break
            if not text:
                continue
            if text == "/quit":
                send_message(sock, {"type": "quit"})
                break
            elif text == "/help":
                print(HELP)
            elif text == "/list":
                send_message(sock, {"type": "list"})
            elif text.startswith("/msg"):
                parts = text.split(" ", 2)
                if len(parts) < 3:
                    print("Usage: /msg <name> <text>")
                else:
                    send_message(sock, {"type": "private", "to": parts[1], "text": parts[2]})
            elif text.startswith("/"):
                print("Unknown command. Type /help.")
            else:
                send_message(sock, {"type": "public", "text": text})
    except (KeyboardInterrupt, EOFError, OSError):
        pass
    finally:
        sock.close()
        print("Goodbye!")


if __name__ == "__main__":
    main()