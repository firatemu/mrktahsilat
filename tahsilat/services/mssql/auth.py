"""Authentication-oriented MSSQL helpers."""

from tahsilat.services.mssql.common import (
    build_lookup_candidates,
    normalize_turkish_lookup_text,
    safe_decode_mssql_value,
)

AUTH_USER_QUERY = """
SELECT [ID], [KullaniciAdi], [Sifre], [Departman]
FROM [GO3].[dbo].[KULLANICITB]
"""


def _match_user_row(rows, username, fallbacks):
    normalized_input = normalize_turkish_lookup_text(str(username or '').upper())
    for user in rows:
        db_username = safe_decode_mssql_value(
            user[1].strip() if user[1] else "",
            fallbacks=fallbacks,
        )
        normalized_db_username = normalize_turkish_lookup_text(db_username.upper())
        if normalized_db_username == normalized_input:
            return user
    return None


def find_mssql_user_row(cursor, username, fallbacks):
    """Try a narrow lookup first, then fall back to a full scan for legacy rows."""
    lookup_candidates = build_lookup_candidates(username)
    uppercase_candidates = sorted({candidate.upper() for candidate in lookup_candidates})

    if uppercase_candidates:
        placeholders = ', '.join(['UPPER(?)'] * len(uppercase_candidates))
        targeted_query = f"""
        {AUTH_USER_QUERY}
        WHERE UPPER([KullaniciAdi]) IN ({placeholders})
        """
        cursor.execute(targeted_query, uppercase_candidates)
        matched = _match_user_row(cursor.fetchall(), username, fallbacks)
        if matched:
            return matched

    cursor.execute(AUTH_USER_QUERY)
    return _match_user_row(cursor.fetchall(), username, fallbacks)
