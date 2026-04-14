import socket
import threading
import logging
import time
import random
from collections import deque

import config
from protocol import send_message, recv_message


class AuctionServer:
    def __init__(self):
        self.users = {}
        self.sessions = {}
        self.auction_queue = deque()
        self.current_auction = None
        self.lock = threading.Lock()
        self.queue_event = threading.Event()
        self.running = True

    # ------------------------------------------------------------------ #
    #  Main server loop                                                    #
    # ------------------------------------------------------------------ #

    def start(self):
        threading.Thread(target=self._auction_manager_loop, daemon=True).start()

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((config.SERVER_HOST, config.SERVER_PORT))
        srv.listen(20)
        srv.settimeout(1.0)
        logging.info("Auction Server listening on %s:%d",
                      config.SERVER_HOST, config.SERVER_PORT)
        try:
            while self.running:
                try:
                    client, addr = srv.accept()
                    threading.Thread(target=self._handle_client,
                                     args=(client, addr), daemon=True).start()
                except socket.timeout:
                    pass
        finally:
            srv.close()

    def _handle_client(self, sock, addr):
        try:
            msg = recv_message(sock)
            dispatch = {
                "REGISTER":            self._on_register,
                "LOGIN":               self._on_login,
                "LOGOUT":              self._on_logout,
                "REQUEST_AUCTION":     self._on_request_auction,
                "GET_CURRENT_AUCTION": self._on_get_current_auction,
                "GET_AUCTION_DETAILS": self._on_get_auction_details,
                "PLACE_BID":           self._on_place_bid,
                "NOTIFY_PURCHASE":     self._on_notify_purchase,
            }
            handler = dispatch.get(msg.get("type"))
            if handler:
                handler(sock, msg)
            else:
                send_message(sock, {"type": "ERROR",
                                     "message": "Unknown request type"})
        except Exception as exc:
            logging.error("Client %s error: %s", addr, exc)
        finally:
            sock.close()

    # ------------------------------------------------------------------ #
    #  Account management: register / login / logout                       #
    # ------------------------------------------------------------------ #

    def _on_register(self, sock, msg):
        uname, pwd = msg["username"], msg["password"]
        with self.lock:
            if uname in self.users:
                resp = {"type": "REGISTER_RESP", "success": False,
                        "message": "Username already taken. Choose another."}
            else:
                self.users[uname] = {"password": pwd,
                                      "num_auctions_seller": 0,
                                      "num_auctions_bidder": 0}
                resp = {"type": "REGISTER_RESP", "success": True,
                        "message": "Registered successfully."}
                logging.info("REGISTER  %s", uname)
        send_message(sock, resp)

    def _on_login(self, sock, msg):
        uname, pwd = msg["username"], msg["password"]
        with self.lock:
            if uname not in self.users:
                send_message(sock, {"type": "LOGIN_RESP", "success": False,
                                     "token_id": None,
                                     "message": "User not found."})
                return
            if self.users[uname]["password"] != pwd:
                send_message(sock, {"type": "LOGIN_RESP", "success": False,
                                     "token_id": None,
                                     "message": "Wrong password."})
                return
            for s in self.sessions.values():
                if s["username"] == uname:
                    send_message(sock, {"type": "LOGIN_RESP", "success": False,
                                         "token_id": None,
                                         "message": "Already logged in."})
                    return
            token = str(random.randint(100000, 999999))
            while token in self.sessions:
                token = str(random.randint(100000, 999999))
            self.sessions[token] = {"username": uname,
                                     "ip_address": None, "port": None}
            logging.info("LOGIN     %s  token=%s", uname, token)
        send_message(sock, {"type": "LOGIN_RESP", "success": True,
                             "token_id": token,
                             "message": "Login successful."})

    def _on_logout(self, sock, msg):
        token = msg["token_id"]
        with self.lock:
            if token not in self.sessions:
                send_message(sock, {"type": "LOGOUT_RESP", "success": False,
                                     "message": "Invalid token."})
                return
            uname = self.sessions[token]["username"]
            del self.sessions[token]
            self.auction_queue = deque(
                i for i in self.auction_queue if i[0] != token)
            logging.info("LOGOUT    %s", uname)
        send_message(sock, {"type": "LOGOUT_RESP", "success": True,
                             "message": "Logged out."})

    # ------------------------------------------------------------------ #
    #  Auction request handling                                            #
    # ------------------------------------------------------------------ #

    def _on_request_auction(self, sock, msg):
        token = msg["token_id"]
        with self.lock:
            if token not in self.sessions:
                send_message(sock, {"type": "REQUEST_AUCTION_RESP",
                                     "success": False,
                                     "message": "Invalid token."})
                return
            self.sessions[token]["ip_address"] = msg["ip_address"]
            self.sessions[token]["port"] = msg["port"]
            for it in msg["items"]:
                self.auction_queue.append((
                    token,
                    it["object_id"],
                    it["description"],
                    float(it["start_bid"]),
                    int(it["auction_duration"]),
                ))
                logging.info("QUEUED    %s  from %s",
                             it["object_id"],
                             self.sessions[token]["username"])
        self.queue_event.set()
        send_message(sock, {"type": "REQUEST_AUCTION_RESP", "success": True,
                             "message": "%d item(s) queued." % len(msg["items"])})

    # ------------------------------------------------------------------ #
    #  Auction queries                                                     #
    # ------------------------------------------------------------------ #

    def _on_get_current_auction(self, sock, msg):
        token = msg["token_id"]
        with self.lock:
            if token not in self.sessions:
                send_message(sock, {"type": "CURRENT_AUCTION_RESP",
                                     "active": False,
                                     "message": "Invalid token."})
                return
        threading.Thread(target=self._do_check_active, daemon=True).start()
        with self.lock:
            ca = self.current_auction
            if ca is None:
                send_message(sock, {"type": "CURRENT_AUCTION_RESP",
                                     "active": False,
                                     "object_id": None,
                                     "description": None})
            else:
                send_message(sock, {"type": "CURRENT_AUCTION_RESP",
                                     "active": True,
                                     "object_id": ca["object_id"],
                                     "description": ca["description"]})

    def _on_get_auction_details(self, sock, msg):
        token = msg["token_id"]
        with self.lock:
            if token not in self.sessions:
                send_message(sock, {"type": "AUCTION_DETAILS_RESP",
                                     "success": False,
                                     "message": "Invalid token."})
                return
            ca = self.current_auction
            if ca is None:
                send_message(sock, {"type": "AUCTION_DETAILS_RESP",
                                     "success": False,
                                     "message": "No active auction."})
                return
            remaining = max(0.0, ca["end_time"] - time.time())
            resp = {
                "type": "AUCTION_DETAILS_RESP",
                "success": True,
                "object_id": ca["object_id"],
                "seller_token_id": ca["seller_token_id"],
                "highest_bid": ca["highest_bid"],
                "remaining_time": round(remaining, 1),
            }
            ca["bidders"].add(token)
        threading.Thread(target=self._do_check_active, daemon=True).start()
        send_message(sock, resp)

    # ------------------------------------------------------------------ #
    #  Bidding                                                             #
    # ------------------------------------------------------------------ #

    def _on_place_bid(self, sock, msg):
        token = msg["token_id"]
        oid = msg["object_id"]
        bid = float(msg["bid"])
        with self.lock:
            if token not in self.sessions:
                send_message(sock, {"type": "BID_RESP", "success": False,
                                     "message": "Invalid token."})
                return
            ca = self.current_auction
            if ca is None or ca["object_id"] != oid:
                send_message(sock, {"type": "BID_RESP", "success": False,
                                     "message": "No matching active auction."})
                return
            if time.time() > ca["end_time"]:
                send_message(sock, {"type": "BID_RESP", "success": False,
                                     "message": "Auction expired."})
                return
            if bid <= ca["highest_bid"]:
                send_message(sock, {"type": "BID_RESP", "success": False,
                                     "message": "Bid must exceed current highest."})
                return
            ca["highest_bid"] = bid
            ca["highest_bidder_token_id"] = token
            ca["bidders"].add(token)
            ca["end_time"] = time.time() + ca["duration"]
            dur_reset = ca["duration"]
            uname = self.sessions[token]["username"]
            peers_snap = {
                t: {"ip_address": s["ip_address"], "port": s["port"]}
                for t, s in self.sessions.items()
                if s["ip_address"] and s["port"]
            }
        logging.info("BID       %.2f  by %s  on %s", bid, uname, oid)
        logging.info("TIMER     reset to %ds  (item: %s)", dur_reset, oid)
        send_message(sock, {"type": "BID_RESP", "success": True,
                             "message": "Bid accepted."})
        self._notify_all(peers_snap, {
            "type": "NEW_BID_NOTIFY",
            "object_id": oid,
            "highest_bid": bid,
            "bidder_username": uname,
        })

    def _on_notify_purchase(self, sock, msg):
        token = msg["token_id"]
        oid = msg["object_id"]
        with self.lock:
            uname = self.sessions.get(token, {}).get("username", "?")
        logging.info("PURCHASE  %s  bought %s", uname, oid)
        send_message(sock, {"type": "NOTIFY_PURCHASE_RESP", "success": True})

    # ------------------------------------------------------------------ #
    #  Peer notification helpers                                           #
    # ------------------------------------------------------------------ #

    def _notify_peer(self, ip, port, msg):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(config.SOCKET_TIMEOUT)
            s.connect((ip, int(port)))
            send_message(s, msg)
            try:
                return recv_message(s)
            except Exception:
                return None
            finally:
                s.close()
        except Exception:
            return None

    def _notify_all(self, peers_snap, msg):
        for info in peers_snap.values():
            threading.Thread(
                target=self._notify_peer,
                args=(info["ip_address"], info["port"], msg),
                daemon=True,
            ).start()

    # ------------------------------------------------------------------ #
    #  Seller liveness (checkActive)                                       #
    # ------------------------------------------------------------------ #

    def _do_check_active(self):
        with self.lock:
            ca = self.current_auction
            if ca is None:
                return
            stk = ca["seller_token_id"]
            sess = self.sessions.get(stk)
            if sess is None:
                return
            ip, port = sess["ip_address"], sess["port"]
        if not ip or not port:
            return
        resp = self._notify_peer(ip, port, {"type": "CHECK_ACTIVE"})
        if resp is None or not resp.get("active", False):
            self._cancel_auction("Seller disconnected")

    def _sync_check_active(self):
        with self.lock:
            ca = self.current_auction
            if ca is None:
                return True
            stk = ca["seller_token_id"]
            sess = self.sessions.get(stk)
            if sess is None:
                return False
            ip, port = sess["ip_address"], sess["port"]
        if not ip or not port:
            return False
        resp = self._notify_peer(ip, port, {"type": "CHECK_ACTIVE"})
        return resp is not None and resp.get("active", False)

    # ------------------------------------------------------------------ #
    #  Auction cancel / finalize                                           #
    # ------------------------------------------------------------------ #

    def _cancel_auction(self, reason):
        with self.lock:
            ca = self.current_auction
            if ca is None:
                return
            oid = ca["object_id"]
            stk = ca["seller_token_id"]
            bidder_snap = {}
            for t in ca["bidders"]:
                sess = self.sessions.get(t)
                if sess and sess["ip_address"]:
                    bidder_snap[t] = {"ip_address": sess["ip_address"],
                                       "port": sess["port"]}
            if stk in self.sessions:
                del self.sessions[stk]
            self.current_auction = None
        logging.info("CANCELLED %s  reason: %s", oid, reason)
        self._notify_all(bidder_snap, {
            "type": "AUCTION_CANCELLED",
            "object_id": oid,
            "reason": reason,
        })

    def _finalize_auction(self):
        with self.lock:
            ca = self.current_auction
            if ca is None:
                return
            oid = ca["object_id"]
            stk = ca["seller_token_id"]
            hbid = ca["highest_bid"]
            wtk = ca["highest_bidder_token_id"]

            if wtk is None:
                logging.info("ENDED     %s  (no bids)", oid)
                self.current_auction = None
                return

            seller_sess = self.sessions.get(stk, {})
            winner_sess = self.sessions.get(wtk, {})
            seller_name = seller_sess.get("username", "?")
            winner_name = winner_sess.get("username", "?")

            if seller_name != "?" and seller_name in self.users:
                self.users[seller_name]["num_auctions_seller"] += 1
            if winner_name != "?" and winner_name in self.users:
                self.users[winner_name]["num_auctions_bidder"] += 1

            s_ip = seller_sess.get("ip_address")
            s_port = seller_sess.get("port")
            w_ip = winner_sess.get("ip_address")
            w_port = winner_sess.get("port")
            self.current_auction = None

        logging.info("ENDED     %s  won by %s  for %.2f  (seller: %s)",
                      oid, winner_name, hbid, seller_name)

        if w_ip and w_port:
            self._notify_peer(w_ip, w_port, {
                "type": "AUCTION_WON",
                "object_id": oid,
                "final_bid": hbid,
                "seller_ip": s_ip,
                "seller_port": s_port,
            })
        if s_ip and s_port:
            self._notify_peer(s_ip, s_port, {
                "type": "AUCTION_SOLD",
                "object_id": oid,
                "final_bid": hbid,
                "buyer_username": winner_name,
            })

    # ------------------------------------------------------------------ #
    #  Auction manager thread                                              #
    # ------------------------------------------------------------------ #

    def _auction_manager_loop(self):
        logging.info("Auction manager thread started.")
        while self.running:
            while not self.auction_queue and self.running:
                self.queue_event.wait(timeout=2)
                self.queue_event.clear()
            if not self.running:
                break

            with self.lock:
                if not self.auction_queue:
                    continue
                token, oid, desc, sbid, dur = self.auction_queue.popleft()
                if token not in self.sessions:
                    logging.info("SKIP      %s  (seller offline)", oid)
                    continue
                seller_name = self.sessions[token]["username"]

            end_time = time.time() + dur
            with self.lock:
                self.current_auction = {
                    "object_id": oid,
                    "description": desc,
                    "seller_token_id": token,
                    "start_bid": sbid,
                    "highest_bid": sbid,
                    "highest_bidder_token_id": None,
                    "end_time": end_time,
                    "duration": dur,
                    "bidders": set(),
                }

            logging.info("=" * 55)
            logging.info("AUCTION   %s | %s", oid, desc)
            logging.info("          start_bid=%.2f  duration=%ds  seller=%s",
                          sbid, dur, seller_name)
            logging.info("=" * 55)

            last_chk = time.time()
            while self.running:
                now = time.time()
                with self.lock:
                    if self.current_auction is None:
                        break
                    rem = self.current_auction["end_time"] - now
                if rem <= 0:
                    break
                if now - last_chk >= config.CHECK_ACTIVE_INTERVAL:
                    if not self._sync_check_active():
                        self._cancel_auction("Seller disconnected")
                        break
                    last_chk = now
                time.sleep(1)

            with self.lock:
                still = self.current_auction is not None
            if still:
                self._finalize_auction()

            time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[SERVER %(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    server = AuctionServer()
    try:
        server.start()
    except KeyboardInterrupt:
        server.running = False
        logging.info("Shutting down.")
