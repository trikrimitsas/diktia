import os


def _get_int_env(name: str, default: int) -> int:
	raw = os.getenv(name)
	if raw is None or raw == "":
		return default
	try:
		return int(raw)
	except ValueError:
		return default


SERVER_HOST = os.getenv("DIKTIA_SERVER_HOST", "127.0.0.1")
SERVER_PORT = _get_int_env("DIKTIA_SERVER_PORT", 9000)

# Spec values: POLL=60, ITEM_GEN=120. Reduced for faster demo.
AUCTION_POLL_INTERVAL = 60
CHECK_ACTIVE_INTERVAL = 5
ITEM_GEN_MAX_INTERVAL = 120

BID_INTEREST_PROBABILITY = 0.60
BID_INCREMENT_FACTOR = 0.10
# Final fraction of auction duration where automated peers may bid up to this increment.
LATE_AUCTION_FRACTION = 0.10
LATE_BID_INCREMENT_FACTOR = 0.20

MAX_ACTIVE_AUCTIONS = 2

# UDP loss simulation (Phase 2 demo/test); deterministic when rng seed set in tests.
UDP_DROP_DATA_PROB = float(os.getenv("DIKTIA_UDP_DROP_DATA_PROB", "0.0"))
UDP_ACK_SEND_PROB = float(os.getenv("DIKTIA_UDP_ACK_SEND_PROB", "1.0"))

# Winning bidder proceeds with transaction (peer automated behavior).
TRANSACTION_PROCEED_PROBABILITY = 0.70

SOCKET_TIMEOUT = 5
RECV_BUFFER = 4096
