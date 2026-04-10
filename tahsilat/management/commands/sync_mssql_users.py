import pyodbc
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.contrib.auth.models import User
import logging

logger = logging.getLogger(__name__)


def normalize_turkish_chars(text):
    if not text:
        return ""
    mapping = {
        'EYÃŒP': 'EYÜP', 'EYÃœP': 'EYÜP',
        'Ãœ': 'Ü', 'ÃŒ': 'Ü', 'Ã¼': 'ü',
        'Ä±': 'ı', 'Ä°': 'İ',
        'Ã§': 'ç', 'Ã‡': 'Ç',
        'ÅŸ': 'ş', 'Åž': 'Ş',
        'Ã¶': 'ö', 'Ã–': 'Ö',
        'ÄŸ': 'ğ', 'Äž': 'Ğ',
    }
    result = str(text)
    for k, v in mapping.items():
        result = result.replace(k, v)
    return result


def safe_decode_string(value, fallbacks=None):
    if not value:
        return ""
    if fallbacks is None:
        fallbacks = ['cp1254', 'utf-8', 'latin-1']
    if isinstance(value, bytes):
        for enc in fallbacks:
            try:
                return normalize_turkish_chars(value.decode(enc))
            except Exception:
                continue
        try:
            return normalize_turkish_chars(value.decode('utf-8', errors='ignore'))
        except Exception:
            return str(value)
    return normalize_turkish_chars(value)


class Command(BaseCommand):
    help = 'Sync Django users from MSSQL [GO3].[dbo].[KULLANICITB] table (KullaniciAdi)'

    def add_arguments(self, parser):
        parser.add_argument('--no-delete', action='store_true', help='Do not delete local users missing from MSSQL')
        parser.add_argument('--dry-run', action='store_true', help="Don't make changes, only show what would be done")

    def handle(self, *args, **options):
        no_delete = options['no_delete']
        dry_run = options['dry_run']

        config = getattr(settings, 'MSSQL_CONFIG', None)
        if not config:
            raise CommandError('MSSQL_CONFIG is not defined in settings')

        connection_string = (
            f"DRIVER={{{config['driver']}}};"
            f"SERVER={config['server']},{config['port']};"
            f"DATABASE={config['database']};"
            f"UID={config['username']};"
            f"PWD={config['password']};"
            f"charset={config.get('charset','utf-8')};"
            f"TrustServerCertificate=yes;"
        )
        # Ensure AutoTranslate=no for encoding consistency
        if not config.get('encoding_options', {}).get('auto_translate', True):
            connection_string += 'AutoTranslate=no;'

        try:
            with pyodbc.connect(connection_string) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT [KullaniciAdi], [Departman] FROM [GO3].[dbo].[KULLANICITB]')
                rows = cursor.fetchall()
        except Exception as e:
            raise CommandError(f'Error querying MSSQL: {e}')

        mssql_users = {}
        for row in rows:
            raw_username = row[0] if len(row) > 0 else ''
            departman = row[1] if len(row) > 1 else ''
            uname = safe_decode_string(raw_username).strip()
            if not uname:
                continue
            normalized = normalize_turkish_chars(uname).upper()
            mssql_users[normalized] = {
                'raw': uname,
                'departman': safe_decode_string(departman)
            }

        self.stdout.write(f'Found {len(mssql_users)} users in MSSQL')

        # Create or update users
        created = 0
        updated = 0
        for normalized, info in mssql_users.items():
            username = normalized
            raw = info['raw']
            departman = info['departman']
            user_qs = User.objects.filter(username=username)
            if user_qs.exists():
                user = user_qs.first()
                changed = False
                if user.first_name != departman:
                    if not dry_run:
                        user.first_name = departman
                        user.save()
                    changed = True
                if changed:
                    updated += 1
                continue
            # Create new user with unusable password; MSSQL backend will authenticate
            if dry_run:
                self.stdout.write(f'[DRY] Would create user: {username} (Departman: {departman})')
                created += 1
            else:
                user = User.objects.create_user(username=username)
                user.first_name = departman
                user.is_active = True
                user.set_unusable_password()
                user.save()
                created += 1

        self.stdout.write(f'Users created: {created}, updated: {updated}')

        # Delete local users not present in MSSQL (but never delete superusers)
        if not no_delete:
            to_delete = []
            for user in User.objects.filter(is_superuser=False):
                if user.username.upper() not in mssql_users:
                    to_delete.append(user)

            deleted = 0
            for user in to_delete:
                if dry_run:
                    self.stdout.write(f'[DRY] Would delete user: {user.username}')
                    deleted += 1
                else:
                    self.stdout.write(f'Deleting user: {user.username}')
                    user.delete()
                    deleted += 1

            self.stdout.write(f'Users deleted: {deleted}')

        self.stdout.write(self.style.SUCCESS('MSSQL user sync completed'))
