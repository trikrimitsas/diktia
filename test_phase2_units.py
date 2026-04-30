"""Unit tests for Phase 2 helpers (stdlib only)."""
import random
import socket
import threading
import time
import unittest

import gbn_udp
import reputation


class TestReputation(unittest.TestCase):
    def test_formula(self):
        self.assertAlmostEqual(reputation.update_reputation(1.0, 1.0), 1.0)
        self.assertAlmostEqual(reputation.update_reputation(1.0, 0.0), 0.75)
        n = 1.0
        for _ in range(5):
            n = reputation.update_reputation(n, 0.0)
        self.assertLess(n, 0.5)


class TestGbnUdp(unittest.TestCase):
    def test_packet_sizes(self):
        meta = b"x" * 125
        pkts = gbn_udp.packetize_metadata(meta)
        for p in pkts:
            self.assertEqual(len(p), gbn_udp.PAYLOAD_SIZE)
        n_data = (len(meta) + gbn_udp.DATA_BYTES - 1) // gbn_udp.DATA_BYTES
        self.assertEqual(len(pkts), n_data + 1)

    def test_roundtrip_no_loss(self):
        meta = b"hello metadata " * 20
        pkts = gbn_udp.packetize_metadata(meta)

        buyer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        buyer.bind(("127.0.0.1", 0))
        bport = buyer.getsockname()[1]

        result = [None]

        def recv():
            result[0] = gbn_udp.receive_metadata(
                buyer,
                drop_data_prob=0.0,
                ack_send_prob=1.0,
                rng=random.Random(1),
                deadline=time.time() + 10.0,
            )

        tr = threading.Thread(target=recv, daemon=True)
        tr.start()
        time.sleep(0.1)
        sender_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ok = gbn_udp.GbnUdpSender(
            sender_sock,
            ("127.0.0.1", bport),
            pkts,
            time_fn=time.time,
        ).run()
        tr.join(timeout=15.0)
        self.assertTrue(ok)
        self.assertEqual(result[0], meta)
        buyer.close()


if __name__ == "__main__":
    unittest.main()
