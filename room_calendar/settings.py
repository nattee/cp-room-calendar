import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get('ROOM_CALENDAR_CONFIG', BASE_DIR / '.private/local/config.json'))
with CONFIG_PATH.open() as f:
    CONFIG = json.load(f)

SECRET_KEY = CONFIG['secret_key']
DEBUG = CONFIG.get('debug', False)
PUBLIC_URL = CONFIG.get('public_url', 'https://room.cp.eng.chula.ac.th').rstrip('/')
ALLOWED_HOSTS = CONFIG.get('allowed_hosts', ['room.cp.eng.chula.ac.th', '127.0.0.1', 'localhost'])
CSRF_TRUSTED_ORIGINS = [PUBLIC_URL]
GOOGLE_CREDENTIALS = json.loads(Path(CONFIG['google_credentials']).read_text())['web']
TOKEN_ENCRYPTION_KEY = CONFIG['token_encryption_key']
ORG_DOMAIN = CONFIG.get('org_domain', 'cp.eng.chula.ac.th')
ALLOWED_HOSTED_DOMAINS = CONFIG.get('allowed_hosted_domains', [ORG_DOMAIN])
ROOMS = json.loads((BASE_DIR / 'rooms.json').read_text())

INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
    'django.contrib.staticfiles', 'viewer',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'viewer.middleware.PrivateResponseMiddleware',
]
ROOT_URLCONF = 'room_calendar.urls'
WSGI_APPLICATION = 'room_calendar.wsgi.application'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': ['django.template.context_processors.request']},
}]
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.sqlite3',
    'NAME': Path(CONFIG.get('data_dir', CONFIG_PATH.parent)) / 'db.sqlite3',
    'OPTIONS': {'timeout': 20},
}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
TIME_ZONE = 'Asia/Bangkok'
USE_TZ = True
LANGUAGE_CODE = 'th'
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
SESSION_COOKIE_NAME = 'room_session'
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_NAME = 'room_csrf'
SECURE_SSL_REDIRECT = not DEBUG
SECURE_REDIRECT_EXEMPT = [r'^healthz$']
# Gunicorn binds to loopback. Apache overwrites this header for this HTTPS-only vhost.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
# Deliberately scope HTTPS policy to this host; do not opt into browser preload.
SILENCED_SYSTEM_CHECKS = ['security.W005', 'security.W021']
SECURE_CONTENT_TYPE_NOSNIFF = True
# Preserve same-origin form provenance for HTTPS CSRF validation.
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
LOGGING = {
    'version': 1, 'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'WARNING'},
}
