import subprocess
import sys
import time
import os

NUM_PEERS = 5


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    procs = []

    print("Starting Auction Server...")
    srv = subprocess.Popen(
        [sys.executable, os.path.join(base, "auction_server.py")],
        cwd=base,
    )
    procs.append(srv)
    time.sleep(2)

    for i in range(1, NUM_PEERS + 1):
        print("Starting Peer %d ..." % i)
        p = subprocess.Popen(
            [sys.executable, os.path.join(base, "peer.py"),
             str(i), "user_%d" % i, "pass_%d" % i],
            cwd=base,
        )
        procs.append(p)
        time.sleep(0.5)

    print()
    print("=" * 55)
    print("  Auction system running: 1 server + %d peers" % NUM_PEERS)
    print("  Press Ctrl+C to stop all processes")
    print("=" * 55)
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping all processes...")
        for p in reversed(procs):
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        print("Done.")


if __name__ == "__main__":
    main()
