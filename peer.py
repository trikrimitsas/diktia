import socket
import threading
import logging
import time
import random
import os
import sys

import config
import gbn_udp
from protocol import send_message, recv_message


class Peer:
    def __init__(self, peer_id, username, password, auto_generate_items=True):
        self.peer_id = peer_id
        self.username = username
        self.password = password
        self.token_id = None
        self.running = True
        self.shared_dir = os.path.join("shared_directories", username)
        self.item_counter = 0
        self.peer_port = None
        self.udp_port = None
        self.udp_sock = None
        self.udp_lock = threading.Lock()
        self.auto_generate_items = auto_generate_items

        os.makedirs(self.shared_dir, exist_ok=True)

    # ------------------------------------------------------------------ #
    #  Server communication helper                                         #
    # ------------------------------------------------------------------ #

    def _send_to_server(self, msg):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(config.SOCKET_TIMEOUT)
        s.connect((config.SERVER_HOST, config.SERVER_PORT))
        send_message(s, msg)
        resp = recv_message(s)
        s.close()
        return resp

    # ------------------------------------------------------------------ #
    #  Account management                                                  #
    # ------------------------------------------------------------------ #

    def register(self):
        resp = self._send_to_server({
            "type": "REGISTER",
            "username": self.username,
            "password": self.password,
        })
        logging.info("Register: %s", resp["message"])
        return resp["success"]

    def login(self):
        resp = self._send_to_server({
            "type": "LOGIN",
            "username": self.username,
            "password": self.password,
        })
        if resp["success"]:
            self.token_id = resp["token_id"]
            logging.info("Login OK  token=%s", self.token_id)
            self._send_items_to_server()
        else:
            logging.warning("Login FAIL: %s", resp["message"])
        return resp["success"]

    def logout(self):
        if not self.token_id:
            return
        try:
            resp = self._send_to_server({
                "type": "LOGOUT",
                "token_id": self.token_id,
            })
            logging.info("Logout: %s", resp["message"])
            self.token_id = None
        except Exception as e:
            logging.warning("Logout error: %s", e)

    # ------------------------------------------------------------------ #
    #  requestAuction                                                      #
    # ------------------------------------------------------------------ #

    def _send_items_to_server(self):
        items = self._scan_shared_dir()
        try:
            resp = self._send_to_server({
                "type": "REQUEST_AUCTION",
                "token_id": self.token_id,
                "items": items,
                "ip_address": config.SERVER_HOST,
                "port": self.peer_port,
                "udp_port": self.udp_port,
            })
            if items:
                logging.info("requestAuction: %s", resp["message"])
            else:
                logging.info("requestAuction: 0 item(s) queued (contact info updated)")
        except Exception as e:
            logging.warning("requestAuction error: %s", e)

    def _scan_shared_dir(self):
        items = []
        if not os.path.isdir(self.shared_dir):
            return items
        for fname in os.listdir(self.shared_dir):
            if not fname.endswith(".txt"):
                continue
            if not self.auto_generate_items and fname.startswith("Object_"):
                continue
            meta = self._parse_item(os.path.join(self.shared_dir, fname))
            if meta:
                items.append(meta)
        return items

    @staticmethod
    def _parse_item(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip().strip("[]")
            parts = {}
            for seg in raw.split(";"):
                seg = seg.strip()
                if ":" in seg:
                    k, v = seg.split(":", 1)
                    parts[k.strip()] = v.strip().strip('"')
            return {
                "object_id": parts["object_id"],
                "description": parts.get("description", ""),
                "start_bid": float(parts.get("start_bid", "10")),
                "auction_duration": int(parts.get("auction_duration", "30")),
            }
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    #  Item generator thread                                               #
    # ------------------------------------------------------------------ #

    def _generate_item(self):
        self.item_counter += 1
        oid = "Object_%s_%02d" % (self.peer_id, self.item_counter)
        desc = "Item %d from %s" % (self.item_counter, self.username)
        sbid = round(random.uniform(5, 100), 2)
        dur = random.randint(30, 90)

        content = '[object_id: %s; description: "%s"; start_bid: "%s"; auction_duration: "%s"]' % (
            oid, desc, sbid, dur)
        fpath = os.path.join(self.shared_dir, "%s.txt" % oid)
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write(content)
        logging.info("Generated %s  (bid=%.2f, dur=%ds)", oid, sbid, dur)
        return {"object_id": oid, "description": desc,
                "start_bid": sbid, "auction_duration": dur}

    def _item_generator_loop(self):
        while self.running:
            wait = random.random() * config.ITEM_GEN_MAX_INTERVAL
            logging.info("Next item in %.0fs", wait)
            t0 = time.time()
            while time.time() - t0 < wait and self.running:
                time.sleep(1)
            if not self.running:
                break
            item = self._generate_item()
            if self.token_id and self.peer_port:
                try:
                    resp = self._send_to_server({
                        "type": "REQUEST_AUCTION",
                        "token_id": self.token_id,
                        "items": [item],
                        "ip_address": config.SERVER_HOST,
                        "port": self.peer_port,
                        "udp_port": self.udp_port,
                    })
                    logging.info("Queued %s: %s",
                                 item["object_id"], resp["message"])
                except Exception as e:
                    logging.warning("Queue failed: %s", e)

    # ------------------------------------------------------------------ #
    #  Auction poller thread                                               #
    # ------------------------------------------------------------------ #

    def _auction_poller_loop(self):
        time.sleep(5)
        while self.running:
            if self.token_id:
                self._poll_auction()
            t0 = time.time()
            while time.time() - t0 < config.AUCTION_POLL_INTERVAL and self.running:
                time.sleep(1)

    def _poll_auction(self):
        try:
            resp = self._send_to_server({
                "type": "GET_CURRENT_AUCTION",
                "token_id": self.token_id,
            })
            if not resp.get("active"):
                return

            auctions = resp.get("auctions")
            if not auctions:
                auctions = [{"object_id": resp["object_id"],
                             "description": resp.get("description", "")}]
            for entry in auctions:
                oid = entry["object_id"]
                desc = entry.get("description", "")
                logging.info("Current auction: %s - %s", oid, desc)

                interested = random.random() <= config.BID_INTEREST_PROBABILITY
                if not interested:
                    logging.info("Not interested in %s (coin flip)", oid)
                    continue

                details = self._send_to_server({
                    "type": "GET_AUCTION_DETAILS",
                    "token_id": self.token_id,
                    "object_id": oid,
                })
                if not details.get("success"):
                    continue

                if details["seller_token_id"] == self.token_id:
                    logging.info("Skipping own item %s", oid)
                    continue

                hbid = details["highest_bid"]
                rem = details["remaining_time"]
                dur = details.get("auction_duration", rem)
                if rem <= 0:
                    continue
                if dur and dur > 0:
                    late = rem <= dur * config.LATE_AUCTION_FRACTION
                else:
                    late = False
                inc = (config.LATE_BID_INCREMENT_FACTOR if late
                       else config.BID_INCREMENT_FACTOR)
                new_bid = round(hbid * (1 + random.random() * inc), 2)
                br = self._send_to_server({
                    "type": "PLACE_BID",
                    "token_id": self.token_id,
                    "object_id": oid,
                    "bid": new_bid,
                })
                logging.info("placeBid %.2f on %s: %s",
                             new_bid, oid, br["message"])
        except Exception as e:
            logging.warning("Poll error: %s", e)

    # ------------------------------------------------------------------ #
    #  Peer server (incoming connections from server & other peers)         #
    # ------------------------------------------------------------------ #

    def _peer_server_loop(self):
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_sock.bind((config.SERVER_HOST, 0))
        self.udp_port = self.udp_sock.getsockname()[1]

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((config.SERVER_HOST, 0))
        self.peer_port = srv.getsockname()[1]
        srv.listen(10)
        srv.settimeout(1.0)
        logging.info("Peer TCP %d  UDP %d", self.peer_port, self.udp_port)

        while self.running:
            try:
                cs, _ = srv.accept()
                threading.Thread(target=self._handle_incoming,
                                 args=(cs,), daemon=True).start()
            except socket.timeout:
                pass
        srv.close()
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except OSError:
                pass

    def _handle_incoming(self, sock):
        try:
            msg = recv_message(sock)
            t = msg.get("type")

            if t == "CHECK_ACTIVE":
                send_message(sock,
                             {"type": "CHECK_ACTIVE_RESP", "active": True})

            elif t == "NEW_BID_NOTIFY":
                logging.info("[notify] New bid on %s: %.2f by %s",
                             msg["object_id"], msg["highest_bid"],
                             msg["bidder_username"])
                send_message(sock, {"type": "ACK"})

            elif t == "AUCTION_WON":
                oid = msg["object_id"]
                fb = msg["final_bid"]
                sip = msg["seller_ip"]
                sport = msg["seller_port"]
                sudp = int(msg.get("seller_udp_port", sport))
                logging.info("*** WON auction: %s for %.2f ***", oid, fb)
                send_message(sock, {"type": "ACK"})
                threading.Thread(
                    target=self._do_buy,
                    args=(oid, fb, sip, sport, sudp),
                    daemon=True,
                ).start()

            elif t == "AUCTION_SOLD":
                logging.info("*** SOLD %s for %.2f to %s ***",
                             msg["object_id"], msg["final_bid"],
                             msg["buyer_username"])
                send_message(sock, {"type": "ACK"})

            elif t == "AUCTION_CANCELLED":
                logging.info("[notify] Cancelled %s: %s",
                             msg["object_id"], msg["reason"])
                send_message(sock, {"type": "ACK"})

            elif t == "TRANSACTION_REQ":
                self._handle_sell(sock, msg)

        except Exception as e:
            logging.error("Incoming handler error: %s", e)
        finally:
            sock.close()

    # ------------------------------------------------------------------ #
    #  P2P transaction                                                     #
    # ------------------------------------------------------------------ #

    def _report_seller_tx(self, oid):
        if not self.token_id:
            return
        try:
            self._send_to_server({
                "type": "SELLER_TX_SUCCESS",
                "token_id": self.token_id,
                "object_id": oid,
            })
        except Exception as e:
            logging.warning("seller tx success report: %s", e)

    def _report_seller_tx_failure(self, oid, reason):
        if not self.token_id:
            return
        try:
            self._send_to_server({
                "type": "SELLER_TX_FAILURE",
                "token_id": self.token_id,
                "object_id": oid,
                "reason": reason,
            })
        except Exception as e:
            logging.warning("seller tx failure report: %s", e)

    def _report_tx(self, oid, outcome):
        try:
            self._send_to_server({
                "type": "TRANSACTION_REPORT",
                "token_id": self.token_id,
                "object_id": oid,
                "outcome": outcome,
            })
        except Exception as e:
            logging.warning("transaction report: %s", e)

    def _do_buy(self, oid, bid, seller_ip, seller_port, seller_udp_port):
        time.sleep(0.5)
        proceed = random.random() < config.TRANSACTION_PROCEED_PROBABILITY
        if not proceed:
            logging.info("Declining transaction for %s (simulated cancel)", oid)
            self._report_tx(oid, "cancel")
            return
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(config.SOCKET_TIMEOUT)
            s.connect((seller_ip, int(seller_port)))
            send_message(s, {
                "type": "TRANSACTION_REQ",
                "object_id": oid,
                "bid": bid,
                "buyer_username": self.username,
                "buyer_udp_port": self.udp_port,
            })
            resp = recv_message(s)
            s.close()

            if not resp.get("success"):
                logging.warning("Transaction FAILED for %s (seller declined or missing item)", oid)
                return

            if resp.get("udp"):
                drop_p = float(config.UDP_DROP_DATA_PROB)
                ack_p = float(config.UDP_ACK_SEND_PROB)
                with self.udp_lock:
                    meta_bytes = gbn_udp.receive_metadata(
                        self.udp_sock,
                        drop_data_prob=drop_p,
                        ack_send_prob=ack_p,
                        log=lambda m: logging.info(m),
                        deadline=time.time() + 120.0,
                    )
                if meta_bytes is None:
                    logging.warning("UDP receive failed for %s", oid)
                    self._report_tx(oid, "fail")
                    return
                text = meta_bytes.decode("utf-8")
            else:
                text = resp.get("metadata", "")

            fpath = os.path.join(self.shared_dir, "%s.txt" % oid)
            with open(fpath, "w", encoding="utf-8") as fh:
                fh.write(text)
            logging.info("Transaction OK: received %s", oid)
            self._report_tx(oid, "success")
        except Exception as e:
            logging.error("Buy error for %s: %s", oid, e)
            self._report_tx(oid, "fail")

    def _handle_sell(self, sock, msg):
        oid = msg["object_id"]
        bid = msg["bid"]
        buyer = msg["buyer_username"]
        buyer_udp = msg.get("buyer_udp_port")
        fpath = os.path.join(self.shared_dir, "%s.txt" % oid)

        if not os.path.exists(fpath):
            send_message(sock, {
                "type": "TRANSACTION_RESP",
                "success": False,
                "object_id": oid,
                "metadata": "",
            })
            logging.warning("Sell failed: %s not in shared_directory", oid)
            self._report_seller_tx_failure(oid, "item_not_found")
            return

        with open(fpath, "r", encoding="utf-8") as fh:
            meta = fh.read()

        if buyer_udp is None:
            send_message(sock, {
                "type": "TRANSACTION_RESP",
                "success": True,
                "object_id": oid,
                "metadata": meta,
            })
            os.remove(fpath)
            logging.info("Sold %s to %s (TCP metadata)", oid, buyer)
            self._report_seller_tx(oid)
            return

        buyer_ip = sock.getpeername()[0]
        buyer_udp = int(buyer_udp)
        meta_bytes = meta.encode("utf-8")
        usock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        packets = gbn_udp.packetize_metadata(meta_bytes)
        send_message(sock, {
            "type": "TRANSACTION_RESP",
            "success": True,
            "object_id": oid,
            "metadata": "",
            "udp": True,
        })
        try:
            sock.close()
        except OSError:
            pass
        sender = gbn_udp.GbnUdpSender(
            usock,
            (buyer_ip, buyer_udp),
            packets,
            log=lambda m: logging.info(m),
        )
        ok = sender.run()
        if ok:
            os.remove(fpath)
            logging.info("Sold %s to %s for %.2f (UDP GBN) file removed",
                         oid, buyer, bid)
            self._report_seller_tx(oid)
        else:
            logging.warning("UDP transfer incomplete for %s — file kept", oid)
            self._report_seller_tx_failure(oid, "udp_incomplete")

    # ------------------------------------------------------------------ #
    #  Main entry                                                          #
    # ------------------------------------------------------------------ #

    def start(self):
        srv_t = threading.Thread(target=self._peer_server_loop, daemon=True)
        srv_t.start()
        time.sleep(0.5)

        if not self.register():
            return
        if not self.login():
            return

        if self.auto_generate_items:
            threading.Thread(target=self._item_generator_loop,
                             daemon=True).start()
        else:
            logging.info("Auto item generation disabled for this peer.")
        threading.Thread(target=self._auction_poller_loop,
                         daemon=True).start()

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            self.logout()


if __name__ == "__main__":
    args = [arg for arg in sys.argv[1:] if arg != "--no-auto-items"]
    no_auto_items = "--no-auto-items" in sys.argv[1:]

    pid = int(args[0]) if len(args) > 0 else 1
    uname = args[1] if len(args) > 1 else "user_%s" % pid
    pwd = args[2] if len(args) > 2 else "pass_%s" % pid

    logging.basicConfig(
        level=logging.INFO,
        format="[PEER-%s %%(asctime)s] %%(message)s" % pid,
        datefmt="%H:%M:%S",
    )
    Peer(pid, uname, pwd, auto_generate_items=not no_auto_items).start()
