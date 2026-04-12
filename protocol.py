import json
import struct
import socket

HEADER_FMT = "!I"
HEADER_SIZE = struct.calcsize(HEADER_FMT)


def send_message(sock: socket.socket, msg: dict) -> None:
    # check if msg is in dict format
    if not isinstance(msg, dict):
        raise TypeError("Message must be a dictionary")

    # convert to json
    try:
        # support non-ascii characters
        json_payload = json.dumps(msg, ensure_ascii=False).encode("utf-8") 
    except TypeError as e:
        # if msg cannot be converted to json
        raise TypeError(f"Message must be JSON serializable: {e}")
    # create header with message length
    header = struct.pack(HEADER_FMT, len(json_payload))
    full_msg = header + json_payload
    sock.sendall(full_msg)

# helper to decode msg from bytes and json to dict
def decode_msg(payload):
    try:
        msg = json.loads(payload.decode("utf-8"))
        return msg
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to decode JSON message: {e}")
    except UnicodeDecodeError as e:
        raise ValueError(f"Failed to decode message payload as UTF-8: {e}")
    
def recv_all(sock: socket.socket, n: int) -> bytes:
    # reads as many bytes as we expect, to unpack the header and payload correctly
    # used x2 in rcv_msg: once for header and once for payload after message length is known
    buffer = b""
    # keep recieving until we have the complete message
    while len(buffer) < n:
        remaining = n - len(buffer)
        chunk = sock.recv(remaining)

        # handle connection loss in the middle of communication
        if not chunk:
            raise ConnectionError("Socket connection lost before completing message reception")
        buffer += chunk
    return buffer


def recv_message(sock: socket.socket) -> dict:
    # first recieve header to find out the length of the incoming message
    header = recv_all(sock, HEADER_SIZE)
    # unpack header and take 1st element of the tuple
    msg_len = struct.unpack(HEADER_FMT, header)[0] 
    payload = recv_all(sock, msg_len)
    msg = decode_msg(payload)
    return msg
