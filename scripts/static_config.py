"""Create a non-secret, temporary settings file solely for collectstatic."""
import json
from pathlib import Path
import secrets
import sys
from cryptography.fernet import Fernet

directory = Path(sys.argv[1])
(directory / 'google.json').write_text(json.dumps({'web': {'client_id': 'build-only', 'client_secret': 'build-only'}}))
(directory / 'config.json').write_text(json.dumps({
    'secret_key': secrets.token_urlsafe(64), 'token_encryption_key': Fernet.generate_key().decode(),
    'google_credentials': str(directory / 'google.json'),
}))
