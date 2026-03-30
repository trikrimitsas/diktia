# Αναφορα Εργασιας - Κατανεμημενο Συστημα Δημοπρασιων (Φαση 1)

**Μαθημα:** Δικτυα Υπολογιστων - Εαρινο Εξαμηνο 2025-2026  
**Γλωσσα υλοποιησης:** Python 3  

---

## 1. Συντομη Αναλυση της Υλοποιησης

Το συστημα υλοποιει ενα κατανεμημενο συστημα online δημοπρασιων βασισμενο σε
TCP sockets. Αποτελειται απο εναν **Auction Server** που διαχειριζεται τις
δημοπρασιες, και πολλαπλους **Peers/Bidders** (τουλαχιστον 5) που μπορουν να
πωλουν και να αγοραζουν αντικειμενα.

### Αρχιτεκτονικη

- **Peer -> Server**: Request-response μεσω TCP. Ο peer ανοιγει συνδεση,
  στελνει αιτημα (register, login, bid κλπ.), λαμβανει απαντηση, κλεινει τη
  συνδεση.
- **Server -> Peer**: Push notifications μεσω του ServerSocket καθε peer
  (ειδοποιησεις νεων bids, ληξη δημοπρασιας, ακυρωση, check_active).
- **Peer -> Peer**: P2P transaction μεσω του ServerSocket του πωλητη
  (μεταφορα αρχειου metadata μετα τη ληξη δημοπρασιας).

### Πρωτοκολλο Επικοινωνιας

Ολα τα μηνυματα ακολουθουν τη μορφη: **4 bytes (big-endian) μηκος + JSON
payload σε UTF-8**. Αυτο εξασφαλιζει αξιοπιστη ανταλλαγη μηνυματων μεταβλητου
μεγεθους πανω απο TCP.

### Concurrency

- Ο server χρησιμοποιει **threading** (ενα thread ανα συνδεση + ενα daemon
  thread για τη διαχειριση δημοπρασιων).
- Ολες οι κοινοχρηστες δομες δεδομενων προστατευονται με **threading.Lock**
  για αποφυγη race conditions.
- Καθε peer τρεχει 4 threads: main, peer_server, item_generator, auction_poller.

---

## 2. Περιγραφη Αρχειων

| Αρχειο | Γραμμες | Περιγραφη |
|--------|---------|-----------|
| `config.py` | 14 | Σταθερες: host, port, timeouts, RAND παραμετροι |
| `protocol.py` | 30 | Message framing: `send_message()` / `recv_message()` |
| `auction_server.py` | 485 | Auction Server: socket server, user management, auction lifecycle, notifications, checkActive |
| `peer.py` | 408 | Peer/Bidder: register/login/logout, item generation, auction polling, bidding, P2P transaction |
| `run_demo.py` | 55 | Launcher: εκκινει server + 5 peers σε ξεχωριστα processes |
| `test_scenarios.py` | 195 | Ελεγχομενα test σεναρια για ολες τις απαιτουμενες περιπτωσεις |

### Σχεσεις μεταξυ αρχειων

- `config.py` και `protocol.py` ειναι shared modules που χρησιμοποιουνται
  απο ολα τα αλλα αρχεια.
- `auction_server.py` import-αρει config + protocol.
- `peer.py` import-αρει config + protocol.
- `run_demo.py` εκκινει auction_server.py και peer.py ως ξεχωριστα processes.
- `test_scenarios.py` import-αρει ολα τα modules σε ενα ενιαιο process.

### Μεταγλωττιση / Εκτελεση

Η Python ειναι interpreted γλωσσα - δεν απαιτειται μεταγλωττιση.
Δεν χρειαζονται εξωτερικα dependencies (μονο standard library).

---

## 3. Τεκμηριωση

### Τροπος Εκτελεσης

**Αυτοματο demo (ολα μαζι):**
```
python run_demo.py
```
Εκκινει 1 server + 5 peers. Ctrl+C σταματα ολα τα processes.

**Χειροκινητη εκτελεση (ξεχωριστα terminals):**
```
Terminal 1:  python auction_server.py
Terminal 2:  python peer.py 1 user_1 pass_1
Terminal 3:  python peer.py 2 user_2 pass_2
...
Terminal 6:  python peer.py 5 user_5 pass_5
```

