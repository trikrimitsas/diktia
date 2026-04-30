import json
import logging
import os
import shlex
import socket
import sys
import threading
import time
from typing import Optional

import config
import gbn_udp
from protocol import recv_message, send_message


class ManualClient:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.token_id: Optional[str] = None
        self.running = True
        self.peer_port: Optional[int] = None
        self.udp_port: Optional[int] = None
        self.udp_sock: Optional[socket.socket] = None
        self.udp_lock = threading.Lock()
        self.shared_dir = os.path.join("shared_directories", username)
        self.last_won = None
        self.last_auctions: list = []
        os.makedirs(self.shared_dir, exist_ok=True)

    def _print_json(self, prefix: str, payload: dict) -> None:
        print(prefix)
        print(json.dumps(payload, indent=2, ensure_ascii=False), flush=True)

    def _send_to_server(self, msg: dict) -> dict:
        self._print_json(">>> SERVER REQUEST", msg)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(config.SOCKET_TIMEOUT)
        s.connect((config.SERVER_HOST, config.SERVER_PORT))
        try:
            send_message(s, msg)
            resp = recv_message(s)
        finally:
            s.close()
        self._print_json("<<< SERVER RESPONSE", resp)
        return resp

    def _listener_loop(self) -> None:
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

        print(
            f"[LISTENER] {self.username} TCP {config.SERVER_HOST}:{self.peer_port} "
            f"UDP {self.udp_port}",
            flush=True,
        )

        while self.running:
            try:
                client, _ = srv.accept()
                threading.Thread(
                    target=self._handle_incoming,
                    args=(client,),
                    daemon=True,
                ).start()
            except socket.timeout:
                pass
        srv.close()
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except OSError:
                pass

    def _handle_incoming(self, sock: socket.socket) -> None:
        try:
            msg = recv_message(sock)
            self._print_json("<<< INCOMING MESSAGE", msg)
            msg_type = msg.get("type")

            if msg_type == "CHECK_ACTIVE":
                send_message(sock, {"type": "CHECK_ACTIVE_RESP", "active": True})
                self._print_json(">>> INCOMING RESPONSE", {"type": "CHECK_ACTIVE_RESP", "active": True})
                return

            if msg_type == "NEW_BID_NOTIFY":
                send_message(sock, {"type": "ACK"})
                self._print_json(">>> INCOMING RESPONSE", {"type": "ACK"})
                return

            if msg_type == "AUCTION_CANCELLED":
                send_message(sock, {"type": "ACK"})
                self._print_json(">>> INCOMING RESPONSE", {"type": "ACK"})
                return

            if msg_type == "AUCTION_SOLD":
                send_message(sock, {"type": "ACK"})
                self._print_json(">>> INCOMING RESPONSE", {"type": "ACK"})
                return

            if msg_type == "AUCTION_WON":
                self.last_won = {
                    "object_id": msg["object_id"],
                    "final_bid": msg["final_bid"],
                    "seller_ip": msg["seller_ip"],
                    "seller_port": msg["seller_port"],
                    "seller_udp_port": int(msg.get("seller_udp_port", msg["seller_port"])),
                }
                send_message(sock, {"type": "ACK"})
                self._print_json(">>> INCOMING RESPONSE", {"type": "ACK"})
                print(
                    "[INFO] Use 'buy last' to complete the transaction for the won auction.",
                    flush=True,
                )
                return

            if msg_type == "TRANSACTION_REQ":
                self._handle_sell(sock, msg)
                return

            send_message(sock, {"type": "ACK"})
            self._print_json(">>> INCOMING RESPONSE", {"type": "ACK"})
        except Exception as exc:
            print(f"[ERROR] Incoming handler failed: {exc}", flush=True)
        finally:
            sock.close()

    def _parse_item(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip().strip("[]")
            parts = {}
            for seg in raw.split(";"):
                seg = seg.strip()
                if ":" in seg:
                    key, value = seg.split(":", 1)
                    parts[key.strip()] = value.strip().strip('"')
            return {
                "object_id": parts["object_id"],
                "description": parts.get("description", ""),
                "start_bid": float(parts.get("start_bid", "10")),
                "auction_duration": int(parts.get("auction_duration", "30")),
            }
        except Exception as exc:
            print(f"[ERROR] Could not parse item file {path}: {exc}", flush=True)
            return None

    def _scan_shared_dir(self):
        items = []
        for fname in sorted(os.listdir(self.shared_dir)):
            if not fname.endswith(".txt"):
                continue
            meta = self._parse_item(os.path.join(self.shared_dir, fname))
            if meta:
                items.append(meta)
        return items

    def _handle_sell(self, sock: socket.socket, msg: dict) -> None:
        oid = msg["object_id"]
        path = os.path.join(self.shared_dir, f"{oid}.txt")
        buyer_udp = msg.get("buyer_udp_port")

        if not os.path.exists(path):
            resp = {
                "type": "TRANSACTION_RESP",
                "success": False,
                "object_id": oid,
                "metadata": "",
            }
            send_message(sock, resp)
            self._print_json(">>> INCOMING RESPONSE", resp)
            return

        with open(path, "r", encoding="utf-8") as fh:
            metadata = fh.read()

        if buyer_udp is None:
            resp = {
                "type": "TRANSACTION_RESP",
                "success": True,
                "object_id": oid,
                "metadata": metadata,
            }
            send_message(sock, resp)
            self._print_json(">>> INCOMING RESPONSE", resp)
            os.remove(path)
            print(f"[INFO] Sold {oid} (TCP) and removed {path}", flush=True)
            return

        buyer_ip = sock.getpeername()[0]
        buyer_udp = int(buyer_udp)
        packets = gbn_udp.packetize_metadata(metadata.encode("utf-8"))
        resp = {
            "type": "TRANSACTION_RESP",
            "success": True,
            "object_id": oid,
            "metadata": "",
            "udp": True,
        }
        send_message(sock, resp)
        self._print_json(">>> INCOMING RESPONSE", resp)
        try:
            sock.close()
        except OSError:
            pass

        usock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ok = gbn_udp.GbnUdpSender(
            usock,
            (buyer_ip, buyer_udp),
            packets,
            log=lambda m: print(f"[GBN] {m}", flush=True),
        ).run()
        if ok:
            os.remove(path)
            print(f"[INFO] Sold {oid} via UDP GBN; removed {path}", flush=True)
        else:
            print("[WARN] UDP transfer did not complete; file kept.", flush=True)

    def cmd_register(self) -> None:
        self._send_to_server(
            {
                "type": "REGISTER",
                "username": self.username,
                "password": self.password,
            }
        )

    def cmd_login(self) -> None:
        if self.peer_port is None:
            print("[ERROR] Listener is not ready yet.", flush=True)
            return
        resp = self._send_to_server(
            {
                "type": "LOGIN",
                "username": self.username,
                "password": self.password,
            }
        )
        if resp.get("success"):
            self.token_id = resp.get("token_id")

    def cmd_logout(self) -> None:
        if not self.token_id:
            print("[ERROR] You are not logged in.", flush=True)
            return
        resp = self._send_to_server({"type": "LOGOUT", "token_id": self.token_id})
        if resp.get("success"):
            self.token_id = None

    def cmd_add_item(self, args) -> None:
        if len(args) < 4:
            print("Usage: add-item <object_id> <start_bid> <duration> <description>", flush=True)
            return
        object_id = args[0]
        start_bid = float(args[1])
        duration = int(args[2])
        description = " ".join(args[3:])
        path = os.path.join(self.shared_dir, f"{object_id}.txt")
        content = (
            f'[object_id: {object_id}; description: "{description}"; '
            f'start_bid: "{start_bid:.2f}"; auction_duration: "{duration}"]'
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"[INFO] Created {path}", flush=True)

    def cmd_list_items(self) -> None:
        items = self._scan_shared_dir()
        if not items:
            print("[INFO] No local items found.", flush=True)
            return
        print("[INFO] Local items:", flush=True)
        for item in items:
            print(json.dumps(item, indent=2, ensure_ascii=False), flush=True)

    def cmd_request_all(self) -> None:
        if not self.token_id:
            print("[ERROR] Login first.", flush=True)
            return
        items = self._scan_shared_dir()
        resp = self._send_to_server(
            {
                "type": "REQUEST_AUCTION",
                "token_id": self.token_id,
                "items": items,
                "ip_address": config.SERVER_HOST,
                "port": self.peer_port,
                "udp_port": self.udp_port,
            }
        )
        if not items:
            print("[INFO] Shared directory is empty; server just updated your contact info.", flush=True)
        if not resp.get("success"):
            print("[WARN] REQUEST_AUCTION failed.", flush=True)

    def cmd_current(self) -> None:
        if not self.token_id:
            print("[ERROR] Login first.", flush=True)
            return
        r = self._send_to_server({"type": "GET_CURRENT_AUCTION", "token_id": self.token_id})
        if r.get("active") and r.get("auctions"):
            self.last_auctions = r["auctions"]
        elif r.get("active"):
            self.last_auctions = [{"object_id": r["object_id"], "description": r.get("description", "")}]
        else:
            self.last_auctions = []

    def cmd_details(self, args) -> None:
        if not self.token_id:
            print("[ERROR] Login first.", flush=True)
            return
        oid = args[0] if args else None
        if oid is None:
            if len(self.last_auctions) == 1:
                oid = self.last_auctions[0]["object_id"]
            else:
                print("[ERROR] details <object_id> (or run 'current' when exactly one auction).", flush=True)
                return
        self._send_to_server(
            {
                "type": "GET_AUCTION_DETAILS",
                "token_id": self.token_id,
                "object_id": oid,
            }
        )

    def cmd_bid(self, args) -> None:
        if len(args) != 2:
            print("Usage: bid <object_id> <amount>", flush=True)
            return
        if not self.token_id:
            print("[ERROR] Login first.", flush=True)
            return
        self._send_to_server(
            {
                "type": "PLACE_BID",
                "token_id": self.token_id,
                "object_id": args[0],
                "bid": float(args[1]),
            }
        )

    def cmd_buy(self, args) -> None:
        if args == ["last"]:
            if self.last_won is None:
                print("[ERROR] No won auction is pending.", flush=True)
                return
            info = self.last_won
            object_id = info["object_id"]
            bid = info["final_bid"]
            seller_ip = info["seller_ip"]
            seller_port = info["seller_port"]
            seller_udp = int(info.get("seller_udp_port", seller_port))
        elif len(args) == 5:
            object_id = args[0]
            bid = float(args[1])
            seller_ip = args[2]
            seller_port = int(args[3])
            seller_udp = int(args[4])
        elif len(args) == 4:
            object_id = args[0]
            bid = float(args[1])
            seller_ip = args[2]
            seller_port = int(args[3])
            seller_udp = seller_port
        else:
            print(
                "Usage: buy last | buy <object_id> <bid> <seller_ip> <seller_tcp_port> [seller_udp_port]",
                flush=True,
            )
            return

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(config.SOCKET_TIMEOUT)
        try:
            s.connect((seller_ip, int(seller_port)))
            req = {
                "type": "TRANSACTION_REQ",
                "object_id": object_id,
                "bid": bid,
                "buyer_username": self.username,
                "buyer_udp_port": self.udp_port,
            }
            self._print_json(">>> PEER REQUEST", req)
            send_message(s, req)
            resp = recv_message(s)
            self._print_json("<<< PEER RESPONSE", resp)
        finally:
            s.close()

        if not resp.get("success"):
            print("[ERROR] Transaction failed.", flush=True)
            if self.token_id:
                self._send_to_server(
                    {
                        "type": "TRANSACTION_REPORT",
                        "token_id": self.token_id,
                        "object_id": object_id,
                        "outcome": "fail",
                    }
                )
            return

        if resp.get("udp"):
            if not self.udp_sock:
                print("[ERROR] UDP socket not ready.", flush=True)
                return
            with self.udp_lock:
                meta_bytes = gbn_udp.receive_metadata(
                    self.udp_sock,
                    drop_data_prob=float(config.UDP_DROP_DATA_PROB),
                    ack_send_prob=float(config.UDP_ACK_SEND_PROB),
                    log=lambda m: print(f"[GBN] {m}", flush=True),
                    deadline=time.time() + 120.0,
                )
            if meta_bytes is None:
                print("[ERROR] UDP receive incomplete.", flush=True)
                if self.token_id:
                    self._send_to_server(
                        {
                            "type": "TRANSACTION_REPORT",
                            "token_id": self.token_id,
                            "object_id": object_id,
                            "outcome": "fail",
                        }
                    )
                return
            text = meta_bytes.decode("utf-8")
        else:
            text = resp.get("metadata", "")

        path = os.path.join(self.shared_dir, f"{object_id}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"[INFO] Saved purchased item to {path}", flush=True)

        if self.token_id:
            self._send_to_server(
                {
                    "type": "TRANSACTION_REPORT",
                    "token_id": self.token_id,
                    "object_id": object_id,
                    "outcome": "success",
                }
            )

    def cmd_status(self) -> None:
        print(
            json.dumps(
                {
                    "username": self.username,
                    "logged_in": bool(self.token_id),
                    "token_id": self.token_id,
                    "peer_port": self.peer_port,
                    "udp_port": self.udp_port,
                    "shared_dir": self.shared_dir,
                    "server_host": config.SERVER_HOST,
                    "server_port": config.SERVER_PORT,
                },
                indent=2,
                ensure_ascii=False,
            ),
            flush=True,
        )

    def print_help(self) -> None:
        print(
            "Available commands:\n"
            "  help\n"
            "  status\n"
            "  register\n"
            "  login\n"
            "  logout\n"
            "  add-item <object_id> <start_bid> <duration> <description>\n"
            "  list-items\n"
            "  request-all\n"
            "  current\n"
            "  details [object_id]\n"
            "  bid <object_id> <amount>\n"
            "  buy last\n"
            "  buy <object_id> <bid> <seller_ip> <seller_tcp_port> [seller_udp_port]\n"
            "  exit\n",
            flush=True,
        )

    def run(self) -> None:
        threading.Thread(target=self._listener_loop, daemon=True).start()

        while self.peer_port is None:
            pass

        self.print_help()
        while self.running:
            try:
                raw = input(f"[{self.username}]> ").strip()
            except (EOFError, KeyboardInterrupt):
                raw = "exit"

            if not raw:
                continue

            parts = shlex.split(raw)
            cmd = parts[0].lower()
            args = parts[1:]

            try:
                if cmd == "help":
                    self.print_help()
                elif cmd == "status":
                    self.cmd_status()
                elif cmd == "register":
                    self.cmd_register()
                elif cmd == "login":
                    self.cmd_login()
                elif cmd == "logout":
                    self.cmd_logout()
                elif cmd == "add-item":
                    self.cmd_add_item(args)
                elif cmd == "list-items":
                    self.cmd_list_items()
                elif cmd == "request-all":
                    self.cmd_request_all()
                elif cmd == "current":
                    self.cmd_current()
                elif cmd == "details":
                    self.cmd_details(args)
                elif cmd == "bid":
                    self.cmd_bid(args)
                elif cmd == "buy":
                    self.cmd_buy(args)
                elif cmd in {"exit", "quit"}:
                    if self.token_id:
                        self.cmd_logout()
                    self.running = False
                else:
                    print(f"[ERROR] Unknown command: {cmd}", flush=True)
            except Exception as exc:
                print(f"[ERROR] Command failed: {exc}", flush=True)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python manual_client.py <username> <password>", flush=True)
        raise SystemExit(1)

    logging.basicConfig(level=logging.WARNING)
    client = ManualClient(sys.argv[1], sys.argv[2])
    client.run()


if __name__ == "__main__":
    main()