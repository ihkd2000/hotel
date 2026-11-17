"""HTTP application. JSON API plus a small dependency-free browser client."""
import json
import mimetypes
import re
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .store import RuleError, Store

STATIC = Path(__file__).parent / 'static'


class App(BaseHTTPRequestHandler):
    store = None

    def _send(self, status, data=None, content_type='application/json', cookie=None):
        body = json.dumps(data).encode() if content_type == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'same-origin')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def _token(self):
        raw = self.headers.get('Cookie', '')
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return None
        return cookie['inn_session'].value if 'inn_session' in cookie else None

    def _actor(self):
        actor = self.store.session_user(self._token())
        if not actor:
            raise RuleError('Sign in required', 401)
        return actor

    def _json(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not (0 < size <= 100_000):
                raise RuleError('Invalid request size', 400)
            result = json.loads(self.rfile.read(size))
            if not isinstance(result, dict):
                raise RuleError('Expected JSON object')
            return result
        except (ValueError, json.JSONDecodeError):
            raise RuleError('Invalid JSON')

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == '/' or path in ('/app.js', '/style.css'):
                asset = 'index.html' if path == '/' else path[1:]
                data = (STATIC / asset).read_bytes()
                kind = mimetypes.guess_type(asset)[0] or 'text/plain'
                return self._send(200, data, kind + '; charset=utf-8')
            if not path.startswith('/api/'):
                raise RuleError('Not found', 404)
            actor = self._actor()
            query = parse_qs(parsed.query)
            if path == '/api/me':
                result = {k: actor[k] for k in ('id','username','role')}
            elif path == '/api/dashboard':
                result = self.store.dashboard()
            elif path == '/api/rooms':
                result = self.store.rooms()
            elif path == '/api/available':
                result = self.store.available_rooms(query.get('check_in', [None])[0], query.get('check_out', [None])[0])
            elif path == '/api/guests':
                result = self.store.guests()
            elif path == '/api/reservations':
                result = self.store.reservations()
            elif path == '/api/users':
                result = self.store.users(actor)
            elif path == '/api/activity':
                result = self.store.activity(actor)
            else:
                raise RuleError('Not found', 404)
            self._send(200, result)
        except RuleError as exc:
            self._send(exc.status, {'error': str(exc)})
        except Exception:
            self.log_error('Unhandled GET error: %s', self.path)
            self._send(500, {'error': 'Internal server error'})

    def do_POST(self):
        try:
            path = urlsplit(self.path).path
            if not path.startswith('/api/'):
                raise RuleError('Not found', 404)
            data = self._json()
            if path == '/api/login':
                token = self.store.login(data.get('username'), data.get('password', ''))
                return self._send(200, {'ok': True}, cookie=f'inn_session={token}; HttpOnly; SameSite=Strict; Path=/')
            actor = self._actor()
            result = {'ok': True}
            if path == '/api/logout':
                self.store.logout(self._token())
                return self._send(200, result, cookie='inn_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            if path == '/api/rooms':
                result = {'id': self.store.add_room(actor, data.get('number'), data.get('room_type'), data.get('nightly_rate_cents'))}
            elif (m := re.fullmatch(r'/api/rooms/(\d+)/state', path)):
                self.store.set_room_state(actor, int(m[1]), data.get('state'))
            elif path == '/api/guests':
                result = {'id': self.store.add_guest(actor, data.get('full_name'), data.get('email'), data.get('phone'))}
            elif path == '/api/reservations':
                result = {'id': self.store.create_reservation(actor, data.get('room_id'), data.get('guest_id'),
                                  data.get('check_in'), data.get('check_out'), data.get('note', ''))}
            elif (m := re.fullmatch(r'/api/reservations/(\d+)/(check_in|check_out|cancel)', path)):
                self.store.change_reservation(actor, int(m[1]), m[2])
            elif path == '/api/users':
                result = {'id': self.store.create_user(actor, data.get('username'), data.get('password',''), data.get('role'))}
            else:
                raise RuleError('Not found', 404)
            self._send(200, result)
        except RuleError as exc:
            self._send(exc.status, {'error': str(exc)})
        except Exception:
            self.log_error('Unhandled POST error: %s', self.path)
            self._send(500, {'error': 'Internal server error'})


def serve(db_path, host='127.0.0.1', port=8000):
    store = Store(db_path)
    handler = type('InnkeeperHandler', (App,), {'store': store})
    server = ThreadingHTTPServer((host, port), handler)
    print(f'Innkeeper running at http://{host}:{port} (database: {db_path})', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
