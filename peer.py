import socket
import threading
import logging
import time
import random
import os
import sys
import re

import config
from protocol import send_message, recv_message


class Peer:
    def __init__(self, peer_id, username, password):
        # identifying info:
        self.peer_id = peer_id
        self.username = username
        self.password = password
        # session info:
        self.token_id = None
        self.running = True
        # local storage:
        self.shared_dir = os.path.join("shared_directories", "peer_%s" % peer_id)
        self.item_counter = 0
        # networking info:
        self.peer_port = None

        os.makedirs(self.shared_dir, exist_ok=True)

    # helper method for server communication
    # starts the connections, sends message to server, waits for a response and returns response to caller
    def _send_to_server(self, msg):
        # AF_INET == socket module constant for IPv4 address family signifies internet protocol used
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(config.SOCKET_TIMEOUT)
        s.connect((config.SERVER_HOST, config.SERVER_PORT))
        send_message(s, msg)
        resp = recv_message(s)
        s.close()
        return resp

    # account management functions

    # helper for retry registration
    def new_creds(self):
        # generate new username by choosing a random number to append the base from the next 4 greater numbers than current
        # extract prefix and numeric part 
        match_username = re.match(r"(.*_)(\d+)$", self.username)
        match_password = re.match(r"(.*_)(\d+)$", self.password)
        if not match_username or not match_password:
            logging.error("Username or password format not recognized for generating new credentials.")
            return False
        user_prefix = match_username.group(1) #take first part of username
        pass_prefix = match_password.group(1) #take first part of password
        num = int(match_username.group(2)) #take numeric part of username it's the same for both username and password since they are generated in the same format
        
        new_postfix = num + random.randint(1, 4)
        self.username = f"{user_prefix}{new_postfix}"
        self.password = f"{pass_prefix}{new_postfix}"
        return True
    
    # !!!! register needs retry logic in case username exists
    # !!!! currently if registration fails peer stops execution\
    # changed to automatically generate new credentials and retry registration until success
    def register(self):
        resp = self._send_to_server({
            "type": "REGISTER",
            "username": self.username,
            "password": self.password,
        })
        logging.info("Register attempt(%s): %s",self.username, resp["message"])
        if resp["success"]:
            return True
        else:
            logging.warning("Registration failed for %s. Consider retrying with a different username.", self.username)
            self.new_creds()
            return self.register()

    # !!!! also doesnt have retry logic peer just stops execution
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
        if not items:
            return
        try:
            resp = self._send_to_server({
                "type": "REQUEST_AUCTION",
                "token_id": self.token_id,
                "items": items,
                "ip_address": config.SERVER_HOST,
                "port": self.peer_port,
            })
            logging.info("requestAuction: %s", resp["message"])
        except Exception as e:
            logging.warning("requestAuction error: %s", e)

    def _scan_shared_dir(self):
        items = []
        if not os.path.isdir(self.shared_dir):
            return items
        for fname in os.listdir(self.shared_dir):
            if not fname.endswith(".txt"):
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

            oid = resp["object_id"]
            desc = resp["description"]
            logging.info("Current auction: %s - %s", oid, desc)

            interested = random.random() <= config.BID_INTEREST_PROBABILITY
            if not interested:
                logging.info("Not interested in %s (coin flip)", oid)
                return

            details = self._send_to_server({
                "type": "GET_AUCTION_DETAILS",
                "token_id": self.token_id,
            })
            if not details.get("success"):
                return

            if details["seller_token_id"] == self.token_id:
                logging.info("Skipping own item %s", oid)
                return

            hbid = details["highest_bid"]
            rem = details["remaining_time"]
            if rem <= 0:
                return

            new_bid = round(
                hbid * (1 + random.random() * config.BID_INCREMENT_FACTOR), 2)
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
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((config.SERVER_HOST, 0))
        self.peer_port = srv.getsockname()[1]
        srv.listen(10)
        srv.settimeout(1.0)
        logging.info("Peer server on port %d", self.peer_port)

        while self.running:
            try:
                cs, _ = srv.accept()
                threading.Thread(target=self._handle_incoming,
                                 args=(cs,), daemon=True).start()
            except socket.timeout:
                pass
        srv.close()

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
                logging.info("*** WON auction: %s for %.2f ***", oid, fb)
                send_message(sock, {"type": "ACK"})
                threading.Thread(
                    target=self._do_buy,
                    args=(oid, fb, sip, sport),
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

    def _do_buy(self, oid, bid, seller_ip, seller_port):
        time.sleep(1)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(config.SOCKET_TIMEOUT)
            s.connect((seller_ip, int(seller_port)))
            send_message(s, {
                "type": "TRANSACTION_REQ",
                "object_id": oid,
                "bid": bid,
                "buyer_username": self.username,
            })
            resp = recv_message(s)
            s.close()

            if resp.get("success"):
                fpath = os.path.join(self.shared_dir, "%s.txt" % oid)
                with open(fpath, "w", encoding="utf-8") as fh:
                    fh.write(resp["metadata"])
                logging.info("Transaction OK: received %s", oid)
                try:
                    self._send_to_server({
                        "type": "NOTIFY_PURCHASE",
                        "token_id": self.token_id,
                        "object_id": oid,
                    })
                except Exception:
                    pass
            else:
                logging.warning("Transaction FAILED for %s", oid)
        except Exception as e:
            logging.error("Buy error for %s: %s", oid, e)

    def _handle_sell(self, sock, msg):
        oid = msg["object_id"]
        bid = msg["bid"]
        buyer = msg["buyer_username"]
        fpath = os.path.join(self.shared_dir, "%s.txt" % oid)

        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as fh:
                meta = fh.read()
            send_message(sock, {
                "type": "TRANSACTION_RESP",
                "success": True,
                "object_id": oid,
                "metadata": meta,
            })
            os.remove(fpath)
            logging.info("Sold %s to %s for %.2f - file transferred & removed",
                          oid, buyer, bid)
        else:
            send_message(sock, {
                "type": "TRANSACTION_RESP",
                "success": False,
                "object_id": oid,
                "metadata": "",
            })
            logging.warning("Sell failed: %s not in shared_directory", oid)

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

        threading.Thread(target=self._item_generator_loop,
                         daemon=True).start()
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
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    uname = sys.argv[2] if len(sys.argv) > 2 else "user_%s" % pid
    pwd = sys.argv[3] if len(sys.argv) > 3 else "pass_%s" % pid

    logging.basicConfig(
        level=logging.INFO,
        format="[PEER-%s %%(asctime)s] %%(message)s" % pid,
        datefmt="%H:%M:%S",
    )
    Peer(pid, uname, pwd).start()
