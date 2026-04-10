from .settings import *
import os

DEBUG = True
ALLOWED_HOSTS = ['mrktahsilat.com', 'www.mrktahsilat.com', '72.62.52.105', 'localhost', '127.0.0.1']

# CSRF Settings
CSRF_TRUSTED_ORIGINS = [
    'http://mrktahsilat.com',
    'https://mrktahsilat.com',
    'http://www.mrktahsilat.com',
    'https://www.mrktahsilat.com',
    'http://72.62.52.105',
    'https://72.62.52.105'
]

CSRF_COOKIE_SECURE = False
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SECURE = False

# Cache control - Updated content için cache temizleme
CACHE_MIDDLEWARE_SECONDS = 0
USE_ETAGS = True

# Static files versioning
STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'

# Cache control middleware ekle
MIDDLEWARE = [
    'django.middleware.cache.UpdateCacheMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'tahsilat.middleware.URLDebugMiddleware',  # Debug middleware for URL tracking
    'django.middleware.cache.FetchFromCacheMiddleware',
]

STATIC_URL = '/static/'
STATIC_ROOT = '/var/www/mrktahsilat/staticfiles/'
STATICFILES_DIRS = []

MEDIA_URL = '/media/'
MEDIA_ROOT = '/var/www/mrktahsilat/media/'

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# Encoding ayarları - Kalıcı çözüm
import locale
import os
import sys

# Sistem encoding'ini zorla UTF-8 yap
os.environ['LANG'] = 'tr_TR.UTF-8'
os.environ['LC_ALL'] = 'tr_TR.UTF-8'

# Python default encoding
if sys.version_info >= (3, 7):
    # Python 3.7+ için
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.detach())

# Logging
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
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': '/var/log/django/mrktahsilat.log',
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'tahsilat': {
            'handlers': ['file', 'console'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'tahsilat.authentication': {
            'handlers': ['file', 'console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'tahsilat.mssql_service': {
            'handlers': ['file', 'console'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}

# MSSQL Database Configuration
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# MSSQL Connection Settings (for custom connections) - Kalıcı encoding çözümü
MSSQL_CONFIG = {
    'server': '88.247.8.178',
    'port': '2024',
    'database': 'GO3',
    'username': 'sa',
    'password': '8423Otomotiv',
    'driver': 'ODBC Driver 17 for SQL Server',
    'charset': 'cp1254',
    'autocommit': True,
    # Kalıcı encoding ayarları
    'encoding_options': {
        'connection_encoding': 'cp1254',
        'auto_translate': False,
        'use_unicode': True,
        'charset': 'cp1254',
        'encoding_fallbacks': ['cp1254', 'utf-8', 'latin-1', 'iso-8859-9']
    }
}

# Authentication Backends
AUTHENTICATION_BACKENDS = [
    'tahsilat.authentication.MSSQLAuthenticationBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# Türkçe dil ayarları - kalıcı
LANGUAGE_CODE = 'tr-tr'
TIME_ZONE = 'Europe/Istanbul'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# Encoding ile ilgili Django ayarları
DEFAULT_CHARSET = 'utf-8'
FILE_CHARSET = 'utf-8'

# Session ayarları
SESSION_COOKIE_NAME = 'mrktahsilat_sessionid'
SESSION_SAVE_EVERY_REQUEST = True