**Ελεγχομενα σεναρια (αναπαραγωγη screenshots):**
```
python test_scenarios.py
```

### Ρυθμισεις (config.py)

Οι τρεχουσες τιμες ειναι βελτιστοποιημενες για demo:
- `AUCTION_POLL_INTERVAL = 10` (spec: 60s)
- `ITEM_GEN_MAX_INTERVAL = 30` (spec: 120s)
- `BID_INTEREST_PROBABILITY = 0.60` (60% πιθανοτητα ενδιαφεροντος)
- `BID_INCREMENT_FACTOR = 0.10` (NewBid = Highest * (1 + RAND/10))

Για πληρη συμμορφωση με τις προδιαγραφες, αλλαξτε τις σε 60 και 120.

### Αποκλισεις απο τις Προδιαγραφες

Δεν υπαρχουν ουσιαστικες αποκλισεις. Ολες οι λειτουργιες υλοποιηθηκαν
οπως περιγραφονται στην εκφωνηση. Οι μονες προσαρμογες αφορουν τα
χρονικα διαστηματα (μικροτερα για γρηγοροτερο demo).

---

## 4. Screenshots Εξοδου

Ολα τα παρακατω παραχθηκαν αυτοματα απο το `test_scenarios.py`.
Οι γραμμες με `[SERVER ...]` ειναι η πλευρα του Auction Server.
Οι γραμμες με `[peer_name]` ειναι η πλευρα του Peer.

### 4.1 Register peer στον Auction Server

```
[alice] -> REGISTER username=alice password=pass123
[SERVER] REGISTER  alice
[alice] <- success=True  msg='Registered successfully.'

[alice] -> REGISTER username=alice (duplicate)
[alice] <- success=False  msg='Username already taken. Choose another.'

[bob]   -> REGISTER username=bob password=bobpass
[SERVER] REGISTER  bob
[bob]   <- success=True  msg='Registered successfully.'
```

**Peer-side:** Ο peer στελνει μηνυμα REGISTER με username+password.
Λαμβανει απαντηση success/failure.

**Server-side:** Ο server ελεγχει αν το username υπαρχει ηδη. Αν ναι,
απαντα με αποτυχια. Αν οχι, δημιουργει λογαριασμο.

### 4.2 Login peer στον Auction Server

```
[alice] -> LOGIN username=alice password=pass123
[SERVER] LOGIN     alice  token=264246
[alice] <- success=True  token=264246  msg='Login successful.'

[alice] -> LOGIN again (duplicate session)
[alice] <- success=False  msg='Already logged in.'
```

**Peer-side:** Ο peer στελνει LOGIN, λαμβανει τυχαιο token_id.
**Server-side:** Ο server πιστοποιει τον peer, δημιουργει μοναδικο token_id.

### 4.3 Logout peer απο τον Auction Server

```
[bob]   -> LOGOUT token=799875
[SERVER] LOGOUT    bob
[bob]   <- success=True  msg='Logged out.'

[bob]   -> LOGOUT again (invalid token)
[bob]   <- success=False  msg='Invalid token.'
```

**Peer-side:** Ο peer στελνει LOGOUT με token_id.
**Server-side:** Ο server αφαιρει τη session, ακυρωνει το token_id.

### 4.4 Πληροφοριες για τρεχουσα δημοπρασια

```
[alice] -> REQUEST_AUCTION  Object_A_01 (Vintage Watch, bid=50, dur=25s)
[SERVER] QUEUED    Object_A_01  from alice
[SERVER] AUCTION   Object_A_01 | Vintage Watch
[SERVER]           start_bid=50.00  duration=25s  seller=alice

[bob]   -> GET_CURRENT_AUCTION
[bob]   <- active=True  object_id=Object_A_01  description=Vintage Watch

[bob]   -> GET_AUCTION_DETAILS
[bob]   <- highest_bid=50.00  seller_token=264246  remaining=22.0s
```

### 4.5 Επιτυχης προσφορα πλειοδοσιας

