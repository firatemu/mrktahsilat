from .settings import *
from .env_loader import get_env_bool, get_env_int, get_env_list, get_env_var

DEBUG = get_env_bool('DEBUG', default=False)
ALLOWED_HOSTS = get_env_list('ALLOWED_HOSTS', default=ALLOWED_HOSTS)
CSRF_TRUSTED_ORIGINS = get_env_list(
    'CSRF_TRUSTED_ORIGINS', default=CSRF_TRUSTED_ORIGINS)

CSRF_COOKIE_SECURE = get_env_bool(
    'CSRF_COOKIE_SECURE', default=not DEBUG)
CSRF_COOKIE_HTTPONLY = get_env_bool(
    'CSRF_COOKIE_HTTPONLY', default=False)
SESSION_COOKIE_SECURE = get_env_bool(
    'SESSION_COOKIE_SECURE', default=not DEBUG)

# Cache control - Updated content için cache temizleme
CACHE_MIDDLEWARE_SECONDS = get_env_int(
    'CACHE_MIDDLEWARE_SECONDS', default=0)
USE_ETAGS = get_env_bool('USE_ETAGS', default=True)

# Static files versioning
STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'

MIDDLEWARE = [
    'django.middleware.cache.UpdateCacheMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'tahsilat.middleware.URLDebugMiddleware',
    'django.middleware.cache.FetchFromCacheMiddleware',
]

STATIC_URL = '/static/'
STATIC_ROOT = '/var/www/mrktahsilat/staticfiles/'
STATICFILES_DIRS = []

MEDIA_URL = '/media/'
MEDIA_ROOT = '/var/www/mrktahsilat/media/'

SECURE_BROWSER_XSS_FILTER = get_env_bool(
    'SECURE_BROWSER_XSS_FILTER', default=True)
SECURE_CONTENT_TYPE_NOSNIFF = get_env_bool(
    'SECURE_CONTENT_TYPE_NOSNIFF', default=True)
X_FRAME_OPTIONS = get_env_var('X_FRAME_OPTIONS', default='DENY')

TAHSILAT_DEBUG_REQUESTS = get_env_bool(
    'TAHSILAT_DEBUG_REQUESTS', default=False)
TAHSILAT_DEBUG_SQL = get_env_bool(
    'TAHSILAT_DEBUG_SQL', default=False)

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
            'filename': '/var/log/django/mrktahsilat.log',
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
        'console': {
            'level': LOG_LEVEL,
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file'],
            'level': LOG_LEVEL,
            'propagate': True,
        },
        'tahsilat': {
            'handlers': ['file', 'console'],
            'level': LOG_LEVEL,
            'propagate': True,
        },
        'tahsilat.authentication': {
            'handlers': ['file', 'console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'tahsilat.mssql_service': {
            'handlers': ['file', 'console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
    },
}

MSSQL_CONFIG = {
    **MSSQL_CONFIG,
    'charset': get_env_var(
        'MSSQL_CHARSET',
        default=MSSQL_CONFIG.get('charset', 'cp1254'),
    ),
    'autocommit': get_env_bool('MSSQL_AUTOCOMMIT', default=True),
    'encoding_options': {
        **MSSQL_CONFIG.get('encoding_options', {}),
        'connection_encoding': get_env_var(
            'MSSQL_CONNECTION_ENCODING', default='cp1254'),
        'auto_translate': get_env_bool(
            'MSSQL_AUTO_TRANSLATE', default=False),
        'use_unicode': get_env_bool('MSSQL_USE_UNICODE', default=True),
        'charset': get_env_var('MSSQL_ENCODING_CHARSET', default='cp1254'),
        'encoding_fallbacks': get_env_list(
            'MSSQL_ENCODING_FALLBACKS',
            default=['cp1254', 'utf-8', 'latin-1', 'iso-8859-9'],
        ),
    },
}

SESSION_COOKIE_NAME = get_env_var(
    'SESSION_COOKIE_NAME', default='mrktahsilat_sessionid')
SESSION_SAVE_EVERY_REQUEST = get_env_bool(
    'SESSION_SAVE_EVERY_REQUEST', default=True)
