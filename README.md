# Innkeeper

A small, runnable hotel front-desk application built with **Python 3.10+, SQLite, and vanilla JavaScript**. No third-party packages, build tools, or external services required.

This is an intentionally limited first version: enough for real workflow exploration without pre-building features you may want to add later.

## Run

From the repository root:

```bash
python -m hotel --init-admin
python -m hotel
```

Open **http://127.0.0.1:8000**. The admin setup prompt asks for a username and a password of at least ten characters. The database is `innkeeper.sqlite3` in the current directory. Set `INNKEEPER_DB` or use `--db hotel-dev.sqlite3` to use a different database. To change the listening port: `python -m hotel --port 8001`.

Run tests:

```bash
python -m unittest discover -s tests -v
```

## What it does

- **Staff login:** one-time initial administrator creation; admin can create front-desk users; HTTP-only SameSite session cookie; PBKDF2-SHA256 password hashes.
- **Rooms:** admin registers a room, type, nightly rate, and can mark it ready/out of service. Cannot take an occupied room out of service.
- **Guests:** front desk and admin add guest profiles and search existing guests.
- **Reservations:** find rooms available in a date range, make a reservation, check in, check out, or cancel a booking. Overlapping bookings for the same room are refused atomically (SQLite `BEGIN IMMEDIATE`). The checkout date is exclusive, so departures and arrivals may share a date.
- **History:** admin can review recent changes. Each reservation snapshots the nightly rate when created.
- **Dashboard:** room and reservation counts.

### Roles

| Function | Admin | Front desk |
|---|---|---|
| View rooms, guests, reservations | Yes | Yes |
| Add rooms / change room state | Yes | No |
| Create guest and reservation | Yes | Yes |
| Check-in / checkout / cancel booking | Yes | Yes |
| Create staff accounts / view history | Yes | No |

### Key rules and deliberate limits

1. Reservations cover `[check_in, check_out)` and block other active reservations overlapping the same room.
2. Check-in is permitted only during the reserved stay, and a checked-in room cannot be put out of service.
3. Cancellation is only permitted for booked reservations. Checked-in stays must be checked out.
4. The original nightly rate is saved on the reservation; there is no billing or payment ledger yet.
5. The application is designed for **local development and evaluation**. It does not include TLS, CSRF tokens, user password reset, rate limits, availability/maintenance calendars, payment processing, tax calculation, no-show processing, or multi-property support. Do not expose it directly to the public internet.
6. Guest names, phone numbers and emails are stored in SQLite without application-level encryption; use only test data in this starter version.

## Files

```text
hotel/
  __main__.py         CLI / start server
  store.py            SQLite schema, transactions and business rules
  server.py           HTTP API and static file serving
  static/
    index.html
    app.js            Browser UI
    style.css
 tests/
   test_store.py       Unit tests (incl. overlap concurrency)
   test_http.py        API smoke tests
```

## Possible next features

Features are intentionally **not** implemented. Good next engineering tasks include room moves and rebooking, housekeeping and readiness, multi-night billing and payments, reservation amendments with conflict checking, cancellation/no-show policies, and operational reports.
