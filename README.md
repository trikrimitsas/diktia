# diktia phase 2

Distributed auction system in Python using only the standard library.

## Phase 2 features

- Up to two active auctions at the same time.
- `object_id` routing for details, bids and transaction reports.
- Automated late bidding: up to 10% normally, up to 20% in the final 10%.
- Bid ranking and fallback award when the current buyer cancels or fails.
- Buyer reputation initialized at 1.0 and updated with `0.75 * old + 0.25 * outcome`.
- Reputation-aware queue selection between the first two queued sellers.
- P2P metadata transfer over UDP Go-Back-N with 64-byte packets, window size 3, cumulative ACKs and timeout retransmission.
- Configurable UDP packet/ACK loss simulation for demos.

## Run

```bash
python3 auction_server.py
python3 peer.py 1 alice pass123 --no-auto-items
python3 peer.py 2 bob bobpass --no-auto-items
```

For an automated multi-peer run:

```bash
python3 run_demo.py
```

For deterministic scenario output:

```bash
python3 test_scenarios.py
```

For Phase 2 unit tests:

```bash
python3 -m unittest test_phase2_units.py
```
