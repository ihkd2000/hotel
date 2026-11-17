"""SQLite persistence and business rules for Innkeeper.

No external dependencies. Dates are ISO YYYY-MM-DD and checkout is exclusive.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path


class RuleError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def password_hash(password, salt=None):
    if len(password) < 10:
        raise RuleError('Password must have at least 10 characters')
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260_000)
    return f'pbkdf2_sha256$260000${salt}${digest.hex()}'


def check_password(password, stored):
    try:
        algorithm, rounds, salt, digest = stored.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(candidate, bytes.fromhex(digest))
    except (ValueError, AttributeError):
        return False


def today():
    return date.today().isoformat()


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def require_text(value, label, max_len=120):
    value = str(value or '').strip()
    if not value or len(value) > max_len:
        raise RuleError(f'{label} is required (maximum {max_len} characters)')
    return value


def parse_date(value, label):
    try:
        d = date.fromisoformat(value)
        if d.isoformat() != value:
            raise ValueError()
        return d
    except (TypeError, ValueError):
        raise RuleError(f'{label} must be YYYY-MM-DD')


class Store:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._schema()

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _schema(self):
        with self.db(write=True) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                  password TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','frontdesk')),
                  active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS sessions (
                  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rooms (
                  id INTEGER PRIMARY KEY, number TEXT NOT NULL UNIQUE COLLATE NOCASE,
                  room_type TEXT NOT NULL, nightly_rate_cents INTEGER NOT NULL CHECK(nightly_rate_cents>=0),
                  state TEXT NOT NULL DEFAULT 'ready' CHECK(state IN ('ready','out_of_service'))
                );
                CREATE TABLE IF NOT EXISTS guests (
                  id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '',
                  phone TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS reservations (
                  id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL REFERENCES rooms(id),
                  guest_id INTEGER NOT NULL REFERENCES guests(id),
                  check_in TEXT NOT NULL, check_out TEXT NOT NULL,
                  rate_cents INTEGER NOT NULL CHECK(rate_cents>=0),
                  status TEXT NOT NULL CHECK(status IN ('booked','checked_in','checked_out','cancelled')),
                  created_at TEXT NOT NULL, checked_in_at TEXT, checked_out_at TEXT,
                  note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_booking_overlap
                  ON reservations(room_id, status, check_in, check_out);
                CREATE TABLE IF NOT EXISTS activity (
                  id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id),
                  action TEXT NOT NULL, details TEXT NOT NULL, occurred_at TEXT NOT NULL
                );
            ''')

    def log(self, db, actor, action, details):
        db.execute('INSERT INTO activity(actor_id,action,details,occurred_at) VALUES(?,?,?,?)',
                   (actor, action, details, timestamp()))

    def bootstrap_admin(self, username, password):
        username = require_text(username, 'Username', 60)
        with self.db(write=True) as db:
            if db.execute('SELECT 1 FROM users').fetchone():
                raise RuleError('Users already exist; initial admin creation is disabled', 409)
            db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',
                       (username, password_hash(password), 'admin'))

    def create_user(self, actor, username, password, role):
        self.require_role(actor, 'admin')
        username = require_text(username, 'Username', 60)
        if role not in ('admin', 'frontdesk'):
            raise RuleError('Role must be admin or frontdesk')
        hashed = password_hash(password)
        with self.db(write=True) as db:
            try:
                cur = db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',
                                 (username, hashed, role))
            except sqlite3.IntegrityError:
                raise RuleError('Username already exists', 409)
            self.log(db, actor['id'], 'user_created', f'{username} ({role})')
            return cur.lastrowid

    def users(self, actor):
        self.require_role(actor, 'admin')
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT id, username, role, active FROM users ORDER BY username')]

    def login(self, username, password):
        with self.db(write=True) as db:
            row = db.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE', (username,)).fetchone()
            if not row or not row['active'] or not check_password(password, row['password']):
                raise RuleError('Invalid credentials', 401)
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), row['id'], timestamp()))
            return token

    def session_user(self, token):
        if not token:
            return None
        hashed = hashlib.sha256(token.encode()).hexdigest()
        with self.db() as db:
            row = db.execute('''SELECT u.id,u.username,u.role,u.active FROM users u
                              JOIN sessions s ON s.user_id=u.id WHERE s.token_hash=?''', (hashed,)).fetchone()
            return dict(row) if row and row['active'] else None

    def logout(self, token):
        if token:
            with self.db(write=True) as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?',
                           (hashlib.sha256(token.encode()).hexdigest(),))

    @staticmethod
    def require_role(actor, *roles):
        if not actor:
            raise RuleError('Sign in required', 401)
        if actor['role'] not in roles:
            raise RuleError('Not authorized', 403)

    def add_room(self, actor, number, room_type, nightly_rate_cents):
        self.require_role(actor, 'admin')
        number = require_text(number, 'Room number', 20)
        room_type = require_text(room_type, 'Room type', 60)
        if type(nightly_rate_cents) is not int or nightly_rate_cents < 0:
            raise RuleError('Nightly rate must be a nonnegative number of cents')
        with self.db(write=True) as db:
            try:
                cur = db.execute('INSERT INTO rooms(number,room_type,nightly_rate_cents) VALUES(?,?,?)',
                                 (number, room_type, nightly_rate_cents))
            except sqlite3.IntegrityError:
                raise RuleError('Room number already exists', 409)
            self.log(db, actor['id'], 'room_added', number)
            return cur.lastrowid

    def set_room_state(self, actor, room_id, state):
        self.require_role(actor, 'admin')
        if state not in ('ready', 'out_of_service'):
            raise RuleError('Invalid room state')
        with self.db(write=True) as db:
            row = db.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone()
            if not row:
                raise RuleError('Room not found', 404)
            if state == 'out_of_service':
                occupied = db.execute('''SELECT 1 FROM reservations WHERE room_id=? AND status='checked_in' ''',
                                      (room_id,)).fetchone()
                if occupied:
                    raise RuleError('Cannot take an occupied room out of service', 409)
            db.execute('UPDATE rooms SET state=? WHERE id=?', (state, room_id))
            self.log(db, actor['id'], 'room_state_changed', f'{row["number"]}: {state}')

    def rooms(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM rooms ORDER BY number')]

    def add_guest(self, actor, full_name, email='', phone=''):
        self.require_role(actor, 'admin', 'frontdesk')
        full_name = require_text(full_name, 'Guest name', 120)
        email, phone = str(email or '').strip(), str(phone or '').strip()
        if len(email) > 160 or len(phone) > 40:
            raise RuleError('Contact information is too long')
        with self.db(write=True) as db:
            cur = db.execute('INSERT INTO guests(full_name,email,phone) VALUES(?,?,?)',
                             (full_name, email, phone))
            self.log(db, actor['id'], 'guest_added', f'Guest #{cur.lastrowid}')
            return cur.lastrowid

    def guests(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM guests ORDER BY full_name,id')]

    def available_rooms(self, check_in, check_out):
        start, end = parse_date(check_in, 'Check-in'), parse_date(check_out, 'Check-out')
        if end <= start:
            raise RuleError('Check-out must be after check-in')
        with self.db() as db:
            return [dict(r) for r in db.execute('''SELECT r.* FROM rooms r
                WHERE r.state='ready' AND NOT EXISTS (
                  SELECT 1 FROM reservations b WHERE b.room_id=r.id
                    AND b.status IN ('booked','checked_in')
                    AND b.check_in < ? AND ? < b.check_out)
                ORDER BY r.number''', (check_out, check_in))]

    def create_reservation(self, actor, room_id, guest_id, check_in, check_out, note=''):
        self.require_role(actor, 'admin', 'frontdesk')
        start, end = parse_date(check_in, 'Check-in'), parse_date(check_out, 'Check-out')
        if end <= start:
            raise RuleError('Check-out must be after check-in')
        if start < date.today():
            raise RuleError('Check-in cannot be in the past')
        note = str(note or '').strip()
        if len(note) > 500:
            raise RuleError('Note is too long')
        with self.db(write=True) as db:
            room = db.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone()
            if not room:
                raise RuleError('Room not found', 404)
            if room['state'] != 'ready':
                raise RuleError('Room is out of service', 409)
            if not db.execute('SELECT 1 FROM guests WHERE id=?', (guest_id,)).fetchone():
                raise RuleError('Guest not found', 404)
            busy = db.execute('''SELECT 1 FROM reservations WHERE room_id=?
                AND status IN ('booked','checked_in') AND check_in < ? AND ? < check_out''',
                              (room_id, check_out, check_in)).fetchone()
            if busy:
                raise RuleError('Room is not available for those dates', 409)
            cur = db.execute('''INSERT INTO reservations(room_id,guest_id,check_in,check_out,rate_cents,status,created_at,note)
                               VALUES(?,?,?,?,?,'booked',?,?)''',
                             (room_id, guest_id, check_in, check_out, room['nightly_rate_cents'], timestamp(), note))
            self.log(db, actor['id'], 'reservation_created', f'#{cur.lastrowid} room {room["number"]}')
            return cur.lastrowid

    def reservations(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('''SELECT b.*, r.number AS room_number,
                g.full_name AS guest_name, CAST(julianday(b.check_out)-julianday(b.check_in) AS INTEGER) AS nights
                FROM reservations b JOIN rooms r ON r.id=b.room_id
                JOIN guests g ON g.id=b.guest_id ORDER BY b.id DESC''')]

    def change_reservation(self, actor, booking_id, action):
        self.require_role(actor, 'admin', 'frontdesk')
        transitions = {'check_in': ('booked', 'checked_in'),
                       'check_out': ('checked_in', 'checked_out'),
                       'cancel': ('booked', 'cancelled')}
        if action not in transitions:
            raise RuleError('Unknown reservation action')
        old, new = transitions[action]
        with self.db(write=True) as db:
            row = db.execute('''SELECT b.*, r.state as room_state FROM reservations b
                                JOIN rooms r ON r.id=b.room_id WHERE b.id=?''', (booking_id,)).fetchone()
            if not row:
                raise RuleError('Reservation not found', 404)
            if row['status'] != old:
                raise RuleError(f'Cannot {action.replace("_", " ")} a {row["status"]} reservation', 409)
            if action == 'check_in':
                if row['room_state'] != 'ready':
                    raise RuleError('Room is out of service', 409)
                if not (row['check_in'] <= today() < row['check_out']):
                    raise RuleError('Check-in is allowed only during the reserved stay', 409)
                # Recheck for another checked-in booking before the transition.
                if db.execute('''SELECT 1 FROM reservations WHERE room_id=? AND id<>?
                     AND status='checked_in' ''', (row['room_id'], booking_id)).fetchone():
                    raise RuleError('Room already occupied', 409)
            if action == 'check_out' and today() < row['check_in']:
                raise RuleError('Cannot check out before scheduled arrival', 409)
            time_field = {'check_in': 'checked_in_at', 'check_out': 'checked_out_at'}.get(action)
            if time_field:
                db.execute(f'UPDATE reservations SET status=?, {time_field}=? WHERE id=?',
                           (new, timestamp(), booking_id))
            else:
                db.execute('UPDATE reservations SET status=? WHERE id=?', (new, booking_id))
            self.log(db, actor['id'], f'reservation_{new}', f'#{booking_id}')

    def dashboard(self):
        with self.db() as db:
            return dict(db.execute('''SELECT
             (SELECT COUNT(*) FROM rooms) AS rooms,
             (SELECT COUNT(*) FROM rooms WHERE state='out_of_service') AS out_of_service,
             (SELECT COUNT(*) FROM guests) AS guests,
             (SELECT COUNT(*) FROM reservations WHERE status='booked') AS booked,
             (SELECT COUNT(*) FROM reservations WHERE status='checked_in') AS checked_in''').fetchone())

    def activity(self, actor):
        self.require_role(actor, 'admin')
        with self.db() as db:
            return [dict(r) for r in db.execute('''SELECT a.*, COALESCE(u.username,'(deleted)') AS actor
                   FROM activity a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 200''')]
