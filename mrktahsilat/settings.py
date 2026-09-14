"""
Django settings for mrktahsilat project.
GÜVENLIK: Hassas bilgiler .env dosyasından yüklenir
"""
from pathlib import Path
import os
from .env_loader import load_env_file, get_env_var, get_env_bool, get_env_list, get_env_int

BASE_DIR = Path(__file__).resolve().parent.parent

# .env dosyasını yükle
load_env_file(os.path.join(BASE_DIR, '.env'))

# Güvenlik Ayarları - Environment Variables'dan Yüklenir
SECRET_KEY = get_env_var('SECRET_KEY', required=True)
DEBUG = False
ALLOWED_HOSTS = ['mrktahsilat.com', 'www.mrktahsilat.com',
                 '72.62.52.105', 'localhost', '127.0.0.1']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'tahsilat',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'tahsilat.middleware.URLDebugMiddleware',  # Debug middleware for URL tracking
]

ROOT_URLCONF = 'mrktahsilat.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': False,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
            'debug': get_env_bool('DEBUG', default=False),
            # Önbelleksiz loader: Şablon değişiklikleri tüm worker'larda anında görünsün (arada bir hatalı gösterim olmasın)
            'loaders': [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ],
        },
    },
]

WSGI_APPLICATION = 'mrktahsilat.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTHENTICATION_BACKENDS = [
    'tahsilat.authentication.MSSQLAuthenticationBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# MSSQL Konfigürasyonu - Environment Variables'dan Yüklenir
MSSQL_CONFIG = {
    'server': get_env_var('MSSQL_SERVER', required=True),
    'port': get_env_int('MSSQL_PORT', default=1433),
    'database': get_env_var('MSSQL_DATABASE', required=True),
    'username': get_env_var('MSSQL_USERNAME', required=True),
    'password': get_env_var('MSSQL_PASSWORD', required=True),
    'driver': get_env_var('MSSQL_DRIVER', default='ODBC Driver 17 for SQL Server'),
    'charset': get_env_var('MSSQL_CHARSET', default='utf-8'),
    'trust_server_certificate': get_env_var('MSSQL_TRUST_CERT', default='yes'),
    'encrypt': get_env_var('MSSQL_ENCRYPT', default='no')
}

# Güvenlik Ayarları
AUTH_PASSWORD_VALIDATORS = []

# Güvenlik Headers (Production için)
if not DEBUG:
    SECURE_SSL_REDIRECT = get_env_bool('SECURE_SSL_REDIRECT', default=True)
    SECURE_BROWSER_XSS_FILTER = get_env_bool(
        'SECURE_BROWSER_XSS_FILTER', default=True)
    SECURE_CONTENT_TYPE_NOSNIFF = get_env_bool(
        'SECURE_CONTENT_TYPE_NOSNIFF', default=True)
    X_FRAME_OPTIONS = get_env_var('X_FRAME_OPTIONS', default='DENY')
    SECURE_HSTS_SECONDS = get_env_int('SECURE_HSTS_SECONDS', default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = get_env_bool(
        'SECURE_HSTS_INCLUDE_SUBDOMAINS', default=True)
    SECURE_HSTS_PRELOAD = get_env_bool('SECURE_HSTS_PRELOAD', default=True)
    # Nginx X-Forwarded-Proto başlığını dikkate alarak güvenli istekleri tanı
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Session ve CSRF Güvenlik Ayarları
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = 86400
# Bazı Android cihazlarda cookie gönderimini garanti altına almak için
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_SECURE = get_env_bool(
    'SESSION_COOKIE_SECURE', default=not DEBUG)
SESSION_COOKIE_HTTPONLY = get_env_bool('SESSION_COOKIE_HTTPONLY', default=True)
# SameSite=None, Secure=True bazı Android cihazlarda gerekli olabilir
# Ancak SameSite=None için Secure=True zorunludur (HTTPS gerektirir)
SESSION_COOKIE_SAMESITE = get_env_var(
    'SESSION_COOKIE_SAMESITE', default='Lax')
# Cookie domain - www ile www olmayan arasında paylaşım için
SESSION_COOKIE_DOMAIN = get_env_var('SESSION_COOKIE_DOMAIN', default=None)

CSRF_COOKIE_SECURE = get_env_bool('CSRF_COOKIE_SECURE', default=not DEBUG)
CSRF_COOKIE_HTTPONLY = get_env_bool(
    'CSRF_COOKIE_HTTPONLY', default=False)  # JavaScript erişimi için False
CSRF_COOKIE_SAMESITE = get_env_var(
    'CSRF_COOKIE_SAMESITE', default='Lax')  # AJAX istekleri için Lax

# Uygulama debug bayrakları
TAHSILAT_DEBUG_REQUESTS = get_env_bool(
    'TAHSILAT_DEBUG_REQUESTS', default=False)
TAHSILAT_DEBUG_SQL = get_env_bool('TAHSILAT_DEBUG_SQL', default=False)

CSRF_TRUSTED_ORIGINS = [
    'http://mrktahsilat.com',
    'https://mrktahsilat.com',
    'http://www.mrktahsilat.com',
    'https://www.mrktahsilat.com',
    'http://72.62.52.105',
    'https://72.62.52.105'
]

# Localization
LANGUAGE_CODE = 'tr'
TIME_ZONE = 'Europe/Istanbul'
USE_I18N = True
USE_TZ = True

# Static ve Media Files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Aşağıdaki insecure overrides kaldırıldı; değerler environment'a göre yukarıda ayarlanır

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# Logging configuration
LOG_LEVEL = get_env_var('DJANGO_LOG_LEVEL', default='INFO').upper()
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': LOG_LEVEL,
            'class': 'logging.FileHandler',
            'filename': os.path.join(BASE_DIR, 'logs', 'django.log'),
            'formatter': 'verbose',
        },
        'console': {
            'level': LOG_LEVEL,
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file', 'console'],
            'level': LOG_LEVEL,
            'propagate': True,
        },
        'tahsilat': {
            'handlers': ['file', 'console'],
            'level': LOG_LEVEL,
            'propagate': True,
        },
    },
}
