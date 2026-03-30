"""
test_scenarios.py
Controlled demonstration of all required auction system scenarios.
Produces clearly separated output for each case (peer-side + server-side).
"""
import socket, threading, logging, time, os, sys, shutil
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
                pr("[%s] notification -> new bid on %s: %.2f by %s" % (
                    name, msg["object_id"], msg["highest_bid"], msg["bidder_username"]))
                send_message(sock, {"type": "ACK"})
            elif t == "AUCTION_WON":
                pr("[%s] *** WON auction %s for %.2f ***" % (name, msg["object_id"], msg["final_bid"]))
                send_message(sock, {"type": "ACK"})
                threading.Thread(target=_do_buy, args=(
                    name, msg["object_id"], msg["final_bid"],
                    msg["seller_ip"], msg["seller_port"]), daemon=True).start()
            elif t == "AUCTION_SOLD":
                pr("[%s] *** SOLD %s for %.2f to %s ***" % (
                    name, msg["object_id"], msg["final_bid"], msg["buyer_username"]))
                send_message(sock, {"type": "ACK"})
            elif t == "AUCTION_CANCELLED":
                pr("[%s] AUCTION CANCELLED: %s reason=%s" % (name, msg["object_id"], msg["reason"]))
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
            pr("[%s] Transaction OK -> file saved to %s" % (buyer, fp))
            if buyer in tokens:
                try:
                    send_req({"type": "NOTIFY_PURCHASE",
                              "token_id": tokens[buyer], "object_id": oid})
                    pr("[%s] NOTIFY_PURCHASE sent to server" % buyer)
                except Exception:
                    pass
        else:
            pr("[%s] Transaction FAILED for %s" % (buyer, oid))
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
        pr("[%s] Sold %s -> file transferred & removed" % (seller, oid))
    else:
        send_message(sock, {"type": "TRANSACTION_RESP", "success": False,
                             "object_id": oid, "metadata": ""})


def main():
    shutil.rmtree("shared_directories", ignore_errors=True)
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

    os.makedirs("shared_directories/alice", exist_ok=True)
    with open("shared_directories/alice/Object_A_01.txt", "w") as f:
        f.write('[object_id: Object_A_01; description: "Vintage Watch"; '
                'start_bid: "50.00"; auction_duration: "25"]')

    pr("[alice] -> REQUEST_AUCTION  Object_A_01 (Vintage Watch, bid=50, dur=25s)")
    r = send_req({"type": "REQUEST_AUCTION", "token_id": tokens["alice"],
                   "items": [{"object_id": "Object_A_01", "description": "Vintage Watch",
                              "start_bid": 50.0, "auction_duration": 25}],
                   "ip_address": "127.0.0.1", "port": ports["alice"]})
    pr("[alice] <- %s" % r["message"])

    time.sleep(3)

    pr("[bob]   -> GET_CURRENT_AUCTION")
    r = send_req({"type": "GET_CURRENT_AUCTION", "token_id": tokens["bob"]})
    pr("[bob]   <- active=%s  object_id=%s  description=%s" % (
        r.get("active"), r.get("object_id"), r.get("description")))

    pr("[bob]   -> GET_AUCTION_DETAILS")
    r = send_req({"type": "GET_AUCTION_DETAILS", "token_id": tokens["bob"]})
    pr("[bob]   <- highest_bid=%.2f  seller_token=%s  remaining=%.1fs" % (
        r.get("highest_bid", 0), r.get("seller_token_id", ""), r.get("remaining_time", 0)))

    # ======================== 4. SUCCESSFUL BIDS ========================
    banner("4. Successful bid (placeBid)")

    pr("[bob]   -> PLACE_BID Object_A_01 bid=52.50")
    r = send_req({"type": "PLACE_BID", "token_id": tokens["bob"],
                   "object_id": "Object_A_01", "bid": 52.50})
    pr("[bob]   <- success=%s  msg='%s'" % (r["success"], r["message"]))
    time.sleep(1)

    pr("[carol] -> PLACE_BID Object_A_01 bid=56.70")
    r = send_req({"type": "PLACE_BID", "token_id": tokens["carol"],
                   "object_id": "Object_A_01", "bid": 56.70})
    pr("[carol] <- success=%s  msg='%s'" % (r["success"], r["message"]))
    time.sleep(1)

    pr("[dave]  -> PLACE_BID Object_A_01 bid=60.00")
    r = send_req({"type": "PLACE_BID", "token_id": tokens["dave"],
                   "object_id": "Object_A_01", "bid": 60.00})
    pr("[dave]  <- success=%s  msg='%s'" % (r["success"], r["message"]))

    # ======================== 5. AUCTION END + AWARD ========================
    banner("5. Auction ends - awarded to highest bidder")
    pr("Waiting for auction Object_A_01 to end (~20s remaining)...")
    time.sleep(22)

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
    pr("[alice] seller file Object_A_01.txt exists = %s (should be False)" %
       os.path.exists("shared_directories/alice/Object_A_01.txt"))
    pr("[dave]  buyer  file Object_A_01.txt exists = %s (should be True)" %
       os.path.exists("shared_directories/dave/Object_A_01.txt"))

    # ======================== 7. SELLER DISCONNECT ========================
    banner("7. Auction cancellation - seller disconnects")

    os.makedirs("shared_directories/eve", exist_ok=True)
    with open("shared_directories/eve/Object_E_01.txt", "w") as f:
        f.write('[object_id: Object_E_01; description: "Rare Book"; '
                'start_bid: "30.00"; auction_duration: "40"]')

    pr("[eve]   -> REQUEST_AUCTION  Object_E_01 (Rare Book, bid=30, dur=40s)")
    r = send_req({"type": "REQUEST_AUCTION", "token_id": tokens["eve"],
                   "items": [{"object_id": "Object_E_01",
                              "description": "Rare Book",
                              "start_bid": 30.0, "auction_duration": 40}],
                   "ip_address": "127.0.0.1", "port": ports["eve"]})
    pr("[eve]   <- %s" % r["message"])
    time.sleep(3)

    pr("[bob]   -> PLACE_BID Object_E_01 bid=35.00")
    r = send_req({"type": "PLACE_BID", "token_id": tokens["bob"],
                   "object_id": "Object_E_01", "bid": 35.0})
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

    banner("ALL 8 SCENARIOS COMPLETED")
    server.running = False
    time.sleep(1)


if __name__ == "__main__":
    main()
