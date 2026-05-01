"""Unit tests for Phase 2 helpers (stdlib only)."""
import random
import socket
import threading
import time
import unittest

from auction_server import AuctionServer
import gbn_udp
from peer import Peer
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
        meta = b"hello metadata " * 40
        pkts = gbn_udp.packetize_metadata(meta)
        logs = []

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
                log=logs.append,
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
            timeout_sec=0.5,
            log=logs.append,
            time_fn=time.time,
        ).run()
        tr.join(timeout=15.0)
        self.assertTrue(ok)
        self.assertEqual(result[0], meta)
        self.assertFalse(any("TIMEOUT" in line for line in logs), logs)
        buyer.close()
        sender_sock.close()

    def test_receiver_discards_out_of_order_and_ack_is_cumulative(self):
        meta = b"abc" * 30
        pkts = gbn_udp.packetize_metadata(meta)
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver_addr = receiver.getsockname()
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.bind(("127.0.0.1", 0))
        sender.settimeout(1.0)
        logs = []

        result = [None]

        def recv():
            result[0] = gbn_udp.receive_metadata(
                receiver,
                drop_data_prob=0.0,
                ack_send_prob=1.0,
                rng=random.Random(2),
                log=logs.append,
                deadline=time.time() + 5.0,
            )

        tr = threading.Thread(target=recv, daemon=True)
        tr.start()
        time.sleep(0.1)

        sender.sendto(pkts[1], receiver_addr)
        ack = gbn_udp.parse_ack(sender.recvfrom(2048)[0])
        self.assertEqual(ack, 0)

        sender.sendto(pkts[0], receiver_addr)
        ack = gbn_udp.parse_ack(sender.recvfrom(2048)[0])
        self.assertEqual(ack, 1)

        for pkt in pkts[1:]:
            sender.sendto(pkt, receiver_addr)
            gbn_udp.parse_ack(sender.recvfrom(2048)[0])

        tr.join(timeout=5.0)
        self.assertEqual(result[0], meta)
        self.assertTrue(any("out_of_order" in line for line in logs), logs)
        receiver.close()
        sender.close()


class TestPhase2AuctionState(unittest.TestCase):
    def _server_with_reps(self):
        srv = AuctionServer()
        srv.sessions = {
            "t1": {"username": "alice"},
            "t2": {"username": "bob"},
        }
        srv.users = {
            "alice": {"reputation": 0.4},
            "bob": {"reputation": 1.0},
        }
        return srv

    def test_queue_selection_prefers_second_when_first_seller_has_lower_reputation(self):
        srv = self._server_with_reps()
        srv.auction_queue.append(("t1", "a", "A", 10.0, 30))
        srv.auction_queue.append(("t2", "b", "B", 20.0, 30))

        self.assertEqual(srv._pop_next_queue_item()[1], "b")
        self.assertEqual(srv._pop_next_queue_item()[1], "a")

    def test_queue_selection_keeps_fcfs_when_first_reputation_is_not_lower(self):
        srv = self._server_with_reps()
        srv.users["alice"]["reputation"] = 1.0
        srv.users["bob"]["reputation"] = 1.0
        srv.auction_queue.append(("t1", "a", "A", 10.0, 30))
        srv.auction_queue.append(("t2", "b", "B", 20.0, 30))

        self.assertEqual(srv._pop_next_queue_item()[1], "a")

    def test_failed_awarded_buyer_loses_reputation_and_fallback_is_awarded(self):
        srv = AuctionServer()
        sent = []
        srv._notify_peer = lambda ip, port, msg: sent.append(msg) or {"type": "ACK"}
        srv.users = {
            "seller": {"reputation": 1.0, "num_auctions_seller": 0, "num_auctions_bidder": 0},
            "top": {"reputation": 1.0, "num_auctions_seller": 0, "num_auctions_bidder": 0},
            "second": {"reputation": 1.0, "num_auctions_seller": 0, "num_auctions_bidder": 0},
        }
        srv.sessions = {
            "s": {"username": "seller", "ip_address": "127.0.0.1", "port": 1, "udp_port": 11},
            "b1": {"username": "top", "ip_address": "127.0.0.1", "port": 2, "udp_port": 22},
            "b2": {"username": "second", "ip_address": "127.0.0.1", "port": 3, "udp_port": 33},
        }
        srv.active_auctions["obj"] = {
            "object_id": "obj",
            "description": "Object",
            "seller_token_id": "s",
            "phase": "awarding",
            "current_buyer_token": "b1",
            "bid_rank_queue": ["b2"],
            "bid_ranking": [
                {"token": "b1", "username": "top", "bid": 80.0},
                {"token": "b2", "username": "second", "bid": 70.0},
            ],
        }

        srv._handle_transaction_outcome("obj", "cancel")

        self.assertAlmostEqual(srv.users["top"]["reputation"], 0.75)
        self.assertEqual(srv.active_auctions["obj"]["current_buyer_token"], "b2")
        self.assertEqual(srv.active_auctions["obj"]["pending_final_bid"], 70.0)
        self.assertTrue(any(m["type"] == "AUCTION_WON" for m in sent))


class TestPeerBidding(unittest.TestCase):
    def test_late_bid_uses_twenty_percent_cap(self):
        self.assertEqual(Peer.calculate_auto_bid(100.0, 11.0, 100.0, 1.0), 110.0)
        self.assertEqual(Peer.calculate_auto_bid(100.0, 10.0, 100.0, 1.0), 120.0)


if __name__ == "__main__":
    unittest.main()
