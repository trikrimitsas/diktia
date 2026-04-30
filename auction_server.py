import socket
import threading
import logging
import time
import random
from collections import deque

import config
import reputation
from protocol import send_message, recv_message


class AuctionServer:
    def __init__(self):
        self.users = {}
        self.sessions = {}
        self.auction_queue = deque()
        self.active_auctions = {}
        self.lock = threading.Lock()
        self.queue_event = threading.Event()
        self.running = True

    # ------------------------------------------------------------------ #
    #  Main server loop                                                  #
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
                "REGISTER": self._on_register,
                "LOGIN": self._on_login,
                "LOGOUT": self._on_logout,
                "REQUEST_AUCTION": self._on_request_auction,
                "GET_CURRENT_AUCTION": self._on_get_current_auction,
                "GET_AUCTION_DETAILS": self._on_get_auction_details,
                "PLACE_BID": self._on_place_bid,
                "NOTIFY_PURCHASE": self._on_transaction_report,
                "TRANSACTION_REPORT": self._on_transaction_report,
                "SELLER_TX_SUCCESS": self._on_seller_tx_success,
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
    #  Account management                                                #
    # ------------------------------------------------------------------ #

    def _on_register(self, sock, msg):
        uname, pwd = msg["username"], msg["password"]
        with self.lock:
            if uname in self.users:
                resp = {"type": "REGISTER_RESP", "success": False,
                        "message": "Username already taken. Choose another."}
            else:
                self.users[uname] = {
                    "password": pwd,
                    "num_auctions_seller": 0,
                    "num_auctions_bidder": 0,
                    "reputation": reputation.INITIAL,
                }
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
            self.sessions[token] = {
                "username": uname,
                "ip_address": None,
                "port": None,
                "udp_port": None,
            }
            if "reputation" not in self.users[uname]:
                self.users[uname]["reputation"] = reputation.INITIAL
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
    #  Auction queue selection (reputation between first two)           #
    # ------------------------------------------------------------------ #

    def _seller_rep(self, seller_token):
        uname = self.sessions.get(seller_token, {}).get("username")
        if not uname or uname not in self.users:
            return reputation.INITIAL
        return float(self.users[uname].get("reputation", reputation.INITIAL))

    def _pop_next_queue_item(self):
        """FCFS unless first seller reputation < second seller's — then take second first."""
        if not self.auction_queue:
            return None
        if len(self.auction_queue) == 1:
            return self.auction_queue.popleft()
        t0, oid0, d0, sb0, du0 = self.auction_queue[0]
        t1, oid1, d1, sb1, du1 = self.auction_queue[1]
        r0 = self._seller_rep(t0)
        r1 = self._seller_rep(t1)
        if r0 < r1:
            del self.auction_queue[1]
            return (t1, oid1, d1, sb1, du1)
        return self.auction_queue.popleft()

    # ------------------------------------------------------------------ #
    #  Auction request                                                   #
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
            udp_p = msg.get("udp_port", msg["port"])
            self.sessions[token]["udp_port"] = int(udp_p)
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
    #  Queries                                                           #
    # ------------------------------------------------------------------ #

    def _on_get_current_auction(self, sock, msg):
        token = msg["token_id"]
        with self.lock:
            if token not in self.sessions:
                send_message(sock, {"type": "CURRENT_AUCTION_RESP",
                                     "active": False,
                                     "message": "Invalid token."})
                return
        threading.Thread(target=self._check_all_active_sellers, daemon=True).start()
        with self.lock:
            if not self.active_auctions:
                send_message(sock, {"type": "CURRENT_AUCTION_RESP",
                                     "active": False,
                                     "object_id": None,
                                     "description": None,
                                     "auctions": []})
                return
            first_oid = next(iter(self.active_auctions))
            ca = self.active_auctions[first_oid]
            auctions = [
                {"object_id": a["object_id"], "description": a["description"]}
                for a in self.active_auctions.values()
            ]
            send_message(sock, {"type": "CURRENT_AUCTION_RESP",
                                 "active": True,
                                 "object_id": ca["object_id"],
                                 "description": ca["description"],
                                 "auctions": auctions})

    def _on_get_auction_details(self, sock, msg):
        token = msg["token_id"]
        oid = msg.get("object_id")
        with self.lock:
            if token not in self.sessions:
                send_message(sock, {"type": "AUCTION_DETAILS_RESP",
                                     "success": False,
                                     "message": "Invalid token."})
                return
            if oid is None:
                if len(self.active_auctions) == 1:
                    oid = next(iter(self.active_auctions))
                else:
                    send_message(sock, {"type": "AUCTION_DETAILS_RESP",
                                         "success": False,
                                         "message": "object_id required (multiple active auctions)."})
                    return
            ca = self.active_auctions.get(oid)
            if ca is None:
                send_message(sock, {"type": "AUCTION_DETAILS_RESP",
                                     "success": False,
                                     "message": "No active auction for this object_id."})
                return
            remaining = max(0.0, ca["end_time"] - time.time())
            resp = {
                "type": "AUCTION_DETAILS_RESP",
                "success": True,
                "object_id": ca["object_id"],
                "seller_token_id": ca["seller_token_id"],
                "highest_bid": ca["highest_bid"],
                "remaining_time": round(remaining, 1),
                "auction_duration": ca["duration"],
            }
            ca["bidders"].add(token)
        threading.Thread(target=self._check_all_active_sellers, daemon=True).start()
        send_message(sock, resp)

    # ------------------------------------------------------------------ #
    #  Bidding                                                           #
    # ------------------------------------------------------------------ #

    def _record_bid_ranking(self, ca, token, bid):
        uname = self.sessions.get(token, {}).get("username", "?")
        ca["bid_ranking"] = [e for e in ca["bid_ranking"] if e["token"] != token]
        ca["bid_ranking"].append({"token": token, "username": uname, "bid": bid})
        ca["bid_ranking"].sort(key=lambda e: (-e["bid"], e["token"]))

    def _on_place_bid(self, sock, msg):
        token = msg["token_id"]
        oid = msg["object_id"]
        bid = float(msg["bid"])
        with self.lock:
            if token not in self.sessions:
                send_message(sock, {"type": "BID_RESP", "success": False,
                                     "message": "Invalid token."})
                return
            ca = self.active_auctions.get(oid)
            if ca is None:
                send_message(sock, {"type": "BID_RESP", "success": False,
                                     "message": "No matching active auction."})
                return
            if time.time() > ca["end_time"]:
                send_message(sock, {"type": "BID_RESP", "success": False,
                                     "message": "Auction expired."})
                return
            if token == ca["seller_token_id"]:
                send_message(sock, {"type": "BID_RESP", "success": False,
                                     "message": "Seller cannot bid on own item."})
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
            self._record_bid_ranking(ca, token, bid)
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

    def _on_transaction_report(self, sock, msg):
        token = msg["token_id"]
        oid = msg["object_id"]
        outcome = msg.get("outcome", "success")
        if msg.get("type") == "NOTIFY_PURCHASE":
            outcome = "success"
        with self.lock:
            if token not in self.sessions:
                send_message(sock, {"type": "TRANSACTION_REPORT_RESP",
                                     "success": False,
                                     "message": "Invalid token."})
                return
            pend = self.active_auctions.get(oid)
            if pend is None or pend.get("phase") != "awarding":
                send_message(sock, {"type": "TRANSACTION_REPORT_RESP",
                                     "success": False,
                                     "message": "No pending transaction for this auction."})
                return
            if pend.get("current_buyer_token") != token:
                send_message(sock, {"type": "TRANSACTION_REPORT_RESP",
                                     "success": False,
                                     "message": "Not the current awarded bidder."})
                return
            uname = self.sessions[token]["username"]
        logging.info("TX_REPORT %s  object=%s  outcome=%s", uname, oid, outcome)
        send_message(sock, {"type": "TRANSACTION_REPORT_RESP", "success": True,
                             "message": "Recorded."})
        threading.Thread(
            target=self._handle_transaction_outcome,
            args=(oid, outcome),
            daemon=True,
        ).start()

    def _on_seller_tx_success(self, sock, msg):
        """Seller confirms successful P2P metadata transfer (Phase 2 PDF)."""
        token = msg["token_id"]
        oid = msg["object_id"]
        with self.lock:
            if token not in self.sessions:
                send_message(sock, {"type": "SELLER_TX_SUCCESS_RESP",
                                     "success": False,
                                     "message": "Invalid token."})
                return
            ca = self.active_auctions.get(oid)
            if ca is None or ca.get("phase") != "awarding":
                send_message(sock, {"type": "SELLER_TX_SUCCESS_RESP",
                                     "success": False,
                                     "message": "No awarding auction for this object_id."})
                return
            if ca["seller_token_id"] != token:
                send_message(sock, {"type": "SELLER_TX_SUCCESS_RESP",
                                     "success": False,
                                     "message": "Not the seller for this auction."})
                return
            uname = self.sessions[token]["username"]
        logging.info("SELLER_TX_SUCCESS %s  object=%s", uname, oid)
        send_message(sock, {"type": "SELLER_TX_SUCCESS_RESP", "success": True,
                             "message": "Recorded."})

    # ------------------------------------------------------------------ #
    #  Peer notification                                                 #
    # ------------------------------------------------------------------ #

    def _notify_peer(self, ip, port, msg):
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(config.SOCKET_TIMEOUT)
            s.connect((ip, int(port)))
            send_message(s, msg)
            try:
                return recv_message(s)
            except Exception:
                return None
        except Exception:
            return None
        finally:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass

    def _notify_all(self, peers_snap, msg):
        for info in peers_snap.values():
            threading.Thread(
                target=self._notify_peer,
                args=(info["ip_address"], info["port"], msg),
                daemon=True,
            ).start()

    # ------------------------------------------------------------------ #
    #  Seller liveness                                                   #
    # ------------------------------------------------------------------ #

    def _check_seller_for_auction(self, ca):
        stk = ca["seller_token_id"]
        with self.lock:
            sess = self.sessions.get(stk)
        if not sess or not sess.get("ip_address") or not sess.get("port"):
            return False
        resp = self._notify_peer(sess["ip_address"], sess["port"],
                                 {"type": "CHECK_ACTIVE"})
        return resp is not None and resp.get("active", False)

    def _check_all_active_sellers(self):
        with self.lock:
            auctions = list(self.active_auctions.values())
        for ca in auctions:
            if ca.get("phase") != "bidding":
                continue
            if not self._check_seller_for_auction(ca):
                self._cancel_auction(ca["object_id"], "Seller disconnected")

    def _sync_check_active(self, ca):
        return self._check_seller_for_auction(ca)

    # ------------------------------------------------------------------ #
    #  Cancel / finalize / awarding                                      #
    # ------------------------------------------------------------------ #

    def _cancel_auction(self, oid, reason):
        with self.lock:
            ca = self.active_auctions.pop(oid, None)
            if ca is None:
                return
            stk = ca["seller_token_id"]
            bidder_snap = {}
            for t in ca["bidders"]:
                sess = self.sessions.get(t)
                if sess and sess.get("ip_address"):
                    bidder_snap[t] = {"ip_address": sess["ip_address"],
                                      "port": sess["port"]}
            if stk in self.sessions:
                del self.sessions[stk]
        logging.info("CANCELLED %s  reason: %s", oid, reason)
        self._notify_all(bidder_snap, {
            "type": "AUCTION_CANCELLED",
            "object_id": oid,
            "reason": reason,
        })

    def _start_auction_locked(self, token, oid, desc, sbid, dur):
        if token not in self.sessions:
            logging.info("SKIP      %s  (seller offline)", oid)
            return
        seller_name = self.sessions[token]["username"]
        if seller_name in self.users:
            self.users[seller_name]["num_auctions_seller"] += 1
        end_time = time.time() + dur
        self.active_auctions[oid] = {
            "object_id": oid,
            "description": desc,
            "seller_token_id": token,
            "start_bid": sbid,
            "highest_bid": sbid,
            "highest_bidder_token_id": None,
            "end_time": end_time,
            "duration": dur,
            "bidders": set(),
            "bid_ranking": [],
            "phase": "bidding",
        }
        logging.info("=" * 55)
        logging.info("AUCTION   %s | %s", oid, desc)
        logging.info("          start_bid=%.2f  duration=%ds  seller=%s",
                     sbid, dur, seller_name)
        logging.info("=" * 55)

    def _auction_timer_expired(self, oid):
        with self.lock:
            ca = self.active_auctions.get(oid)
            if ca is None or ca.get("phase") != "bidding":
                return
            if ca.get("highest_bidder_token_id") is None:
                logging.info("ENDED     %s  (no bids)", oid)
                self.active_auctions.pop(oid, None)
                return
            ca["phase"] = "awarding"
            ca["bid_rank_queue"] = [e["token"] for e in ca["bid_ranking"]]
            ca["current_buyer_token"] = None
        self._try_award_next_buyer(oid)

    def _try_award_next_buyer(self, oid):
        notify = None
        with self.lock:
            ca = self.active_auctions.get(oid)
            if ca is None or ca.get("phase") != "awarding":
                return
            queue = ca.get("bid_rank_queue", [])
            wtk = None
            hbid = None
            pick_idx = None
            for idx, cand in enumerate(queue):
                if cand == ca["seller_token_id"]:
                    continue
                sess = self.sessions.get(cand)
                if not sess or not sess.get("ip_address") or not sess.get("port"):
                    continue
                bid_val = None
                for e in ca["bid_ranking"]:
                    if e["token"] == cand:
                        bid_val = e["bid"]
                        break
                if bid_val is None:
                    continue
                wtk = cand
                hbid = bid_val
                pick_idx = idx
                break
            if wtk is None:
                logging.info("ENDED     %s  (no eligible bidder)", oid)
                self.active_auctions.pop(oid, None)
                return
            stk = ca["seller_token_id"]
            seller_sess = self.sessions.get(stk, {})
            winner_sess = self.sessions.get(wtk, {})
            ca["current_buyer_token"] = wtk
            ca["pending_final_bid"] = hbid
            if pick_idx is not None:
                ca["bid_rank_queue"] = queue[pick_idx + 1 :]
            notify = {
                "oid": oid,
                "hbid": hbid,
                "winner_name": winner_sess.get("username", "?"),
                "s_ip": seller_sess.get("ip_address"),
                "s_port": seller_sess.get("port"),
                "s_udp": int(seller_sess.get("udp_port") or seller_sess.get("port") or 0),
                "w_ip": winner_sess.get("ip_address"),
                "w_port": winner_sess.get("port"),
                "w_udp": int(winner_sess.get("udp_port") or winner_sess.get("port") or 0),
            }

        logging.info("AWARD     %s  try buyer=%s  bid=%.2f",
                     notify["oid"], notify["winner_name"], notify["hbid"])

        if notify["w_ip"] and notify["w_port"]:
            self._notify_peer(notify["w_ip"], notify["w_port"], {
                "type": "AUCTION_WON",
                "object_id": notify["oid"],
                "final_bid": notify["hbid"],
                "seller_ip": notify["s_ip"],
                "seller_port": notify["s_port"],
                "seller_udp_port": notify["s_udp"],
            })
        if notify["s_ip"] and notify["s_port"]:
            self._notify_peer(notify["s_ip"], notify["s_port"], {
                "type": "AUCTION_SOLD",
                "object_id": notify["oid"],
                "final_bid": notify["hbid"],
                "buyer_username": notify["winner_name"],
                "buyer_ip": notify["w_ip"],
                "buyer_udp_port": notify["w_udp"],
            })

    def _handle_transaction_outcome(self, oid, outcome):
        o = str(outcome).lower()
        success = o in ("success", "ok", "1", "true")
        try_again = False
        winner_name = "?"
        seller_name = "?"
        with self.lock:
            ca = self.active_auctions.get(oid)
            if ca is None or ca.get("phase") != "awarding":
                return
            wtk = ca.get("current_buyer_token")
            if wtk is None:
                return
            winner_name = self.sessions.get(wtk, {}).get("username", "?")
            seller_tok = ca["seller_token_id"]
            seller_name = self.sessions.get(seller_tok, {}).get("username", "?")
            if winner_name != "?" and winner_name in self.users:
                old = float(self.users[winner_name].get("reputation", reputation.INITIAL))
                self.users[winner_name]["reputation"] = reputation.update_reputation(
                    old, 1.0 if success else 0.0)
                logging.info(
                    "REPUTATION %s  %.4f -> %.4f (outcome=%s)",
                    winner_name,
                    old,
                    self.users[winner_name]["reputation"],
                    "ok" if success else "fail",
                )
            if success:
                if winner_name != "?" and winner_name in self.users:
                    self.users[winner_name]["num_auctions_bidder"] += 1
                self.active_auctions.pop(oid, None)
            else:
                ca["current_buyer_token"] = None
                try_again = True

        if success:
            logging.info(
                "COMPLETED %s  buyer=%s  seller=%s",
                oid,
                winner_name,
                seller_name,
            )
        elif try_again:
            logging.info(
                "TX_FAIL   %s  buyer=%s — trying fallback",
                oid,
                winner_name,
            )
            self._try_award_next_buyer(oid)

    # ------------------------------------------------------------------ #
    #  Auction manager                                                   #
    # ------------------------------------------------------------------ #

    def _auction_manager_loop(self):
        logging.info("Auction manager thread started.")
        last_chk = time.time()
        while self.running:
            with self.lock:
                while (len(self.active_auctions) < config.MAX_ACTIVE_AUCTIONS
                       and self.auction_queue):
                    entry = self._pop_next_queue_item()
                    if entry is None:
                        break
                    token, oid, desc, sbid, dur = entry
                    self._start_auction_locked(token, oid, desc, sbid, dur)

            now = time.time()
            with self.lock:
                oids = list(self.active_auctions.keys())
            for oid in oids:
                with self.lock:
                    ca = self.active_auctions.get(oid)
                if ca is None:
                    continue
                if ca.get("phase") == "bidding":
                    if now >= ca["end_time"]:
                        self._auction_timer_expired(oid)
                elif ca.get("phase") == "awarding":
                    pass

            if now - last_chk >= config.CHECK_ACTIVE_INTERVAL:
                with self.lock:
                    bidding = [a for a in self.active_auctions.values()
                               if a.get("phase") == "bidding"]
                for ca in bidding:
                    if not self._sync_check_active(ca):
                        self._cancel_auction(ca["object_id"], "Seller disconnected")
                last_chk = now

            if not self.auction_queue and not self.active_auctions:
                self.queue_event.wait(timeout=0.5)
                self.queue_event.clear()
            else:
                time.sleep(0.2)


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
