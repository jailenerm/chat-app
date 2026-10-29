"""
protocol.py - the shared message format for our chat app.

Rules:
  - Every message is a JSON object with a "type" field.
  - Every message ends with a newline (\n). This is our framing rule:
    the newline tells the receiver where one message stops.
  - TCP is a stream, so one recv() might give us half a message,
    or several messages stuck together. The buffer below handles both.
"""

import json

ENCODING = "utf-8"
MAX_MESSAGE_SIZE = 64 * 1024   # 64 KB; anything bigger is treated as malformed


def encode_message(msg: dict) -> bytes:
    """Turn a Python dict into bytes ready to send: JSON + newline."""
    return (json.dumps(msg) + "\n").encode(ENCODING)


def send_message(sock, msg: dict):
    """Send one message over a socket."""
    sock.sendall(encode_message(msg))


class MessageReader:
    """
    Reads complete messages from a socket, one at a time.

    It keeps leftover bytes in a buffer between reads, so:
      - partial messages wait until the rest arrives
      - multiple messages in one read are split apart
    """

    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""

    def read_message(self):
        """
        Returns the next message as a dict.
        Returns None if the connection closed.
        Raises ValueError if the message isn't valid JSON (malformed).
        """
        while b"\n" not in self.buffer:
            if len(self.buffer) > MAX_MESSAGE_SIZE:
                raise ValueError("message too large")
            chunk = self.sock.recv(4096)
            if not chunk:              # empty = the other side closed the connection
                return None
            self.buffer += chunk

        line, self.buffer = self.buffer.split(b"\n", 1)
        try:
            msg = json.loads(line.decode(ENCODING))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("not valid JSON")
        if not isinstance(msg, dict) or "type" not in msg:
            raise ValueError("missing 'type' field")
        return msg