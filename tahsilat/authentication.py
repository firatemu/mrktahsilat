import pyodbc
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import User
from django.conf import settings
import logging

from tahsilat.services.mssql.auth import find_mssql_user_row
from tahsilat.services.mssql.common import (
    build_mssql_connection_string,
    fix_turkish_encoding,
    get_mssql_fallbacks,
    normalize_turkish_lookup_text,
    safe_decode_mssql_value,
)

logger = logging.getLogger(__name__)


def normalize_login_password(value):
    """MSSQL ve klavye farklarından kaynaklanan ö/o vb. şifre uyumsuzluklarını giderir."""
    text = str(value or '').strip()
    for src, dst in (('ö', 'o'), ('Ö', 'O')):
        text = text.replace(src, dst)
    return text


def passwords_match(db_password, input_password):
    db_password = str(db_password or '').strip()
    input_password = str(input_password or '').strip()
    if db_password == input_password:
        return True
    return normalize_login_password(db_password) == normalize_login_password(input_password)


def resolve_login_username(username):
    """MSSQL'de kayıtlı kullanıcı için kanonik kullanıcı adını döndürür."""
    if not username:
        return username
    try:
        connection_string = build_mssql_connection_string(settings.MSSQL_CONFIG)
        with pyodbc.connect(connection_string) as conn:
            cursor = conn.cursor()
            user_data = find_mssql_user_row(
                cursor,
                username=username,
                fallbacks=get_mssql_fallbacks(settings.MSSQL_CONFIG),
            )
            if user_data and user_data[1]:
                return safe_decode_mssql_value(
                    user_data[1],
                    fallbacks=get_mssql_fallbacks(settings.MSSQL_CONFIG),
                    preserve_turkish=True,
                ).strip()
    except Exception as e:
        logger.warning('resolve_login_username failed for %s: %s', username, e)
    return str(username).strip()


class MSSQLAuthenticationBackend(BaseBackend):
    """MSSQL KULLANICITB tablosu ile kimlik doğrulama - Kalıcı encoding çözümü"""

    def __init__(self):
        super().__init__()
        # Kalıcı encoding ayarlarını settings'ten al
        self.config = settings.MSSQL_CONFIG
        self.encoding_options = self.config.get('encoding_options', {})
        self.encoding_fallbacks = get_mssql_fallbacks(self.config)

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None

        # Authentication attempt
        
        try:
            connection_string = build_mssql_connection_string(settings.MSSQL_CONFIG)
            with pyodbc.connect(connection_string) as conn:
                cursor = conn.cursor()
                normalized_input_username = self.normalize_turkish_chars(username.upper())
                user_data = find_mssql_user_row(
                    cursor,
                    username=username,
                    fallbacks=self.encoding_fallbacks,
                )

                if user_data:
                    # Şifreyi manuel olarak kontrol et
                    db_password = self.safe_decode_string(user_data[2].strip() if user_data[2] else "")
                    input_password = password.strip() if password else ""


                    
                    if passwords_match(db_password, input_password):

                        
                        # Django User oluştur veya güncelle
                        # Username'i MSSQL'deki gerçek kullanıcı adı ile kaydet (normalize etmeden, büyük/küçük harf ve Türkçe karakterler korunarak)
                        # Bu sayede ALİ -> ALİ, SEZEN -> SEZEN, OĞUZ -> OĞUZ olarak kaydedilir
                        real_username = self.safe_decode_string_preserve_turkish(user_data[1]).strip() if user_data[1] else ""
                        
                        # Django User'ı bul - önce gerçek username ile, sonra normalize edilmiş username ile
                        user = None
                        try:
                            # Önce gerçek username ile dene (OĞUZ)
                            user = User.objects.get(username=real_username)
                        except User.DoesNotExist:
                            try:
                                # Normalize edilmiş username ile dene (OGUZ)
                                user = User.objects.get(username=normalized_input_username)
                                # Eğer normalize edilmiş username ile bulunduysa, gerçek username'e güncelle
                                if user.username != real_username:
                                    # Geçici isimle değiştir (UNIQUE constraint için)
                                    temp_username = f'TEMP_{user.id}_{real_username}'
                                    user.username = temp_username
                                    user.save()
                                    # Sonra gerçek username'e güncelle
                                    user.username = real_username
                                    user.save()
                            except User.DoesNotExist:
                                # Hiçbiri yoksa yeni kullanıcı oluştur
                                user = User.objects.create(
                                    username=real_username,
                                    first_name=self.safe_decode_string(user_data[3]) if user_data[3] else '',
                                    is_active=True
                                )
                                user.set_unusable_password()
                                user.save()

                        # Session'a MSSQL user verilerini kaydet
                        # Kullanıcı adını veritabanındaki haliyle (büyük/küçük harf ve Türkçe karakterler korunarak) kaydet
                        if hasattr(request, 'session'):
                            request.session['mssql_user_data'] = {
                                'id': user_data[0],
                                'kullanici_adi': self.safe_decode_string_preserve_turkish(user_data[1]),
                                'departman': self.safe_decode_string(user_data[3]),  # user_data[3] = Departman
                                'plasiyer': self.safe_decode_string_preserve_turkish(user_data[1])  # KullaniciAdi = Plasiyer
                            }


                        logger.info(f"MSSQL authentication successful for user: {username}")
                        return user
                    else:

                        return None
                else:

                    return None

        except Exception as e:

            logger.error(f"MSSQL authentication error: {e}")
            return None

    def safe_decode_string_preserve_turkish(self, value):
        """String değeri güvenli şekilde decode eder - Türkçe karakterleri ve büyük/küçük harf durumunu korur"""
        return safe_decode_mssql_value(
            value,
            fallbacks=self.encoding_fallbacks,
            preserve_turkish=True,
        )

    def fix_encoding_errors_only(self, text):
        """Sadece encoding hatalarını düzeltir, Türkçe karakterleri ve büyük/küçük harf durumunu korur"""
        return fix_turkish_encoding(text)

    def safe_decode_string(self, value):
        """String değeri güvenli şekilde decode eder - Kalıcı çözüm"""
        return safe_decode_mssql_value(
            value,
            fallbacks=self.encoding_fallbacks,
            preserve_turkish=False,
        )

    def normalize_turkish_chars(self, text):
        """Türkçe karakter encoding sorunlarını düzelt ve Türkçe karakterleri İngilizce karakterlere çevir"""
        return normalize_turkish_lookup_text(text)

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None