import pyodbc
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import User
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class MSSQLAuthenticationBackend(BaseBackend):
    """MSSQL KULLANICITB tablosu ile kimlik doğrulama - Kalıcı encoding çözümü"""

    def __init__(self):
        super().__init__()
        # Kalıcı encoding ayarlarını settings'ten al
        self.config = settings.MSSQL_CONFIG
        self.encoding_options = self.config.get('encoding_options', {})
        self.encoding_fallbacks = self.encoding_options.get('encoding_fallbacks', ['cp1254', 'utf-8', 'latin-1'])

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None

        # Authentication attempt
        
        try:
            # MSSQL bağlantısı - kalıcı ayarlarla
            config = settings.MSSQL_CONFIG
            connection_string = (
                f"DRIVER={{{config['driver']}}};"
                f"SERVER={config['server']},{config['port']};"
                f"DATABASE={config['database']};"
                f"UID={config['username']};"
                f"PWD={config['password']};"
                f"charset={config['charset']};"
                f"TrustServerCertificate=yes;"
            )
            
            # AutoTranslate=no ekle (kalıcı encoding çözümü)
            if not self.encoding_options.get('auto_translate', True):
                connection_string += "AutoTranslate=no;"

            with pyodbc.connect(connection_string) as conn:
                cursor = conn.cursor()



                # KULLANICITB tablosundan tüm kullanıcıları getir ve Python'da karşılaştır
                # Çünkü veritabanında encoding sorunlu karakterler var
                query = """
                SELECT [ID], [KullaniciAdi], [Sifre], [Departman]
                FROM [GO3].[dbo].[KULLANICITB]
                """
                cursor.execute(query)
                all_users = cursor.fetchall()



                user_data = None
                for user in all_users:
                    db_username = self.safe_decode_string(user[1].strip() if user[1] else "")
                    # Türkçe karakter normalizasyonu - sadece karşılaştırma için
                    normalized_db_username = self.normalize_turkish_chars(db_username.upper())
                    normalized_input_username = self.normalize_turkish_chars(username.upper())
                    

                    
                    if normalized_db_username == normalized_input_username:
                        user_data = user

                        break

                if user_data:
                    # Şifreyi manuel olarak kontrol et
                    db_password = self.safe_decode_string(user_data[2].strip() if user_data[2] else "")
                    input_password = password.strip() if password else ""


                    
                    if db_password == input_password:

                        
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
        if not value:
            return ""
            
        if not isinstance(value, (str, bytes)):
            return str(value)
            
        # Eğer bytes ise önce decode et
        if isinstance(value, bytes):
            # Settings'ten gelen encoding listesini kullan
            for encoding in self.encoding_fallbacks:
                try:
                    decoded = value.decode(encoding)
                    # Sadece encoding hatalarını düzelt, Türkçe karakterleri koru
                    return self.fix_encoding_errors_only(decoded)
                except:
                    continue
            # Hiçbiri olmadı ise errors='ignore' ile decode et
            try:
                decoded = value.decode('utf-8', errors='ignore')
                return self.fix_encoding_errors_only(decoded)
            except:
                return str(value)
        
        # String ise sadece encoding hatalarını düzelt
        return self.fix_encoding_errors_only(value)

    def fix_encoding_errors_only(self, text):
        """Sadece encoding hatalarını düzeltir, Türkçe karakterleri ve büyük/küçük harf durumunu korur"""
        if not text:
            return ""
        
        # Sadece encoding sorunlarını düzelt (örneğin 'ÃŒ' -> 'Ü', 'Ã¼' -> 'ü')
        # Türkçe karakterleri İngilizce karakterlere çevirme
        encoding_fix_map = {
            # EYÜP özel durumu - encoding hatası düzeltmesi
            'EYÃŒP': 'EYÜP',
            'EYÃœP': 'EYÜP',
            
            # Ü karakteri encoding hataları -> doğru Ü karakteri
            'Ãœ': 'Ü', 'ÃŒ': 'Ü', 'Ã¼': 'ü',
            
            # I/İ karakteri encoding hataları -> doğru İ/ı karakteri
            'Ä±': 'ı', 'Ä°': 'İ',
            
            # Ç karakteri encoding hataları -> doğru Ç karakteri
            'Ã§': 'ç', 'Ã‡': 'Ç',
            
            # Ş karakteri encoding hataları -> doğru Ş karakteri
            'ÅŸ': 'ş', 'Åž': 'Ş',
            
            # Ö karakteri encoding hataları -> doğru Ö karakteri
            'Ã¶': 'ö', 'Ã–': 'Ö',
            
            # Ğ karakteri encoding hataları -> doğru Ğ karakteri
            'ÄŸ': 'ğ', 'Äž': 'Ğ',
        }
        
        result = str(text)
        # Sadece encoding hatalarını düzelt
        for old, new in encoding_fix_map.items():
            result = result.replace(old, new)
        
        return result

    def safe_decode_string(self, value):
        """String değeri güvenli şekilde decode eder - Kalıcı çözüm"""
        if not value:
            return ""
            
        if not isinstance(value, (str, bytes)):
            return str(value)
            
        # Eğer bytes ise önce decode et
        if isinstance(value, bytes):
            # Settings'ten gelen encoding listesini kullan
            for encoding in self.encoding_fallbacks:
                try:
                    decoded = value.decode(encoding)
                    return self.normalize_turkish_chars(decoded)
                except:
                    continue
            # Hiçbiri olmadı ise errors='ignore' ile decode et
            try:
                decoded = value.decode('utf-8', errors='ignore')
                return self.normalize_turkish_chars(decoded)
            except:
                return str(value)
        
        # String ise direkt normalize et
        return self.normalize_turkish_chars(value)

    def normalize_turkish_chars(self, text):
        """Türkçe karakter encoding sorunlarını düzelt ve Türkçe karakterleri İngilizce karakterlere çevir"""
        if not text:
            return ""
        
        # Kalıcı encoding sorun çözücü mapping
        encoding_fix_map = {
            # EYÜP özel durumu
            'EYÃŒP': 'EYUP',
            'EYÃœP': 'EYUP',
            
            # Ü karakteri varyasyonları -> U
            'Ãœ': 'U', 'ÃŒ': 'U', 'Ã¼': 'u', 'Ü': 'U', 'ü': 'u',
            
            # I/İ karakteri varyasyonları -> I
            'Ä±': 'i', 'Ä°': 'I', 'ı': 'i', 'İ': 'I',
            
            # Ç karakteri varyasyonları -> C
            'Ã§': 'c', 'Ã‡': 'C', 'ç': 'c', 'Ç': 'C',
            
            # Ş karakteri varyasyonları -> S
            'ÅŸ': 's', 'Åž': 'S', 'ş': 's', 'Ş': 'S',
            
            # Ö karakteri varyasyonları -> O
            'Ã¶': 'o', 'Ã–': 'O', 'ö': 'o', 'Ö': 'O',
            
            # Ğ karakteri varyasyonları -> G
            'ÄŸ': 'g', 'Äž': 'G', 'ğ': 'g', 'Ğ': 'G',
            
            # Özel kelime düzeltmeleri
            'ÅŸen': 'sen', 'oÄŸuz': 'oguz', 'OĞUZ': 'OGUZ', 'Oğuz': 'OGUZ',
            'fÄ±rat': 'firat', 'FIRAT': 'FIRAT', 'Fırat': 'FIRAT',
            'sÃ¼leyman': 'suleyman', 'SÜLEYMAN': 'SULEYMAN', 'Süleyman': 'SULEYMAN',
        }
        
        result = str(text)
        # Önce encoding sorunlarını düzelt
        for old, new in encoding_fix_map.items():
            result = result.replace(old, new)
        
        # Sonra kalan Türkçe karakterleri İngilizce karakterlere çevir (genel mapping)
        turkish_to_english = {
            'Ü': 'U', 'ü': 'u',
            'İ': 'I', 'ı': 'i',
            'Ç': 'C', 'ç': 'c',
            'Ş': 'S', 'ş': 's',
            'Ö': 'O', 'ö': 'o',
            'Ğ': 'G', 'ğ': 'g',
        }
        for turkish, english in turkish_to_english.items():
            result = result.replace(turkish, english)
        
        return result

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None