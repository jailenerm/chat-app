import socket
from protocol import MessageReader, encode_message

# socketpair() gives us two connected sockets, perfect for testing
a, b = socket.socketpair()
reader = MessageReader(b)

# Test 1: two messages arrive stuck together in one send
a.sendall(encode_message({"type": "public", "text": "first"}) +
          encode_message({"type": "public", "text": "second"}))
print("Test 1a:", reader.read_message())
print("Test 1b:", reader.read_message())

# Test 2: one message arrives in two halves
data = encode_message({"type": "private", "to": "Isha", "text": "hello"})
a.sendall(data[:10])
a.sendall(data[10:])
print("Test 2: ", reader.read_message())

# Test 3: a malformed (broken) message
a.sendall(b"this is not json\n")
try:
    reader.read_message()
except ValueError as e:
    print("Test 3:  caught malformed message ->", e)

# Test 4: connection closes
a.close()
print("Test 4: ", reader.read_message(), "(None means the connection closed)")