```
[bob]   -> PLACE_BID Object_A_01 bid=52.50
[SERVER] BID       52.50  by bob  on Object_A_01
[bob]   <- success=True  msg='Bid accepted.'
[alice] notification -> new bid on Object_A_01: 52.50 by bob
[carol] notification -> new bid on Object_A_01: 52.50 by bob
[dave]  notification -> new bid on Object_A_01: 52.50 by bob

[carol] -> PLACE_BID Object_A_01 bid=56.70
[SERVER] BID       56.70  by carol  on Object_A_01
[carol] <- success=True  msg='Bid accepted.'

[dave]  -> PLACE_BID Object_A_01 bid=60.00
[SERVER] BID       60.00  by dave  on Object_A_01
[dave]  <- success=True  msg='Bid accepted.'
```

Καθε νεο bid ενημερωνει ολους τους active peers μεσω push notification.

### 4.6 Κατοχυρωση αντικειμενου στον highest bidder

```
[SERVER] ENDED     Object_A_01  won by dave  for 60.00  (seller: alice)
[dave]  *** WON auction Object_A_01 for 60.00 ***
[alice] *** SOLD Object_A_01 for 60.00 to dave ***
```

Ο server ενημερωνει τον νικητη (AUCTION_WON) με τα στοιχεια του πωλητη
και τον πωλητη (AUCTION_SOLD) με τα στοιχεια του αγοραστη.

### 4.7 Επιτυχης ολοκληρωση συναλλαγης (P2P transfer)

```
[alice] Sold Object_A_01 -> file transferred & removed
[dave]  Transaction OK -> file saved to shared_directories\dave\Object_A_01.txt
[SERVER] PURCHASE  dave  bought Object_A_01
[dave]  NOTIFY_PURCHASE sent to server

--- Checking shared_directories ---
  alice/ : (empty)
  dave/ : ['Object_A_01.txt']
    -> [object_id: Object_A_01; description: "Vintage Watch"; start_bid: "50.00"; auction_duration: "25"]

[alice] seller file Object_A_01.txt exists = False (should be False)
[dave]  buyer  file Object_A_01.txt exists = True  (should be True)
```

Ο αγοραστης (dave) συνδεεται P2P στον πωλητη (alice), λαμβανει το αρχειο
metadata, και ενημερωνει τον server. Το αρχειο αφαιρειται απο τον πωλητη.

### 4.8 Ακυρωση δημοπρασιας λογω αποσυνδεσης πωλητη

```
[eve]   -> REQUEST_AUCTION  Object_E_01 (Rare Book, bid=30, dur=40s)
[SERVER] AUCTION   Object_E_01 | Rare Book
[SERVER]           start_bid=30.00  duration=40s  seller=eve

[bob]   -> PLACE_BID Object_E_01 bid=35.00
[SERVER] BID       35.00  by bob  on Object_E_01
[bob]   <- Bid accepted.

>>> [eve] DISCONNECTING (simulating seller crash) <<<

[SERVER] CANCELLED Object_E_01  reason: Seller disconnected
[bob]   AUCTION CANCELLED: Object_E_01 reason=Seller disconnected
```

Ο server ανιχνευει την αποσυνδεση μεσω checkActive (συνδεση στο ServerSocket
του πωλητη αποτυγχανει). Ακυρωνει τη δημοπρασια και ενημερωνει ολους τους
bidders. Το token_id του πωλητη ακυρωνεται.

---

## 5. Επιπλεον Screenshots

### 5.1 Notifications σε ολους τους active peers

```
[SERVER] BID       52.50  by bob  on Object_A_01
[bob]   notification -> new bid on Object_A_01: 52.50 by bob
[dave]  notification -> new bid on Object_A_01: 52.50 by bob
[alice] notification -> new bid on Object_A_01: 52.50 by bob
[carol] notification -> new bid on Object_A_01: 52.50 by bob
[eve]   notification -> new bid on Object_A_01: 52.50 by bob
```

Σε καθε νεο bid, ο server ενημερωνει ΟΛΟ τους ενεργους peers (οχι μονο
τους bidders) μεσω push notification στο ServerSocket τους.

### 5.2 Full auction cycle (run_demo.py)

Το `run_demo.py` εκκινει 1 server + 5 peers με πληρως αυτοματοποιημενη
συμπεριφορα:
- Αυτοματη παραγωγη αντικειμενων (RAND * 30s intervals)
- Αυτοματο polling (καθε 10s)
- Αυτοματη αποφαση ενδιαφεροντος (60% πιθανοτητα)
- Αυτοματος υπολογισμος bid (NewBid = Highest * (1 + RAND/10))

---
