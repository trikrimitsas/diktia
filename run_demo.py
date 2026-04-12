import subprocess
import sys
import time
import os

NUM_PEERS = 5


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base, "logs")
    os.makedirs(log_dir, exist_ok=True)

    procs = []
    log_files = []

    print("Starting Auction Server...")
    srv_log = open(os.path.join(log_dir, "server.log"), "w")
    log_files.append(srv_log)
    srv = subprocess.Popen(
        [sys.executable, os.path.join(base, "auction_server.py")],
        cwd=base,
        stdout=srv_log,
        stderr=subprocess.STDOUT,
    )
    procs.append(srv)
    time.sleep(2)

    for i in range(1, NUM_PEERS + 1):
        print("Starting Peer %d ..." % i)
        p_log = open(os.path.join(log_dir, "peer_%d.log" % i), "w")
        log_files.append(p_log)
        p = subprocess.Popen(
            [sys.executable, os.path.join(base, "peer.py"),
             str(i), "user_%d" % i, "pass_%d" % i],
            cwd=base,
            stdout=p_log,
            stderr=subprocess.STDOUT,
        )
        procs.append(p)
        time.sleep(0.5)

    print()
    print("=" * 55)
    print("  Auction system running: 1 server + %d peers" % NUM_PEERS)
    print("  Logs: %s/" % log_dir)
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
        for f in log_files:
            f.close()
        print("Done. Logs saved in %s/" % log_dir)


if __name__ == "__main__":
    main()
