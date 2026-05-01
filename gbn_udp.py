"""
Go-Back-N over UDP for Phase 2 P2P metadata transfer.

64-byte datagrams: 4-byte big-endian seq + 60-byte body.
Data seq: 0 .. n-1 where n = ceil(|file| / 60) (n=0 if file empty).
EOT seq: n, body = uint32_be(|file|) + zero padding.

Cumulative ACK: 4-byte big-endian "next expected seq" (receiver sends k when
it has received all packets with seq < k).
"""
from __future__ import annotations

import random
import socket
import struct
import threading
import time
from typing import Callable, Optional

PAYLOAD_SIZE = 64
SEQ_FMT = "!I"
SEQ_SIZE = 4
DATA_BYTES = PAYLOAD_SIZE - SEQ_SIZE


def _num_data_chunks(file_len: int) -> int:
    if file_len <= 0:
        return 0
    return (file_len + DATA_BYTES - 1) // DATA_BYTES


def packetize_metadata(metadata: bytes) -> list[bytes]:
    n = _num_data_chunks(len(metadata))
    out: list[bytes] = []
    for seq in range(n):
        i = seq * DATA_BYTES
        chunk = metadata[i : i + DATA_BYTES]
        body = chunk.ljust(DATA_BYTES, b"\x00")
        out.append(struct.pack(SEQ_FMT, seq) + body)
    eot_body = struct.pack("!I", len(metadata)).ljust(DATA_BYTES, b"\x00")
    out.append(struct.pack(SEQ_FMT, n) + eot_body)
    return out


def parse_ack(data: bytes) -> Optional[int]:
    if len(data) < SEQ_SIZE:
        return None
    return struct.unpack(SEQ_FMT, data[:SEQ_SIZE])[0]


def build_ack(next_expected: int) -> bytes:
    return struct.pack(SEQ_FMT, next_expected)


def _is_eot(seq: int, body: bytes) -> bool:
    if len(body) < 4:
        return False
    total = struct.unpack("!I", body[:4])[0]
    if body[4:].strip(b"\x00") != b"":
        return False
    n_data = _num_data_chunks(total)
    return seq == n_data


class GbnUdpSender:
    def __init__(
        self,
        sock: socket.socket,
        dest: tuple,
        packets: list[bytes],
        *,
        window: int = 3,
        timeout_sec: float = 2.0,
        log: Optional[Callable[[str], None]] = None,
        time_fn: Callable[[], float] = time.time,
        close_socket: bool = False,
    ):
        self.sock = sock
        self.dest = dest
        self.packets = packets
        self.window = window
        self.timeout_sec = timeout_sec
        self.log = log or (lambda _m: None)
        self.time_fn = time_fn
        self.close_socket = close_socket

    def run(self) -> bool:
        if not self.packets:
            return True
        num_packets = len(self.packets)
        base = 0
        next_seq = 0
        lock = threading.Lock()
        ack_next = 0
        timer_started = None

        def recv_loop():
            nonlocal ack_next
            while True:
                try:
                    data, _ = self.sock.recvfrom(2048)
                except OSError:
                    return
                v = parse_ack(data)
                if v is None:
                    continue
                with lock:
                    if v > ack_next:
                        ack_next = v
                        self.log("GBN sender: ACK cumulative next_expected=%d" % ack_next)

        t = threading.Thread(target=recv_loop, daemon=True)
        t.start()
        try:
            while base < num_packets:
                with lock:
                    observed_ack = ack_next
                if observed_ack > base:
                    base = min(observed_ack, num_packets)
                    if base >= num_packets:
                        return True
                    if base == next_seq:
                        timer_started = None
                    else:
                        timer_started = self.time_fn()

                while next_seq < min(base + self.window, num_packets):
                    self.sock.sendto(self.packets[next_seq], self.dest)
                    if next_seq == num_packets - 1:
                        self.log("GBN sender: SEND seq=%d (EOT)" % next_seq)
                    else:
                        self.log("GBN sender: SEND seq=%d" % next_seq)
                    if base == next_seq:
                        timer_started = self.time_fn()
                    next_seq += 1

                if timer_started is None:
                    time.sleep(0.01)
                    continue

                if self.time_fn() - timer_started < self.timeout_sec:
                    time.sleep(0.01)
                    continue

                with lock:
                    if ack_next >= num_packets:
                        return True
                    base = max(base, min(ack_next, num_packets))
                if base >= num_packets:
                    return True

                self.log("GBN sender: TIMEOUT base=%d retransmit" % base)
                resend_until = min(next_seq, base + self.window, num_packets)
                for s in range(base, resend_until):
                    self.log("GBN sender: RETRANSMIT seq=%d" % s)
                    self.sock.sendto(self.packets[s], self.dest)
                timer_started = self.time_fn()
        finally:
            if self.close_socket:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()
            t.join(timeout=1.0)
        return ack_next >= num_packets


def receive_metadata(
    sock: socket.socket,
    *,
    drop_data_prob: float = 0.0,
    ack_send_prob: float = 1.0,
    rng: Optional[random.Random] = None,
    log: Optional[Callable[[str], None]] = None,
    deadline: Optional[float] = None,
    time_fn: Callable[[], float] = time.time,
) -> Optional[bytes]:
    log = log or (lambda _m: None)
    rng = rng or random.Random()
    next_exp = 0
    chunks: dict[int, bytes] = {}
    peer_addr = None

    def ack_to(addr):
        nonlocal peer_addr
        if peer_addr is None:
            peer_addr = addr
        if rng.random() > ack_send_prob:
            log("GBN receiver: ACK_DROP next_expected=%d" % next_exp)
            return
        try:
            sock.sendto(build_ack(next_exp), addr)
            log("GBN receiver: ACK_SEND next_expected=%d" % next_exp)
        except OSError as e:
            log("GBN receiver: ACK error %s" % e)

    while True:
        if deadline is not None and time_fn() >= deadline:
            log("GBN receiver: deadline exceeded")
            return None
        to = 1.0 if deadline is None else max(0.05, min(1.0, deadline - time_fn()))
        sock.settimeout(to)
        try:
            data, addr = sock.recvfrom(PAYLOAD_SIZE + 64)
        except socket.timeout:
            continue
        except OSError:
            return None
        if len(data) < PAYLOAD_SIZE:
            continue
        pkt = memoryview(data)[:PAYLOAD_SIZE]
        seq = struct.unpack(SEQ_FMT, pkt[:SEQ_SIZE])[0]
        body = bytes(pkt[SEQ_SIZE:])

        if seq != next_exp:
            log("GBN receiver: DROP out_of_order got=%d expected=%d" % (seq, next_exp))
            ack_to(peer_addr or addr)
            continue

        if rng.random() < drop_data_prob:
            log("GBN receiver: DROP sim_loss seq=%d" % seq)
            ack_to(addr)
            continue

        if _is_eot(seq, body):
            total = struct.unpack("!I", body[:4])[0]
            n_data = _num_data_chunks(total)
            if sorted(chunks.keys()) != list(range(n_data)):
                log("GBN receiver: EOT but missing chunks have=%s need=%d" % (sorted(chunks), n_data))
                ack_to(addr)
                return None
            out = bytearray()
            for i in range(n_data):
                out.extend(chunks[i])
            next_exp = seq + 1
            log("GBN receiver: RECV EOT seq=%d total=%d" % (seq, total))
            ack_to(addr)
            return bytes(out[:total])

        chunks[seq] = body
        next_exp = seq + 1
        log("GBN receiver: RECV seq=%d" % seq)
        ack_to(addr)
