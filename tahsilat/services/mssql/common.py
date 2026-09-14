"""Shared MSSQL connection and encoding helpers."""

from django.conf import settings

PRESERVE_FIX_MAP = {
    'EYÃŒP': 'EYÜP',
    'EYÃœP': 'EYÜP',
    'Ãœ': 'Ü',
    'ÃÌ': 'Ü',
    'ÃŒ': 'Ü',
    'Ã¼': 'ü',
    'Ä±': 'ı',
    'Ä°': 'İ',
    'Ã§': 'ç',
    'Ã‡': 'Ç',
    'ÅŸ': 'ş',
    'Åž': 'Ş',
    'Ã¶': 'ö',
    'Ã–': 'Ö',
    'ÄŸ': 'ğ',
    'Äž': 'Ğ',
}

LOOKUP_FIX_MAP = {
    'EYÃŒP': 'EYUP',
    'EYÃœP': 'EYUP',
    'Ãœ': 'U',
    'ÃÌ': 'U',
    'ÃŒ': 'U',
    'Ã¼': 'u',
    'Ü': 'U',
    'ü': 'u',
    'Ä±': 'i',
    'Ä°': 'I',
    'ı': 'i',
    'İ': 'I',
    'Ã§': 'c',
    'Ã‡': 'C',
    'ç': 'c',
    'Ç': 'C',
    'ÅŸ': 's',
    'Åž': 'S',
    'ş': 's',
    'Ş': 'S',
    'Ã¶': 'o',
    'Ã–': 'O',
    'ö': 'o',
    'Ö': 'O',
    'ÄŸ': 'g',
    'Äž': 'G',
    'ğ': 'g',
    'Ğ': 'G',
}

DEFAULT_MSSQL_FALLBACKS = ['cp1254', 'utf-8', 'latin-1', 'iso-8859-9']


def get_mssql_fallbacks(config=None):
    config = config or settings.MSSQL_CONFIG
    return config.get('encoding_options', {}).get(
        'encoding_fallbacks',
        DEFAULT_MSSQL_FALLBACKS,
    )


def build_mssql_connection_string(config=None):
    config = config or settings.MSSQL_CONFIG
    connection_string = (
        f"DRIVER={{{config['driver']}}};"
        f"SERVER={config['server']},{config['port']};"
        f"DATABASE={config['database']};"
        f"UID={config['username']};"
        f"PWD={config['password']};"
        f"charset={config.get('charset', 'utf-8')};"
        f"TrustServerCertificate={config.get('trust_server_certificate', 'yes')};"
    )

    if not config.get('encoding_options', {}).get('auto_translate', True):
        connection_string += 'AutoTranslate=no;'
    if config.get('autocommit'):
        connection_string += 'AUTOCOMMIT=Yes;'
    return connection_string


def fix_turkish_encoding(text):
    if not text:
        return ""
    result = str(text)
    for old, new in PRESERVE_FIX_MAP.items():
        result = result.replace(old, new)
    return result


def normalize_turkish_lookup_text(text):
    if not text:
        return ""
    result = str(text)
    for old, new in LOOKUP_FIX_MAP.items():
        result = result.replace(old, new)
    return result


def safe_decode_mssql_value(value, fallbacks=None, preserve_turkish=False):
    if not value:
        return ""
    if not isinstance(value, (str, bytes)):
        return str(value)

    fallbacks = fallbacks or DEFAULT_MSSQL_FALLBACKS

    if isinstance(value, bytes):
        for encoding in fallbacks:
            try:
                decoded = value.decode(encoding)
                break
            except Exception:
                decoded = None
        else:
            try:
                decoded = value.decode('utf-8', errors='ignore')
            except Exception:
                return str(value)
    else:
        decoded = value

    if preserve_turkish:
        return fix_turkish_encoding(decoded)
    return normalize_turkish_lookup_text(decoded)


def build_lookup_candidates(value):
    raw = str(value or '').strip()
    if not raw:
        return []

    candidates = {
        raw,
        raw.upper(),
        fix_turkish_encoding(raw),
        fix_turkish_encoding(raw).upper(),
        normalize_turkish_lookup_text(raw),
        normalize_turkish_lookup_text(raw).upper(),
    }
    return [candidate for candidate in candidates if candidate]
