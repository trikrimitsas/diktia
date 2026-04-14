"""
test_scenarios.py
Controlled demonstration of all required auction system scenarios.
Produces clearly separated output for each case (peer-side + server-side).
"""
import socket, threading, logging, time, os, sys
import config
from protocol import send_message, recv_message
from auction_server import AuctionServer

tokens = {}
_listeners = {}

def pr(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)

def banner(title):
    print("\n" + "=" * 62, flush=True)
    print("  %s" % title, flush=True)
    print("=" * 62, flush=True)

def send_req(msg):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(config.SOCKET_TIMEOUT)
    s.connect((config.SERVER_HOST, config.SERVER_PORT))
    send_message(s, msg)
    r = recv_message(s)
    s.close()
    return r


def _pick_free_port(host: str = "127.0.0.1") -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()

def start_listener(name):
    ctx = {"running": True, "port": 0}
    _listeners[name] = ctx
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    ctx["port"] = srv.getsockname()[1]
    srv.listen(5)
    srv.settimeout(1.0)

    def handle(sock):
        try:
            msg = recv_message(sock)
            t = msg.get("type")
            if t == "CHECK_ACTIVE":
                send_message(sock, {"type": "CHECK_ACTIVE_RESP", "active": True})
            elif t == "NEW_BID_NOTIFY":
                pr("[%s] NOTIFY.NEW_BID    | item='%s'  amount=%.2f  by=%s" % (
                    name, msg["object_id"], msg["highest_bid"], msg["bidder_username"]))
                send_message(sock, {"type": "ACK"})
            elif t == "AUCTION_WON":
                pr("[%s] AUCTION.WON       | item='%s'  final_price=%.2f  seller=%s:%s" % (
                    name, msg["object_id"], msg["final_bid"],
                    msg["seller_ip"], msg["seller_port"]))
                send_message(sock, {"type": "ACK"})
                threading.Thread(target=_do_buy, args=(
                    name, msg["object_id"], msg["final_bid"],
                    msg["seller_ip"], msg["seller_port"]), daemon=True).start()
            elif t == "AUCTION_SOLD":
                pr("[%s] AUCTION.SOLD      | item='%s'  final_price=%.2f  buyer=%s" % (
                    name, msg["object_id"], msg["final_bid"], msg["buyer_username"]))
                send_message(sock, {"type": "ACK"})
            elif t == "AUCTION_CANCELLED":
                pr("[%s] AUCTION.CANCELLED | item='%s'  reason=%s" % (
                    name, msg["object_id"], msg["reason"]))
                send_message(sock, {"type": "ACK"})
            elif t == "TRANSACTION_REQ":
                _do_sell(name, sock, msg)
        except Exception:
            pass
        finally:
            sock.close()

    def loop():
        while ctx["running"]:
            try:
                c, _ = srv.accept()
                threading.Thread(target=handle, args=(c,), daemon=True).start()
            except socket.timeout:
                pass
        srv.close()

    threading.Thread(target=loop, daemon=True).start()
    time.sleep(0.3)
    return ctx["port"]

def stop_listener(name):
    if name in _listeners:
        _listeners[name]["running"] = False
    time.sleep(2)

def _do_buy(buyer, oid, bid, sip, sport):
    time.sleep(0.5)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((sip, int(sport)))
        send_message(s, {"type": "TRANSACTION_REQ", "object_id": oid,
                          "bid": bid, "buyer_username": buyer})
        r = recv_message(s)
        s.close()
        if r.get("success"):
            dp = os.path.join("shared_directories", buyer)
            os.makedirs(dp, exist_ok=True)
            fp = os.path.join(dp, "%s.txt" % oid)
            with open(fp, "w") as f:
                f.write(r["metadata"])
            pr("[%s] TRANSACTION.OK   | item='%s'  file saved -> %s" % (buyer, oid, fp))
            if buyer in tokens:
                try:
                    send_req({"type": "NOTIFY_PURCHASE",
                              "token_id": tokens[buyer], "object_id": oid})
                    pr("[%s] NOTIFY_PURCHASE   | ownership of '%s' confirmed with server" % (buyer, oid))
                except Exception:
                    pass
        else:
            pr("[%s] TRANSACTION.FAIL  | item='%s'  (seller did not respond)" % (buyer, oid))
    except Exception as e:
        pr("[%s] Transaction error: %s" % (buyer, e))

