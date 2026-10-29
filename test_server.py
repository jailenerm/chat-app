"""
test_server.py - checks the server with three fake clients.

1. Start the server in one terminal:   python3 server.py
2. Run this in another terminal:       python3 test_server.py

Each test prints what we EXPECT and whether it PASSED.
"""

import socket
import time

from protocol import MessageReader, send_message

HOST, PORT = "127.0.0.1", 5050
results = []


class TestClient:
    def __init__(self):
        self.sock = socket.create_connection((HOST, PORT))
        self.sock.settimeout(2)
        self.reader = MessageReader(self.sock)

    def send(self, msg):
        send_message(self.sock, msg)

    def next(self):
        """Next message, or None if nothing arrives within 2 seconds."""
        try:
            return self.reader.read_message()
        except socket.timeout:
            return None

    def next_of(self, kind):
        """Skip join/leave notices and return the next message of this type."""
        while True:
            msg = self.next()
            if msg is None or msg["type"] == kind:
                return msg

    def nothing_arrives(self):
        self.sock.settimeout(0.5)
        try:
            msg = self.reader.read_message()
        except socket.timeout:
            msg = None
        self.sock.settimeout(2)
        return msg is None or msg["type"] in ("user_joined", "user_left")


def check(name, expected, passed):
    results.append(bool(passed))
    print(f"{'PASS' if passed else 'FAIL'}  {name}\n      expected: {expected}")


# --- Three users join ---
jay, isha, sam = TestClient(), TestClient(), TestClient()
jay.send({"type": "join", "username": "Jay"})
isha.send({"type": "join", "username": "Isha"})
sam.send({"type": "join", "username": "Sam"})
ok = all(c.next_of("joined") for c in (jay, isha, sam))
check("Three users join", "Jay, Isha and Sam all get 'joined'", ok)

# --- Duplicate username ---
copycat = TestClient()
copycat.send({"type": "join", "username": "jay"})
reply = copycat.next()
check("Duplicate username is rejected", "error 'name_taken'",
      reply and reply.get("code") == "name_taken")
copycat.sock.close()

# --- Public message reaches all three ---
jay.send({"type": "public", "text": "Hi everyone!"})
got = [c.next_of("public") for c in (jay, isha, sam)]
check("Public message reaches everyone", "Jay, Isha and Sam all receive it",
      all(m and m["text"] == "Hi everyone!" and m["from"] == "Jay" for m in got))

# --- Private message reaches ONLY Isha ---
jay.send({"type": "private", "to": "Isha", "text": "Ready to present?"})
m = isha.next_of("private")
check("Private message reaches the recipient", "Isha receives it, from Jay",
      m and m["from"] == "Jay" and m["text"] == "Ready to present?")
d = jay.next_of("delivered")
check("Sender gets a delivery confirmation", "Jay gets 'delivered' to Isha",
      d and d["to"] == "Isha")
check("Third client does NOT get the private message", "Sam receives nothing",
      sam.nothing_arrives())

# --- Timestamps ---
check("Messages carry a server timestamp", "private message has a 'time' like '2:15 PM'",
      m and isinstance(m.get("time"), str) and m["time"].endswith(("AM", "PM")))

# --- Read receipts ---
sam.send({"type": "read", "id": m["id"]})        # Sam tries to mark Isha's message read
r = sam.next_of("error")
check("Only the recipient can send a read receipt", "Sam gets error 'unknown_message'",
      r and r["code"] == "unknown_message")
isha.send({"type": "read", "id": m["id"]})       # Isha really reads it
r = jay.next_of("read_receipt")
check("Sender gets a read receipt", "Jay is told 'read by Isha', with a time",
      r and r["by"] == "Isha" and r["id"] == m["id"] and r.get("time"))
isha.send({"type": "read", "id": m["id"]})       # reading it twice
r = isha.next_of("error")
check("A message can only be marked read once", "second 'read' gets 'unknown_message'",
      r and r["code"] == "unknown_message")

# --- Server decides the sender, not the client ---
sam.send({"type": "public", "from": "Jay", "text": "I am Jay, trust me"})
m = isha.next_of("public")
check("Client can't fake the sender", "message shows from 'Sam', not 'Jay'",
      m and m["from"] == "Sam")
jay.next_of("public"); sam.next_of("public")

# --- Unknown recipient ---
jay.send({"type": "private", "to": "Nobody", "text": "hello?"})
m = jay.next_of("error")
check("Private message to unknown user", "error 'unknown_recipient'",
      m and m["code"] == "unknown_recipient")

# --- Malformed request ---
jay.sock.sendall(b"this is not json\n")
m = jay.next_of("error")
check("Malformed message is rejected (server keeps running)", "error 'malformed'",
      m and m["code"] == "malformed")

# --- Rapid messages arrive complete and in order ---
start = time.perf_counter()
for i in range(100):
    jay.send({"type": "public", "text": f"msg {i}"})
texts = [isha.next_of("public")["text"] for _ in range(100)]
elapsed = time.perf_counter() - start
check("100 rapid messages arrive in order", "Isha gets msg 0 ... msg 99 in order",
      texts == [f"msg {i}" for i in range(100)])
for c in (jay, sam):
    for _ in range(100):
        c.next_of("public")
print(f"      (100 messages took {elapsed * 1000:.0f} ms)")

# --- Abrupt disconnect: Sam's window just closes ---
sam.sock.close()
m = jay.next_of("user_left")
jay.send({"type": "list"})
users = jay.next_of("users")
check("Abrupt disconnect keeps the online list correct",
      "Jay is told Sam left, and the list is [Isha, Jay]",
      m and m["username"] == "Sam" and users and users["users"] == ["Isha", "Jay"])

# --- Response time ---
times = []
for i in range(20):
    t0 = time.perf_counter()
    jay.send({"type": "private", "to": "Isha", "text": f"ping {i}"})
    jay.next_of("delivered")
    times.append((time.perf_counter() - t0) * 1000)
    isha.next_of("private")
print(f"\nResponse time (private message -> delivery confirmation), 20 tries:")
print(f"      average {sum(times) / len(times):.2f} ms, slowest {max(times):.2f} ms")

# --- Offline messages ---
isha.sock.close()
jay.next_of("user_left")
jay.send({"type": "private", "to": "Isha", "text": "Call me when you're back"})
st = jay.next_of("stored")
check("Message to an offline user is saved", "Jay gets 'stored' for Isha",
      st and st["to"] == "Isha")
jay.send({"type": "private", "to": "Ghost", "text": "hello?"})
e = jay.next_of("error")
check("Message to a name that never joined is rejected", "error 'unknown_recipient'",
      e and e["code"] == "unknown_recipient")
isha = TestClient()
isha.send({"type": "join", "username": "Isha"})
j = isha.next_of("joined")
m = isha.next_of("private")
check("Offline message is delivered when they come back",
      "Isha rejoins, is told 1 is waiting, and receives it marked offline",
      j and j.get("waiting") == 1 and m and m["text"] == "Call me when you're back"
      and m.get("offline") is True and m["from"] == "Jay")
isha.send({"type": "read", "id": m["id"]})
r = jay.next_of("read_receipt")
check("Read receipt works for offline messages", "Jay is told 'read by Isha'",
      r and r["by"] == "Isha" and r["id"] == m["id"])
isha.sock.close()
isha = TestClient()
isha.send({"type": "join", "username": "Isha"})
j = isha.next_of("joined")
check("Offline messages are delivered only once", "rejoining again: 0 waiting",
      j and j.get("waiting") == 0)

jay.sock.close()
isha.sock.close()
print(f"\n{sum(results)} of {len(results)} tests passed")