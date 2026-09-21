import json
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth.models import User
from django.db import models


class GoogleToken(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    encrypted = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    def read(self):
        return json.loads(Fernet(settings.TOKEN_ENCRYPTION_KEY).decrypt(self.encrypted.encode()))

    def write(self, token):
        # Never persist the ID token, profile, or arbitrary provider response fields.
        safe = {k: token[k] for k in ('access_token', 'refresh_token', 'expires_at', 'scope', 'token_type') if k in token}
        self.encrypted = Fernet(settings.TOKEN_ENCRYPTION_KEY).encrypt(json.dumps(safe).encode()).decode()
        self.save()
