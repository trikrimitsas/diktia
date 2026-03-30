import json
import struct
import socket

HEADER_FMT = "!I"
HEADER_SIZE = struct.calcsize(HEADER_FMT)


def send_message(sock: socket.socket, msg: dict) -> None:
    payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    header = struct.pack(HEADER_FMT, len(payload))
    sock.sendall(header + payload)


def recv_all(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed while receiving data")
        data += chunk
    return data


def recv_message(sock: socket.socket) -> dict:
    header = recv_all(sock, HEADER_SIZE)
    (length,) = struct.unpack(HEADER_FMT, header)
    payload = recv_all(sock, length)
    return json.loads(payload.decode("utf-8"))
