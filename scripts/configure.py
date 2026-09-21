#!/usr/bin/env python3
"""Create private configuration once, without printing any secrets."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
from cryptography.fernet import Fernet

parser = argparse.ArgumentParser()
parser.add_argument('credentials', type=Path)
parser.add_argument('--production', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
private = root / '.private' / ('production' if args.production else 'local')
private.mkdir(mode=0o700, parents=True, exist_ok=True)
private.parent.chmod(0o700)
private.chmod(0o700)
source = json.loads(args.credentials.read_text())
web = source.get('web', {})
if not web.get('client_id') or not web.get('client_secret'):
    raise SystemExit('Expected a Google web-application credential JSON.')
expected = 'https://room.cp.eng.chula.ac.th/auth/google/callback'
if expected not in web.get('redirect_uris', []):
    raise SystemExit('Missing production callback URI in Google credentials.')
destination = private / 'google.json'
config = private / 'config.json'
if config.exists():
    raise SystemExit('Configuration already exists; refusing to rotate existing keys.')
os.umask(0o077)
shutil.copyfile(args.credentials, destination)
destination.chmod(0o600)
data = {
    'secret_key': secrets.token_urlsafe(64),
    'token_encryption_key': Fernet.generate_key().decode(),
    'google_credentials': '/etc/room-calendar/google.json' if args.production else str(destination),
    'data_dir': '/var/lib/room-calendar' if args.production else str(private),
    'debug': not args.production,
    'public_url': 'https://room.cp.eng.chula.ac.th' if args.production else 'http://127.0.0.1:8000',
    'allowed_hosts': ['room.cp.eng.chula.ac.th', '127.0.0.1', 'localhost'],
    'org_domain': 'cp.eng.chula.ac.th',
    'allowed_hosted_domains': ['cp.eng.chula.ac.th'],
}
config.write_text(json.dumps(data, indent=2) + '\n')
config.chmod(0o600)
print('Private configuration created:', config)
if not args.production:
    print('For local sign-in, also register http://127.0.0.1:8000/auth/google/callback in Google Cloud.')
