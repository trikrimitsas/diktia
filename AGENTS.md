# AGENTS.md

## Cursor Cloud specific instructions

### Overview
Distributed auction system ("diktia") — pure Python 3 (standard library only, zero external dependencies).

### Services
| Service | Command | Notes |
|---|---|---|
| Auction Server | `python3 auction_server.py` | TCP server on `127.0.0.1:9000`. Must start before peers. |
| Peer | `python3 peer.py <id> <username> <password>` | Each peer binds an ephemeral port for notifications. |
| Demo (server + 5 peers) | `python3 run_demo.py` | Launches everything as subprocesses. Ctrl+C stops all. |

### Testing
- **End-to-end test:** `python3 test_scenarios.py` — runs all 8 required scenarios (~50s). Exits 0 on success.
- There is no lint or type-checking configured in this repo.

### Caveats
- Port `9000` must be free before starting the server or tests. If a previous run left a zombie, kill it by PID before retrying.
- `test_scenarios.py` creates a `shared_directories/` folder in the working directory; it cleans it up on start via `shutil.rmtree`.
- The demo (`run_demo.py`) runs indefinitely; auctions are generated randomly with long intervals (up to 120s). For quick validation, prefer `test_scenarios.py`.