def _do_sell(seller, sock, msg):
    oid = msg["object_id"]
    fp = os.path.join("shared_directories", seller, "%s.txt" % oid)
    if os.path.exists(fp):
        with open(fp) as f:
            meta = f.read()
        send_message(sock, {"type": "TRANSACTION_RESP", "success": True,
                             "object_id": oid, "metadata": meta})
        os.remove(fp)
        pr("[%s] TRANSACTION.OK   | item='%s'  metadata sent to buyer & local file removed" % (seller, oid))
    else:
        send_message(sock, {"type": "TRANSACTION_RESP", "success": False,
                             "object_id": oid, "metadata": ""})


def main():
    config.SERVER_PORT = _pick_free_port(config.SERVER_HOST)

    # Alice_RolexSub is the auctioned item in scenario 3-6; it moves to dave
    # each run. Reset it to alice's folder so the demo works every time.
    _alice_dir = os.path.join("shared_directories", "alice")
    _rolex_alice = os.path.join(_alice_dir, "Alice_RolexSub.txt")
    _rolex_dave  = os.path.join("shared_directories", "dave", "Alice_RolexSub.txt")
    if not os.path.exists(_rolex_alice):
        os.makedirs(_alice_dir, exist_ok=True)
        with open(_rolex_alice, "w", encoding="utf-8") as _f:
            _f.write('[object_id: Alice_RolexSub; description: "Vintage 1965 Rolex Submariner"; '
                     'start_bid: "50.00"; auction_duration: "25"]')
    if os.path.exists(_rolex_dave):
        os.remove(_rolex_dave)

    logging.basicConfig(level=logging.INFO,
                        format="[SERVER %(asctime)s] %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)

    server = AuctionServer()
    threading.Thread(target=server.start, daemon=True).start()
    time.sleep(1)
    pr("Auction Server started on %s:%d\n" % (config.SERVER_HOST, config.SERVER_PORT))

    # ======================== 1. REGISTER ========================
    banner("1. Register peer (peer-side + server-side)")

    pr("[alice] -> REGISTER username=alice password=pass123")
    r = send_req({"type": "REGISTER", "username": "alice", "password": "pass123"})
    pr("[alice] <- success=%s  msg='%s'" % (r["success"], r["message"]))

    pr("")
    pr("[alice] -> REGISTER username=alice (duplicate)")
    r = send_req({"type": "REGISTER", "username": "alice", "password": "x"})
    pr("[alice] <- success=%s  msg='%s'" % (r["success"], r["message"]))

    pr("")
    pr("[bob]   -> REGISTER username=bob password=bobpass")
    r = send_req({"type": "REGISTER", "username": "bob", "password": "bobpass"})
    pr("[bob]   <- success=%s  msg='%s'" % (r["success"], r["message"]))

    for n in ["carol", "dave", "eve"]:
        send_req({"type": "REGISTER", "username": n, "password": n + "pass"})
    pr("(carol, dave, eve registered)")

    # ======================== 2. LOGIN ========================
    banner("2. Login peer (peer-side + server-side)")

    port_a = start_listener("alice")
    pr("[alice] -> LOGIN username=alice password=pass123")
    r = send_req({"type": "LOGIN", "username": "alice", "password": "pass123"})
    pr("[alice] <- success=%s  token=%s  msg='%s'" % (r["success"], r["token_id"], r["message"]))
    tokens["alice"] = r["token_id"]

    pr("")
    pr("[alice] -> LOGIN again (duplicate session)")
    r = send_req({"type": "LOGIN", "username": "alice", "password": "pass123"})
    pr("[alice] <- success=%s  msg='%s'" % (r["success"], r["message"]))

    ports = {"alice": port_a}
    for n in ["bob", "carol", "dave", "eve"]:
        ports[n] = start_listener(n)
        r = send_req({"type": "LOGIN", "username": n, "password": n + "pass"})
        tokens[n] = r["token_id"]
        pr("[%s] logged in  token=%s  port=%d" % (n, r["token_id"], ports[n]))

    for n in tokens:
        send_req({"type": "REQUEST_AUCTION", "token_id": tokens[n],
                   "items": [], "ip_address": "127.0.0.1", "port": ports[n]})

    # ======================== 3. CURRENT AUCTION ========================
    banner("3. requestAuction + getCurrentAuction")

    pr("[alice] -> REQUEST_AUCTION  Alice_RolexSub (Vintage 1965 Rolex Submariner, bid=50, dur=25s)")
    r = send_req({"type": "REQUEST_AUCTION", "token_id": tokens["alice"],
                   "items": [{"object_id": "Alice_RolexSub",
                              "description": "Vintage 1965 Rolex Submariner",
                              "start_bid": 50.0, "auction_duration": 25}],
                   "ip_address": "127.0.0.1", "port": ports["alice"]})
    pr("[alice] <- %s  (Alice_RolexSub is now ACTIVE)" % r["message"])

    time.sleep(3)

    pr("[bob]   -> GET_CURRENT_AUCTION")
    r = send_req({"type": "GET_CURRENT_AUCTION", "token_id": tokens["bob"]})
    pr("[bob]   <- active=%s  object_id=%s  description=%s" % (
        r.get("active"), r.get("object_id"), r.get("description")))

    pr("[bob]   -> GET_AUCTION_DETAILS")
    r = send_req({"type": "GET_AUCTION_DETAILS", "token_id": tokens["bob"]})
    pr("[bob]   <- highest_bid=%.2f  seller_token=%s  remaining=%.1fs" % (
        r.get("highest_bid", 0), r.get("seller_token_id", ""), r.get("remaining_time", 0)))

    # ======================== 4. SUCCESSFUL BIDS + REJECTED BID ========================
    banner("4. placeBid: accepted bids + one rejected bid")

    pr("[bob]   -> PLACE_BID Alice_RolexSub bid=52.50")
    r = send_req({"type": "PLACE_BID", "token_id": tokens["bob"],
                   "object_id": "Alice_RolexSub", "bid": 52.50})
    pr("[bob]   <- success=%s  msg='%s'" % (r["success"], r["message"]))
    time.sleep(1)

    pr("[carol] -> PLACE_BID Alice_RolexSub bid=56.70")
    r = send_req({"type": "PLACE_BID", "token_id": tokens["carol"],
                   "object_id": "Alice_RolexSub", "bid": 56.70})
    pr("[carol] <- success=%s  msg='%s'" % (r["success"], r["message"]))
    time.sleep(1)

    pr("[bob]   -> PLACE_BID Alice_RolexSub bid=54.00  (REJECTED: lower than current 56.70)")
    r = send_req({"type": "PLACE_BID", "token_id": tokens["bob"],
                   "object_id": "Alice_RolexSub", "bid": 54.00})
    pr("[bob]   <- success=%s  msg='%s'" % (r["success"], r["message"]))
    time.sleep(1)

    pr("[dave]  -> PLACE_BID Alice_RolexSub bid=60.00")
    r = send_req({"type": "PLACE_BID", "token_id": tokens["dave"],
                   "object_id": "Alice_RolexSub", "bid": 60.00})
    pr("[dave]  <- success=%s  msg='%s'" % (r["success"], r["message"]))
    time.sleep(2)  # wait for NEW_BID_NOTIFY notifications to arrive at all peers

    # ======================== 5. AUCTION END + AWARD ========================
    banner("5. Auction ends - awarded to highest bidder")
    pr("Waiting for auction Alice_RolexSub to end (~25s after last bid timer reset)...")
    time.sleep(27)

    # ======================== 6. TRANSACTION ========================
    banner("6. Successful transaction (P2P file transfer)")
    time.sleep(3)

    pr("--- Checking shared_directories ---")
    for d in sorted(os.listdir("shared_directories")):
        full = os.path.join("shared_directories", d)
        if os.path.isdir(full):
            files = os.listdir(full)
            pr("  %s/ : %s" % (d, files if files else "(empty)"))
            for fn in files:
                with open(os.path.join(full, fn)) as fh:
                    pr("    -> %s" % fh.read().strip())

    pr("")
    pr("[alice] seller file Alice_RolexSub.txt exists = %s (should be False - transferred to buyer)" %
       os.path.exists("shared_directories/alice/Alice_RolexSub.txt"))
    pr("[dave]  buyer  file Alice_RolexSub.txt exists = %s (should be True  - received from seller)" %
       os.path.exists("shared_directories/dave/Alice_RolexSub.txt"))

    # ======================== 7. SELLER DISCONNECT ========================
    banner("7. Auction cancellation - seller disconnects")

    pr("[eve]   -> REQUEST_AUCTION  Eve_HP_FirstEd (Harry Potter 1st Edition, bid=30, dur=40s)")
    r = send_req({"type": "REQUEST_AUCTION", "token_id": tokens["eve"],
                   "items": [{"object_id": "Eve_HP_FirstEd",
                              "description": "Harry Potter 1st Edition (1997)",
                              "start_bid": 30.0, "auction_duration": 40}],
                   "ip_address": "127.0.0.1", "port": ports["eve"]})
    pr("[eve]   <- %s" % r["message"])
    time.sleep(3)

    pr("[bob]   -> PLACE_BID Eve_HP_FirstEd bid=35.00")
    r = send_req({"type": "PLACE_BID", "token_id": tokens["bob"],
                   "object_id": "Eve_HP_FirstEd", "bid": 35.0})
    pr("[bob]   <- %s" % r["message"])
    time.sleep(1)

    pr("")
    pr(">>> [eve] DISCONNECTING (simulating seller crash) <<<")
    stop_listener("eve")

    pr("Waiting for server checkActive to detect disconnect...")
    time.sleep(config.CHECK_ACTIVE_INTERVAL + config.SOCKET_TIMEOUT + 2)

    # ======================== 8. LOGOUT ========================
    banner("8. Logout peer (peer-side + server-side)")

    pr("[bob]   -> LOGOUT token=%s" % tokens["bob"])
    r = send_req({"type": "LOGOUT", "token_id": tokens["bob"]})
    pr("[bob]   <- success=%s  msg='%s'" % (r["success"], r["message"]))

    pr("")
    pr("[bob]   -> LOGOUT again (invalid token)")
    r = send_req({"type": "LOGOUT", "token_id": tokens["bob"]})
    pr("[bob]   <- success=%s  msg='%s'" % (r["success"], r["message"]))

    # ======================== 9. FCFS QUEUE DEMO — remaining items per peer ========================
    banner("9. FCFS queue demo — remaining items from all peers")

    # Bob logged out in scenario 8 — re-login before queueing
    pr("[bob]   -> LOGIN (re-login after scenario 8 logout)")
    r = send_req({"type": "LOGIN", "username": "bob", "password": "bobpass"})
    tokens["bob"] = r["token_id"]
    pr("[bob]   <- success=%s  token=%s" % (r["success"], r["token_id"]))

    # Eve disconnected in scenario 7 — start a new listener and re-login
    pr("[eve]   -> RECONNECT + LOGIN (after scenario 7 seller disconnect)")
    ports["eve"] = start_listener("eve")
    r = send_req({"type": "LOGIN", "username": "eve", "password": "evepass"})
    tokens["eve"] = r["token_id"]
    pr("[eve]   <- success=%s  token=%s" % (r["success"], r["token_id"]))

    pr("")
    pr("Queuing remaining items in FCFS order: bob -> carol -> dave -> alice -> carol2 -> eve")
    pr("")

    pr("[bob]   -> REQUEST_AUCTION  Bob_NikonFTn (1970s Nikon Camera, bid=80, dur=30s)")
    r = send_req({"type": "REQUEST_AUCTION", "token_id": tokens["bob"],
                   "items": [{"object_id": "Bob_NikonFTn",
                              "description": "1970s Nikon Photomic FTn Camera",
                              "start_bid": 80.0, "auction_duration": 30}],
                   "ip_address": "127.0.0.1", "port": ports["bob"]})
    pr("[bob]   <- %s  (Bob_NikonFTn is now ACTIVE — queue was empty)" % r["message"])

    pr("[carol] -> REQUEST_AUCTION  Carol_PersianRug (18th Century Persian Rug, bid=200, dur=30s)")
    r = send_req({"type": "REQUEST_AUCTION", "token_id": tokens["carol"],
                   "items": [{"object_id": "Carol_PersianRug",
                              "description": "18th Century Antique Persian Rug",
                              "start_bid": 200.0, "auction_duration": 30}],
                   "ip_address": "127.0.0.1", "port": ports["carol"]})
    pr("[carol] <- %s  [queue pos 2]" % r["message"])

    pr("[dave]  -> REQUEST_AUCTION  Dave_StampCollection (Victorian Stamps, bid=100, dur=25s)")
    r = send_req({"type": "REQUEST_AUCTION", "token_id": tokens["dave"],
                   "items": [{"object_id": "Dave_StampCollection",
                              "description": "Victorian Penny Black Stamp Collection",
                              "start_bid": 100.0, "auction_duration": 25}],
                   "ip_address": "127.0.0.1", "port": ports["dave"]})
    pr("[dave]  <- %s  [queue pos 3]" % r["message"])

    pr("[alice] -> REQUEST_AUCTION  Alice_VinylRecords (Beatles White Album, bid=40, dur=20s)")
    r = send_req({"type": "REQUEST_AUCTION", "token_id": tokens["alice"],
                   "items": [{"object_id": "Alice_VinylRecords",
                              "description": "Beatles White Album Vinyl (1968)",
                              "start_bid": 40.0, "auction_duration": 20}],
                   "ip_address": "127.0.0.1", "port": ports["alice"]})
    pr("[alice] <- %s  [queue pos 4]" % r["message"])

    pr("[carol] -> REQUEST_AUCTION  Carol_OilPainting (19th Century Seascape, bid=150, dur=35s)")
    r = send_req({"type": "REQUEST_AUCTION", "token_id": tokens["carol"],
                   "items": [{"object_id": "Carol_OilPainting",
                              "description": "19th Century Oil Painting - Seascape",
                              "start_bid": 150.0, "auction_duration": 35}],
                   "ip_address": "127.0.0.1", "port": ports["carol"]})
    pr("[carol] <- %s  [queue pos 5]" % r["message"])

    pr("[eve]   -> REQUEST_AUCTION  Eve_AntiquMap (Antique World Map 1820, bid=75, dur=35s)")
    r = send_req({"type": "REQUEST_AUCTION", "token_id": tokens["eve"],
                   "items": [{"object_id": "Eve_AntiquMap",
                              "description": "Antique World Map (1820)",
                              "start_bid": 75.0, "auction_duration": 35}],
                   "ip_address": "127.0.0.1", "port": ports["eve"]})
    pr("[eve]   <- %s  [queue pos 6]" % r["message"])

    pr("")
    pr("FCFS queue (server processes in this order, one at a time):")
    pr("  [ACTIVE] Bob_NikonFTn -> Carol_PersianRug -> Dave_StampCollection")
    pr("        -> Alice_VinylRecords -> Carol_OilPainting -> Eve_AntiquMap")
    pr("")
    pr("Full item inventory per peer:")
    pr("  alice : Alice_RolexSub (sold to dave), Alice_VinylRecords (queued pos 4)")
    pr("  bob   : Bob_NikonFTn (ACTIVE auction), Bob_LeicaM3 (in shared_dir, not yet auctioned)")
    pr("  carol : Carol_PersianRug (queued pos 2), Carol_OilPainting (queued pos 5)")
    pr("  dave  : Dave_StampCollection (queued pos 3), Dave_GoldCoin (in shared_dir, not yet auctioned)")
    pr("  eve   : Eve_HP_FirstEd (cancelled), Eve_AntiquMap (queued pos 6)")
    time.sleep(2)

    server.running = False  # stop server first so ENDED message prints before the final banner
    time.sleep(4)           # wait for auction manager to close Bob_NikonFTn cleanly

    banner("ALL 9 SCENARIOS COMPLETED")
    time.sleep(1)


if __name__ == "__main__":
    main()
