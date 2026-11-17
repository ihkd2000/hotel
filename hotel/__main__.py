import argparse
import getpass
import os
from pathlib import Path
from .server import serve
from .store import Store, RuleError


def main():
    parser = argparse.ArgumentParser(description='Innkeeper hotel management')
    parser.add_argument('--db', default=os.environ.get('INNKEEPER_DB', 'innkeeper.sqlite3'))
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--init-admin', action='store_true', help='Create the initial administrator (once)')
    args = parser.parse_args()
    if args.init_admin:
        username = input('Admin username: ').strip()
        password = getpass.getpass('Admin password (10+ characters): ')
        try:
            Store(args.db).bootstrap_admin(username, password)
            print('Administrator created. Run: python -m hotel')
        except RuleError as exc:
            parser.error(str(exc))
    else:
        serve(args.db, args.host, args.port)


if __name__ == '__main__':
    main()
