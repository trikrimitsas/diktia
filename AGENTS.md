# AGENTS.md

## Cursor Cloud specific instructions

This is a pure-Python 3 project (standard library only, no third-party dependencies). No `requirements.txt`, `setup.py`, or `pyproject.toml` exists.

### Running the application

- **Full demo** (1 server + 5 peers): `python3 run_demo.py` — logs go to `logs/` directory. Stop with Ctrl-C.
- **Integration tests** (all 8 scenarios): `python3 test_scenarios.py` — runs in ~60 seconds (includes auction timer waits). Produces clearly labeled pass/fail output for: register, login, auction info, bidding, award, P2P transfer, seller disconnect/cancel, and logout.
- **Manual start**: `python3 auction_server.py` in one terminal, then `python3 peer.py <id> <user> <pass>` per peer.

### Gotchas

- `test_scenarios.py` starts its own `AuctionServer` in-process on port 9000. Make sure no other server is running on that port before executing tests.
- The `shared_directories/` folder is created at runtime and cleaned by `test_scenarios.py` on start. It is not checked into git.
- `run_demo.py` redirects subprocess stdout to log files; the terminal itself only shows the startup banner. Check `logs/*.log` for actual output.
- There is no linter or formatter configured in the repo. Python's built-in `py_compile` can verify syntax: `python3 -m py_compile auction_server.py peer.py protocol.py config.py run_demo.py test_scenarios.py`.
- Auction timers (`config.py`) default to spec values (60s poll, 120s item gen). `test_scenarios.py` uses shorter durations (25s, 40s) for its auctions.
