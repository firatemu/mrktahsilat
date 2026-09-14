import pyodbc
from django.conf import settings
from datetime import datetime, date
from django.utils import timezone
import logging
import json
import re
import time
import traceback

from tahsilat.services.mssql.common import (
    build_mssql_connection_string,
    fix_turkish_encoding,
    get_mssql_fallbacks,
    safe_decode_mssql_value,
)
from tahsilat.services.mssql.tahsilat import build_gunluk_tahsilat_filters

logger = logging.getLogger(__name__)


def _odbc_keys_lower(rows, colnames):
    """pyodbc + SQL Server sütun adlarını bazen UPPERCASE döndürür; .get('ay') hep None kalabiliyor."""
    out = []
    for tup in rows:
        out.append({
            str(c).strip().lower() if c is not None else '': val
            for c, val in zip(colnames, tup)
        })
    return out


def _sql_debug_enabled():
    return getattr(settings, 'TAHSILAT_DEBUG_SQL', False)


def _safe_log_payload(data):
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except TypeError:
        return str(data)


def _log_sql_debug(message, **payload):
    if not _sql_debug_enabled():
        return
    if payload:
        logger.debug("%s | %s", message, _safe_log_payload(payload))
    else:
        logger.debug("%s", message)


def _sanitize_sql_params(params, preview_limit=5):
    if not params:
        return []
    sanitized = []
    for index, value in enumerate(params):
        if index >= preview_limit:
            sanitized.append('...')
            break
        if value is None:
            sanitized.append(None)
        elif isinstance(value, (int, float, bool)):
            sanitized.append(value)
        else:
            text = str(value)
            sanitized.append(text if len(text) <= 24 else text[:21] + '...')
    return sanitized


_MSSQL_LIST_CACHE = {}


def _cache_get(key):
    cached = _MSSQL_LIST_CACHE.get(key)
    if not cached:
        return None
    if cached['expires_at'] <= time.time():
        _MSSQL_LIST_CACHE.pop(key, None)
        return None
    return cached['value']


def _cache_set(key, value, ttl_seconds=300):
    _MSSQL_LIST_CACHE[key] = {
        'value': value,
        'expires_at': time.time() + ttl_seconds,
    }
    return value


# FATURA: Logo'da [PLASİYER] metni sık sık boş kalır; [PLASİYER KOD] dolu olur. Yalnızca [PLASİYER]'a
# bakınca satış satırları tamamen düşer, dashboard "Toplam Satışlar" 0 kalır.
_FATURA_PLASIYER_CANON = """CASE 
  WHEN NULLIF(LTRIM(RTRIM(ISNULL(CAST([PLASİYER] AS NVARCHAR(120)), N''))), N'') IS NOT NULL 
    THEN NULLIF(LTRIM(RTRIM(ISNULL(CAST([PLASİYER] AS NVARCHAR(120)), N''))), N'') 
  WHEN NULLIF(LTRIM(RTRIM(ISNULL(CAST([PLASİYER KOD] AS NVARCHAR(80)), N''))), N'') IS NOT NULL 
    THEN NULLIF(LTRIM(RTRIM(ISNULL(CAST([PLASİYER KOD] AS NVARCHAR(80)), N''))), N'') 
  ELSE N'(Belirtilmemiş)' 
END"""


class MSSQLService:
    def get_stok_detayli_analiz(self, malzeme_kodu='', aciklamasi='', marka='', malzeme_turu='', mevcut_stok='', page=1, page_size=50):
        """MALZEME_STOK_PERFORMANS tablosundan filtreli detaylı stok verisi getirir - Sayfalama destekli"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Filtreleme koşullarını hazırla
            where_conditions = ["1=1"]
            params = []

            if malzeme_kodu:
                where_conditions.append("[MALZEME KODU] LIKE ?")
                params.append(f"%{malzeme_kodu}%")
            if aciklamasi:
                where_conditions.append("[AÇIKLAMASI] LIKE ?")
                params.append(f"%{aciklamasi}%")
            if marka:
                where_conditions.append("[MARKA] = ?")
                params.append(marka)
            if malzeme_turu:
                where_conditions.append("[MALZEME TÜRÜ] = ?")
                params.append(malzeme_turu)
            if mevcut_stok:
                # Stokta olanlar veya stokta olmayanlar için filtre
                if mevcut_stok == "stokta":
                    where_conditions.append("[Mevcut_Stok] > 0")
                elif mevcut_stok == "biten":
                    where_conditions.append("[Mevcut_Stok] <= 0")

            # Önce toplam kayıt sayısını al
            count_query = f"""
                SELECT COUNT(*) as total_count
                FROM [GO3].[dbo].[MALZEME_STOK_PERFORMANS]
                WHERE {" AND ".join(where_conditions)}
            """

            cursor.execute(count_query, params)
            total_count = cursor.fetchone()[0]

            # Sayfalama için offset hesapla
            offset = (page - 1) * page_size

            # Ana sorgu - sayfalama ile
            query = f"""
                SELECT [MALZEME KODU], [AÇIKLAMASI], [MARKA], [MALZEME TÜRÜ], [Mevcut_Stok], [GRUP KODU],
                       [SATINALMA MİKTAR], [SATINALMA TUTAR], [SON SATIN ALMA TARİHİ],
                       [SATIŞ İADE MİKTAR], [SATIŞ İADE TUTAR], [SON SATIŞ İADE TARİHİ],
                       [SATIŞ MİKTAR], [SATIŞ TUTAR], [SON SATIŞ TARİHİ],
                       [SATINALMA İADE MİKTAR], [SATINALMA AİDE TUTAR], [SON İADE ALMA TARİHİ]
                FROM [GO3].[dbo].[MALZEME_STOK_PERFORMANS]
                WHERE {" AND ".join(where_conditions)}
                ORDER BY [MALZEME KODU]
                OFFSET ? ROWS
                FETCH NEXT ? ROWS ONLY
            """

            # Sayfalama parametrelerini ekle
            params.extend([offset, page_size])

            cursor.execute(query, params)
            rows = cursor.fetchall()

            # Sonuçları formatlı şekilde hazırla
            result = []
            for row in rows:
                result.append({
                    'malzeme_kodu': self.safe_decode_string(row[0]),
                    'aciklamasi': self.safe_decode_string(row[1]),
                    'marka': self.safe_decode_string(row[2]),
                    'malzeme_turu': self.safe_decode_string(row[3]),
                    'mevcut_stok': row[4],
                    'grup_kodu': self.safe_decode_string(row[5]),
                    'satinalma_miktar': row[6],
                    'satinalma_tutar': float(row[7] if row[7] is not None else 0),
                    'son_satinalma_tarihi': row[8],
                    'satis_iade_miktar': row[9],
                    'satis_iade_tutar': float(row[10] if row[10] is not None else 0),
                    'son_satis_iade_tarihi': row[11],
                    'satis_miktar': row[12],
                    'satis_tutar': float(row[13] if row[13] is not None else 0),
                    'son_satis_tarihi': row[14],
                    'satinalma_iade_miktar': row[15],
                    'satinalma_iade_tutar': float(row[16] if row[16] is not None else 0),
                    'son_iade_alma_tarihi': row[17],
                })

            # Sayfalama bilgilerini hesapla
            total_pages = (total_count + page_size - 1) // page_size
            has_next = page < total_pages
            has_previous = page > 1

            pagination_info = {
                'total_count': total_count,
                'total_pages': total_pages,
                'current_page': page,
                'page_size': page_size,
                'has_next': has_next,
                'has_previous': has_previous,
                'next_page': page + 1 if has_next else None,
                'previous_page': page - 1 if has_previous else None,
            }

            conn.close()
            return result, pagination_info

        except Exception as e:
            logger.error(f"get_stok_detayli_analiz error: {e}")
            return [], {
                'total_count': 0,
                'total_pages': 1,
                'current_page': 1,
                'page_size': page_size,
                'has_next': False,
                'has_previous': False,
                'next_page': None,
                'previous_page': None,
            }

    def get_malzeme_filter_options(self):
        """Malzeme türü ve marka seçeneklerini veritabanından getirir"""
        cached = _cache_get('malzeme_filter_options')
        if cached is not None:
            return cached
        logger.info("=== get_malzeme_filter_options started ===")
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            logger.info("Database connection established")

            # Malzeme türlerini ve markaları al
            query = """
            SELECT DISTINCT 
                [MALZEME TÜRÜ],
                [MARKA]
            FROM [GO3].[dbo].[MALZEME_STOK_PERFORMANS]
            WHERE [MALZEME TÜRÜ] IS NOT NULL 
                AND [MARKA] IS NOT NULL 
                AND [MALZEME TÜRÜ] != ''
                AND [MARKA] != ''
            """

            logger.info(f"Executing query: {query}")
            cursor.execute(query)
            rows = cursor.fetchall()

            logger.info(f"Query returned {len(rows)} rows")

            malzeme_turleri = sorted(
                list(set([self.safe_decode_string(row[0]) for row in rows if row[0]])))
            markalar = sorted(
                list(set([self.safe_decode_string(row[1]) for row in rows if row[1]])))

            logger.info(f"Found {len(malzeme_turleri)} unique malzeme türleri")
            logger.info(f"Found {len(markalar)} unique markalar")

            # Log some sample values
            if malzeme_turleri:
                logger.info(f"Sample malzeme türleri: {malzeme_turleri[:5]}")
            if markalar:
                logger.info(f"Sample markalar: {markalar[:5]}")

            result = {
                'malzeme_turleri': malzeme_turleri,
                'markalar': markalar
            }

            logger.info(
                "=== get_malzeme_filter_options completed successfully ===")
            return _cache_set('malzeme_filter_options', result)
        except Exception as e:
            import traceback
            logger.error("=== get_malzeme_filter_options failed ===")
            logger.error(f"Error: {str(e)}")

    def get_unique_tahsilat_bankalar(self):
        """Tahsilat kayıtlarında geçen benzersiz banka adlarını döndürür"""
        cached = _cache_get('unique_tahsilat_bankalar')
        if cached is not None:
            return cached
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
            SELECT DISTINCT [BANKA]
            FROM [GO3].[dbo].[TAHSILAT_LOGO]
            WHERE [BANKA] IS NOT NULL AND [BANKA] != ''
            ORDER BY [BANKA]
            """

            cursor.execute(query)
            rows = cursor.fetchall()
            raw_bankalar = [self.safe_decode_string(r[0]) for r in rows if r and r[0]]

            # Normalize bank names to canonical short forms used in UI
            def normalize(name):
                if not name:
                    return ''
                s = name.strip()
                s_upper = s.upper()

                # Remove common suffixes/words
                for junk in ['ŞAHSİ', 'ŞAHSI', 'ŞUBESİ', 'SUBESI', 'ŞUBE', 'SUBE', 'HESABI', 'HESAP']:
                    s_upper = s_upper.replace(junk, '')

                # Mapping by substring to canonical display names
                mappings = [
                    ('GARANT', 'GARANTİ'),
                    ('FİNANS', 'FİNANSBANK'), ('FINANS', 'FİNANSBANK'),
                    ('VAKIF', 'VAKIFBANK'),
                    ('AKBANK', 'AKBANK'),
                    ('YAPI KRED', 'YAPIKREDİ'), ('YAPIKRED', 'YAPIKREDİ'),
                    ('İŞBANK', 'İŞBANKASI'), ('ISBANK', 'İŞBANKASI'),
                    ('DENİZ', 'DENİZBANK'), ('DENIZ', 'DENİZBANK'),
                    ('HALK', 'HALKBANK'),
                    ('TEB', 'TEB'),
                    ('QNB', 'QNB FİNANSBANK'),
                    ('FINANSBANK', 'FİNANSBANK'),
                    ('MRKBANK', 'MRKBANK')
                ]

                for key, canon in mappings:
                    if key in s_upper:
                        return canon

                # Fallback: return stripped uppercase trimmed tokens (first token)
                cleaned = ' '.join([t for t in s_upper.split() if t])
                return cleaned

            bankalar = []
            seen = set()
            for raw in raw_bankalar:
                canon = normalize(raw)
                if canon and canon not in seen:
                    seen.add(canon)
                    bankalar.append(canon)
            conn.close()
            return _cache_set('unique_tahsilat_bankalar', bankalar)
        except Exception as e:
            logger.error(f'get_unique_tahsilat_bankalar error: {e}')
            return []

    def get_unique_plasiyerler(self):
        """Tahsilat tablosundan benzersiz plasiyer isimlerini döndürür"""
        cached = _cache_get('unique_plasiyerler')
        if cached is not None:
            return cached
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
            SELECT DISTINCT [PLASİYER]
            FROM [GO3].[dbo].[TAHSILAT_LOGO]
            WHERE [PLASİYER] IS NOT NULL AND [PLASİYER] != ''
            ORDER BY [PLASİYER]
            """

            cursor.execute(query)
            rows = cursor.fetchall()
            plasiyerler = [self.safe_decode_string(r[0]) for r in rows if r and r[0]]
            conn.close()
            return _cache_set('unique_plasiyerler', plasiyerler)
        except Exception as e:
            logger.error(f'get_unique_plasiyerler error: {e}')
            return []

    def get_unique_kullanicilar(self):
        """KULLANICITB tablosundan benzersiz kullanıcı isimlerini döndürür"""
        cached = _cache_get('unique_kullanicilar')
        if cached is not None:
            return cached
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
            SELECT DISTINCT [KullaniciAdi]
            FROM [GO3].[dbo].[KULLANICITB]
            WHERE [KullaniciAdi] IS NOT NULL AND [KullaniciAdi] != ''
            ORDER BY [KullaniciAdi]
            """

            cursor.execute(query)
            rows = cursor.fetchall()
            kullanicilar = [self.safe_decode_string(r[0]) for r in rows if r and r[0]]
            conn.close()
            return _cache_set('unique_kullanicilar', kullanicilar)
        except Exception as e:
            logger.error(f'get_unique_kullanicilar error: {e}')
            logger.error(f"Traceback: {traceback.format_exc()}")
            return []

    def get_kullanici_bilgileri(self, username):
        """Kullanıcı bilgilerini (plasiyer kodu ve kullanıcı adı) döndürür"""
        # Gelişmiş sorgu eklenebilir, şimdilik username ve plasiyer kodu döndürülüyor
        return {
            'KULLANICI_ADI': username,
            'PLASIYER_KOD': username  # Gelişmiş sistemde gerçek plasiyer kodu sorgulanabilir
        }

    def get_kullanici_adi_from_mssql(self, username):
        """MSSQL'den kullanıcı adını (KullaniciAdi) doğru şekilde çeker (Türkçe karakterler ve büyük/küçük harf korunarak)"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Normalize edilmiş username ile karşılaştırma yap (karşılaştırma için)
            normalized_input = self.normalize_turkish_chars(username.upper())
            
            query = """
                SELECT [KullaniciAdi]
                FROM [GO3].[dbo].[KULLANICITB]
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            
            # Tüm kullanıcıları kontrol et
            for row in rows:
                if not row[0]:
                    continue
                db_username = self.safe_decode_string_preserve_turkish(row[0]).strip()
                normalized_db = self.normalize_turkish_chars(db_username.upper())
                
                if normalized_db == normalized_input:
                    conn.close()
                    return db_username  # Gerçek kullanıcı adını döndür (Türkçe karakterler ve büyük/küçük harf korunarak)
            
            conn.close()
            return username  # Bulunamazsa input'u döndür
            
        except Exception as e:
            logger.error(f"get_kullanici_adi_from_mssql error for {username}: {e}")
            return username  # Hata durumunda input'u döndür

    def get_plasiyer_carileri(self, plasiyer_kod, search_term=None):
        """Belirli bir plasiyerin cari hesap listesini getirir"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
                SELECT DISTINCT [CARİ KOD], [CARİ ÜNVAN]
                FROM [GO3].[dbo].[TUMCARIHARETLER] 
                WHERE [CARİ KOD] IS NOT NULL AND [CARİ ÜNVAN] IS NOT NULL
            """

            params = []

            # Plasiyer filtresi (isteğe bağlı, şimdilik tüm carileri getir)
            # if plasiyer_kod:
            #     query += " AND [PLASIYER] = ?"
            #     params.append(plasiyer_kod)

            if search_term:
                query += " AND ([CARİ KOD] LIKE ? OR [CARİ ÜNVAN] LIKE ?)"
                params.extend([f'%{search_term}%', f'%{search_term}%'])

            query += " ORDER BY [CARİ KOD]"

            cursor.execute(query, params)
            rows = cursor.fetchall()

            cariler = []
            for row in rows:
                cari = {
                    'cari_kod': self.safe_decode_string(row[0]),
                    'cari_unvan': self.safe_decode_string(row[1])
                }
                cariler.append(cari)

            conn.close()
            return cariler

        except Exception as e:
            logger.error(f"get_plasiyer_carileri error: {e}")
            return []
    """MSSQL veritabanı bağlantı servisi - Kalıcı encoding çözümü ile"""

    def __init__(self):
        self.config = settings.MSSQL_CONFIG
        self.last_failure_time = 0
        self.failure_threshold = 10  # saniye - geçici hata sonrası tekrar deneme süresi
        # Kalıcı encoding ayarlarını kullan
        encoding_opts = self.config.get('encoding_options', {})
        self.connection_string = build_mssql_connection_string(self.config) + "LoginTimeout=5;"
        self.encoding_fallbacks = get_mssql_fallbacks(self.config)
        self.primary_encoding = encoding_opts.get(
            'connection_encoding', 'cp1254')

    def get_connection(self):
        """MSSQL bağlantısı döndürür - Kalıcı encoding ayarları ile"""
        # Circuit breaker: Son hatadan sonra belirli bir süre bekleyelim
        current_time = time.time()
        if current_time - self.last_failure_time < self.failure_threshold:
            remaining = int(self.failure_threshold - (current_time - self.last_failure_time))
            logger.warning(f"MSSQL connection skipped due to recent failure. Circuit breaker active for {remaining}s.")
            raise Exception(f"MSSQL connection skipped (Circuit breaker active). Please try again in {remaining} seconds.")

        try:
            _log_sql_debug(
                "Attempting MSSQL connection",
                server=self.config.get('server'),
                port=self.config.get('port'),
                database=self.config.get('database'),
                encoding=self.primary_encoding,
            )

            connection = pyodbc.connect(self.connection_string)

            _log_sql_debug(
                "MSSQL connection established",
                encoding=self.primary_encoding,
            )
            return connection

        except Exception as e:
            # Hata zamanını kaydet
            self.last_failure_time = time.time()
            
            import traceback
            logger.error("=== MSSQL Connection Error ===")
            logger.error(f"Error: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise

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
            preserve_turkish=True,
        )

    @staticmethod
    def _normalize_sql_column_key(name):
        """ODBC sütun adını sözlük anahtarı yapar (boşluk / köşeli parantez yok)."""
        if not name:
            return ''
        s = str(name).strip().replace('[', '').replace(']', '').lower()
        return ''.join(s.split())

    def _mssql_cell_to_str(self, value):
        """NULL, sayı (0 dahil), bool ve metin — Excel aktarımı için string (safe_decode 0'ı yanlışlıkla boş yapar)."""
        if value is None:
            return ''
        if isinstance(value, (bytes, str)):
            return self.safe_decode_string(value)
        if isinstance(value, bool):
            return '1' if value else '0'
        return str(value).strip()

    def _row_to_norm_dict(self, row, description):
        """Satırı sütun adına göre dict yapar; SELECT sırası ODBC'de farklı olsa da doğru eşleşir."""
        if not description or row is None:
            return {}
        out = {}
        for i, col in enumerate(description):
            key = self._normalize_sql_column_key(col[0])
            if key and i < len(row):
                out[key] = row[i]
        return out

    def normalize_turkish_chars(self, text):
        """Türkçe karakter encoding sorunlarını düzelt - Statik mapping"""
        return fix_turkish_encoding(text)

    def execute_query_safe(self, query, params=None):
        """Encoding sorunlarına karşı güvenli SQL sorgusu - Kalıcı çözüm"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)

                # Önce column isimlerini alalım
                columns = [column[0] for column in cursor.description]
                
                _log_sql_debug(
                    'MSSQL query columns loaded',
                    columns=columns[:10] if len(columns) > 10 else columns,
                )

                results = []
                processed_rows = 0
                skipped_rows = 0

                # Her satırı tek tek işle ve encoding hatalarını yakala
                while True:
                    try:
                        row = cursor.fetchone()
                        if row is None:
                            break

                        # Her satırı dictionary'ye çevir
                        row_dict = {}
                        for i, value in enumerate(row):
                            column_name = columns[i]

                            # Field isimlerini normalize et (boşlukları underscore yap)
                            normalized_name = column_name.replace(' ', '_')

                            # String değerleri güvenli decode et
                            if isinstance(value, (str, bytes)):
                                value = self.safe_decode_string(value)

                            # Tarih kolonları için özel işlem
                            if column_name in ['Tarih', 'EklemeTarihi', 'TARİH', 'TARIHS', 'TARIHT', 'İŞLEM TARİHİ'] and value is not None:
                                # MSSQL datetime'ı Python datetime'a çevir
                                if hasattr(value, 'strftime'):
                                    # Zaten datetime objesi
                                    row_dict[column_name] = value
                                    if normalized_name != column_name:
                                        row_dict[normalized_name] = value
                                else:
                                    # String ise datetime'a çevir
                                    try:
                                        if isinstance(value, str):
                                            # 2025-02-01 00:00:00.000 formatını parse et
                                            parsed_date = datetime.strptime(
                                                value.split('.')[0], '%Y-%m-%d %H:%M:%S')
                                            row_dict[column_name] = parsed_date
                                            if normalized_name != column_name:
                                                row_dict[normalized_name] = parsed_date
                                        else:
                                            row_dict[column_name] = value
                                            if normalized_name != column_name:
                                                row_dict[normalized_name] = value
                                    except (ValueError, AttributeError):
                                        row_dict[column_name] = value
                                        if normalized_name != column_name:
                                            row_dict[normalized_name] = value
                            else:
                                # Normal kolonlar
                                row_dict[column_name] = value
                                # Normalize edilmiş isimle de sakla
                                if normalized_name != column_name:
                                    row_dict[normalized_name] = value
                            
                            if processed_rows == 0:
                                _log_sql_debug(
                                    'MSSQL first row processed',
                                    column_name=column_name,
                                    normalized_name=normalized_name,
                                    row_dict_keys=list(row_dict.keys())[:10],
                                )

                        results.append(row_dict)
                        processed_rows += 1

                    except UnicodeDecodeError as e:
                        skipped_rows += 1
                        logger.warning(
                            f"Unicode decode error in row {processed_rows + skipped_rows}, skipping: {e}")
                        continue
                    except Exception as e:
                        skipped_rows += 1
                        logger.warning(
                            f"Error processing row {processed_rows + skipped_rows}, skipping: {e}")
                        continue

                logger.info(
                    f"Query completed: {processed_rows} rows processed, {skipped_rows} rows skipped")
                return results

        except Exception as e:
            logger.error(f"MSSQL query error: {e}")
            # Fallback: Boş sonuç döndür
            return []

    def execute_query(self, query, params=None):
        """SQL sorgusu çalıştırır ve sonuçları döndürür"""
        return self.execute_query_safe(query, params)

    def execute_insert(self, query, params=None):
        """INSERT işlemleri için özel metod - commit ile"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            # INSERT işlemini commit et
            conn.commit()

            logger.info(f"INSERT işlemi başarılı: {query[:50]}...")
            return True

        except Exception as e:
            logger.error(f"MSSQL INSERT error: {e}")
            if 'conn' in locals():
                conn.rollback()
            return False
        finally:
            if 'conn' in locals():
                conn.close()

    def execute_insert_with_identity(self, query, params=None):
        """INSERT işlemi yapar ve OUTPUT clause ile yeni ID'yi döndürür"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            # OUTPUT clause ile dönen değeri al
            result = cursor.fetchone()
            new_id = result[0] if result else None

            # INSERT işlemini commit et
            conn.commit()

            logger.info(f"INSERT işlemi başarılı, yeni ID: {new_id}")
            return new_id

        except Exception as e:
            logger.error(f"MSSQL INSERT error: {e}")
            if 'conn' in locals():
                conn.rollback()
            return None
        finally:
            if 'conn' in locals():
                conn.close()

    def get_tahsilat_list(self, plasiyer=None, limit=10):
        """Tahsilat listesi getirir"""
        query = """
        SELECT TOP (?) 
            [ID], 
            [Tarih], 
            [CariKod],
            [CariUnvan],
            [TahsilatTuru],
            [Tutar], 
            [Kullanici], 
            [EklemeTarihi], 
            [Durum], 
            [Aciklama],
            [Bölge],
            [CariID],
            [Plasiyer],
            [TeslimDurumu], 
            [BankaID], 
            [Taksit], 
            [EvrakNo],
            [R/G],
            [ACTIVE],
            [Banka] as BANKAADI
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        """
        params = [limit]

        if plasiyer:
            query += " WHERE UPPER([Plasiyer]) = UPPER(?)"
            params.append(plasiyer)

        query += " ORDER BY [ID] DESC"

        try:
            return self.execute_query(query, params)
        except Exception as e:
            logger.error(f"get_tahsilat_list error: {e}")
            return []

    def get_tahsilat_listesi_paginated(self, page=1, page_size=100, baslangic_tarihi='', bitis_tarihi='', plasiyer_filter='', cari_kod_filter='', cari_unvan_filter='', banka_filter=''):
        """Sayfalama destekli tahsilat listesi — [GO3].[dbo].[TAHSILAT_LOGO] (tüm kayıtlar)."""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # WHERE koşullarını oluştur
            where_conditions = ["1 = 1"]
            params = []

            # Tarih filtresi
            if baslangic_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) >= ?")
                params.append(baslangic_tarihi)
            
            if bitis_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) <= ?")
                params.append(bitis_tarihi)

            # Plasiyer filtresi
            if plasiyer_filter:
                where_conditions.append(
                    "UPPER(RTRIM(LTRIM([PLASİYER]))) = UPPER(RTRIM(LTRIM(?)))"
                )
                params.append(plasiyer_filter)

            # Cari Kod filtresi
            if cari_kod_filter:
                where_conditions.append("[CARİ KOD] LIKE ?")
                params.append(f"%{cari_kod_filter}%")

            # Cari Ünvan filtresi
            if cari_unvan_filter:
                where_conditions.append("[CARİ ÜNVAN] LIKE ?")
                params.append(f"%{cari_unvan_filter}%")

            # Banka filtresi
            if banka_filter:
                where_conditions.append("[BANKA] = ?")
                params.append(banka_filter)

            where_clause = " AND ".join(where_conditions)
            logger.debug("Tahsilat listesi WHERE: %s", where_clause)

            # Kayıt sayısı + toplam tutar tek sorguda (round-trip tasarrufu)
            count_sum_query = f"""
            SELECT COUNT(*), ISNULL(SUM(CAST([TUTAR] AS DECIMAL(18,2))), 0)
            FROM [GO3].[dbo].[TAHSILAT_LOGO]
            WHERE {where_clause}
            """
            cursor.execute(count_sum_query, params)
            cs_row = cursor.fetchone()
            total_count = int(cs_row[0] or 0)
            total_amount = float(cs_row[1] or 0)

            # Sayfalanmış veriyi al
            offset = (page - 1) * page_size

            query = f"""
            SELECT [LOGICALREF],
                   [TARİH],
                   [CARİ KOD],
                   [CARİ ÜNVAN],
                   [TAHSİLAT TÜRÜ],
                   [TUTAR], 
                   [PLASİYER], 
                   [AÇIKLAMA],
                   [BANKA],
                   [BÖLGE],
                   [PLASİYER KOD]
            FROM [GO3].[dbo].[TAHSILAT_LOGO]
            WHERE {where_clause}
            ORDER BY [TARİH] DESC, [LOGICALREF] DESC
            OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY
            """

            logger.debug(f"Sayfalanmış tahsilat listesi sorgusu: {query}")
            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                columns = [column[0] for column in cursor.description]
                row_dict = dict(zip(columns, row))
                results.append(row_dict)

            cursor.close()
            connection.close()

            # Template için uygun formata dönüştür
            tahsilat_listesi = []
            for row in results:
                # Tarih formatını düzelt
                tarih_obj = row.get('TARİH')
                if tarih_obj:
                    if hasattr(tarih_obj, 'strftime'):
                        formatted_date = tarih_obj.strftime('%d.%m.%Y')
                    else:
                        # YYYY-MM-DD formatından
                        formatted_date = str(tarih_obj)[:10]
                else:
                    formatted_date = ''

                tutar = float(row.get('TUTAR') or 0)
                logical_ref = row.get('LOGICALREF')
                pls = self.safe_decode_string(row.get('PLASİYER'))
                ck = self.safe_decode_string(row.get('CARİ KOD'))
                cu = self.safe_decode_string(row.get('CARİ ÜNVAN'))
                tt = self.safe_decode_string(row.get('TAHSİLAT TÜRÜ'))
                ac = self.safe_decode_string(row.get('AÇIKLAMA'))
                bn = self.safe_decode_string(row.get('BANKA'))
                bl = self.safe_decode_string(row.get('BÖLGE'))
                pk = self.safe_decode_string(row.get('PLASİYER KOD'))

                tahsilat_listesi.append({
                    # Veritabanı sütunları ile uyumlu anahtarlar
                    'LOGICALREF': logical_ref,
                    'TARIH': tarih_obj,
                    'FormattedDate': formatted_date,
                    'TAHSILAT_TURU': tt,
                    'CARI_KOD': ck,
                    'CARI_UNVAN': cu,
                    'PLASIYER': pls,
                    'ACIKLAMA': ac,
                    'TUTAR': tutar,
                    'BANKA': bn,
                    'BOLGE': bl,
                    'PLASIYER_KOD': pk,
                    # Şablon geriye dönük uyumluluk
                    'ID': logical_ref,
                    'Tarih': tarih_obj,
                    'CariKod': ck,
                    'CariUnvan': cu,
                    'TahsilatTuru': tt,
                    'Tutar': tutar,
                    'Aciklama': ac,
                    'Plasiyer': pls,
                    'BANKAADI': bn,
                })

            # Sayfalama bilgilerini hesapla
            total_pages = (total_count + page_size - 1) // page_size
            has_previous = page > 1
            has_next = page < total_pages

            pagination_info = {
                'total_count': total_count,
                'total_pages': total_pages,
                'current_page': page,
                'page_size': page_size,
                'has_previous': has_previous,
                'has_next': has_next,
                'previous_page': page - 1 if has_previous else None,
                'next_page': page + 1 if has_next else None,
                'page_range': range(max(1, page - 2), min(total_pages + 1, page + 3)),
                'total_amount': total_amount
            }

            return tahsilat_listesi, pagination_info

        except Exception as e:
            logger.error(f"get_tahsilat_listesi_paginated error: {e}")
            return [], {}

    def get_tahsilat_list_paginated_logo(self, plasiyer=None, page=1, page_size=100):
        """Tahsilat listesi - TAHSILATLOGO tablosundan Pagination ile (Dashboard için)"""
        offset = (page - 1) * page_size

        # Toplam kayıt sayısını bul
        count_query = """
        SELECT COUNT(*) as total_count 
        FROM [GO3].[dbo].[TAHSILATLOGO]
        """
        count_params = []

        if plasiyer:
            count_query += " WHERE UPPER([PLASİYER]) = UPPER(?)"
            count_params.append(plasiyer)

        try:
            count_result = self.execute_query(count_query, count_params)
            total_count = count_result[0]['total_count'] if count_result else 0
        except Exception as e:
            logger.error(f"Count query error: {e}")
            total_count = 0

        # Ana sorgu
        query = """
        SELECT 
            [LOGICALREF] as ID, 
            [TARİH] as Tarih, 
            [CARİ KOD] as CariKod,
            [CARİ ÜNVAN] as CariUnvan,
            [TAHSİLAT TÜRÜ] as TahsilatTuru,
            [TUTAR] as Tutar, 
            '' as Kullanici, 
            [TARİH] as EklemeTarihi, 
            CASE 
                WHEN [TR] = 70 THEN 'TAHSİL EDİLDİ'
                WHEN [TR] = 20 THEN 'NAKİT TAHSİLAT'
                WHEN [TR] = 1 THEN 'ÇEKMECE'
                WHEN [TR] = 62 THEN 'SENET'
                WHEN [TR] = 61 THEN 'ÇEK'
                ELSE 'TAMAMLANDI'
            END as Durum, 
            [AÇIKLAMA] as Aciklama,
            [PLASİYER] as Plasiyer,
            '' as TeslimDurumu, 
            [BANKA] as BANKAADI, 
            '' as Taksit, 
            [FATURANO] as EvrakNo
        FROM [GO3].[dbo].[TAHSILATLOGO]
        """
        params = []

        if plasiyer:
            query += " WHERE UPPER([PLASİYER]) = UPPER(?)"
            params.append(plasiyer)

        query += f" ORDER BY [LOGICALREF] DESC OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY"

        try:
            results = self.execute_query(query, params)

            # Pagination info
            total_pages = (total_count + page_size - 1) // page_size
            has_next = page < total_pages
            has_prev = page > 1

            # Page range hesaplama (sayfa numaraları için)
            page_range_size = 5  # Gösterilecek sayfa numarası sayısı
            start_page = max(1, page - page_range_size // 2)
            end_page = min(total_pages, start_page + page_range_size - 1)

            # Eğer end_page'den start_page'e kadar yeterli sayfa yoksa start_page'i düzelt
            if end_page - start_page + 1 < page_range_size:
                start_page = max(1, end_page - page_range_size + 1)

            page_range = list(range(start_page, end_page + 1))

            pagination_info = {
                'current_page': page,
                'total_pages': total_pages,
                'total_count': total_count,
                'total_count': total_count,  # Template için alternatif isim
                'page_size': page_size,
                'has_next': has_next,
                'has_previous': has_prev,  # Template için alternatif isim
                'has_prev': has_prev,
                'next_page': page + 1 if has_next else None,
                'previous_page': page - 1 if has_prev else None,  # Template için alternatif isim
                'prev_page': page - 1 if has_prev else None,
                'start_item': offset + 1 if total_count > 0 else 0,
                'end_item': min(offset + page_size, total_count),
                'page_range': page_range
            }

            return results, pagination_info

        except Exception as e:
            logger.error(f"get_tahsilat_list_paginated error: {e}")
            return [], {
                'current_page': 1,
                'total_pages': 0,
                'total_count': 0,
                'page_size': page_size,
                'has_next': False,
                'has_prev': False
            }

    def get_tahsilat_list_direct(self, plasiyer=None, limit=100):
        """Tahsilat listesi - GunlukTahsilat_V view'ından getirir"""
        query = """
        SELECT TOP (?) 
            [ID], 
            [Tarih], 
            [CariKod],
            [CariUnvan],
            [TahsilatTuru],
            [Tutar], 
            [Kullanici], 
            [EklemeTarihi], 
            [Durum], 
            [Aciklama],
            [Bölge],
            [CariID],
            [Plasiyer],
            [TeslimDurumu], 
            [BankaID], 
            [Taksit], 
            [EvrakNo],
            [R/G],
            [ACTIVE],
            [Banka] as BANKAADI
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        """
        params = [limit]

        if plasiyer:
            query += " WHERE UPPER([Plasiyer]) = UPPER(?)"
            params.append(plasiyer)

        query += " ORDER BY [ID] DESC"

        try:
            logger.info(
                f"Executing direct tahsilat query for plasiyer: '{plasiyer}'")
            _log_sql_debug(
                'Direct tahsilat query prepared',
                param_count=len(params),
                params_preview=_sanitize_sql_params(params),
            )
            results = self.execute_query(query, params)
            logger.info(
                f"Direct tahsilat query returned {len(results)} results")

            # İlk birkaç kayıt için debug
            if results and len(results) > 0:
                for i, result in enumerate(results[:3]):
                    logger.info(f"Sample result {i}: {result}")

            return results
        except Exception as e:
            logger.error(f"get_tahsilat_list_direct error: {e}")
            return []

    def get_tahsilat_list_paginated_gunluk(self, plasiyer=None, page=1, page_size=100, cari_kod=None, cari_unvan=None, tarih_filtresi='all', baslangic_tarihi=None, bitis_tarihi=None):
        """Tahsilat listesi - GunlukTahsilat_V tablosundan Pagination ile (Tahsilatlarım sayfası için)"""
        offset = (page - 1) * page_size

        # Toplam kayıt sayısını bul
        count_query = """
        SELECT COUNT(*) as total_count 
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        """
        count_params = []

        # WHERE koşulları
        where_conditions = []

        if plasiyer:
            where_conditions.append("UPPER([Plasiyer]) = UPPER(?)")
            count_params.append(plasiyer)

        if cari_kod:
            where_conditions.append("UPPER([CariKod]) LIKE UPPER(?)")
            count_params.append(f"%{cari_kod}%")

        if cari_unvan:
            where_conditions.append("UPPER([CariUnvan]) LIKE UPPER(?)")
            count_params.append(f"%{cari_unvan}%")

        # Tarih filtreleme
        if tarih_filtresi and tarih_filtresi != 'all':
            if tarih_filtresi == 'today':
                where_conditions.append(
                    "CAST([Tarih] AS DATE) = CAST(GETDATE() AS DATE)")
            elif tarih_filtresi == 'week':
                where_conditions.append(
                    "CAST([Tarih] AS DATE) >= CAST(DATEADD(WEEK, DATEDIFF(WEEK, 0, GETDATE()), 0) AS DATE)")
            elif tarih_filtresi == 'month':
                where_conditions.append(
                    "CAST([Tarih] AS DATE) >= CAST(DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()), 0) AS DATE)")

        # Tarih aralığı (explicit start/end)
        if baslangic_tarihi and bitis_tarihi:
            where_conditions.append("CAST([Tarih] AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)")
            count_params.append(baslangic_tarihi)
            count_params.append(bitis_tarihi)

        if where_conditions:
            count_query += " WHERE " + " AND ".join(where_conditions)

        try:
            count_result = self.execute_query(count_query, count_params)
            total_count = count_result[0]['total_count'] if count_result else 0
        except Exception as e:
            logger.error(f"Count query error: {e}")
            total_count = 0

        # Ana sorgu - GunlukTahsilat_V tablosundan
        query = """
        SELECT 
            [ID], 
            [Tarih], 
            [CariKod],
            [CariUnvan],
            [TahsilatTuru],
            [Tutar], 
            [Kullanici], 
            [EklemeTarihi], 
            [Durum], 
            [Aciklama],
            [Bölge],
            [CariID],
            [Plasiyer],
            [TeslimDurumu], 
            [BankaID], 
            [Taksit], 
            [EvrakNo],
            [R/G],
            [ACTIVE],
            [Banka] as BANKAADI
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        """
        params = []

        # WHERE koşulları (ana sorgu için aynı filtreleri kullan)
        where_conditions_main = []

        if plasiyer:
            where_conditions_main.append("UPPER([Plasiyer]) = UPPER(?)")
            params.append(plasiyer)

        if cari_kod:
            where_conditions_main.append("UPPER([CariKod]) LIKE UPPER(?)")
            params.append(f"%{cari_kod}%")

        if cari_unvan:
            where_conditions_main.append("UPPER([CariUnvan]) LIKE UPPER(?)")
            params.append(f"%{cari_unvan}%")

        # Tarih filtreleme (ana sorgu için)
        if tarih_filtresi and tarih_filtresi != 'all':
            if tarih_filtresi == 'today':
                where_conditions_main.append(
                    "CAST([Tarih] AS DATE) = CAST(GETDATE() AS DATE)")
            elif tarih_filtresi == 'week':
                where_conditions_main.append(
                    "CAST([Tarih] AS DATE) >= CAST(DATEADD(WEEK, DATEDIFF(WEEK, 0, GETDATE()), 0) AS DATE)")
            elif tarih_filtresi == 'month':
                where_conditions_main.append(
                    "CAST([Tarih] AS DATE) >= CAST(DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()), 0) AS DATE)")

        # Tarih aralığı (ana sorgu)
        if baslangic_tarihi and bitis_tarihi:
            where_conditions_main.append("CAST([Tarih] AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)")
            params.append(baslangic_tarihi)
            params.append(bitis_tarihi)

        if where_conditions_main:
            query += " WHERE " + " AND ".join(where_conditions_main)

        query += f" ORDER BY [ID] DESC OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY"

        try:
            results = self.execute_query(query, params)

            # Pagination info
            total_pages = (total_count + page_size - 1) // page_size
            has_next = page < total_pages
            has_prev = page > 1

            # Page range hesaplama (sayfa numaraları için)
            page_range_size = 5  # Gösterilecek sayfa numarası sayısı
            start_page = max(1, page - page_range_size // 2)
            end_page = min(total_pages, start_page + page_range_size - 1)

            # Eğer end_page'den start_page'e kadar yeterli sayfa yoksa start_page'i düzelt
            if end_page - start_page + 1 < page_range_size:
                start_page = max(1, end_page - page_range_size + 1)

            page_range = list(range(start_page, end_page + 1))

            pagination_info = {
                'current_page': page,
                'total_pages': total_pages,
                'total_count': total_count,
                'total_count': total_count,  # Template için alternatif isim
                'page_size': page_size,
                'has_next': has_next,
                'has_previous': has_prev,  # Template için alternatif isim
                'has_prev': has_prev,
                'next_page': page + 1 if has_next else None,
                'previous_page': page - 1 if has_prev else None,  # Template için alternatif isim
                'prev_page': page - 1 if has_prev else None,
                'start_item': offset + 1 if total_count > 0 else 0,
                'end_item': min(offset + page_size, total_count),
                'page_range': page_range
            }

            return results, pagination_info

        except Exception as e:
            logger.error(f"get_tahsilat_list_paginated_gunluk error: {e}")
            return [], {
                'current_page': 1,
                'total_pages': 0,
                'total_count': 0,
                'total_count': 0,
                'page_size': page_size,
                'has_next': False,
                'has_previous': False,
                'has_prev': False,
                'next_page': None,
                'previous_page': None,
                'prev_page': None,
                'start_item': 0,
                'end_item': 0,
                'page_range': []
            }

    def get_tahsilat_total_amount_gunluk(self, plasiyer=None, cari_kod=None, cari_unvan=None, tarih_filtresi='all', baslangic_tarihi=None, bitis_tarihi=None):
        """Tahsilatların toplam tutarını hesaplar - GunlukTahsilat_V tablosundan (Filtreleme ile)"""

        # Toplam tutar sorgusu
        query = """
        SELECT 
            ISNULL(SUM(CAST([Tutar] as DECIMAL(15,2))), 0) as total_amount
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        """
        params = []

        # WHERE koşulları (pagination fonksiyonu ile aynı)
        where_conditions = []

        if plasiyer:
            where_conditions.append("UPPER([Plasiyer]) = UPPER(?)")
            params.append(plasiyer)

        if cari_kod:
            where_conditions.append("UPPER([CariKod]) LIKE UPPER(?)")
            params.append(f"%{cari_kod}%")

        if cari_unvan:
            where_conditions.append("UPPER([CariUnvan]) LIKE UPPER(?)")
            params.append(f"%{cari_unvan}%")

        # Tarih filtreleme (pagination ile aynı koşullar)
        if tarih_filtresi and tarih_filtresi != 'all':
            if tarih_filtresi == 'today':
                where_conditions.append(
                    "CAST([Tarih] AS DATE) = CAST(GETDATE() AS DATE)")
            elif tarih_filtresi == 'week':
                where_conditions.append(
                    "CAST([Tarih] AS DATE) >= CAST(DATEADD(WEEK, DATEDIFF(WEEK, 0, GETDATE()), 0) AS DATE)")
            elif tarih_filtresi == 'month':
                where_conditions.append(
                    "CAST([Tarih] AS DATE) >= CAST(DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()), 0) AS DATE)")

        # Tarih aralığı (explicit start/end)
        if baslangic_tarihi and bitis_tarihi:
            where_conditions.append("CAST([Tarih] AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)")
            params.append(baslangic_tarihi)
            params.append(bitis_tarihi)

        if where_conditions:
            query += " WHERE " + " AND ".join(where_conditions)

        try:
            result = self.execute_query(query, params)
            if result and len(result) > 0:
                return float(result[0]['total_amount'] or 0)
            else:
                return 0.0
        except Exception as e:
            logger.error(f"get_tahsilat_total_amount_gunluk error: {e}")
            return 0.0

    def get_tahsilat_list_paginated_gunluk_all(self, page=1, page_size=100, cari_kod=None, cari_unvan=None, tarih_filtresi='all', tahsilat_turu=None, teslim_durumu=None, kullanici=None, banka=None, baslangic_tarihi=None, bitis_tarihi=None, plasiyer_filter=None):
        """Tahsilat listesi - GunlukTahsilat_V tablosundan Pagination ile (Tüm plasiyerler - PLS koşulu yok)"""
        offset = (page - 1) * page_size

        # Toplam kayıt sayısını bul
        count_query = """
        SELECT COUNT(*) as total_count 
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        """
        count_params = []

        where_conditions, count_params = build_gunluk_tahsilat_filters(
            cari_kod=cari_kod,
            cari_unvan=cari_unvan,
            tarih_filtresi=tarih_filtresi,
            tahsilat_turu=tahsilat_turu,
            teslim_durumu=teslim_durumu,
            kullanici=kullanici,
            banka=banka,
            baslangic_tarihi=baslangic_tarihi,
            bitis_tarihi=bitis_tarihi,
            plasiyer_filter=plasiyer_filter,
            banka_column='[Banka]',
        )

        # WHERE koşullarını ekle
        if where_conditions:
            count_query += " WHERE " + " AND ".join(where_conditions)

        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Toplam kayıt sayısını al
            cursor.execute(count_query, count_params)
            total_count = cursor.fetchone()[0]

            # Ana sorgu
            main_select = """
            SELECT [ID]
                  ,[Tarih]
                  ,[CariKod]
                  ,[CariUnvan]
                  ,[TahsilatTuru]
                  ,[Tutar]
                  ,[Kullanici]
                  ,[EklemeTarihi]
                  ,[Durum]
                  ,[Aciklama]
                  ,[Bölge]
                  ,[CariID]
                  ,[Plasiyer]
                  ,[TeslimDurumu]
                  ,[BankaID]
                  ,[Taksit]
                  ,[EvrakNo]
                  ,[R/G]
                  ,[ACTIVE]
                  ,[Banka]
              FROM [GO3].[dbo].[GunlukTahsilat_V]
            """

            where_sql = ""
            if where_conditions:
                where_sql = " WHERE " + " AND ".join(where_conditions)
            order_sql = " ORDER BY [ID] DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
            main_params = count_params + [offset, page_size]

            rows = None
            description = None
            try:
                cursor.execute(
                    main_select + where_sql + order_sql, main_params)
                description = cursor.description
                rows = cursor.fetchall()
            except Exception as e:
                raise

            # Sonuçları formatla — sütun sırasına bağlı kalmadan description ile eşleştir
            results = []
            for row in rows:
                rd = self._row_to_norm_dict(row, description)
                # Kod kolonu view'dan kalktığı için Frontend'in data-kod attribute'unu boş / None bırakabiliriz
                kod_str = ''
                tv = rd.get('tutar')
                try:
                    tutar_num = float(tv) if tv is not None else 0.0
                except (TypeError, ValueError):
                    tutar_num = 0.0
                result = {
                    'ID': rd.get('id'),
                    'Tarih': rd.get('tarih'),
                    'CariKod': self._mssql_cell_to_str(rd.get('carikod')),
                    'Kod': kod_str,
                    'CariUnvan': self._mssql_cell_to_str(rd.get('cariunvan')),
                    'TahsilatTuru': self._mssql_cell_to_str(rd.get('tahsilatturu')),
                    'Tutar': tutar_num,
                    'Kullanici': self._mssql_cell_to_str(rd.get('kullanici')),
                    'EklemeTarihi': rd.get('eklemetarihi'),
                    'Durum': self._mssql_cell_to_str(rd.get('durum')),
                    'Aciklama': self._mssql_cell_to_str(rd.get('aciklama')),
                    'Plasiyer': self._mssql_cell_to_str(rd.get('plasiyer')),
                    'TeslimDurumu': self._mssql_cell_to_str(rd.get('teslimdurumu')),
                    'BANKAADI': self._mssql_cell_to_str(rd.get('banka')),
                    'Taksit': rd.get('taksit'),
                    'EvrakNo': self._mssql_cell_to_str(rd.get('evrakno')),
                    'Bolge': self._mssql_cell_to_str(rd.get('bölge')),
                    'CariID': rd.get('cariid'),
                    'R/G': self._mssql_cell_to_str(rd.get('r/g')),
                    'ACTIVE': rd.get('active'),
                }
                results.append(result)

            conn.close()

            # Pagination bilgilerini hesapla
            total_pages = (total_count + page_size - 1) // page_size
            has_next = page < total_pages
            has_previous = page > 1
            next_page = page + 1 if has_next else None
            previous_page = page - 1 if has_previous else None

            pagination_info = {
                'current_page': page,
                'total_pages': total_pages,
                'total_count': total_count,
                'page_size': page_size,
                'has_next': has_next,
                'has_previous': has_previous,
                'next_page': next_page,
                'previous_page': previous_page,
                'start_item': offset + 1,
                'end_item': min(offset + page_size, total_count),
                'page_range': list(range(max(1, page - 2), min(total_pages + 1, page + 3)))
            }

            return results, pagination_info

        except Exception as e:
            logger.error(f"get_tahsilat_list_paginated_gunluk_all error: {e}")
            return [], {
                'current_page': 1,
                'total_pages': 0,
                'total_count': 0,
                'page_size': page_size,
                'has_next': False,
                'has_previous': False,
                'next_page': None,
                'previous_page': None,
                'start_item': 0,
                'end_item': 0,
                'page_range': []
            }

    def get_tahsilat_total_amount_gunluk_all(self, cari_kod=None, cari_unvan=None, tarih_filtresi='all', tahsilat_turu=None, teslim_durumu=None, kullanici=None, banka=None, baslangic_tarihi=None, bitis_tarihi=None, plasiyer_filter=None):
        """Tahsilatların toplam tutarını hesaplar - Tüm plasiyerler (PLS koşulu yok)"""
        query = f"""
        SELECT 
            ISNULL(SUM(CAST([Tutar] as DECIMAL(15,2))), 0) as total_amount
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        """
        where_conditions, params = build_gunluk_tahsilat_filters(
            cari_kod=cari_kod,
            cari_unvan=cari_unvan,
            tarih_filtresi=tarih_filtresi,
            tahsilat_turu=tahsilat_turu,
            teslim_durumu=teslim_durumu,
            kullanici=kullanici,
            banka=banka,
            baslangic_tarihi=baslangic_tarihi,
            bitis_tarihi=bitis_tarihi,
            plasiyer_filter=plasiyer_filter,
            banka_column='[BANKAADI]',
        )

        # WHERE koşullarını ekle
        if where_conditions:
            query += " WHERE " + " AND ".join(where_conditions)

        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            result = cursor.fetchone()
            conn.close()

            if result and result[0] is not None:
                return float(result[0])
            else:
                return 0.0

        except Exception as e:
            logger.error(f"get_tahsilat_total_amount_gunluk_all error: {e}")
            return 0.0

    def get_nakit_teslim_edilmedi_total(self):
        """Nakit tahsilat türünde TESLİM EDİLMEDİ durumundaki tutarların toplamını hesaplar"""
        query = """
        SELECT 
            ISNULL(SUM(CAST([Tutar] as DECIMAL(15,2))), 0) as total_amount
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        WHERE [TahsilatTuru] = 'Nakit' AND [TeslimDurumu] = 'TESLİM EDİLMEDİ'
        """

        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(query)
            result = cursor.fetchone()
            conn.close()

            if result and result[0] is not None:
                return float(result[0])
            else:
                return 0.0

        except Exception as e:
            logger.error(f"get_nakit_teslim_edilmedi_total error: {e}")
            return 0.0

    def get_nakit_teslim_edilmedi_by_user(self):
        """Her kullanıcı için nakit tahsilat türünde TESLİM EDİLMEDİ durumundaki tutarların toplamını hesaplar"""
        query = """
        SELECT 
            [Kullanici],
            ISNULL(SUM(CAST([Tutar] as DECIMAL(15,2))), 0) as total_amount
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        WHERE [TahsilatTuru] = 'Nakit' AND [TeslimDurumu] = 'TESLİM EDİLMEDİ'
        GROUP BY [Kullanici]
        ORDER BY [Kullanici]
        """

        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()

            results = []
            for row in rows:
                kullanici = self.safe_decode_string(
                    row[0]) if row[0] else 'Bilinmeyen'
                tutar = float(row[1]) if row[1] else 0.0
                results.append({
                    'kullanici': kullanici,
                    'tutar': tutar
                })

            return results

        except Exception as e:
            logger.error(f"get_nakit_teslim_edilmedi_by_user error: {e}")
            return []

    def get_nakit_teslim_edilmedi_total_with_date(self, baslangic_tarihi='', bitis_tarihi=''):
        """Tarih aralığı ile nakit tahsilat türünde TESLİM EDİLMEDİ durumundaki tutarların toplamını hesaplar"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Tarih filtreleme
            where_conditions = ["[TahsilatTuru] = 'Nakit'",
                                "[TeslimDurumu] = 'TESLİM EDİLMEDİ'"]
            params = []

            if baslangic_tarihi:
                where_conditions.append("CAST([Tarih] AS DATE) >= ?")
                params.append(baslangic_tarihi)

            if bitis_tarihi:
                where_conditions.append("CAST([Tarih] AS DATE) <= ?")
                params.append(bitis_tarihi)

            query = f"""
            SELECT 
                ISNULL(SUM(CAST([Tutar] as DECIMAL(15,2))), 0) as total_amount
            FROM [GO3].[dbo].[GunlukTahsilat_V]
            WHERE {" AND ".join(where_conditions)}
            """

            cursor.execute(query, params)
            result = cursor.fetchone()
            conn.close()

            if result and result[0] is not None:
                return float(result[0])
            else:
                return 0.0

        except Exception as e:
            logger.error(
                f"get_nakit_teslim_edilmedi_total_with_date error: {e}")
            return 0.0

    def get_nakit_teslim_edilmedi_by_user_with_date(self, baslangic_tarihi='', bitis_tarihi=''):
        """Tarih aralığı ile her kullanıcı için nakit tahsilat türünde TESLİM EDİLMEDİ durumundaki tutarların toplamını hesaplar"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Tarih filtreleme
            where_conditions = ["[TahsilatTuru] = 'Nakit'",
                                "[TeslimDurumu] = 'TESLİM EDİLMEDİ'"]
            params = []

            if baslangic_tarihi:
                where_conditions.append("CAST([Tarih] AS DATE) >= ?")
                params.append(baslangic_tarihi)

            if bitis_tarihi:
                where_conditions.append("CAST([Tarih] AS DATE) <= ?")
                params.append(bitis_tarihi)

            query = f"""
            SELECT 
                [Kullanici],
                ISNULL(SUM(CAST([Tutar] as DECIMAL(15,2))), 0) as total_amount
            FROM [GO3].[dbo].[GunlukTahsilat_V]
            WHERE {" AND ".join(where_conditions)}
            GROUP BY [Kullanici]
            ORDER BY [Kullanici]
            """

            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            results = []
            for row in rows:
                kullanici = self.safe_decode_string(
                    row[0]) if row[0] else 'Bilinmeyen'
                tutar = float(row[1]) if row[1] else 0.0
                results.append({
                    'kullanici': kullanici,
                    'tutar': tutar
                })

            return results

        except Exception as e:
            logger.error(
                f"get_nakit_teslim_edilmedi_by_user_with_date error: {e}")
            return []

    def get_tahsilatlar_by_date(self, baslangic_tarihi='', bitis_tarihi=''):
        """Verilen tarih aralığındaki tüm tahsilatları (detay) döndürür (GunlukTahsilat_V)."""
        try:
            where_conditions = ["1=1"]
            params = []

            if baslangic_tarihi:
                where_conditions.append("CAST([Tarih] AS DATE) >= ?")
                params.append(baslangic_tarihi)

            if bitis_tarihi:
                where_conditions.append("CAST([Tarih] AS DATE) <= ?")
                params.append(bitis_tarihi)

            query = f"""
            SELECT 
                [ID], [Tarih], [CariKod], [CariUnvan], [TahsilatTuru],
                [Tutar], [Kullanici], [Plasiyer], [TeslimDurumu], [Banka] as BANKAADI,
                [Aciklama], [EvrakNo]
            FROM [GO3].[dbo].[GunlukTahsilat_V]
            WHERE {" AND ".join(where_conditions)}
            ORDER BY [Tarih] ASC, [ID] ASC
            """

            return self.execute_query(query, params)
        except Exception as e:
            logger.error(f"get_tahsilatlar_by_date error: {e}")
            return []

    def get_satislar_by_date(self, baslangic_tarihi='', bitis_tarihi=''):
        """Verilen tarih aralığındaki tüm satış faturalarını (detay) döndürür (FATURA)."""
        try:
            where_conditions = ["(TRCODE=8 OR TRCODE=7)"]
            params = []

            if baslangic_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) >= ?")
                params.append(baslangic_tarihi)

            if bitis_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) <= ?")
                params.append(bitis_tarihi)

            query = f"""
            SELECT 
                [FATURAID], [TARİH], [CARİ KOD], [CARİ ÜNVAN], [FATURA NO],
                [TUTAR] as NET_TOPLAM, [PLASİYER] as SATAN_KİŞİ, [BÖLGE], [FATURA TÜRÜ]
            FROM [GO3].[dbo].[FATURA]
            WHERE {" AND ".join(where_conditions)}
            ORDER BY [TARİH] ASC, [FATURAID] ASC
            """

            return self.execute_query(query, params)
        except Exception as e:
            logger.error(f"get_satislar_by_date error: {e}")
            return []

    def update_teslim_durumu_batch(self, tahsilat_ids, new_status):
        """Birden fazla tahsilat kaydının teslim durumunu günceller"""
        if not tahsilat_ids or not new_status:
            return 0

        # ID'leri string'e çevir ve temizle
        id_list = [str(id).strip() for id in tahsilat_ids if str(id).strip()]
        if not id_list:
            return 0

        # SQL IN clause için placeholder'lar oluştur
        placeholders = ','.join(['?' for _ in id_list])

        query = f"""
        UPDATE [GO3].[dbo].[GunlukTahsilat_V]
        SET [TeslimDurumu] = ?
        WHERE [ID] IN ({placeholders})
        """

        params = [new_status] + id_list

        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            updated_count = cursor.rowcount
            conn.commit()
            conn.close()

            logger.info(
                f"Updated {updated_count} records to status: {new_status}")
            return updated_count

        except Exception as e:
            logger.error(f"update_teslim_durumu_batch error: {e}")
            return 0

    def update_logo_durumu_batch(self, tahsilat_ids, new_status):
        """Birden fazla tahsilat kaydının Logo durumunu günceller"""
        if not tahsilat_ids or not new_status:
            return 0

        # ID'leri string'e çevir ve temizle
        id_list = [str(id).strip() for id in tahsilat_ids if str(id).strip()]
        if not id_list:
            return 0

        # SQL IN clause için placeholder'lar oluştur
        placeholders = ','.join(['?' for _ in id_list])

        query = f"""
        UPDATE [GO3].[dbo].[GunlukTahsilat_V]
        SET [Durum] = ?
        WHERE [ID] IN ({placeholders})
        """

        params = [new_status] + id_list

        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            updated_count = cursor.rowcount
            conn.commit()
            conn.close()

            logger.info(
                f"Updated {updated_count} records Logo durum to: {new_status}")
            return updated_count

        except Exception as e:
            logger.error(f"update_logo_durumu_batch error: {e}")
            return 0

    def check_tahsilat_user_permission(self, tahsilat_ids, current_user):
        """Seçili tahsilat kayıtlarının kullanıcı yetkisini kontrol eder"""
        if not tahsilat_ids or not current_user:
            return []

        # ID'leri string'e çevir ve temizle
        id_list = [str(id).strip() for id in tahsilat_ids if str(id).strip()]
        if not id_list:
            return []

        # SQL IN clause için placeholder'lar oluştur
        placeholders = ','.join(['?' for _ in id_list])

        query = f"""
        SELECT [ID] 
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        WHERE [ID] IN ({placeholders}) 
        AND UPPER([Kullanici]) != UPPER(?)
        """

        params = id_list + [current_user]

        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            unauthorized_ids = [row[0] for row in cursor.fetchall()]
            conn.close()

            logger.info(
                f"Unauthorized IDs for user {current_user}: {unauthorized_ids}")
            return unauthorized_ids

        except Exception as e:
            logger.error(f"check_tahsilat_user_permission error: {e}")
            return []

    def get_tahsilat_stats(self, plasiyer):
        """Tahsilat istatistikleri (gün/hafta/ay) - GunlukTahsilat_V ile tutarlı kaynak."""
        query = """
        DECLARE @today DATE = CAST(GETDATE() AS DATE);
        DECLARE @week_start DATE = DATEADD(day, -((DATEPART(WEEKDAY, @today) + @@DATEFIRST - 2) % 7), @today);
        DECLARE @month_start DATE = DATEFROMPARTS(YEAR(@today), MONTH(@today), 1);

        SELECT
            COUNT(CASE WHEN CAST([Tarih] AS DATE) = @today THEN 1 END) as gunluk_adet,
            ISNULL(SUM(CASE WHEN CAST([Tarih] AS DATE) = @today THEN CAST([Tutar] as DECIMAL(15,2)) END), 0) as gunluk_tutar,
            COUNT(CASE WHEN CAST([Tarih] AS DATE) >= @week_start THEN 1 END) as haftalik_adet,
            ISNULL(SUM(CASE WHEN CAST([Tarih] AS DATE) >= @week_start THEN CAST([Tutar] as DECIMAL(15,2)) END), 0) as haftalik_tutar,
            COUNT(CASE WHEN CAST([Tarih] AS DATE) >= @month_start THEN 1 END) as aylik_adet,
            ISNULL(SUM(CASE WHEN CAST([Tarih] AS DATE) >= @month_start THEN CAST([Tutar] as DECIMAL(15,2)) END), 0) as aylik_tutar
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        WHERE UPPER(RTRIM(LTRIM([Plasiyer]))) = UPPER(RTRIM(LTRIM(?)))
        """

        try:
            results = self.execute_query(query, (plasiyer,))
            if results and len(results) > 0:
                return results[0]
            else:
                return {
                    'gunluk_adet': 0, 'gunluk_tutar': 0,
                    'haftalik_adet': 0, 'haftalik_tutar': 0,
                    'aylik_adet': 0, 'aylik_tutar': 0
                }
        except Exception as e:
            logger.error(f"get_tahsilat_stats error: {e}")
            return {
                'gunluk_adet': 0, 'gunluk_tutar': 0,
                'haftalik_adet': 0, 'haftalik_tutar': 0,
                'aylik_adet': 0, 'aylik_tutar': 0
            }

    def get_tahsilat_stats_with_month_filter(self, plasiyer=None, selected_months=None):
        """Ay filtreli tahsilat istatistikleri — Genel Dashboard: [GO3].[dbo].[TAHSILAT_LOGO]."""
        # Ay filtresini hazırla (aylik için)
        if selected_months:
            month_list = ','.join([str(m) for m in selected_months])
            month_condition = f"AND MONTH([TARİH]) IN ({month_list})"
        else:
            # Bu fonksiyon ay filtreli kullanılıyor; boş gelirse mevcut ayı baz al.
            month_condition = "AND MONTH([TARİH]) = MONTH(GETDATE())"

        query = f"""
        DECLARE @today DATE = CAST(GETDATE() AS DATE);
        DECLARE @week_start DATE = DATEADD(day, -((DATEPART(WEEKDAY, @today) + @@DATEFIRST - 2) % 7), @today);
        SELECT
            COUNT(CASE WHEN CAST([TARİH] AS DATE) = @today THEN 1 END) as gunluk_adet,
            ISNULL(SUM(CASE WHEN CAST([TARİH] AS DATE) = @today THEN CAST([TUTAR] AS DECIMAL(15,2)) END), 0) as gunluk_tutar,
            COUNT(CASE WHEN CAST([TARİH] AS DATE) >= @week_start AND YEAR([TARİH]) = YEAR(GETDATE()) {month_condition} THEN 1 END) as haftalik_adet,
            ISNULL(SUM(CASE WHEN CAST([TARİH] AS DATE) >= @week_start AND YEAR([TARİH]) = YEAR(GETDATE()) {month_condition} THEN CAST([TUTAR] AS DECIMAL(15,2)) END), 0) as haftalik_tutar,
            COUNT(CASE WHEN YEAR([TARİH]) = YEAR(GETDATE()) {month_condition} THEN 1 END) as aylik_adet,
            ISNULL(SUM(CASE WHEN YEAR([TARİH]) = YEAR(GETDATE()) {month_condition} THEN CAST([TUTAR] AS DECIMAL(15,2)) END), 0) as aylik_tutar
        FROM [GO3].[dbo].[TAHSILAT_LOGO]
        WHERE UPPER(RTRIM(LTRIM([PLASİYER]))) = UPPER(RTRIM(LTRIM(?)))
        """
        if not plasiyer:
            return {
                'gunluk_adet': 0, 'gunluk_tutar': 0,
                'haftalik_adet': 0, 'haftalik_tutar': 0,
                'aylik_adet': 0, 'aylik_tutar': 0
            }
        params = [plasiyer]

        try:
            results = self.execute_query(query, params)
            if results and len(results) > 0:
                row = results[0]
                return {
                    'gunluk_adet': int(row.get('gunluk_adet', 0) or 0),
                    'gunluk_tutar': float(row.get('gunluk_tutar', 0) or 0),
                    'haftalik_adet': int(row.get('haftalik_adet', 0) or 0),
                    'haftalik_tutar': float(row.get('haftalik_tutar', 0) or 0),
                    'aylik_adet': int(row.get('aylik_adet', 0) or 0),
                    'aylik_tutar': float(row.get('aylik_tutar', 0) or 0),
                }
            return {
                'gunluk_adet': 0, 'gunluk_tutar': 0,
                'haftalik_adet': 0, 'haftalik_tutar': 0,
                'aylik_adet': 0, 'aylik_tutar': 0
            }
        except Exception as e:
            logger.error(f"get_tahsilat_stats_with_month_filter error: {e}")
            return {
                'gunluk_adet': 0, 'gunluk_tutar': 0,
                'haftalik_adet': 0, 'haftalik_tutar': 0,
                'aylik_adet': 0, 'aylik_tutar': 0
            }

    def get_satis_list(self, plasiyer=None, limit=10):
        """Son satışları getirir"""
        query = """
        SELECT TOP (?) [ID], [TARİH], [CARİ KOD], [CARİ ÜNVAN], [FATURA NO],
               [NET TOPLAM], [CARİ PLASİYER], [BÖLGE]
        FROM [GO3].[dbo].[FATURALAR]
        """
        params = [limit]

        if plasiyer:
            query += " WHERE UPPER([CARİ PLASİYER]) = UPPER(?)"
            params.append(plasiyer)

        query += " ORDER BY [ID] DESC"

        try:
            return self.execute_query(query, params)
        except Exception as e:
            logger.error(f"get_satis_list error: {e}")
            return []

    def get_top_cariler(self, plasiyer, limit=5):
        """Bu ayki en yüksek tahsilata sahip carileri getirir"""
        query = """
        SELECT TOP (?)
            [CariUnvan],
            ISNULL(SUM(CAST([Tutar] as DECIMAL(15,2))), 0) as toplam_tutar,
            COUNT(*) as tahsilat_sayisi
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        WHERE UPPER([Plasiyer]) = UPPER(?)
            AND MONTH([Tarih]) = MONTH(GETDATE())
            AND YEAR([Tarih]) = YEAR(GETDATE())
        GROUP BY [CariUnvan]
        ORDER BY toplam_tutar DESC
        """

        try:
            return self.execute_query(query, (limit, plasiyer))
        except Exception as e:
            logger.error(f"get_top_cariler error: {e}")
            return []

    def get_cari_hesaplar(self, specode):
        """Belirli SPECODE'a sahip cari hesapları getirir (LOGICALREF dahil)"""
        query = """
        SELECT [CODE], [DEFINITION_], [SPECODE], [BAKİYE], [BOLGE], [LOGICALREF]
        FROM [GO3].[dbo].[CARIBAKIYE]
        WHERE [SPECODE] = ?
        ORDER BY [DEFINITION_]
        """

        try:
            return self.execute_query(query, (specode,))
        except Exception as e:
            logger.error(f"get_cari_hesaplar error: {e}")
            return []

    def get_cari_hesaplar_all(self):
        """Tüm cari hesapları getirir (LOGICALREF dahil) - SPECODE filtresi yok"""
        query = """
        SELECT [CODE], [DEFINITION_], [SPECODE], [BAKİYE], [BOLGE], [LOGICALREF]
        FROM [GO3].[dbo].[CARIBAKIYE]
        ORDER BY [DEFINITION_]
        """

        try:
            logger.info(f"get_cari_hesaplar_all: SQL sorgusu çalıştırılıyor...")
            result = self.execute_query(query)
            logger.info(f"get_cari_hesaplar_all: Sorgu sonucu {len(result) if result else 0} kayıt döndü")
            if result and len(result) > 0:
                logger.info(f"get_cari_hesaplar_all: İlk 3 kayıt: {[r.get('CODE') for r in result[:3]]}")
            else:
                logger.warning(f"get_cari_hesaplar_all: Boş liste döndü!")
            return result
        except Exception as e:
            logger.error(f"get_cari_hesaplar_all error: {e}")
            import traceback
            logger.error(f"get_cari_hesaplar_all traceback: {traceback.format_exc()}")
            return []

    def get_cari_hesaplar_by_code_prefix(self, code_prefix):
        """Belirli bir prefix ile başlayan cari hesapları getirir (LOGICALREF dahil)"""
        query = """
        SELECT [CODE], [DEFINITION_], [SPECODE], [BAKİYE], [BOLGE], [LOGICALREF]
        FROM [GO3].[dbo].[CARIBAKIYE]
        WHERE [CODE] LIKE ?
        ORDER BY [DEFINITION_]
        """

        try:
            return self.execute_query(query, (f"{code_prefix}%",))
        except Exception as e:
            logger.error(f"get_cari_hesaplar_by_code_prefix error: {e}")
            return []

    def resolve_cari_logicalref_yeni_tahsilat_post(
        self, cari_code, username_upper, django_username
    ):
        """
        Yeni tahsilat POST için tek satır MSSQL erişimi.
        CARIBAKIYE üzerinden LOGICALREF döner; yetki yoksa veya cari yoksa None.
        GET'teki listeyle aynı kurallar: özel kullanıcılar 120* kodlar, diğerleri SPECODE = kullanıcı adı.
        """
        code = (cari_code or "").strip()
        if not code:
            return None
        ALL_CARI = ("SEZEN",)
        SPECIAL = ("FIRAT", "SEZEN", "OĞUZ", "OGUZ", "TURAN")
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT TOP 1 [LOGICALREF], [CODE], [SPECODE]
                FROM [GO3].[dbo].[CARIBAKIYE]
                WHERE [CODE] = ?
                """,
                (code,),
            )
            row = cursor.fetchone()
            if not row or row[0] is None:
                return None
            logicalref = int(row[0])
            db_code = self.safe_decode_string(row[1]) if row[1] is not None else code
            specode_raw = row[2]
            specode = (
                self.safe_decode_string(specode_raw).strip()
                if specode_raw is not None
                else ""
            )

            if username_upper in ALL_CARI:
                return logicalref
            if username_upper in SPECIAL:
                if not (db_code.strip().startswith("120")):
                    return None
            else:
                uname = (django_username or "").strip()
                if specode != uname:
                    return None
            return logicalref
        except Exception as e:
            logger.error(f"resolve_cari_logicalref_yeni_tahsilat_post error: {e}")
            return None
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def search_cari_yeni_tahsilat(
        self,
        q,
        username_upper,
        django_username,
        scope="plasiyer",
        limit=45,
    ):
        """
        Yeni tahsilat cari araması — sınırlı sonuç, sunucu tarafı LIKE.
        scope=plasiyer: plasiyer kuralları (120% veya SPECODE = kullanıcı).
        scope=muhasebe: tüm CARIBAKIYE (yalnızca yetkili sayfalar kullanmalı).
        """
        raw = (q or "").strip()
        # LIKE özel karakterlerini kaldır (enjeksyon / beklenmeyen joker)
        for ch in "%_[]":
            raw = raw.replace(ch, "")
        raw = raw.strip()
        if len(raw) < 2:
            return []
        try:
            lim = max(5, min(int(limit or 45), 80))
        except (TypeError, ValueError):
            lim = 45
        pattern = f"%{raw}%"
        SPECIAL = ("FIRAT", "SEZEN", "OĞUZ", "OGUZ", "TURAN")

        if scope == "muhasebe":
            query = """
            SELECT TOP (?) [CODE], [DEFINITION_], [SPECODE], [BAKİYE], [BOLGE], [LOGICALREF]
            FROM [GO3].[dbo].[CARIBAKIYE]
            WHERE ([CODE] LIKE ? OR [DEFINITION_] LIKE ?)
            ORDER BY [DEFINITION_]
            """
            params = (lim, pattern, pattern)
        elif username_upper in SPECIAL:
            query = """
            SELECT TOP (?) [CODE], [DEFINITION_], [SPECODE], [BAKİYE], [BOLGE], [LOGICALREF]
            FROM [GO3].[dbo].[CARIBAKIYE]
            WHERE [CODE] LIKE N'120%' AND ([CODE] LIKE ? OR [DEFINITION_] LIKE ?)
            ORDER BY [DEFINITION_]
            """
            params = (lim, pattern, pattern)
        else:
            uname = (django_username or "").strip()
            query = """
            SELECT TOP (?) [CODE], [DEFINITION_], [SPECODE], [BAKİYE], [BOLGE], [LOGICALREF]
            FROM [GO3].[dbo].[CARIBAKIYE]
            WHERE [SPECODE] = ? AND ([CODE] LIKE ? OR [DEFINITION_] LIKE ?)
            ORDER BY [DEFINITION_]
            """
            params = (lim, uname, pattern, pattern)

        try:
            return self.execute_query(query, params)
        except Exception as e:
            logger.error(f"search_cari_yeni_tahsilat error: {e}")
            return []

    def get_cari_bakiye_listesi(self, specode, bolge=None):
        """Cari bakiye listesini getirir - SPECODE ve isteğe bağlı BOLGE filtresi ile"""
        query = """
        SELECT [CODE]
              ,[DEFINITION_]
              ,[SPECODE]
              ,[TARIHS]
              ,[SATISGECENSURE]
              ,[TARIHT]
              ,[TAHSILATGECENSURE]
              ,[BAKİYE]
              ,[BOLGE]
        FROM [GO3].[dbo].[CARIBAKIYE]
        WHERE [SPECODE] = ?
        """

        params = [specode]

        # BOLGE filtresi ekle
        if bolge and bolge != 'all':
            query += " AND [BOLGE] = ?"
            params.append(bolge)

        query += " ORDER BY [BAKİYE] DESC"

        try:
            return self.execute_query(query, params)
        except Exception as e:
            logger.error(f"get_cari_bakiye_listesi error: {e}")
            return []

    def get_unique_bolgeler(self, specode):
        """Belirli SPECODE için benzersiz bölgeleri getirir"""
        query = """
        SELECT DISTINCT [BOLGE]
        FROM [GO3].[dbo].[CARIBAKIYE]
        WHERE [SPECODE] = ? AND [BOLGE] IS NOT NULL AND [BOLGE] != ''
        ORDER BY [BOLGE]
        """

        try:
            results = self.execute_query(query, (specode,))
            return [row['BOLGE'] for row in results]
        except Exception as e:
            logger.error(f"get_unique_bolgeler error: {e}")
            return []

    def get_all_unique_bolgeler(self):
        """Tüm plasiyerler için benzersiz bölgeleri getirir"""
        try:
            # Sabit bölge listesi - veritabanından alınan bilinen bölgeler
            known_bolgeler = [
                'ADANA', 'KOZAN', 'MARAŞ', 'KADİRLİ', 'İSTANBUL', 'ANKARA', 
                'İZMİR', 'BURSA', 'ANTALYA', 'ADAPAZARI', 'BOLU', 'ZONGULDAK',
                'TRABZON', 'SAMSUN', 'GAZİANTEP', 'ŞANLIURFA', 'DİYARBAKIR',
                'MALATYA', 'ELAZIĞ', 'VAN', 'ERZURUM', 'KAYSERİ', 'SİVAS'
            ]
            
            # Veritabanından gerçek bölgeleri almaya çalış
            try:
                plasiyer_list = self.get_all_plasiyer_list()
                all_bolgeler = set()
                for plasiyer in plasiyer_list:
                    bolgeler = self.get_unique_bolgeler(plasiyer)
                    all_bolgeler.update(bolgeler)
                
                if all_bolgeler:
                    result = sorted(list(all_bolgeler))
                    logger.info(f"get_all_unique_bolgeler - veritabanından alınan bölgeler: {result}")
                    return result
                else:
                    logger.info(f"get_all_unique_bolgeler - veritabanı boş, sabit liste kullanılıyor: {known_bolgeler}")
                    return known_bolgeler
                    
            except Exception as e:
                logger.error(f"get_all_unique_bolgeler - veritabanı hatası: {e}, sabit liste kullanılıyor")
                return known_bolgeler
                
        except Exception as e:
            logger.error(f"get_all_unique_bolgeler error: {e}")
            return []

    def get_all_unique_ebelge_turleri(self):
        """Tüm benzersiz E-Belge Türü değerlerini getirir"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            
            query = """
                SELECT DISTINCT [E-BELGE TÜRÜ]
                FROM [GO3].[dbo].[FATURA]
                WHERE [E-BELGE TÜRÜ] IS NOT NULL 
                  AND [E-BELGE TÜRÜ] != ''
                  AND (TRCODE=8 OR TRCODE=7)
                ORDER BY [E-BELGE TÜRÜ]
            """
            
            cursor.execute(query)
            results = cursor.fetchall()
            
            ebelge_turleri = []
            for row in results:
                if row[0]:
                    decoded_value = self.safe_decode_string(row[0]).strip()
                    if decoded_value and decoded_value not in ebelge_turleri:
                        ebelge_turleri.append(decoded_value)
            
            cursor.close()
            connection.close()
            
            return sorted(ebelge_turleri)
            
        except Exception as e:
            logger.error(f"get_all_unique_ebelge_turleri error: {e}")
            return []

    def get_faturalar_listesi(self, plasiyer, page=1, page_size=100):
        """FATURA master tablosunu sayfalama ile getirir - PLASİYER'e göre filtrelenir"""

        # Offset hesapla
        offset = (page - 1) * page_size

        # Toplam kayıt sayısını al
        count_query = """
        SELECT COUNT(*) as total_count
        FROM [GO3].[dbo].[FATURA]
        WHERE [PLASİYER] = ? AND [TRCODE] = 8
        """

        # Ana sorgu
        query = """
        SELECT [CARİ KOD]
              ,[CARİ ÜNVAN]
              ,[FATURA NO]
              ,[TARİH]
              ,[TUTAR]
              ,[FATURAID]
              ,[TRCODE]
              ,[FATURA TÜRÜ]
              ,[AÇIKLAMA]
              ,[BELGE NO]
              ,[İPTAL DURUMU]
              ,[PLASİYER]
              ,[BÖLGE]
              ,[RESMİYET]
              ,[PLASİYER KOD]
        FROM [GO3].[dbo].[FATURA]
        WHERE [PLASİYER] = ? AND [TRCODE] = 8
        ORDER BY [FATURAID] DESC
        OFFSET ? ROWS
        FETCH NEXT ? ROWS ONLY
        """

        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Toplam sayıyı al
            cursor.execute(count_query, (plasiyer,))
            total_count = cursor.fetchone()[0]

            # Ana veriyi al
            cursor.execute(query, (plasiyer, offset, page_size))
            rows = cursor.fetchall()

            # Satış listesini formatla
            satis_listesi = []
            for row in rows:
                # Tarih formatını düzenle
                tarih_obj = row[3] if row[3] else None  # TARİH
                formatted_date = ""
                if tarih_obj:
                    try:
                        if hasattr(tarih_obj, 'strftime'):
                            formatted_date = tarih_obj.strftime('%d.%m.%Y')
                        else:
                            formatted_date = str(tarih_obj)[:10]
                    except:
                        formatted_date = str(tarih_obj)

                satis_listesi.append({
                    'ID': row[5],  # FATURAID
                    'TARİH': tarih_obj,
                    'FormattedDate': formatted_date,
                    'CARİ_KOD': self.safe_decode_string(row[0]),  # CARİ KOD
                    # CARİ ÜNVAN
                    'CARİ_ÜNVAN': self.safe_decode_string(row[1]),
                    'NET_TOPLAM': float(row[4] if row[4] else 0),  # TUTAR
                    # PLASİYER
                    'CARİ_PLASİYER': self.safe_decode_string(row[11]),
                    'BÖLGE': self.safe_decode_string(row[12]),  # BÖLGE
                    # BÖLGE as ÇIKIŞ_ŞUBE
                    'ÇIKIŞ_ŞUBE': self.safe_decode_string(row[12]),
                    # FATURA TÜRÜ
                    'FATURA_TÜRÜ': self.safe_decode_string(row[7]),
                    'BELGE_NO': self.safe_decode_string(row[9]),  # BELGE NO
                    'FATURA_NO': self.safe_decode_string(row[2]),  # FATURA NO
                    'İŞLEM_TARİHİ': tarih_obj,
                    'TRCODE': row[6],  # TRCODE
                    'AÇIKLAMA': self.safe_decode_string(row[8]),  # AÇIKLAMA
                    # İPTAL DURUMU
                    'İPTAL_DURUMU': self.safe_decode_string(row[10]),
                    'RESMİYET': self.safe_decode_string(row[13]),  # RESMİYET
                    # PLASİYER KOD
                    'PLASİYER_KOD': self.safe_decode_string(row[14]),
                    # PLASİYER as SATAN_KİŞİ
                    'SATAN_KİŞİ': self.safe_decode_string(row[11]),
                })

            # Sayfalama bilgilerini hesapla
            total_pages = (total_count + page_size - 1) // page_size
            has_previous = page > 1
            has_next = page < total_pages

            pagination_info = {
                'total_count': total_count,
                'total_pages': total_pages,
                'current_page': page,
                'page_size': page_size,
                'has_previous': has_previous,
                'has_next': has_next,
                'previous_page': page - 1 if has_previous else None,
                'next_page': page + 1 if has_next else None,
            }

            connection.close()
            return satis_listesi, pagination_info

        except Exception as e:
            logger.error(f"get_faturalar_listesi error: {e}")
            return [], {}

    def get_fatura_detaylari(self, invoice_id):
        """Belirli fatura ID'sine ait detayları DETAY tablosundan getirir"""
        query = """
        SELECT [TARİH]
              ,[MALZEME KODU]
              ,[AÇIKLAMASI]
              ,[MARKA]
              ,[MALZEME TÜRÜ]
              ,[MİKTAR]
              ,[BİRİM]
              ,[BİRİM BRÜT]
              ,[BİRİM İNDİRİM]
              ,[BİRİM NET]
              ,[BRÜT TOPLAM]
              ,[TOPLAM İNDİRİM]
              ,[KDV MATRAH]
              ,[KDV TUTARI]
              ,[KDV %]
              ,[NET TOPLAM]
              ,[CARİ KOD]
              ,[CARİ ÜNVAN]
              ,[FATURA NO]
              ,[TRCODE]
              ,[FATURA TÜRÜ]
              ,[AÇIKLAMA]
              ,[BELGE NO]
              ,[FATURAID]
              ,[DETAYID]
              ,[LOGICALREF]
        FROM [GO3].[dbo].[DETAY]
        WHERE [FATURAID] = ?
        ORDER BY [AÇIKLAMASI]
        """

        try:
            return self.execute_query(query, (invoice_id,))
        except Exception as e:
            logger.error(f"get_fatura_detaylari error: {e}")
            return []

    def get_satislar_istatistikleri(self, plasiyer):
        """Satışlar için istatistik verilerini FATURA tablosundan getirir"""
        query = """
        SELECT
            COUNT(*) as toplam_fatura,
            SUM([TUTAR]) as toplam_tutar,
            COUNT(DISTINCT [CARİ KOD]) as benzersiz_cari,
            AVG([TUTAR]) as ortalama_fatura
        FROM [GO3].[dbo].[FATURA]
        WHERE [PLASİYER] = ?
        """

        try:
            results = self.execute_query(query, (plasiyer,))
            return results[0] if results else {}
        except Exception as e:
            logger.error(f"get_satislar_istatistikleri error: {e}")
            return {}

    def get_fatura_stats(self, plasiyer=None):
        """Satışlar için istatistik verilerini getirir - diğer sayfalarla tutarlı tarih hesaplamaları"""
        try:
            # Tüm istatistikleri tek sorguda al - diğer sayfalarla tutarlı
            query = """
            SELECT
                -- Günlük (bugün)
                COUNT(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE) THEN 1 END) as gunluk_adet,
                ISNULL(SUM(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE) THEN CAST([TUTAR] as DECIMAL(15,2)) END), 0) as gunluk_tutar,
                -- Haftalık (bu hafta - Pazartesi'den itibaren)
                COUNT(CASE WHEN CAST([TARİH] AS DATE) >= DATEADD(day, -(DATEPART(WEEKDAY, GETDATE()) - 2), CAST(GETDATE() AS DATE)) THEN 1 END) as haftalik_adet,
                ISNULL(SUM(CASE WHEN CAST([TARİH] AS DATE) >= DATEADD(day, -(DATEPART(WEEKDAY, GETDATE()) - 2), CAST(GETDATE() AS DATE)) THEN CAST([TUTAR] as DECIMAL(15,2)) END), 0) as haftalik_tutar,
                -- Aylık (bu ay - ayın 1'den itibaren)
                COUNT(CASE WHEN CAST([TARİH] AS DATE) >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1) THEN 1 END) as aylik_adet,
                ISNULL(SUM(CASE WHEN CAST([TARİH] AS DATE) >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1) THEN CAST([TUTAR] as DECIMAL(15,2)) END), 0) as aylik_tutar
            FROM [GO3].[dbo].[FATURA]
            WHERE (TRCODE=8 OR TRCODE=7)
            """

            if plasiyer:
                query += f" AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))"
                params = (plasiyer,)
            else:
                params = ()

            result = self.execute_query(query, params)

            if result and len(result) > 0:
                data = result[0]
                return {
                    'gunluk_adet': data.get('gunluk_adet', 0),
                    'gunluk_tutar': float(data.get('gunluk_tutar', 0)),
                    'haftalik_adet': data.get('haftalik_adet', 0),
                    'haftalik_tutar': float(data.get('haftalik_tutar', 0)),
                    'aylik_adet': data.get('aylik_adet', 0),
                    'aylik_tutar': float(data.get('aylik_tutar', 0))
                }
            else:
                return {
                    'gunluk_adet': 0, 'gunluk_tutar': 0.0,
                    'haftalik_adet': 0, 'haftalik_tutar': 0.0,
                    'aylik_adet': 0, 'aylik_tutar': 0.0
                }

        except Exception as e:
            logger.error(f"get_fatura_stats error: {e}")
            return {
                'gunluk_adet': 0, 'gunluk_tutar': 0.0,
                'haftalik_adet': 0, 'haftalik_tutar': 0.0,
                'aylik_adet': 0, 'aylik_tutar': 0.0
            }

    def get_fatura_list(self, limit=100):
        """Toptan satış faturalarının detaylı listesini getirir"""
        query = """
        SELECT TOP (?)
            [ID],
            [TARİH],
            [CARİ KOD],
            [CARİ ÜNVAN],
            [NET TOPLAM],
            [CARİ PLASİYER],
            [BÖLGE],
            [ÇIKIŞ ŞUBE],
            [FATURA TÜRÜ],  
            [FATURA NO]
        FROM [GO3].[dbo].[FATURALAR] 
        WHERE [FATURA TÜRÜ] = 'Toptan satış faturası'
        ORDER BY [TARİH] DESC
        """

        try:
            results = self.execute_query(query, (limit,))

            # Veriyi template için uygun formata çevir
            fatura_listesi = []
            for row in results:
                fatura_listesi.append({
                    'ID': row.get('ID'),
                    'Tarih': row.get('TARİH'),
                    'CariKod': row.get('CARİ KOD'),
                    'CariUnvan': row.get('CARİ ÜNVAN'),
                    'Tutar': float(row.get('NET TOPLAM', 0)),
                    'Plasiyer': row.get('CARİ PLASİYER'),
                    'Bolge': row.get('BÖLGE'),
                    'CikisSube': row.get('ÇIKIŞ ŞUBE'),
                    'FaturaTuru': row.get('FATURA TÜRÜ'),
                    'EvrakNo': row.get('FATURA NO'),
                })

            return fatura_listesi

        except Exception as e:
            logger.error(f"get_fatura_list error: {e}")

    def get_fatura_list(self, limit=100):
        """Toptan satış faturalarının detaylı listesini getirir"""
        query = """
        SELECT TOP (?)
            [ID],
            [TARİH],
            [CARİ KOD],
            [CARİ ÜNVAN],
            [NET TOPLAM],
            [CARİ PLASİYER],
            [BÖLGE],
            [ÇIKIŞ ŞUBE],
            [FATURA TÜRÜ],  
            [FATURA NO]
        FROM [GO3].[dbo].[FATURALAR] 
        WHERE [FATURA TÜRÜ] = 'Toptan satış faturası'
        ORDER BY [TARİH] DESC
        """

        try:
            results = self.execute_query(query, (limit,))

            # Veriyi template için uygun formata çevir
            fatura_listesi = []
            for row in results:
                fatura_listesi.append({
                    'ID': row.get('ID'),
                    'Tarih': row.get('TARİH'),
                    'CariKod': row.get('CARİ KOD'),
                    'CariUnvan': row.get('CARİ ÜNVAN'),
                    'Tutar': float(row.get('NET TOPLAM', 0)),
                    'Plasiyer': row.get('CARİ PLASİYER'),
                    'Bölge': row.get('BÖLGE'),  # Template ile uyumlu
                    'CikisSube': row.get('ÇIKIŞ ŞUBE'),
                    'SatışTuru': row.get('FATURA TÜRÜ'),  # Template ile uyumlu
                    'Durum': 'Onaylandı',  # Varsayılan durum
                    'EvrakNo': row.get('FATURA NO'),
                })

            return fatura_listesi

        except Exception as e:
            logger.error(f"get_fatura_list error: {e}")
            return []

    def get_satis_stats(self, plasiyer=None):
        """Satış istatistikleri (gün/hafta/ay) - parametreli, hafta= Pazartesi başlangıçlı."""
        zeros = {
            'gunluk_tutar': 0, 'gunluk_adet': 0,
            'haftalik_tutar': 0, 'haftalik_adet': 0,
            'aylik_tutar': 0, 'aylik_adet': 0,
        }
        if not plasiyer:
            return zeros
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            stats_query = f"""
            DECLARE @today DATE = CAST(GETDATE() AS DATE);
            DECLARE @week_start DATE = DATEADD(day, -((DATEPART(WEEKDAY, @today) + @@DATEFIRST - 2) % 7), @today);
            DECLARE @month_start DATE = DATEFROMPARTS(YEAR(@today), MONTH(@today), 1);

            SELECT
                (SELECT ISNULL(SUM([TUTAR]), 0)
                 FROM [GO3].[dbo].[FATURA]
                 WHERE (TRCODE=8 OR TRCODE=7)
                 AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
                 AND CAST([TARİH] AS DATE) = @today) AS gunluk_tutar,
                (SELECT COUNT(*)
                 FROM [GO3].[dbo].[FATURA]
                 WHERE (TRCODE=8 OR TRCODE=7)
                 AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
                 AND CAST([TARİH] AS DATE) = @today) AS gunluk_adet,
                (SELECT ISNULL(SUM([TUTAR]), 0)
                 FROM [GO3].[dbo].[FATURA]
                 WHERE (TRCODE=8 OR TRCODE=7)
                 AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
                 AND CAST([TARİH] AS DATE) >= @week_start) AS haftalik_tutar,
                (SELECT COUNT(*)
                 FROM [GO3].[dbo].[FATURA]
                 WHERE (TRCODE=8 OR TRCODE=7)
                 AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
                 AND CAST([TARİH] AS DATE) >= @week_start) AS haftalik_adet,
                (SELECT ISNULL(SUM([TUTAR]), 0)
                 FROM [GO3].[dbo].[FATURA]
                 WHERE (TRCODE=8 OR TRCODE=7)
                 AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
                 AND CAST([TARİH] AS DATE) >= @month_start) AS aylik_tutar,
                (SELECT COUNT(*)
                 FROM [GO3].[dbo].[FATURA]
                 WHERE (TRCODE=8 OR TRCODE=7)
                 AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
                 AND CAST([TARİH] AS DATE) >= @month_start) AS aylik_adet
            """
            params = [plasiyer] * 6
            cursor.execute(stats_query, params)
            row = cursor.fetchone()

            cursor.close()
            connection.close()

            if row is None:
                return zeros
            def safe_val(r, name):
                v = getattr(r, name, None)
                return v if v is not None else 0
            return {
                'gunluk_tutar': float(safe_val(row, 'gunluk_tutar')),
                'gunluk_adet': int(safe_val(row, 'gunluk_adet')),
                'haftalik_tutar': float(safe_val(row, 'haftalik_tutar')),
                'haftalik_adet': int(safe_val(row, 'haftalik_adet')),
                'aylik_tutar': float(safe_val(row, 'aylik_tutar')),
                'aylik_adet': int(safe_val(row, 'aylik_adet')),
            }
        except Exception as e:
            logger.error(f"get_satis_stats error: {e}")
            return zeros

    def get_satis_stats_with_month_filter(self, plasiyer=None, selected_months=None):
        """Ay filtreli satış istatistikleri - FATURA tablosu, parametreli plasiyer filtresi, günlük=bugün."""
        zeros = {
            'gunluk_tutar': 0, 'gunluk_adet': 0,
            'haftalik_tutar': 0, 'haftalik_adet': 0,
            'aylik_tutar': 0, 'aylik_adet': 0,
        }
        if not plasiyer:
            return zeros

        if selected_months:
            month_list = ','.join([str(m) for m in selected_months])
            month_filter = f"AND MONTH([TARİH]) IN ({month_list})"
        else:
            # Ay filtreli fonksiyon; boş gelirse mevcut ayı baz al.
            month_filter = "AND MONTH([TARİH]) = MONTH(GETDATE())"

        # Parametreli sorgu: plasiyer 6 alt sorguda kullanılıyor (SQL enjeksiyonu önlemi)
        stats_query = f"""
        SELECT
            (SELECT ISNULL(SUM([TUTAR]), 0)
             FROM [GO3].[dbo].[FATURA]
             WHERE (TRCODE=8 OR TRCODE=7)
             AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
             AND CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE)) AS gunluk_tutar,
            (SELECT COUNT(*)
             FROM [GO3].[dbo].[FATURA]
             WHERE (TRCODE=8 OR TRCODE=7)
             AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
             AND CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE)) AS gunluk_adet,
            (SELECT ISNULL(SUM([TUTAR]), 0)
             FROM [GO3].[dbo].[FATURA]
             WHERE (TRCODE=8 OR TRCODE=7)
             AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
             AND CAST([TARİH] AS DATE) >= DATEADD(day, -((DATEPART(WEEKDAY, CAST(GETDATE() AS DATE)) + @@DATEFIRST - 2) % 7), CAST(GETDATE() AS DATE))
             AND YEAR([TARİH]) = YEAR(GETDATE()) {month_filter}) AS haftalik_tutar,
            (SELECT COUNT(*)
             FROM [GO3].[dbo].[FATURA]
             WHERE (TRCODE=8 OR TRCODE=7)
             AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
             AND CAST([TARİH] AS DATE) >= DATEADD(day, -((DATEPART(WEEKDAY, CAST(GETDATE() AS DATE)) + @@DATEFIRST - 2) % 7), CAST(GETDATE() AS DATE))
             AND YEAR([TARİH]) = YEAR(GETDATE()) {month_filter}) AS haftalik_adet,
            (SELECT ISNULL(SUM([TUTAR]), 0)
             FROM [GO3].[dbo].[FATURA]
             WHERE (TRCODE=8 OR TRCODE=7)
             AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
             AND YEAR([TARİH]) = YEAR(GETDATE()) {month_filter}) AS aylik_tutar,
            (SELECT COUNT(*)
             FROM [GO3].[dbo].[FATURA]
             WHERE (TRCODE=8 OR TRCODE=7)
             AND UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))
             AND YEAR([TARİH]) = YEAR(GETDATE()) {month_filter}) AS aylik_adet
        """
        params = [plasiyer] * 6

        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            cursor.execute(stats_query, params)
            row = cursor.fetchone()
            cursor.close()
            connection.close()

            if row is None:
                return zeros
            def safe_val(r, name):
                v = getattr(r, name, None)
                return v if v is not None else 0
            return {
                'gunluk_tutar': float(safe_val(row, 'gunluk_tutar')),
                'gunluk_adet': int(safe_val(row, 'gunluk_adet')),
                'haftalik_tutar': float(safe_val(row, 'haftalik_tutar')),
                'haftalik_adet': int(safe_val(row, 'haftalik_adet')),
                'aylik_tutar': float(safe_val(row, 'aylik_tutar')),
                'aylik_adet': int(safe_val(row, 'aylik_adet')),
            }
        except Exception as e:
            logger.error(f"get_satis_stats_with_month_filter error: {e}")
            return zeros

    def get_all_satis_list(self, limit=100):
        """Tüm plasiyerlerin satış listesini döndürür"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Tüm plasiyer listesi
            plasiyer_list = ['ALİ', 'ATAKAN', 'AZİZ', 'EYÜP',
                             'SÜLEYMAN', 'YİĞİT', 'HALİL']
            plasiyer_filter = "', '".join(plasiyer_list)

            query = f"""
            SELECT TOP {limit}
                [ID],
                [TARİH],
                [CARİ KOD],
                [CARİ ÜNVAN],
                [NET TOPLAM],
                [CARİ PLASİYER],
                [SATAN KİŞİ],
                [BÖLGE],
                [ÇIKIŞ ŞUBE],
                [FATURA TÜRÜ],
                [FATURA NO]
            FROM [GO3].[dbo].[FATURALAR]
            WHERE [FATURA TÜRÜ] = 'Toptan satış faturası'
            AND [CARİ PLASİYER] IN ('{plasiyer_filter}')
            ORDER BY [TARİH] DESC
            """

            logger.debug(f"Satış listesi sorgusu: {query}")
            cursor.execute(query)

            results = []
            for row in cursor.fetchall():
                # Sütun isimlerini alın
                columns = [column[0] for column in cursor.description]
                row_dict = dict(zip(columns, row))
                results.append(row_dict)

            cursor.close()
            connection.close()

            # Template için uygun formata dönüştür
            satis_listesi = []
            for row in results:
                satis_listesi.append({
                    'ID': row.get('ID'),
                    'Tarih': row.get('TARİH'),
                    'CariKod': self.safe_decode_string(row.get('CARİ KOD')),
                    'CariUnvan': self.safe_decode_string(row.get('CARİ ÜNVAN')),
                    'Tutar': float(row.get('NET TOPLAM', 0)),
                    'Plasiyer': self.safe_decode_string(row.get('CARİ PLASİYER')),
                    'SatanKisi': self.safe_decode_string(row.get('SATAN KİŞİ')),
                    'Bölge': self.safe_decode_string(row.get('BÖLGE')),
                    'CikisSube': self.safe_decode_string(row.get('ÇIKIŞ ŞUBE')),
                    'SatışTuru': self.safe_decode_string(row.get('FATURA TÜRÜ')),
                    'Durum': 'Onaylandı',  # Varsayılan durum
                    'EvrakNo': self.safe_decode_string(row.get('FATURA NO')),
                })

            return satis_listesi

        except Exception as e:
            logger.error(f"get_all_satis_list error: {e}")
            return []

    def get_satis_listesi_paginated(self, page=1, page_size=100, plasiyer=None, baslangic_tarihi=None, bitis_tarihi=None, cari_kod=None, cari_unvan=None):
        """Sayfalama destekli satış listesi - FATURA tablosu"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Dinamik koşullar
            where_conditions = ["(TRCODE=8 OR TRCODE=7)"]
            if plasiyer:
                where_conditions.append(
                    "(([PLASİYER] = ?) OR ([PLASİYER KOD] = ?))")
            if baslangic_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) >= ?")
            if bitis_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) <= ?")
            if cari_kod:
                where_conditions.append("[CARİ KOD] LIKE ?")
            if cari_unvan:
                where_conditions.append("[CARİ ÜNVAN] LIKE ?")

            # Önce toplam kayıt sayısını al
            count_query = f"""
            SELECT COUNT(*)
            FROM [GO3].[dbo].[FATURA] 
            WHERE {' AND '.join(where_conditions)}
            """

            params = []
            if plasiyer:
                params.extend([plasiyer, plasiyer])
            if baslangic_tarihi:
                params.append(baslangic_tarihi)
            if bitis_tarihi:
                params.append(bitis_tarihi)
            if cari_kod:
                params.append(f"%{cari_kod}%")
            if cari_unvan:
                params.append(f"%{cari_unvan}%")

            cursor.execute(count_query, params)
            total_count = cursor.fetchone()[0]

            # Sayfalanmış veriyi al
            if page_size is None:
                # Tüm kayıtları al (sayfalama yok)
                query = f"""
                SELECT [CARİ KOD],
                       [CARİ ÜNVAN],
                       [FATURA NO],
                       [TARİH],
                       [TUTAR],
                       [FATURAID],
                       [TRCODE],
                       [FATURA TÜRÜ],
                       [AÇIKLAMA],
                       [BELGE NO],
                       [İPTAL DURUMU],
                       [PLASİYER],
                       [BÖLGE],
                       [RESMİYET],
                       [PLASİYER KOD]
                FROM [GO3].[dbo].[FATURA] 
                WHERE {' AND '.join(where_conditions)}
                ORDER BY [TARİH] DESC, [FATURAID] DESC
                """
            else:
                # Normal sayfalama
                offset = (page - 1) * page_size
                query = f"""
                SELECT [CARİ KOD],
                       [CARİ ÜNVAN],
                       [FATURA NO],
                       [TARİH],
                       [TUTAR],
                       [FATURAID],
                       [TRCODE],
                       [FATURA TÜRÜ],
                       [AÇIKLAMA],
                       [BELGE NO],
                       [İPTAL DURUMU],
                       [PLASİYER],
                       [BÖLGE],
                       [RESMİYET],
                       [PLASİYER KOD]
                FROM [GO3].[dbo].[FATURA] 
                WHERE {' AND '.join(where_conditions)}
                ORDER BY [TARİH] DESC, [FATURAID] DESC
                OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY
                """

            logger.debug(f"Sayfalanmış satış listesi sorgusu: {query}")
            logger.debug(f"Query params: {params}")
            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                columns = [column[0] for column in cursor.description]
                row_dict = dict(zip(columns, row))
                results.append(row_dict)

            cursor.close()
            connection.close()

            # Template için uygun formata dönüştür
            satis_listesi = []
            for row in results:
                # Tarih formatını düzelt
                tarih_obj = row.get('TARİH')
                if tarih_obj:
                    if hasattr(tarih_obj, 'strftime'):
                        formatted_date = tarih_obj.strftime('%d.%m.%Y')
                    else:
                        # YYYY-MM-DD formatından
                        formatted_date = str(tarih_obj)[:10]
                else:
                    formatted_date = ''

                satis_listesi.append({
                    'ID': row.get('FATURAID'),
                    'TARİH': tarih_obj,  # Original datetime object
                    'FormattedDate': formatted_date,  # Formatted string
                    'CARİ_KOD': self.safe_decode_string(row.get('CARİ KOD')),
                    'CARİ_ÜNVAN': self.safe_decode_string(row.get('CARİ ÜNVAN')),
                    'NET_TOPLAM': float(row.get('TUTAR', 0)),
                    'CARİ_PLASİYER': self.safe_decode_string(row.get('PLASİYER')),
                    'SATAN_KİŞİ': self.safe_decode_string(row.get('PLASİYER')),
                    'BÖLGE': self.safe_decode_string(row.get('BÖLGE')),
                    'ÇIKIŞ_ŞUBE': self.safe_decode_string(row.get('BÖLGE')),
                    'FATURA_TÜRÜ': self.safe_decode_string(row.get('FATURA TÜRÜ')),
                    'FATURA_NO': self.safe_decode_string(row.get('FATURA NO')),
                    'TRCODE': row.get('TRCODE'),
                    'AÇIKLAMA': self.safe_decode_string(row.get('AÇIKLAMA')),
                    'BELGE_NO': self.safe_decode_string(row.get('BELGE NO')),
                    'İPTAL_DURUMU': self.safe_decode_string(row.get('İPTAL DURUMU')),
                    'RESMİYET': self.safe_decode_string(row.get('RESMİYET')),
                })

            # Sayfalama bilgilerini hesapla
            if page_size is None:
                # Tüm kayıtlar tek sayfada
                pagination_info = {
                    'total_count': total_count,
                    'total_pages': 1,
                    'current_page': 1,
                    'page_size': total_count,
                    'has_previous': False,
                    'has_next': False,
                    'previous_page': None,
                    'next_page': None,
                    'page_range': range(1, 2)
                }
            else:
                # Normal sayfalama
                total_pages = (total_count + page_size - 1) // page_size
                has_previous = page > 1
                has_next = page < total_pages

                pagination_info = {
                    'total_count': total_count,
                    'total_pages': total_pages,
                    'current_page': page,
                    'page_size': page_size,
                    'has_previous': has_previous,
                    'has_next': has_next,
                    'previous_page': page - 1 if has_previous else None,
                    'next_page': page + 1 if has_next else None,
                    'page_range': range(max(1, page - 2), min(total_pages + 1, page + 3))
                }

            return satis_listesi, pagination_info

        except Exception as e:
            logger.error(f"get_satis_listesi_paginated error: {e}")
            return [], {}

    def get_satis_listesi_all(self, baslangic_tarihi=None, bitis_tarihi=None, cari_kod=None, cari_unvan=None, plasiyer=None, bolge=None, e_belge_turu=None):
        """Tüm satış listesi - sayfalama olmadan - sadece dolu parametrelerle filtreleme"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Dinamik koşullar - sadece dolu parametreler için
            where_conditions = ["(TRCODE=8 OR TRCODE=7)"]
            params = []

            if baslangic_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) >= ?")
                params.append(baslangic_tarihi)
            if bitis_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) <= ?")
                params.append(bitis_tarihi)
            if cari_kod:
                where_conditions.append("[CARİ KOD] LIKE ?")
                params.append(f"%{cari_kod}%")
            if cari_unvan:
                where_conditions.append("[CARİ ÜNVAN] LIKE ?")
                params.append(f"%{cari_unvan}%")
            if plasiyer:
                where_conditions.append("[PLASİYER] = ?")
                params.append(plasiyer)
            if bolge:
                where_conditions.append("[BÖLGE] = ?")
                params.append(bolge)
            if e_belge_turu:
                where_conditions.append("[E-BELGE TÜRÜ] = ?")
                params.append(e_belge_turu)

            # Tüm kayıtları al
            query = f"""
            SELECT [CARİ KOD],
                   [CARİ ÜNVAN],
                   [FATURA NO],
                   [TARİH],
                   [TUTAR],
                   [FATURAID],
                   [TRCODE],
                   [FATURA TÜRÜ],
                   [AÇIKLAMA],
                   [BELGE NO],
                   [İPTAL DURUMU],
                   [PLASİYER],
                   [BÖLGE],
                   [RESMİYET],
                   [PLASİYER KOD],
                   [E-BELGE TÜRÜ]
            FROM [GO3].[dbo].[FATURA] 
            WHERE {' AND '.join(where_conditions)}
            ORDER BY [TARİH] DESC, [FATURAID] DESC
            """

            logger.debug(f"Tüm satış listesi sorgusu: {query}")
            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                columns = [column[0] for column in cursor.description]
                row_dict = dict(zip(columns, row))
                results.append(row_dict)

            cursor.close()
            connection.close()

            # Template için uygun formata dönüştür
            satis_listesi = []
            for row in results:
                # Tarih formatını düzelt
                tarih_obj = row.get('TARİH')
                if tarih_obj:
                    if hasattr(tarih_obj, 'strftime'):
                        formatted_date = tarih_obj.strftime('%d.%m.%Y')
                    else:
                        # YYYY-MM-DD formatından
                        formatted_date = str(tarih_obj)[:10]
                else:
                    formatted_date = ''

                satis_listesi.append({
                    'ID': row.get('FATURAID'),
                    'TARİH': tarih_obj,  # Original datetime object
                    'FormattedDate': formatted_date,  # Formatted string
                    'CARİ_KOD': self.safe_decode_string(row.get('CARİ KOD')),
                    'CARİ_ÜNVAN': self.safe_decode_string(row.get('CARİ ÜNVAN')),
                    'NET_TOPLAM': float(row.get('TUTAR', 0)),
                    'CARİ_PLASİYER': self.safe_decode_string(row.get('PLASİYER')),
                    'SATAN_KİŞİ': self.safe_decode_string(row.get('PLASİYER')),
                    'BÖLGE': self.safe_decode_string(row.get('BÖLGE')),
                    'ÇIKIŞ_ŞUBE': self.safe_decode_string(row.get('BÖLGE')),
                    'FATURA_TÜRÜ': self.safe_decode_string(row.get('FATURA TÜRÜ')),
                    'FATURA_NO': self.safe_decode_string(row.get('FATURA NO')),
                    'TRCODE': row.get('TRCODE'),
                    'AÇIKLAMA': self.safe_decode_string(row.get('AÇIKLAMA')),
                    'BELGE_NO': self.safe_decode_string(row.get('BELGE NO')),
                    'İPTAL_DURUMU': self.safe_decode_string(row.get('İPTAL DURUMU')),
                    'RESMİYET': self.safe_decode_string(row.get('RESMİYET')),
                    'E_BELGE_TÜRÜ': self.safe_decode_string(row.get('E-BELGE TÜRÜ')),
                })

            return satis_listesi

        except Exception as e:
            logger.error(f"get_satis_listesi_all error: {e}")
            return []

    def get_cari_genel_analiz(self, baslangic_tarihi=None, bitis_tarihi=None, plasiyer=None, bolge=None):
        """Cari bazlı satış ve tahsilat analizi - FATURA ve TAHSILAT_LOGO tablolarından"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # WHERE koşulları için parametreler
            fatura_where = ["(TRCODE=8 OR TRCODE=7)"]
            tahsilat_where = []
            fatura_params = []
            tahsilat_params = []

            # Tarih filtresi
            if baslangic_tarihi:
                fatura_where.append("CAST([TARİH] AS DATE) >= ?")
                fatura_params.append(baslangic_tarihi)
                tahsilat_where.append("CAST([TARİH] AS DATE) >= ?")
                tahsilat_params.append(baslangic_tarihi)

            if bitis_tarihi:
                fatura_where.append("CAST([TARİH] AS DATE) <= ?")
                fatura_params.append(bitis_tarihi)
                tahsilat_where.append("CAST([TARİH] AS DATE) <= ?")
                tahsilat_params.append(bitis_tarihi)

            # Plasiyer filtresi
            if plasiyer:
                fatura_where.append("[PLASİYER] = ?")
                fatura_params.append(plasiyer)
                tahsilat_where.append("[PLASİYER] = ?")
                tahsilat_params.append(plasiyer)

            # Bölge filtresi
            if bolge:
                fatura_where.append("[BÖLGE] = ?")
                fatura_params.append(bolge)
                tahsilat_where.append("[BÖLGE] = ?")
                tahsilat_params.append(bolge)

            # FATURA tablosundan satış verilerini al (cari bazında grupla)
            fatura_query = f"""
            SELECT 
                [CARİ KOD],
                [CARİ ÜNVAN],
                ISNULL([PLASİYER], '') as PLASİYER,
                ISNULL([BÖLGE], '') as BÖLGE,
                COUNT(*) as FATURA_SAYISI,
                SUM([TUTAR]) as TOPLAM_SATIS
            FROM [GO3].[dbo].[FATURA]
            WHERE {' AND '.join(fatura_where)}
            GROUP BY [CARİ KOD], [CARİ ÜNVAN], [PLASİYER], [BÖLGE]
            ORDER BY TOPLAM_SATIS DESC
            """

            cursor.execute(fatura_query, fatura_params)
            fatura_results = cursor.fetchall()

            # TAHSILAT_LOGO tablosundan tahsilat verilerini al (cari bazında grupla)
            if tahsilat_where:
                tahsilat_query = f"""
                SELECT 
                    [CARİ KOD],
                    [CARİ ÜNVAN],
                    ISNULL([PLASİYER], '') as PLASİYER,
                    ISNULL([BÖLGE], '') as BÖLGE,
                    COUNT(*) as TAHSILAT_SAYISI,
                    SUM([TUTAR]) as TOPLAM_TAHSILAT
                FROM [GO3].[dbo].[TAHSILAT_LOGO]
                WHERE {' AND '.join(tahsilat_where)}
                GROUP BY [CARİ KOD], [CARİ ÜNVAN], [PLASİYER], [BÖLGE]
                ORDER BY TOPLAM_TAHSILAT DESC
                """
            else:
                tahsilat_query = """
                SELECT 
                    [CARİ KOD],
                    [CARİ ÜNVAN],
                    ISNULL([PLASİYER], '') as PLASİYER,
                    ISNULL([BÖLGE], '') as BÖLGE,
                    COUNT(*) as TAHSILAT_SAYISI,
                    SUM([TUTAR]) as TOPLAM_TAHSILAT
                FROM [GO3].[dbo].[TAHSILAT_LOGO]
                GROUP BY [CARİ KOD], [CARİ ÜNVAN], [PLASİYER], [BÖLGE]
                ORDER BY TOPLAM_TAHSILAT DESC
                """

            cursor.execute(tahsilat_query, tahsilat_params)
            tahsilat_results = cursor.fetchall()

            # Verileri birleştir
            cari_dict = {}

            # FATURA verilerini ekle
            for row in fatura_results:
                cari_kod = self.safe_decode_string(row[0]) if row[0] else ''
                cari_unvan = self.safe_decode_string(row[1]) if row[1] else ''
                plasiyer_val = self.safe_decode_string(row[2]) if row[2] else ''
                bolge_val = self.safe_decode_string(row[3]) if row[3] else ''
                
                if cari_kod not in cari_dict:
                    cari_dict[cari_kod] = {
                        'cari_kod': cari_kod,
                        'cari_unvan': cari_unvan,
                        'plasiyer': plasiyer_val,
                        'bolge': bolge_val,
                        'fatura_sayisi': 0,
                        'toplam_satis': 0.0,
                        'tahsilat_sayisi': 0,
                        'toplam_tahsilat': 0.0
                    }
                
                cari_dict[cari_kod]['fatura_sayisi'] = int(row[4] or 0)
                cari_dict[cari_kod]['toplam_satis'] = float(row[5] or 0)

            # TAHSILAT verilerini ekle
            for row in tahsilat_results:
                cari_kod = self.safe_decode_string(row[0]) if row[0] else ''
                cari_unvan = self.safe_decode_string(row[1]) if row[1] else ''
                plasiyer_val = self.safe_decode_string(row[2]) if row[2] else ''
                bolge_val = self.safe_decode_string(row[3]) if row[3] else ''
                
                if cari_kod not in cari_dict:
                    cari_dict[cari_kod] = {
                        'cari_kod': cari_kod,
                        'cari_unvan': cari_unvan,
                        'plasiyer': plasiyer_val,
                        'bolge': bolge_val,
                        'fatura_sayisi': 0,
                        'toplam_satis': 0.0,
                        'tahsilat_sayisi': 0,
                        'toplam_tahsilat': 0.0
                    }
                
                cari_dict[cari_kod]['tahsilat_sayisi'] = int(row[4] or 0)
                cari_dict[cari_kod]['toplam_tahsilat'] = float(row[5] or 0)

            # Listeye dönüştür ve toplam satışa göre sırala
            result_list = list(cari_dict.values())
            result_list.sort(key=lambda x: x['toplam_satis'], reverse=True)

            cursor.close()
            connection.close()

            return result_list

        except Exception as e:
            logger.error(f"get_cari_genel_analiz error: {e}")
            return []

    def get_cari_aylik_satis(self, baslangic_tarihi=None, bitis_tarihi=None):
        """FATURA tablosundan cari bazında aylık satış toplamları (TRCODE=7,8)."""
        try:
            where_conditions = ["(TRCODE=8 OR TRCODE=7)"]
            params = []
            if baslangic_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) >= ?")
                params.append(baslangic_tarihi)
            if bitis_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) <= ?")
                params.append(bitis_tarihi)
            query = f"""
            SELECT
                LTRIM(RTRIM(ISNULL([CARİ KOD], ''))) as cari_kod,
                ISNULL([CARİ ÜNVAN], '') as cari_unvan,
                YEAR([TARİH]) as yil,
                MONTH([TARİH]) as ay,
                SUM(CAST(ISNULL([TUTAR], 0) AS DECIMAL(15,2))) as toplam_satis
            FROM [GO3].[dbo].[FATURA]
            WHERE {" AND ".join(where_conditions)}
            GROUP BY [CARİ KOD], [CARİ ÜNVAN], YEAR([TARİH]), MONTH([TARİH])
            ORDER BY cari_kod, yil, ay
            """
            results = self.execute_query(query, params)
            out = []
            for row in results:
                out.append({
                    'cari_kod': (row.get('cari_kod') or '').strip(),
                    'cari_unvan': self.safe_decode_string(row.get('cari_unvan')),
                    'yil': int(row.get('yil') or 0),
                    'ay': int(row.get('ay') or 0),
                    'toplam_satis': float(row.get('toplam_satis') or 0),
                })
            return out
        except Exception as e:
            logger.error(f"get_cari_aylik_satis error: {e}")
            return []

    def get_cari_aylik_tahsilat(self, baslangic_tarihi=None, bitis_tarihi=None):
        """GunlukTahsilat_V view'ından cari bazında aylık tahsilat toplamları."""
        try:
            where_conditions = ["1=1"]
            params = []
            if baslangic_tarihi:
                where_conditions.append("CAST([Tarih] AS DATE) >= ?")
                params.append(baslangic_tarihi)
            if bitis_tarihi:
                where_conditions.append("CAST([Tarih] AS DATE) <= ?")
                params.append(bitis_tarihi)
            query = f"""
            SELECT
                LTRIM(RTRIM(ISNULL([CariKod], ''))) as cari_kod,
                ISNULL([CariUnvan], '') as cari_unvan,
                YEAR([Tarih]) as yil,
                MONTH([Tarih]) as ay,
                SUM(CAST(ISNULL([Tutar], 0) AS DECIMAL(15,2))) as toplam_tahsilat
            FROM [GO3].[dbo].[GunlukTahsilat_V]
            WHERE {" AND ".join(where_conditions)}
            GROUP BY [CariKod], [CariUnvan], YEAR([Tarih]), MONTH([Tarih])
            ORDER BY cari_kod, yil, ay
            """
            results = self.execute_query(query, params)
            out = []
            for row in results:
                out.append({
                    'cari_kod': (row.get('cari_kod') or '').strip(),
                    'cari_unvan': self.safe_decode_string(row.get('cari_unvan')),
                    'yil': int(row.get('yil') or 0),
                    'ay': int(row.get('ay') or 0),
                    'toplam_tahsilat': float(row.get('toplam_tahsilat') or 0),
                })
            return out
        except Exception as e:
            logger.error(f"get_cari_aylik_tahsilat error: {e}")
            return []

    def get_cari_bakiye_hepsi(self):
        """CARIBAKIYE tablosundan tüm carilerin güncel bakiye bilgisi (CODE, DEFINITION_, BAKİYE, BORÇ, ALACAK, BOLGE)."""
        try:
            query = """
            SELECT
                LTRIM(RTRIM(ISNULL([CODE], ''))) as code,
                ISNULL([DEFINITION_], '') as definition_,
                CAST(ISNULL([BAKİYE], 0) AS DECIMAL(15,2)) as bakiye,
                CAST(ISNULL([BORÇ], 0) AS DECIMAL(15,2)) as borc,
                CAST(ISNULL([ALACAK], 0) AS DECIMAL(15,2)) as alacak,
                ISNULL([BOLGE], '') as bolge
            FROM [GO3].[dbo].[CARIBAKIYE]
            ORDER BY [CODE]
            """
            results = self.execute_query(query)
            out = []
            for row in results:
                out.append({
                    'code': (row.get('code') or '').strip(),
                    'definition_': self.safe_decode_string(row.get('definition_')),
                    'bakiye': float(row.get('bakiye') or 0),
                    'borc': float(row.get('borc') or 0),
                    'alacak': float(row.get('alacak') or 0),
                    'bolge': self.safe_decode_string(row.get('bolge')),
                })
            return out
        except Exception as e:
            logger.error(f"get_cari_bakiye_hepsi error: {e}")
            return []

    def get_satis_ozet_stats(self):
        """Satış özet istatistiklerini FATURA tablosundan tahsilat benzeri formatta döndürür"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Plasiyer listesi - FATURA tablosundaki PLASİYER kolonundan
            plasiyer_list = ['ALİ', 'ATAKAN', 'AZİZ', 'EYÜP',
                             'SÜLEYMAN', 'YİĞİT', 'CAN', 'RECEP', 'HALİL']
            plasiyer_where = "(" + \
                ",".join([f"'{p}'" for p in plasiyer_list]) + ")"

            # Toplam satış istatistikleri - günlük, haftalık, aylık (aynı mantıkla)
            stats_query = """
            SELECT 
                'gunluk' as donem,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE) THEN 1 END) as adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE) THEN [TUTAR] ELSE 0 END) as toplam_tutar
            FROM [GO3].[dbo].[FATURA] 
            WHERE (TRCODE=8 OR TRCODE=7)
            AND [TARİH] >= DATEADD(month, -1, GETDATE())
            
            UNION ALL
            
            SELECT 
                'haftalik' as donem,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) >= DATEADD(day, -(DATEPART(WEEKDAY, GETDATE()) - 2), CAST(GETDATE() AS DATE)) THEN 1 END) as adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) >= DATEADD(day, -(DATEPART(WEEKDAY, GETDATE()) - 2), CAST(GETDATE() AS DATE)) THEN [TUTAR] ELSE 0 END) as toplam_tutar
            FROM [GO3].[dbo].[FATURA] 
            WHERE (TRCODE=8 OR TRCODE=7)
            
            UNION ALL
            
            SELECT 
                'aylik' as donem,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1) THEN 1 END) as adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1) THEN [TUTAR] ELSE 0 END) as toplam_tutar
            FROM [GO3].[dbo].[FATURA] 
            WHERE (TRCODE=8 OR TRCODE=7)
            """

            cursor.execute(stats_query)
            stats_rows = cursor.fetchall()

            # İstatistikleri organize et
            toplam_stats = {
                'gunluk_tutar': 0,
                'gunluk_adet': 0,
                'haftalik_tutar': 0,
                'haftalik_adet': 0,
                'aylik_tutar': 0,
                'aylik_adet': 0,
            }

            for row in stats_rows:
                donem = row[0]
                adet = row[1] or 0
                tutar = float(row[2] or 0)

                if donem == 'gunluk':
                    toplam_stats['gunluk_tutar'] = tutar
                    toplam_stats['gunluk_adet'] = adet
                elif donem == 'haftalik':
                    toplam_stats['haftalik_tutar'] = tutar
                    toplam_stats['haftalik_adet'] = adet
                elif donem == 'aylik':
                    toplam_stats['aylik_tutar'] = tutar
                    toplam_stats['aylik_adet'] = adet

            # Plasiyer bazlı detaylı istatistikler
            plasiyer_query = f"""
            SELECT 
                {_FATURA_PLASIYER_CANON} as plasiyer,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE) THEN 1 END) as gunluk_adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE) THEN [TUTAR] ELSE 0 END) as gunluk_tutar,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) >= DATEADD(day, -(DATEPART(WEEKDAY, GETDATE()) - 2), CAST(GETDATE() AS DATE)) THEN 1 END) as haftalik_adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) >= DATEADD(day, -(DATEPART(WEEKDAY, GETDATE()) - 2), CAST(GETDATE() AS DATE)) THEN [TUTAR] ELSE 0 END) as haftalik_tutar,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1) THEN 1 END) as aylik_adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1) THEN [TUTAR] ELSE 0 END) as aylik_tutar
            FROM [GO3].[dbo].[FATURA] 
            WHERE (TRCODE=8 OR TRCODE=7)
            GROUP BY {_FATURA_PLASIYER_CANON}
            ORDER BY aylik_tutar DESC
            """

            cursor.execute(plasiyer_query)
            plasiyer_rows = cursor.fetchall()

            plasiyer_verileri = []
            for row in plasiyer_rows:
                plasiyer_data = {
                    'plasiyer': self.safe_decode_string(row[0]),
                    'satis': {
                        'gunluk_adet': row[1] or 0,
                        'gunluk_tutar': float(row[2] or 0),
                        'haftalik_adet': row[3] or 0,
                        'haftalik_tutar': float(row[4] or 0),
                        'aylik_adet': row[5] or 0,
                        'aylik_tutar': float(row[6] or 0),
                    }
                }
                plasiyer_verileri.append(plasiyer_data)

            connection.close()

            return {
                'toplam_stats': toplam_stats,
                'plasiyer_verileri': plasiyer_verileri
            }

        except Exception as e:
            logger.error(f"get_satis_ozet_stats error: {e}")
            return {
                'toplam_stats': {
                    'gunluk_tutar': 0, 'gunluk_adet': 0,
                    'haftalik_tutar': 0, 'haftalik_adet': 0,
                    'aylik_tutar': 0, 'aylik_adet': 0,
                },
                'plasiyer_verileri': []
            }

    def get_tahsilat_ozet_stats(self, selected_months=None):
        """Tahsilat özet istatistikleri — [GO3].[dbo].[TAHSILAT_LOGO] tüm kayıtlar."""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            month_nums = []
            if selected_months:
                for month in selected_months:
                    try:
                        month_nums.append(int(month))
                    except (TypeError, ValueError):
                        continue

            use_month_filter = len(month_nums) > 0

            if use_month_filter:
                month_in = ','.join(str(m) for m in month_nums)
                stats_query = f"""
                SELECT
                    'secilen_aylar' AS donem,
                    COUNT(*) AS adet,
                    ISNULL(SUM(CAST([TUTAR] AS DECIMAL(18,2))), 0) AS toplam_tutar
                FROM [GO3].[dbo].[TAHSILAT_LOGO]
                WHERE YEAR([TARİH]) = YEAR(GETDATE())
                  AND MONTH([TARİH]) IN ({month_in})
                """
                logger.info(
                    'Tahsilat özet ay filtresi: YEAR=GETDATE, MONTH IN (%s)', month_in)
            else:
                # Hafta başlangıcı — genel dashboard ile aynı @@DATEFIRST mantığı
                stats_query = """
                DECLARE @today DATE = CAST(GETDATE() AS DATE);
                DECLARE @week_start DATE = DATEADD(day, -((DATEPART(WEEKDAY, @today) + @@DATEFIRST - 2) % 7), @today);

                SELECT 'gunluk' AS donem,
                    SUM(CASE WHEN CAST([TARİH] AS DATE) = @today THEN 1 ELSE 0 END) AS adet,
                    ISNULL(SUM(CASE WHEN CAST([TARİH] AS DATE) = @today THEN CAST([TUTAR] AS DECIMAL(18,2)) ELSE 0 END), 0) AS toplam_tutar
                FROM [GO3].[dbo].[TAHSILAT_LOGO]
                UNION ALL
                SELECT 'haftalik',
                    SUM(CASE WHEN CAST([TARİH] AS DATE) >= @week_start AND YEAR([TARİH]) = YEAR(@today) THEN 1 ELSE 0 END),
                    ISNULL(SUM(CASE WHEN CAST([TARİH] AS DATE) >= @week_start AND YEAR([TARİH]) = YEAR(@today) THEN CAST([TUTAR] AS DECIMAL(18,2)) ELSE 0 END), 0)
                FROM [GO3].[dbo].[TAHSILAT_LOGO]
                UNION ALL
                SELECT 'aylik',
                    SUM(CASE WHEN MONTH([TARİH]) = MONTH(@today) AND YEAR([TARİH]) = YEAR(@today) THEN 1 ELSE 0 END),
                    ISNULL(SUM(CASE WHEN MONTH([TARİH]) = MONTH(@today) AND YEAR([TARİH]) = YEAR(@today) THEN CAST([TUTAR] AS DECIMAL(18,2)) ELSE 0 END), 0)
                FROM [GO3].[dbo].[TAHSILAT_LOGO]
                """

            cursor.execute(stats_query)
            stats_rows = cursor.fetchall()

            # İstatistikleri organize et
            toplam_stats = {
                'gunluk_tutar': 0,
                'gunluk_adet': 0,
                'haftalik_tutar': 0,
                'haftalik_adet': 0,
                'aylik_tutar': 0,
                'aylik_adet': 0,
            }

            for row in stats_rows:
                donem = row[0]
                adet = int(row[1] or 0)
                tutar = float(row[2] or 0)

                if donem == 'gunluk':
                    toplam_stats['gunluk_tutar'] = tutar
                    toplam_stats['gunluk_adet'] = adet
                elif donem == 'haftalik':
                    toplam_stats['haftalik_tutar'] = tutar
                    toplam_stats['haftalik_adet'] = adet
                elif donem == 'aylik':
                    toplam_stats['aylik_tutar'] = tutar
                    toplam_stats['aylik_adet'] = adet
                elif donem == 'secilen_aylar':
                    toplam_stats['aylik_tutar'] = tutar
                    toplam_stats['aylik_adet'] = adet
                    toplam_stats['gunluk_tutar'] = 0
                    toplam_stats['gunluk_adet'] = 0
                    toplam_stats['haftalik_tutar'] = 0
                    toplam_stats['haftalik_adet'] = 0

            pla_key = """
                CASE
                    WHEN [PLASİYER] IS NULL OR LTRIM(RTRIM([PLASİYER])) = N'' THEN N'(Belirtilmemiş)'
                    ELSE LTRIM(RTRIM([PLASİYER]))
                END
            """

            if use_month_filter:
                month_in = ','.join(str(m) for m in month_nums)
                plasiyer_query = f"""
                SELECT
                    {pla_key.strip()} AS plasiyer_key,
                    0 AS gunluk_adet,
                    CAST(0 AS DECIMAL(18,2)) AS gunluk_tutar,
                    0 AS haftalik_adet,
                    CAST(0 AS DECIMAL(18,2)) AS haftalik_tutar,
                    COUNT(*) AS aylik_adet,
                    ISNULL(SUM(CAST([TUTAR] AS DECIMAL(18,2))), 0) AS aylik_tutar
                FROM [GO3].[dbo].[TAHSILAT_LOGO]
                WHERE YEAR([TARİH]) = YEAR(GETDATE())
                  AND MONTH([TARİH]) IN ({month_in})
                GROUP BY {pla_key.strip()}
                ORDER BY aylik_tutar DESC
                """
            else:
                plasiyer_query = f"""
                DECLARE @today DATE = CAST(GETDATE() AS DATE);
                DECLARE @week_start DATE = DATEADD(day, -((DATEPART(WEEKDAY, @today) + @@DATEFIRST - 2) % 7), @today);

                SELECT
                    {pla_key.strip()} AS plasiyer_key,
                    SUM(CASE WHEN CAST([TARİH] AS DATE) = @today THEN 1 ELSE 0 END) AS gunluk_adet,
                    ISNULL(SUM(CASE WHEN CAST([TARİH] AS DATE) = @today THEN CAST([TUTAR] AS DECIMAL(18,2)) ELSE 0 END), 0) AS gunluk_tutar,
                    SUM(CASE WHEN CAST([TARİH] AS DATE) >= @week_start AND YEAR([TARİH]) = YEAR(@today) THEN 1 ELSE 0 END) AS haftalik_adet,
                    ISNULL(SUM(CASE WHEN CAST([TARİH] AS DATE) >= @week_start AND YEAR([TARİH]) = YEAR(@today) THEN CAST([TUTAR] AS DECIMAL(18,2)) ELSE 0 END), 0) AS haftalik_tutar,
                    SUM(CASE WHEN MONTH([TARİH]) = MONTH(@today) AND YEAR([TARİH]) = YEAR(@today) THEN 1 ELSE 0 END) AS aylik_adet,
                    ISNULL(SUM(CASE WHEN MONTH([TARİH]) = MONTH(@today) AND YEAR([TARİH]) = YEAR(@today) THEN CAST([TUTAR] AS DECIMAL(18,2)) ELSE 0 END), 0) AS aylik_tutar
                FROM [GO3].[dbo].[TAHSILAT_LOGO]
                GROUP BY {pla_key.strip()}
                ORDER BY aylik_tutar DESC
                """

            cursor.execute(plasiyer_query)
            plasiyer_rows = cursor.fetchall()

            plasiyer_verileri = []
            for row in plasiyer_rows:
                plasiyer_data = {
                    'plasiyer': self.safe_decode_string(row[0]),
                    'tahsilat': {
                        'gunluk_adet': row[1] or 0,
                        'gunluk_tutar': float(row[2] or 0),
                        'haftalik_adet': row[3] or 0,
                        'haftalik_tutar': float(row[4] or 0),
                        'aylik_adet': row[5] or 0,
                        'aylik_tutar': float(row[6] or 0),
                    }
                }
                plasiyer_verileri.append(plasiyer_data)

            connection.close()

            return {
                'toplam_stats': toplam_stats,
                'plasiyer_verileri': plasiyer_verileri
            }

        except Exception as e:
            logger.error(f"get_tahsilat_ozet_stats error: {e}")
            return {
                'toplam_stats': {
                    'gunluk_tutar': 0, 'gunluk_adet': 0,
                    'haftalik_tutar': 0, 'haftalik_adet': 0,
                    'aylik_tutar': 0, 'aylik_adet': 0,
                },
                'plasiyer_verileri': []
            }

    def get_all_unique_bankalar(self):
        """TAHSILAT_LOGO tablosundan benzersiz banka isimlerini getirir"""
        query = """
        SELECT DISTINCT [BANKA]
        FROM [GO3].[dbo].[TAHSILAT_LOGO]
        WHERE [BANKA] IS NOT NULL AND [BANKA] != ''
        ORDER BY [BANKA]
        """
        try:
            results = self.execute_query(query)
            return [row['BANKA'] for row in results] if results else []
        except Exception as e:
            logger.error(f"get_all_unique_bankalar error: {e}")
            return []

    def get_all_plasiyer_list(self):
        """Belirlenen plasiyerlerin listesini getirir (admin için)"""
        query = """
        SELECT DISTINCT [Plasiyer]
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        WHERE [Plasiyer] IS NOT NULL AND [Plasiyer] != ''
        AND UPPER([Plasiyer]) IN ('ALİ', 'ATAKAN', 'AZİZ', 'CAN', 'EYÜP', 'MERT', 'MURAT', 'BAKIR', 'HALİL', 'OĞUZ', 'SÜLEYMAN', 'TURAN', 'YİĞİT')
        ORDER BY [Plasiyer]
        """

        try:
            results = self.execute_query(query)
            return [row['Plasiyer'] for row in results] if results else []
        except Exception as e:
            logger.error(f"get_all_plasiyer_list error: {e}")
            return []

    def get_plasiyer_list_from_fatura(self):
        """FATURA satış evraklarında (TRCODE 7/8) plasiyer: isim, yoksa kod, yoksa (Belirtilmemiş)."""
        query = f"""
        SELECT DISTINCT {_FATURA_PLASIYER_CANON} AS plv
        FROM [GO3].[dbo].[FATURA]
        WHERE (TRCODE=8 OR TRCODE=7)
        ORDER BY plv
        """

        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            
            plasiyerler = []
            for row in rows:
                plasiyer = self.safe_decode_string(row[0])
                if plasiyer and plasiyer.strip():
                    plasiyerler.append(plasiyer.strip())
            
            cursor.close()
            connection.close()
            
            return plasiyerler
        except Exception as e:
            logger.error(f"get_plasiyer_list_from_fatura error: {e}")
            return []

    def get_hakedis_hedef_filter_options(self):
        """DETAY tablosundan hakediş hedef ekranı için marka ve tür seçeneklerini getirir."""
        query = """
        SELECT DISTINCT
            LTRIM(RTRIM([MARKA])) AS marka,
            NULLIF(LTRIM(RTRIM(COALESCE([MALZEME TÜRÜ], ''))), '') AS malzeme_turu
        FROM [GO3].[dbo].[DETAY]
        WHERE TRCODE IN (7, 8)
          AND [MARKA] IS NOT NULL
          AND LTRIM(RTRIM([MARKA])) <> ''
        ORDER BY marka, malzeme_turu
        """

        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

            marka_tur_map = {}
            for row in rows:
                marka = self.safe_decode_string(row[0]).strip()
                tur = self.safe_decode_string(row[1]).strip() if row[1] else ''
                if not marka:
                    continue
                marka_tur_map.setdefault(marka, set())
                if tur:
                    marka_tur_map[marka].add(tur)

            return {
                'markalar': sorted(marka_tur_map.keys()),
                'marka_turleri': {
                    marka: sorted(list(turler))
                    for marka, turler in marka_tur_map.items()
                }
            }
        except Exception as e:
            logger.error(f"get_hakedis_hedef_filter_options error: {e}")
            return {
                'markalar': [],
                'marka_turleri': {},
            }
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def get_aylik_hakedis_satis_toplamlari(self, yil, ay):
        """DETAY tablosundan aylık plasiyer + marka + tür bazlı NET TOPLAM aggregate verisi döndürür."""
        query = """
        SELECT
            UPPER(LTRIM(RTRIM(COALESCE([PLASİYER], '')))) AS plasiyer,
            LTRIM(RTRIM([MARKA])) AS marka,
            NULLIF(LTRIM(RTRIM(COALESCE([MALZEME TÜRÜ], ''))), '') AS malzeme_turu,
            SUM(CAST([NET TOPLAM] AS DECIMAL(18, 2))) AS net_toplam
        FROM [GO3].[dbo].[DETAY]
        WHERE TRCODE IN (7, 8)
          AND YEAR([TARİH]) = ?
          AND MONTH([TARİH]) = ?
          AND [MARKA] IS NOT NULL
          AND LTRIM(RTRIM([MARKA])) <> ''
        GROUP BY
            UPPER(LTRIM(RTRIM(COALESCE([PLASİYER], '')))),
            LTRIM(RTRIM([MARKA])),
            NULLIF(LTRIM(RTRIM(COALESCE([MALZEME TÜRÜ], ''))), '')
        ORDER BY plasiyer, marka, malzeme_turu
        """

        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            cursor.execute(query, [yil, ay])
            rows = cursor.fetchall()

            results = []
            for row in rows:
                results.append({
                    'plasiyer': self.safe_decode_string(row[0]).strip(),
                    'marka': self.safe_decode_string(row[1]).strip(),
                    'malzeme_turu': self.safe_decode_string(row[2]).strip() if row[2] else '',
                    'net_toplam': float(row[3]) if row[3] is not None else 0.0,
                })
            return results
        except Exception as e:
            logger.error(f"get_aylik_hakedis_satis_toplamlari error: {e}")
            return []
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def get_aylik_hakedis_hedef_toplamlari(self, yil, ay, hedef_filtreleri):
        """
        Hakediş hedefleri için DETAY tablosundan birleşik satış toplamı döndürür.
        Çoklu malzeme türü seçimlerinde SQL tarafında OR koşulu ile tek hedef toplamı hesaplanır.
        """
        if not hedef_filtreleri:
            return {}

        subqueries = []
        params = []

        for hedef in hedef_filtreleri:
            target_key = str(hedef.get('target_key') or '').strip()
            markalar = [
                self.safe_decode_string(marka).strip()
                for marka in (hedef.get('markalar') or [])
                if self.safe_decode_string(marka).strip()
            ]
            if not markalar:
                raw_marka = self.safe_decode_string(hedef.get('marka')).strip()
                if raw_marka:
                    markalar = [raw_marka]
            malzeme_turleri = [
                self.safe_decode_string(tur).strip()
                for tur in (hedef.get('malzeme_turleri') or [])
                if self.safe_decode_string(tur).strip()
            ]

            if not markalar or not target_key:
                continue

            marka_conditions = " OR ".join(
                ["LTRIM(RTRIM([MARKA])) = ?" for _ in markalar]
            )
            subquery = f"""
            SELECT
                UPPER(LTRIM(RTRIM(COALESCE([PLASİYER], '')))) AS plasiyer,
                ? AS target_key,
                CAST([NET TOPLAM] AS DECIMAL(18, 2)) AS net_toplam
            FROM [GO3].[dbo].[DETAY]
            WHERE TRCODE IN (7, 8)
              AND YEAR([TARİH]) = ?
              AND MONTH([TARİH]) = ?
              AND [MARKA] IS NOT NULL
              AND LTRIM(RTRIM([MARKA])) <> ''
              AND ({marka_conditions})
            """
            subquery_params = [target_key, yil, ay, *markalar]

            if malzeme_turleri:
                or_conditions = " OR ".join(
                    [
                        "NULLIF(LTRIM(RTRIM(COALESCE([MALZEME TÜRÜ], ''))), '') = ?"
                        for _ in malzeme_turleri
                    ]
                )
                subquery += f" AND ({or_conditions})"
                subquery_params.extend(malzeme_turleri)

            subqueries.append(subquery)
            params.extend(subquery_params)

        if not subqueries:
            return {}

        query = f"""
        SELECT
            plasiyer,
            target_key,
            SUM(net_toplam) AS net_toplam
        FROM (
            {' UNION ALL '.join(subqueries)}
        ) hedefler
        GROUP BY plasiyer, target_key
        """

        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()

            results = {}
            for row in rows:
                plasiyer = self.safe_decode_string(row[0]).strip().upper()
                target_key = self.safe_decode_string(row[1]).strip()
                results[(plasiyer, target_key)] = float(row[2]) if row[2] is not None else 0.0
            return results
        except Exception as e:
            logger.error(f"get_aylik_hakedis_hedef_toplamlari error: {e}")
            return {}
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def get_cari_bakiye_by_plasiyer(self, plasiyer, bolge=None, cari_tipi=None):
        """Belirli plasiyer için cari bakiye listesini getirir (bakiye büyükten küçüğe sıralı)"""
        query = """
        SELECT [CODE]
              ,[DEFINITION_]
              ,[SPECODE]
              ,[TARIHS]
              ,[SATISGECENSURE]
              ,[TARIHT]
              ,[TAHSILATGECENSURE]
              ,[BAKİYE]
              ,[BOLGE]
        FROM [GO3].[dbo].[CARIBAKIYE]
        WHERE [SPECODE] = ?
        """
        params = [plasiyer]

        if bolge and bolge != 'all':
            query += " AND [BOLGE] = ?"
            params.append(bolge)

        if cari_tipi:
            if cari_tipi == 'tedarikci':
                query += " AND [CODE] LIKE '320.%'"
            elif cari_tipi == 'musteri':
                query += " AND [CODE] LIKE '120.%'"

        query += " ORDER BY CAST([BAKİYE] as DECIMAL(15,2)) DESC"

        logger.info(f"get_cari_bakiye_by_plasiyer - Plasiyer: {plasiyer}, Bölge: {bolge}, Cari Tipi: {cari_tipi}")
        logger.info(f"get_cari_bakiye_by_plasiyer - Query: {query}")
        logger.info(f"get_cari_bakiye_by_plasiyer - Params: {params}")

        try:
            results = self.execute_query(query, params)
            logger.info(f"get_cari_bakiye_by_plasiyer - Sonuç sayısı: {len(results)}")
            if results:
                logger.info(f"get_cari_bakiye_by_plasiyer - İlk sonuç: {results[0]}")
            return results
        except Exception as e:
            logger.error(f"get_cari_bakiye_by_plasiyer error: {e}")
            return []

    def test_ali_plasiyer_data(self):
        """ALİ plasiyeri için test sorgusu"""
        query = """
        SELECT [CODE], [DEFINITION_], [SPECODE], [BOLGE], [BAKİYE]
        FROM [GO3].[dbo].[CARIBAKIYE]
        WHERE [SPECODE] = 'ALİ'
        ORDER BY [BOLGE], [BAKİYE] DESC
        """
        
        try:
            results = self.execute_query(query)
            logger.info(f"test_ali_plasiyer_data - ALİ plasiyeri için toplam kayıt: {len(results)}")
            if results:
                logger.info(f"test_ali_plasiyer_data - İlk 5 kayıt: {results[:5]}")
                # Bölgeleri listele
                bolgeler = set([row['BOLGE'] for row in results if row['BOLGE']])
                logger.info(f"test_ali_plasiyer_data - ALİ plasiyerinin bölgeleri: {sorted(bolgeler)}")
            return results
        except Exception as e:
            logger.error(f"test_ali_plasiyer_data error: {e}")
            return []
    
    def get_cari_bakiye_all_plasiyers(self, bolge=None, cari_tipi=None):
        """Tüm plasiyerler için cari bakiye listesini getirir (bakiye büyükten küçüğe sıralı)"""
        query = """
        SELECT [CODE]
              ,[DEFINITION_]
              ,[SPECODE]
              ,[TARIHS]
              ,[SATISGECENSURE]
              ,[TARIHT]
              ,[TAHSILATGECENSURE]
              ,[BAKİYE]
              ,[BOLGE]
        FROM [GO3].[dbo].[CARIBAKIYE]
        WHERE 1=1
        """
        params = []

        if bolge and bolge != 'all':
            query += " AND [BOLGE] = ?"
            params.append(bolge)

        if cari_tipi:
            if cari_tipi == 'tedarikci':
                query += " AND [CODE] LIKE '320.%'"
            elif cari_tipi == 'musteri':
                query += " AND [CODE] LIKE '120.%'"

        query += " ORDER BY CAST([BAKİYE] as DECIMAL(15,2)) DESC"

        logger.info(f"get_cari_bakiye_all_plasiyers - Bölge: {bolge}, Cari Tipi: {cari_tipi}")
        logger.info(f"get_cari_bakiye_all_plasiyers - Query: {query}")
        logger.info(f"get_cari_bakiye_all_plasiyers - Params: {params}")

        try:
            results = self.execute_query(query, params)
            logger.info(f"get_cari_bakiye_all_plasiyers - Sonuç sayısı: {len(results)}")
            return results
        except Exception as e:
            logger.error(f"get_cari_bakiye_all_plasiyers error: {e}")
            return []

    def get_all_plasiyer_bakiye_summary(self):
        """Belirlenen plasiyerlerin bakiye toplamlarını getirir (dashboard için)"""
        query = """
        SELECT 
            [SPECODE] as plasiyer,
            COUNT(*) as toplam_kayit,
            ISNULL(SUM(CAST([BAKİYE] as DECIMAL(15,2))), 0) as toplam_bakiye,
            ISNULL(SUM(CASE WHEN CAST([BAKİYE] as DECIMAL(15,2)) > 0 THEN CAST([BAKİYE] as DECIMAL(15,2)) ELSE 0 END), 0) as pozitif_bakiye,
            ISNULL(SUM(CASE WHEN CAST([BAKİYE] as DECIMAL(15,2)) < 0 THEN CAST([BAKİYE] as DECIMAL(15,2)) ELSE 0 END), 0) as negatif_bakiye,
            COUNT(CASE WHEN CAST([BAKİYE] as DECIMAL(15,2)) > 0 THEN 1 END) as pozitif_kayit_sayisi,
            COUNT(CASE WHEN CAST([BAKİYE] as DECIMAL(15,2)) < 0 THEN 1 END) as negatif_kayit_sayisi
        FROM [GO3].[dbo].[CARIBAKIYE]
        WHERE [SPECODE] IS NOT NULL AND [SPECODE] != '' 
            AND UPPER([SPECODE]) IN ('ALİ', 'ATAKAN', 'AZİZ', 'EYÜP', 'MERT', 'SÜLEYMAN', 'YİĞİT')
        GROUP BY [SPECODE]
        ORDER BY toplam_bakiye DESC
        """

        try:
            return self.execute_query(query)
        except Exception as e:
            logger.error(f"get_all_plasiyer_bakiye_summary error: {e}")
            return []

    def get_banka_id(self, banka_ref, tahsilat_turu):
        """Banka ID'sini döndürür. Kredi kartı ve Banka Havalesi için banka adından ID mapping yapılır."""
        try:
            # Eğer banka_ref zaten bir sayı ise (ID), direkt döndür
            if banka_ref and str(banka_ref).isdigit():
                logger.info(f"Banka ID kullanılıyor: {banka_ref}")
                return int(banka_ref)
            
            banka_adi = str(banka_ref).strip().upper() if banka_ref else ''
            
            # Banka adını normalize et - "AKBANK KREDİ KARTI" -> "AKBANK" gibi
            banka_adi_normalized = banka_adi
            # Kredi kartı için ek kelimeleri temizle
            if tahsilat_turu == 'Kredi Kartı':
                banka_adi_normalized = banka_adi.replace(' KREDİ KARTI', '').replace(' KREDİ KART', '').replace(' KREDİ', '').strip()
            # Banka havalesi için ek kelimeleri temizle (ŞAHS/ŞAHSİ korunur çünkü farklı ID'leri var)
            elif tahsilat_turu == 'Banka Havalesi':
                banka_adi_normalized = banka_adi.replace(' HAVALESİ', '').replace(' HAVALE', '').strip()
            
            # Normalize edilmiş adı kullan
            if banka_adi_normalized:
                banka_adi = banka_adi_normalized
                logger.info(f"Banka adı normalize edildi: '{banka_ref}' -> '{banka_adi}'")
            
            # Kredi kartı için banka adından ID mapping - içeren kontrolü yapılır
            if tahsilat_turu == 'Kredi Kartı':
                # Banka adı içeren kontrol listesi (sıralama önemli - MRK önce kontrol edilmeli)
                banka_adi_mapping = [
                    ('MRKBANK', 9), ('MRK', 9),
                    ('YAPIKREDI', 1), ('YAPIKRED', 1), ('YKB', 1),
                    ('AKBANK', 2),
                    ('FINANSBANK', 3), ('FİNANSBANK', 3), ('QNB', 3),
                    ('DENIZBANK', 4), ('DENİZBANK', 4),
                    ('GARANTI', 5), ('GARANTİ', 5), ('BBVA', 5),
                    ('ISBANK', 6), ('İŞBANKASI', 6), ('IŞBANK', 6),
                    ('TEB', 7),
                    ('VAKIFBANK', 8), ('VAKIF', 8),
                    ('ZIRAAT', 20), ('ZİRAAT', 20)
                ]
                
                for banka_key, banka_id in banka_adi_mapping:
                    if banka_key in banka_adi:
                        logger.info(f"Kredi Kartı - Banka adı '{banka_adi}' -> '{banka_key}' için ID bulundu: {banka_id}")
                        return banka_id
                
                logger.warning(f"Kredi Kartı - Banka adı '{banka_adi}' için ID mapping bulunamadı")
            
            # Banka Havalesi için banka adından ID mapping
            elif tahsilat_turu == 'Banka Havalesi':
                # Önce ŞAHS/ŞAHSİ içeren özel durumları kontrol et (sıralama önemli!)
                if 'ŞAHS' in banka_adi or 'ŞAHSİ' in banka_adi:
                    if 'GARANTİ' in banka_adi:
                        logger.info(f"Banka Havalesi - Banka adı '{banka_adi}' -> GARANTİ ŞAHSİ için ID bulundu: 18")
                        return 18
                    elif 'İŞBANKASI' in banka_adi or 'ISBANK' in banka_adi:
                        logger.info(f"Banka Havalesi - Banka adı '{banka_adi}' -> İŞBANKASI ŞAHSİ için ID bulundu: 19")
                        return 19
                
                # Normal banka adları mapping - içeren kontrolü (AKBANK MEVDUAT -> AKBANK gibi)
                banka_adi_mapping = [
                    ('YAPIKREDI', 10), ('YAPIKRED', 10), ('YKB', 10),
                    ('AKBANK', 11),
                    ('FINANSBANK', 12), ('FİNANSBANK', 12), ('QNB', 12),
                    ('DENIZBANK', 13), ('DENİZBANK', 13),
                    ('GARANTI', 14), ('GARANTİ', 14), ('BBVA', 14),
                    ('ISBANK', 15), ('İŞBANKASI', 15), ('IŞBANK', 15),
                    ('TEB', 16),
                    ('VAKIFBANK', 17), ('VAKIF', 17),
                    ('ZIRAAT', 21), ('ZİRAAT', 21)
                ]
                
                for banka_key, banka_id in banka_adi_mapping:
                    if banka_key in banka_adi:
                        logger.info(f"Banka Havalesi - Banka adı '{banka_adi}' -> '{banka_key}' için ID bulundu: {banka_id}")
                        return banka_id
                
                logger.warning(f"Banka Havalesi - Banka adı '{banka_adi}' için ID mapping bulunamadı")
            
            # Diğer durumlar için BANKREF kullanılmaya devam edilebilir
            logger.warning(f"Geçersiz banka referansı: {banka_ref}")
            return None
        except (ValueError, TypeError) as e:
            logger.warning(f"Banka ID dönüştürme hatası: {banka_ref}, {e}")
            return None

    def get_banka_name_by_ref(self, banka_ref):
        """BANKREF'ten banka adını (DEFINITION_) döndürür."""
        try:
            if not banka_ref or not str(banka_ref).isdigit():
                return None
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT TOP 1 DEFINITION_
                FROM [GO3].[dbo].[LG_002_BNCARD]
                WHERE LOGICALREF = ?
                """,
                (int(banka_ref),)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return self.safe_decode_string(row[0])
            return None
        except Exception as e:
            logger.error(f"get_banka_name_by_ref error for {banka_ref}: {e}")
            return None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def get_cari_logicalref_by_code(self, cari_kod: str):
        """Cari CODE'dan LOGICALREF döndürür; önce firma tablolarında (LG_002_CLCARD), sonra CLCARD'da dener."""
        try:
            code = (cari_kod or '').strip()
            if not code:
                return None

            conn = self.get_connection()
            cursor = conn.cursor()

            # Önce firma tablosu
            try:
                cursor.execute(
                    """
                    SELECT TOP 1 LOGICALREF
                    FROM [GO3].[dbo].[LG_002_CLCARD]
                    WHERE CODE = ?
                    """,
                    (code,)
                )
                row = cursor.fetchone()
                if row and row[0]:
                    return int(row[0])
            except Exception:
                pass

            # Fallback: genel CLCARD
            try:
                cursor.execute(
                    """
                    SELECT TOP 1 LOGICALREF
                    FROM [GO3].[dbo].[CLCARD]
                    WHERE CODE = ?
                    """,
                    (code,)
                )
                row = cursor.fetchone()
                if row and row[0]:
                    return int(row[0])
            except Exception:
                pass

            return None
        except Exception as e:
            logger.error(f"get_cari_logicalref_by_code error for {cari_kod}: {e}")
            return None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def insert_tahsilat(self, cari_id, tahsilat_turu, banka, tutar, tarih, kullanici, aciklama, taksit=None, evrak_no=None, banka_id=None, fis_no=None):
        """TAHSILATTB tablosuna yeni tahsilat kaydı ekler"""
        from datetime import datetime

        # BankaID'yi hesapla - eğer banka_id parametresi verilmişse direkt kullan, yoksa banka adından hesapla
        if not banka_id and banka and tahsilat_turu:
            # Eğer banka zaten bir ID ise (sayısal), direkt kullan
            if str(banka).isdigit():
                banka_id = int(banka)
            else:
                # Banka adından ID hesapla (eski yöntem - geriye dönük uyumluluk için)
                banka_id = self.get_banka_id(banka, tahsilat_turu)

        query = """
        INSERT INTO [GO3].[dbo].[TAHSILATTB] 
        ([CariID], [TahsilatTuru], [Banka], [BankaID], [Tutar], [Tarih], [Updated], [Kullanici], 
         [EklemeTarihi], [Durum], [Aciklama], [TeslimDurumu], [Taksit], [EvrakNo], [FisNo])
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        try:
            # Şu anki tarih ve saat
            ekleme_tarihi = datetime.now().strftime(
                '%Y-%m-%d %H:%M:%S.%f')[:-3]  # 2023-01-02 00:00:00.000 formatı

            # TeslimDurumu belirleme: Kredi Kartı ise TESLİM EDİLDİ, diğer tüm tahsilat türleri TESLİM EDİLMEDİ
            teslim_durumu = 'TESLİM EDİLDİ' if tahsilat_turu and tahsilat_turu.upper() == 'KREDİ KARTI' else 'TESLİM EDİLMEDİ'

            # Connection oluştur ve execute et
            connection = self.get_connection()
            try:
                cursor = connection.cursor()
                banka_name = None
                if banka_id and str(banka_id).isdigit():
                    # Önce BNCARD (Banka) tablosunda ara
                    cursor.execute(
                        """
                        SELECT TOP 1 DEFINITION_
                        FROM [GO3].[dbo].[LG_002_BNCARD]
                        WHERE LOGICALREF = ?
                        """,
                        (int(banka_id),),
                    )
                    brow = cursor.fetchone()
                    if brow and brow[0]:
                        banka_name = self.safe_decode_string(brow[0])
                    
                    # Bulunamazsa BANKACC (Banka Hesabı) tablosunda ara
                    if not banka_name:
                        cursor.execute(
                            """
                            SELECT TOP 1 DEFINITION_
                            FROM [GO3].[dbo].[LG_002_BANKACC]
                            WHERE LOGICALREF = ?
                            """,
                            (int(banka_id),),
                        )
                        brow = cursor.fetchone()
                        if brow and brow[0]:
                            banka_name = self.safe_decode_string(brow[0])

                if not banka_name and banka and not str(banka).isdigit():
                    banka_name = banka

                params = [
                    cari_id,
                    tahsilat_turu,
                    banka_name if banka_name else None,
                    banka_id,
                    float(tutar),
                    tarih,
                    1,
                    kullanici,
                    ekleme_tarihi,
                    'İŞLENMEDİ',
                    aciklama if aciklama else None,
                    teslim_durumu,
                    int(taksit) if taksit and str(
                        taksit).isdigit() else None,
                    evrak_no if evrak_no else None,
                    fis_no if fis_no else None,
                ]

                cursor.execute(query, params)

                # @@IDENTITY ile son eklenen ID'yi al
                cursor.execute("SELECT @@IDENTITY")
                new_id_result = cursor.fetchone()
                new_id = int(
                    new_id_result[0]) if new_id_result and new_id_result[0] else None

                connection.commit()

                logger.info(
                    f"Yeni tahsilat kaydı eklendi - ID: {new_id}, Kullanıcı: {kullanici}, Tutar: {tutar}, BankaID: {banka_id}")

                # TAHSILATTB'ye kayıt başarılıysa Logo entegrasyonu (Devredışı bırakıldı)
                # if new_id:
                #     try:
                #         self.insert_clfiche_and_clfline(
                #             cari_id=cari_id,
                #             tutar=tutar,
                #             tarih=tarih,
                #             kullanici=kullanici,
                #             evrak_no=evrak_no,
                #             banka_id=banka_id
                #         )
                #         logger.info(
                #             f"CLFICHE ve CLFLINE kayıtları eklendi - Tahsilat ID: {new_id}")
                #     except Exception as clf_error:
                #         logger.error(
                #             f"CLFICHE/CLFLINE kayıt hatası: {clf_error}")
                #         # CLFICHE hatası ana tahsilat kaydını iptal etmez

                return new_id

            except Exception as inner_e:
                connection.rollback()
                raise inner_e
            finally:
                connection.close()

        except Exception as e:
            logger.error(f"insert_tahsilat error: {e}")
            raise e

    def update_tahsilat_durum(self, tahsilat_id, durum="LOGO'DA İŞLENDİ"):
        """
        TAHSILATTB tablosundaki Durum alanını günceller.
        Özellikle Logo entegrasyonu başarılı olduğunda çağrılır.
        """
        query = "UPDATE [GO3].[dbo].[TAHSILATTB] SET [Durum] = ? WHERE [Id] = ?"
        try:
            connection = self.get_connection()
            try:
                cursor = connection.cursor()
                cursor.execute(query, (durum, int(tahsilat_id)))
                connection.commit()
                logger.info(f"Tahsilat ID {tahsilat_id} durumu '{durum}' olarak güncellendi.")
                return True
            except Exception as inner_e:
                connection.rollback()
                logger.error(f"update_tahsilat_durum inner error: {inner_e}")
                return False
            finally:
                connection.close()
        except Exception as e:
            logger.error(f"update_tahsilat_durum error: {e}")
            return False

    def insert_clfiche_and_clfline(self, cari_id, tutar, tarih, kullanici, evrak_no=None, banka_id=None):
        """
        Logo GO3 entegrasyonu: LG_002_05_CLFICHE ve LG_002_05_CLFLINE tablolarına kayıt atar.
        """
        # BankRef (BANKACCREF) banka_id'den gelir, yoksa default 30 (Sanal Pos)
        bank_ref = int(banka_id) if banka_id and str(banka_id).isdigit() else 30
        
        # FicheNo evrak_no'dan gelir, yoksa T ile başlayan bir numara
        import random
        from datetime import datetime
        
        ficheno = str(evrak_no) if evrak_no else f"T{datetime.now().strftime('%y%m%d%H%M')}{random.randint(10, 99)}"
        if len(ficheno) > 17:
            ficheno = ficheno[:17]
            
        tarih_dt = None
        if isinstance(tarih, str):
            try:
                tarih_dt = datetime.strptime(tarih.split('.')[0], '%Y-%m-%d %H:%M:%S')
            except:
                try:
                    tarih_dt = datetime.strptime(tarih, '%Y-%m-%d')
                except:
                    tarih_dt = datetime.now()
        else:
            tarih_dt = tarih if tarih else datetime.now()

        tarih_str = tarih_dt.strftime('%Y-%m-%d %H:%M:%S')
        capi_date = tarih_dt.strftime('%Y-%m-%d 00:00:00.000')
        capi_hour = tarih_dt.hour
        capi_min = tarih_dt.minute
        capi_sec = tarih_dt.second
        logo_time = int(tarih_dt.timestamp() * 1000) % 1000000000

        # Birinci tablo: CLFICHE (TRCODE 70: Kredi Kartı/Banka Havalesi)
        trcode = 70
        
        insert_clfiche_sql = """
        INSERT INTO [GO3].[dbo].[LG_002_05_CLFICHE] (
            FICHENO, DATE_, DOCODE, TRCODE, SPECCODE, CYPHCODE, BRANCH, DEPARTMENT,
            GENEXP1, GENEXP2, GENEXP3, GENEXP4, GENEXP5, GENEXP6, DEBIT, CREDIT,
            REPDEBIT, REPCREDIT, CAPIBLOCK_CREATEDBY, CAPIBLOCK_CREADEDDATE,
            CAPIBLOCK_CREATEDHOUR, CAPIBLOCK_CREATEDMIN, CAPIBLOCK_CREATEDSEC,
            CAPIBLOCK_MODIFIEDBY, CAPIBLOCK_MODIFIEDDATE, CAPIBLOCK_MODIFIEDHOUR,
            CAPIBLOCK_MODIFIEDMIN, CAPIBLOCK_MODIFIEDSEC, ACCOUNTED, INVOREF,
            CASHACCREF, CASHCENREF, PRINTCNT, CANCELLED, CANCELLEDACC, ACCFICHEREF,
            GENEXCTYP, LINEEXCTYP, TEXTINC, SITEID, RECSTATUS, ORGLOGICREF, WFSTATUS,
            TIME, CLCARDREF, BANKACCREF, BNACCREF, BNCENTERREF, TRADINGGRP,
            POSCOMMACCREF, POSCOMMCENREF, POINTCOMMACCREF, POINTCOMMCENREF,
            PROJECTREF, STATUS, WFLOWCRDREF, ORGLOGOID, AFFECTCOLLATRL,
            GRPFIRMTRANS, AFFECTRISK, POSTERMINALNR, POSTERMINALNUM, APPROVE,
            APPROVEDATE, SALESMANREF, CSTRANSREF, DOCDATE, GUID, DEVIR, PRINTDATE,
            FOREXIM, TYPECODE, EINVOICE, HOUR_, MINUTE_, DEDUCTCODE, ELECTDOC,
            NOTIFYCRDREF, GIBACCFICHEREF, PARTIALCSPAYREF, GIBINCMTAXREF, EXIMVAT
        ) 
        OUTPUT INSERTED.LOGICALREF
        VALUES (
            ?, ?, ?, ?, '', '', 0, 0,
            ?, '', '', '', '', '', 0, ?,
            0, ?, 6, ?,
            ?, ?, ?,
            0, NULL, 0,
            0, 0, 0, 0,
            0, 0, 0, 0, 0, 0,
            3, 0, 0, 0, 1, 0, 0,
            ?, ?, ?, 0, 0, '',
            0, 0, 0, 0,
            0, 0, 0, '', 0,
            0, 1, 0, '',
            0, NULL, 0, 0, NULL, '', 0, NULL,
            0, '', 0, 0, 0, '', 0,
            0, 0, 0, 0, 0
        )
        """
        
        # [GO3].[dbo].[LG_002_05_CLFLINE] INSERT
        insert_clfline_sql = """
        INSERT INTO [GO3].[dbo].[LG_002_05_CLFLINE] (
            CLIENTREF, CLACCREF, CLCENTERREF, CASHCENTERREF, CASHACCOUNTREF,
            VIRMANREF, SOURCEFREF, DATE_, DEPARTMENT, BRANCH, MODULENR, TRCODE,
            LINENR, SPECODE, CYPHCODE, TRANNO, DOCODE, LINEEXP, ACCOUNTED, SIGN, AMOUNT, TRCURR, TRRATE, TRNET,
            REPORTRATE, REPORTNET, EXTENREF, PAYDEFREF, ACCFICHEREF, PRINTCNT,
            CAPIBLOCK_CREATEDBY, CAPIBLOCK_CREADEDDATE, CAPIBLOCK_CREATEDHOUR,
            CAPIBLOCK_CREATEDMIN, CAPIBLOCK_CREATEDSEC, CAPIBLOCK_MODIFIEDBY, CAPIBLOCK_MODIFIEDDATE, CAPIBLOCK_MODIFIEDHOUR, CAPIBLOCK_MODIFIEDMIN, CAPIBLOCK_MODIFIEDSEC,
            CANCELLED, TRGFLAG, LINEEXCTYP, ONLYONEPAYLINE, DISCFLAG, DISCRATE,
            VATRATE, CASHAMOUNT, DISCACCREF, DISCCENREF, VATRACCREF, VATRCENREF,
            PAYMENTREF, VATAMOUNT, SITEID, RECSTATUS, ORGLOGICREF, INFIDX,
            POSCOMMACCREF, POSCOMMCENREF, POINTCOMMACCREF, POINTCOMMCENREF, TRADINGGRP, CHEQINFO, CREDITCNO,
            CLPRJREF, STATUS, EXIMFILEREF, EXIMPROCNR, MONTH_, YEAR_,
            FUNDSHARERAT, AFFECTCOLLATRL, GRPFIRMTRANS, REFLVATACCREF, REFLVATOTHACCREF,
            AFFECTRISK, BATCHNUM, APPROVENUM, EUVATSTATUS, EXIMTYPE, EIDISTFLNNR,
            EISRVDSTTYP, EXIMDISTTYP, SALESMANREF, BANKACCREF, BNACCREF, BNCENTERREF, ORGLOGOID, GUID, DOCDATE,
            INSTALREF, DEVIR, DEVIRMODULENR, FTIME, OFFERREF, RETCCFCREF,
            EMFLINEREF, FROMEXCHDIFF, CANDEDUCT, DEDUCTIONPART1, DEDUCTIONPART2,
            UNDERDEDUCTLIMIT, VATDEDUCTRATE, VATDEDUCTACCREF, VATDEDUCTOTHACCREF,
            VATDEDUCTCENREF, VATDEDUCTOTHCENREF, CANTCREDEDUCT, PAIDINCASH,
            BRUTAMOUNT, NETAMOUNT, BRUTAMOUNTTR, NETAMOUNTTR, BRUTAMOUNTREP,
            NETAMOUNTREP, BNLNTRCURR, BNLNTRRATE, BNLNTRNET, INCDEDUCTAMNT,
            AFFECTCOST, FOREXIM, EXIMFILECODECLF, SPECODE2, SERVREASONDEF
        ) VALUES (
            ?, 0, 0, 0, 0, 0, ?, ?, 0, 0, ?, ?, 7, '', '', ?, ?, ?, 0, 1, ?, 0, 0, ?, 1, ?, 0, 1, 0, 0,
            6, ?, ?, ?, ?, 0, NULL, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, '', '', '', 0, 0, 0, 0, ?, ?,
            0, 0, 0, 0, 0, 1, '', '', 0, 0, 0, 0, 0, 0, ?, 0, 0, '', '', ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?, 0, 0, 0, '', '', ''
        )
        """

        connection = self.get_connection()
        try:
            cursor = connection.cursor()
            
            # 1. CLFICHE Insert
            cl_fiche_params = [
                ficheno, tarih_str, evrak_no if evrak_no else '', trcode, 
                f"Web: {kullanici}", tutar, tutar, capi_date, capi_hour, capi_min, capi_sec,
                logo_time, cari_id, bank_ref
            ]
            
            cursor.execute(insert_clfiche_sql, cl_fiche_params)
            new_logicalref_result = cursor.fetchone()
            if not new_logicalref_result:
                raise Exception("CLFICHE INSERT failed to return LOGICALREF")
            new_logicalref = int(new_logicalref_result[0])
            
            # 2. CLFLINE Insert
            cl_line_params = [
                cari_id, new_logicalref, tarih_str, tarih_dt.second, trcode,
                ficheno, evrak_no if evrak_no else '', f"Web Tahsilat: {kullanici}",
                tutar, tutar, tutar, capi_date, capi_hour, capi_min, capi_sec,
                tarih_dt.month, tarih_dt.year, bank_ref, tarih_str, tutar
            ]
            
            cursor.execute(insert_clfline_sql, cl_line_params)
            connection.commit()
            return new_logicalref
            
        except Exception as e:
            connection.rollback()
            logger.error(f"insert_clfiche_and_clfline error: {e}")
            raise e
        finally:
            connection.close()

    def get_tahsilat_by_id(self, tahsilat_id, plasiyer):
        """ID'ye göre tahsilat bilgilerini getir (kullanıcı kontrolü ile)"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # GunlukTahsilat_V view'den veri al - kullanıcı kontrolü ile
            query = """
            SELECT 
                Id,
                CariKod,
                CariUnvan,
                Tutar,
                Tarih,
                Aciklama,
                Durum,
                Plasiyer
            FROM GunlukTahsilat_V 
            WHERE Id = ? AND UPPER(LTRIM(RTRIM(Plasiyer))) = ?
            """

            cursor.execute(query, (tahsilat_id, plasiyer.upper()))
            row = cursor.fetchone()

            if row:
                return {
                    'Id': row.Id,
                    'CariKod': self.safe_decode_string(row.CariKod),
                    'CariUnvan': self.safe_decode_string(row.CariUnvan),
                    'Tutar': float(row.Tutar) if row.Tutar else 0.0,
                    'Tarih': row.Tarih,
                    'Aciklama': self.safe_decode_string(row.Aciklama),
                    'Durum': self.safe_decode_string(row.Durum),
                    'Plasiyer': self.safe_decode_string(row.Plasiyer)
                }

            return None

        except Exception as e:
            logger.error(
                f"get_tahsilat_by_id error - ID: {tahsilat_id}, Plasiyer: {plasiyer}, Error: {e}")
            return None
        finally:
            if 'connection' in locals():
                connection.close()

    # update_tahsilat metodu kaldırılmıştır

    # get_cari_hesaplari metodu kaldırılmıştır

    def delete_tahsilat(self, tahsilat_id, kullanici):
        """Tahsilat kaydını siler - Sadece 'İŞLENMEDİ' durumundaki kayıtlar silinebilir"""
        import os
        from django.conf import settings

        connection = None
        evrak_files_deleted = []

        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Önce tahsilatın varlığını ve durumunu kontrol et
            check_query = """
            SELECT [ID], [Durum], [Kullanici] 
            FROM [GO3].[dbo].[TAHSILATTB] 
            WHERE [ID] = ?
            """
            cursor.execute(check_query, (tahsilat_id,))
            tahsilat = cursor.fetchone()

            if not tahsilat:
                return {"success": False, "error": "Tahsilat bulunamadı"}

            # Durum kontrolü - sadece İŞLENMEDİ olanlar silinebilir
            if tahsilat[1] != "İŞLENMEDİ":
                return {"success": False, "error": f"Bu tahsilat silinemez. Durum: '{tahsilat[1]}'. Sadece 'İŞLENMEDİ' durumundaki tahsilatlar silinebilir."}

            # Kullanıcı kontrolü - sadece kendi kayıtlarını silebilir
            if tahsilat[2] and tahsilat[2].upper() != kullanici.upper():
                return {"success": False, "error": "Bu tahsilat size ait değil"}

            # Bu tahsilata ait evrak dosyalarını bul ve sil
            try:
                from .models import TahsilatEvrak

                # Bu tahsilata ait evrakları bul
                evraklar = TahsilatEvrak.objects.filter(
                    tahsilat_id=tahsilat_id)

                for evrak in evraklar:
                    # Fiziksel dosyayı sil
                    if evrak.evrak_dosyasi and os.path.isfile(evrak.evrak_dosyasi.path):
                        try:
                            os.remove(evrak.evrak_dosyasi.path)
                            evrak_files_deleted.append(
                                evrak.evrak_dosyasi.name)
                            logger.info(
                                f"Evrak dosyası silindi: {evrak.evrak_dosyasi.path}")
                        except OSError as e:
                            logger.warning(
                                f"Evrak dosyası silinemedi {evrak.evrak_dosyasi.path}: {e}")

                # Evrak kayıtlarını veritabanından sil
                deleted_count = evraklar.count()
                evraklar.delete()

                if deleted_count > 0:
                    logger.info(
                        f"Tahsilat ID {tahsilat_id} için {deleted_count} evrak kaydı silindi")

            except Exception as evrak_error:
                logger.warning(f"Evrak silme işleminde hata: {evrak_error}")
                # Evrak silme hatası tahsilat silme işlemini durdurmaz

            # Tahsilat kaydını sil - TAHSILATTB tablosundan direkt sil
            delete_query = """
            DELETE FROM [GO3].[dbo].[TAHSILATTB] 
            WHERE [ID] = ? AND [Durum] = 'İŞLENMEDİ'
            """
            cursor.execute(delete_query, (tahsilat_id,))

            if cursor.rowcount > 0:
                connection.commit()

                success_message = f"Tahsilat başarıyla silindi"
                if evrak_files_deleted:
                    success_message += f" (Evrak dosyaları da silindi: {len(evrak_files_deleted)} dosya)"

                logger.info(
                    f"Tahsilat silindi - ID: {tahsilat_id}, Kullanici: {kullanici}, Evraklar: {evrak_files_deleted}")
                return {"success": True, "message": success_message}
            else:
                return {"success": False, "error": "Tahsilat silinemedi"}

        except Exception as e:
            if connection:
                connection.rollback()
            logger.error(f"delete_tahsilat error: {e}")
            return {"success": False, "error": f"Veritabanı hatası: {str(e)}"}
        finally:
            if connection:
                connection.close()

    def get_plasiyer_stats(self, plasiyer_name):
        """Belirtilen plasiyerin detaylı istatistiklerini getirir - diğer sayfalarla tutarlı"""
        try:
            # Satış istatistikleri - güncellenmiş get_satis_stats kullan (TRCODE filtreli)
            satis_stats = self.get_satis_stats(plasiyer_name)

            # Tahsilat istatistikleri
            tahsilat_stats = self.get_tahsilat_stats(plasiyer_name)

            return {
                'plasiyer': plasiyer_name,
                'satis': satis_stats,
                'tahsilat': tahsilat_stats
            }

        except Exception as e:
            logger.error(f"get_plasiyer_stats error for {plasiyer_name}: {e}")
            return {
                'plasiyer': plasiyer_name,
                'satis': {'gunluk_adet': 0, 'gunluk_tutar': 0.0, 'haftalik_adet': 0, 'haftalik_tutar': 0.0, 'aylik_adet': 0, 'aylik_tutar': 0.0},
                'tahsilat': {'gunluk_adet': 0, 'gunluk_tutar': 0.0, 'haftalik_adet': 0, 'haftalik_tutar': 0.0, 'aylik_adet': 0, 'aylik_tutar': 0.0}
            }

    def get_plasiyer_stats_with_month_filter(self, plasiyer_name, selected_months=None):
        """Belirtilen plasiyerin ay filtrelemeli detaylı istatistiklerini getirir - PLASİYER kolonuna göre grupla"""
        try:
            # Satış istatistikleri - ay filtreli (hem satış hem tahsilat için aynı aylar)
            satis_stats = self.get_satis_stats_with_month_filter(
                plasiyer_name, selected_months)

            # Tahsilat istatistikleri - ay filtreli (hem satış hem tahsilat için aynı aylar)
            tahsilat_stats = self.get_tahsilat_stats_with_month_filter(
                plasiyer_name, selected_months)

            return {
                'plasiyer': plasiyer_name,
                'satis': satis_stats,
                'tahsilat': tahsilat_stats
            }

        except Exception as e:
            logger.error(
                f"get_plasiyer_stats_with_month_filter error for {plasiyer_name}: {e}")
            return {
                'plasiyer': plasiyer_name,
                'satis': {'gunluk_adet': 0, 'gunluk_tutar': 0.0, 'haftalik_adet': 0, 'haftalik_tutar': 0.0, 'aylik_adet': 0, 'aylik_tutar': 0.0},
                'tahsilat': {'gunluk_adet': 0, 'gunluk_tutar': 0.0, 'haftalik_adet': 0, 'haftalik_tutar': 0.0, 'aylik_adet': 0, 'aylik_tutar': 0.0}
            }

    @staticmethod
    def _genel_dashboard_month_sql_filter(selected_months):
        """Dashboard satış/tahsilat toplu sorguları için MONTH(...) ifadesi."""
        if selected_months:
            month_ints = []
            for m in selected_months:
                try:
                    month_ints.append(int(m))
                except (TypeError, ValueError):
                    continue
            if month_ints:
                return (
                    'MONTH([TARİH]) IN ('
                    + ','.join(str(x) for x in month_ints)
                    + ')'
                )
        return 'MONTH([TARİH]) = MONTH(GETDATE())'

    @staticmethod
    def _dashboard_plasiyer_batch_row_maps(rows, cols, safe_decode):
        """GROUP BY plasiyer satırlarını isim -> istatistik sözlüğüne çevirir."""
        by_label = {}
        for r in _odbc_keys_lower(rows, cols):
            lbl = safe_decode(r.get('plasiyer_label') or '')
            lbl = lbl.strip()
            pk = safe_decode(r.get('plasiyer_key') or '')
            pk = pk.strip()
            stats = {
                'gunluk_tutar': float(r.get('gunluk_tutar') or 0),
                'gunluk_adet': int(r.get('gunluk_adet') or 0),
                'haftalik_tutar': float(r.get('haftalik_tutar') or 0),
                'haftalik_adet': int(r.get('haftalik_adet') or 0),
                'aylik_tutar': float(r.get('aylik_tutar') or 0),
                'aylik_adet': int(r.get('aylik_adet') or 0),
            }
            if lbl:
                by_label[lbl] = stats
                by_label[lbl.upper()] = stats
            if pk:
                by_label[pk] = stats
        return by_label

    def _assemble_plasiyer_dashboard_from_lookups(
            self, plasiyerler, satis_lookup, tahsilat_lookup):
        """Plasiyer listesi + iki lookup sözlüğünden get_all_plasiyerler_stats çıktısı üretir."""
        zeros_satis = {
            'gunluk_adet': 0, 'gunluk_tutar': 0.0,
            'haftalik_adet': 0, 'haftalik_tutar': 0.0,
            'aylik_adet': 0, 'aylik_tutar': 0.0,
        }
        zeros_tah = {
            'gunluk_adet': 0, 'gunluk_tutar': 0.0,
            'haftalik_adet': 0, 'haftalik_tutar': 0.0,
            'aylik_adet': 0, 'aylik_tutar': 0.0,
        }

        def _pick(by_map, name, z):
            n = self.safe_decode_string(name).strip()
            if not n:
                return {**z}
            found = by_map.get(n) or by_map.get(n.upper())
            return {**found} if found else {**z}

        plasiyer_data = []
        toplam_satis = {'gunluk_adet': 0, 'gunluk_tutar': 0.0, 'haftalik_adet': 0,
                        'haftalik_tutar': 0.0, 'aylik_adet': 0, 'aylik_tutar': 0.0}
        toplam_tahsilat = {'gunluk_adet': 0, 'gunluk_tutar': 0.0, 'haftalik_adet': 0,
                           'haftalik_tutar': 0.0, 'aylik_adet': 0, 'aylik_tutar': 0.0}

        for plasiyer in plasiyerler:
            satis_stats = _pick(satis_lookup, plasiyer, zeros_satis)
            tahsilat_stats = _pick(tahsilat_lookup, plasiyer, zeros_tah)
            plasiyer_data.append({
                'plasiyer': plasiyer,
                'satis': dict(satis_stats),
                'tahsilat': dict(tahsilat_stats),
            })
            for period in ['gunluk', 'haftalik', 'aylik']:
                toplam_satis[f'{period}_adet'] += satis_stats[f'{period}_adet']
                toplam_satis[f'{period}_tutar'] += float(satis_stats[f'{period}_tutar'])
                toplam_tahsilat[f'{period}_adet'] += tahsilat_stats[f'{period}_adet']
                toplam_tahsilat[f'{period}_tutar'] += float(
                    tahsilat_stats[f'{period}_tutar'])

        return {
            'plasiyerler': plasiyer_data,
            'toplam_satis': toplam_satis,
            'toplam_tahsilat': toplam_tahsilat,
        }

    def _genel_dashboard_plasiyer_batch_sqls(self, mf):
        """FATURA + TAHSILAT_LOGO plasiyer özet toplu sorguları (mf = MONTH ifadesi)."""
        week_start_expr = (
            "DATEADD(day, -((DATEPART(WEEKDAY, CAST(GETDATE() AS DATE)) + @@DATEFIRST - 2) % 7), "
            "CAST(GETDATE() AS DATE))"
        )
        satis_sql = f"""
        SELECT
            UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) AS plasiyer_key,
            MIN({_FATURA_PLASIYER_CANON}) AS plasiyer_label,
            SUM(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE)
                THEN CAST([TUTAR] AS DECIMAL(18,2)) ELSE 0 END) AS gunluk_tutar,
            SUM(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE)
                THEN 1 ELSE 0 END) AS gunluk_adet,
            SUM(CASE WHEN CAST([TARİH] AS DATE) >= {week_start_expr}
                AND YEAR([TARİH]) = YEAR(GETDATE()) AND ({mf})
                THEN CAST([TUTAR] AS DECIMAL(18,2)) ELSE 0 END) AS haftalik_tutar,
            SUM(CASE WHEN CAST([TARİH] AS DATE) >= {week_start_expr}
                AND YEAR([TARİH]) = YEAR(GETDATE()) AND ({mf})
                THEN 1 ELSE 0 END) AS haftalik_adet,
            SUM(CASE WHEN YEAR([TARİH]) = YEAR(GETDATE()) AND ({mf})
                THEN CAST([TUTAR] AS DECIMAL(18,2)) ELSE 0 END) AS aylik_tutar,
            SUM(CASE WHEN YEAR([TARİH]) = YEAR(GETDATE()) AND ({mf})
                THEN 1 ELSE 0 END) AS aylik_adet
        FROM [GO3].[dbo].[FATURA]
        WHERE (TRCODE=8 OR TRCODE=7)
        GROUP BY UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON})))
        """
        tahsilat_sql = f"""
        DECLARE @today DATE = CAST(GETDATE() AS DATE);
        DECLARE @week_start DATE = DATEADD(day, -((DATEPART(WEEKDAY, @today) + @@DATEFIRST - 2) % 7), @today);
        SELECT
            UPPER(RTRIM(LTRIM([PLASİYER]))) AS plasiyer_key,
            MIN([PLASİYER]) AS plasiyer_label,
            SUM(CASE WHEN CAST([TARİH] AS DATE) = @today THEN 1 ELSE 0 END) AS gunluk_adet,
            ISNULL(SUM(CASE WHEN CAST([TARİH] AS DATE) = @today
                THEN CAST([TUTAR] AS DECIMAL(15,2)) ELSE 0 END), 0) AS gunluk_tutar,
            SUM(CASE WHEN CAST([TARİH] AS DATE) >= @week_start
                AND YEAR([TARİH]) = YEAR(GETDATE()) AND ({mf})
                THEN 1 ELSE 0 END) AS haftalik_adet,
            ISNULL(SUM(CASE WHEN CAST([TARİH] AS DATE) >= @week_start
                AND YEAR([TARİH]) = YEAR(GETDATE()) AND ({mf})
                THEN CAST([TUTAR] AS DECIMAL(15,2)) ELSE 0 END), 0) AS haftalik_tutar,
            SUM(CASE WHEN YEAR([TARİH]) = YEAR(GETDATE()) AND ({mf})
                THEN 1 ELSE 0 END) AS aylik_adet,
            ISNULL(SUM(CASE WHEN YEAR([TARİH]) = YEAR(GETDATE()) AND ({mf})
                THEN CAST([TUTAR] AS DECIMAL(15,2)) ELSE 0 END), 0) AS aylik_tutar
        FROM [GO3].[dbo].[TAHSILAT_LOGO]
        WHERE [PLASİYER] IS NOT NULL AND LTRIM(RTRIM([PLASİYER])) <> N''
        GROUP BY UPPER(RTRIM(LTRIM([PLASİYER])))
        """
        return satis_sql, tahsilat_sql

    def get_all_plasiyerler_stats(self, selected_months=None):
        """Tüm plasiyerlerin istatistiklerini getirir — FATURA + TAHSILAT_LOGO için toplu sorgular (dashboard performansı)."""
        try:
            plasiyerler = self.get_plasiyer_list_from_fatura()
            if not plasiyerler:
                plasiyerler = ['EYÜP', 'ALİ', 'MERT', 'ATAKAN', 'AZİZ',
                              'YİĞİT', 'SÜLEYMAN', 'CAN', 'BAKIR', 'HALİL']
                logger.warning(
                    "FATURA tablosundan plasiyer bulunamadı, varsayılan liste kullanılıyor")
            else:
                logger.info(
                    f"FATURA tablosundan {len(plasiyerler)} plasiyer bulundu: {plasiyerler}")
        except Exception as e:
            logger.error(
                f"Plasiyer listesi alınırken hata: {e}, varsayılan liste kullanılıyor")
            plasiyerler = ['EYÜP', 'ALİ', 'MERT', 'ATAKAN', 'AZİZ',
                          'YİĞİT', 'SÜLEYMAN', 'CAN', 'BAKIR', 'HALİL']

        mf = self._genel_dashboard_month_sql_filter(selected_months)
        satis_sql, tahsilat_sql = self._genel_dashboard_plasiyer_batch_sqls(mf)

        satis_lookup = {}
        tahsilat_lookup = {}
        conn = None
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(satis_sql)
            cols_s = [c[0] for c in cur.description]
            satis_lookup = self._dashboard_plasiyer_batch_row_maps(
                cur.fetchall(), cols_s, self.safe_decode_string)
            cur.execute(tahsilat_sql)
            cols_t = [c[0] for c in cur.description]
            tahsilat_lookup = self._dashboard_plasiyer_batch_row_maps(
                cur.fetchall(), cols_t, self.safe_decode_string)
            cur.close()
        except Exception as e:
            logger.error(f"get_all_plasiyerler_stats batch error: {e}")
            satis_lookup, tahsilat_lookup = {}, {}
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        return self._assemble_plasiyer_dashboard_from_lookups(
            plasiyerler, satis_lookup, tahsilat_lookup)

    def get_genel_dashboard_bundle(self, selected_months=None):
        """Genel dashboard için tek MSSQL bağlantısında plasiyer özetleri + aylık grafik verisi."""
        defaults = ['EYÜP', 'ALİ', 'MERT', 'ATAKAN', 'AZİZ',
                      'YİĞİT', 'SÜLEYMAN', 'CAN', 'BAKIR', 'HALİL']
        empty_z = {
            'gunluk_adet': 0, 'gunluk_tutar': 0.0,
            'haftalik_adet': 0, 'haftalik_tutar': 0.0,
            'aylik_adet': 0, 'aylik_tutar': 0.0,
        }
        monthly_empty = {
            m: {'satis': 0.0, 'tahsilat': 0.0} for m in range(1, 13)}
        plasiyer_bundle = {
            'plasiyerler': [],
            'toplam_satis': dict(empty_z),
            'toplam_tahsilat': dict(empty_z),
        }
        monthly_data = {
            m: {'satis': 0.0, 'tahsilat': 0.0} for m in range(1, 13)}

        mf = self._genel_dashboard_month_sql_filter(selected_months)
        satis_sql, tahsilat_sql = self._genel_dashboard_plasiyer_batch_sqls(mf)

        plasiyer_list_sql = f"""
        SELECT DISTINCT v
        FROM (
            SELECT {_FATURA_PLASIYER_CANON} AS v
            FROM [GO3].[dbo].[FATURA]
            WHERE (TRCODE=8 OR TRCODE=7)
            UNION
            SELECT LTRIM(RTRIM(ISNULL(CAST([PLASİYER] AS NVARCHAR(120)), N'')))
            FROM [GO3].[dbo].[TAHSILAT_LOGO]
            WHERE [PLASİYER] IS NOT NULL AND LTRIM(RTRIM([PLASİYER])) <> N''
        ) q
        WHERE q.v <> N'' AND q.v IS NOT NULL
        ORDER BY v
        """

        monthly_satis_sql = """
        SELECT
            MONTH([TARİH]) AS ay,
            YEAR([TARİH]) AS yil,
            ISNULL(SUM([TUTAR]), 0) AS toplam_tutar
        FROM [GO3].[dbo].[FATURA]
        WHERE (TRCODE=8 OR TRCODE=7)
          AND YEAR([TARİH]) = YEAR(GETDATE())
        GROUP BY MONTH([TARİH]), YEAR([TARİH])
        ORDER BY MONTH([TARİH])
        """

        monthly_tahsilat_sql = """
        SELECT
            MONTH([TARİH]) AS ay,
            YEAR([TARİH]) AS yil,
            ISNULL(SUM(CAST([TUTAR] AS DECIMAL(15,2))), 0) AS toplam_tutar
        FROM [GO3].[dbo].[TAHSILAT_LOGO]
        WHERE YEAR([TARİH]) = YEAR(GETDATE())
        GROUP BY MONTH([TARİH]), YEAR([TARİH])
        ORDER BY MONTH([TARİH])
        """

        conn = None
        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute(plasiyer_list_sql)
            plasiyerler = []
            for row in cur.fetchall():
                p = self.safe_decode_string(row[0])
                if p and p.strip():
                    plasiyerler.append(p.strip())
            if not plasiyerler:
                plasiyerler = list(defaults)
                logger.warning(
                    'get_genel_dashboard_bundle: FATURA plasiyer listesi boş, varsayılan kullanılıyor')
            else:
                logger.info(
                    'get_genel_dashboard_bundle: %s plasiyer, tek MSSQL bağlantısı',
                    len(plasiyerler))

            cur.execute(satis_sql)
            cols_s = [c[0] for c in cur.description]
            satis_lookup = self._dashboard_plasiyer_batch_row_maps(
                cur.fetchall(), cols_s, self.safe_decode_string)

            cur.execute(tahsilat_sql)
            cols_t = [c[0] for c in cur.description]
            tahsilat_lookup = self._dashboard_plasiyer_batch_row_maps(
                cur.fetchall(), cols_t, self.safe_decode_string)

            cur.execute(monthly_satis_sql)
            cols_m1 = [c[0] for c in cur.description]
            for rr in _odbc_keys_lower(cur.fetchall(), cols_m1):
                ay = int(rr.get('ay') or 0)
                if 1 <= ay <= 12:
                    monthly_data[ay]['satis'] = float(rr.get('toplam_tutar') or 0)

            cur.execute(monthly_tahsilat_sql)
            cols_m2 = [c[0] for c in cur.description]
            for rr in _odbc_keys_lower(cur.fetchall(), cols_m2):
                ay = int(rr.get('ay') or 0)
                if 1 <= ay <= 12:
                    monthly_data[ay]['tahsilat'] = float(rr.get('toplam_tutar') or 0)

            cur.close()

            plasiyer_bundle = self._assemble_plasiyer_dashboard_from_lookups(
                plasiyerler, satis_lookup, tahsilat_lookup)
        except Exception as e:
            logger.error(f'get_genel_dashboard_bundle error: {e}')
            plasiyer_bundle = {
                'plasiyerler': [],
                'toplam_satis': dict(empty_z),
                'toplam_tahsilat': dict(empty_z),
            }
            monthly_data = dict(monthly_empty)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        return {'plasiyer_data': plasiyer_bundle, 'monthly_stats': monthly_data}

    def get_monthly_satis_tahsilat_stats(self):
        """Tüm ayların satış ve tahsilat toplamlarını getirir (aylık bazda)"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Satış verilerini ay bazında grupla
            satis_query = """
            SELECT 
                MONTH([TARİH]) AS ay,
                YEAR([TARİH]) AS yil,
                ISNULL(SUM([TUTAR]), 0) AS toplam_tutar
            FROM [GO3].[dbo].[FATURA]
            WHERE (TRCODE=8 OR TRCODE=7)
            AND YEAR([TARİH]) = YEAR(GETDATE())
            GROUP BY MONTH([TARİH]), YEAR([TARİH])
            ORDER BY MONTH([TARİH])
            """

            # Tahsilat verilerini ay bazında grupla — tüm TAHSILAT_LOGO satırları (dashboard plasiyer toplamları ile aynı kaynak)
            tahsilat_query = """
            SELECT 
                MONTH([TARİH]) AS ay,
                YEAR([TARİH]) AS yil,
                ISNULL(SUM(CAST([TUTAR] AS DECIMAL(15,2))), 0) AS toplam_tutar
            FROM [GO3].[dbo].[TAHSILAT_LOGO]
            WHERE YEAR([TARİH]) = YEAR(GETDATE())
            GROUP BY MONTH([TARİH]), YEAR([TARİH])
            ORDER BY MONTH([TARİH])
            """

            cursor.execute(satis_query)
            satis_results = cursor.fetchall()

            cursor.execute(tahsilat_query)
            tahsilat_results = cursor.fetchall()

            cursor.close()
            connection.close()

            # Sonuçları dictionary'ye dönüştür
            monthly_data = {}
            for month in range(1, 13):
                monthly_data[month] = {
                    'satis': 0.0,
                    'tahsilat': 0.0
                }

            # Satış verilerini ekle
            for row in satis_results:
                ay = int(row.ay)
                monthly_data[ay]['satis'] = float(row.toplam_tutar or 0)

            # Tahsilat verilerini ekle
            for row in tahsilat_results:
                ay = int(row.ay)
                monthly_data[ay]['tahsilat'] = float(row.toplam_tutar or 0)

            return monthly_data

        except Exception as e:
            logger.error(f"get_monthly_satis_tahsilat_stats error: {e}")
            return {month: {'satis': 0.0, 'tahsilat': 0.0} for month in range(1, 13)}

    def get_malzeme_stok_stats(self):
        """MALZEME_STOK tablosundan özet istatistikleri getirir"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Toplam ürün sayısı
            cursor.execute("SELECT COUNT(*) FROM [GO3].[dbo].[MALZEME_STOK]")
            toplam_urun = cursor.fetchone()[0]

            # Kritik stok (toplam < 10)
            cursor.execute(
                "SELECT COUNT(*) FROM [GO3].[dbo].[MALZEME_STOK] WHERE [TOPLAM] < 10 AND [TOPLAM] > 0")
            kritik_stok = cursor.fetchone()[0]

            # Sıfır stok
            cursor.execute(
                "SELECT COUNT(*) FROM [GO3].[dbo].[MALZEME_STOK] WHERE [TOPLAM] = 0")
            sifir_stok = cursor.fetchone()[0]

            # Toplam stok miktarı
            cursor.execute(
                "SELECT SUM([TOPLAM]) FROM [GO3].[dbo].[MALZEME_STOK]")
            toplam_stok_miktari = cursor.fetchone()[0] or 0

            # En fazla stoku olan ürün
            cursor.execute("""
                SELECT TOP 1 [MALZEME KODU], [AÇIKLAMASI], [TOPLAM] 
                FROM [GO3].[dbo].[MALZEME_STOK] 
                ORDER BY [TOPLAM] DESC
            """)
            en_fazla_row = cursor.fetchone()
            en_fazla_stoklu = self.safe_decode_string(
                en_fazla_row[1]) if en_fazla_row else "N/A"

            # En az stoku olan ürün (sıfırdan büyük)
            cursor.execute("""
                SELECT TOP 1 [MALZEME KODU], [AÇIKLAMASI], [TOPLAM] 
                FROM [GO3].[dbo].[MALZEME_STOK] 
                WHERE [TOPLAM] > 0
                ORDER BY [TOPLAM] ASC
            """)
            en_az_row = cursor.fetchone()
            en_az_stoklu = self.safe_decode_string(
                en_az_row[1]) if en_az_row else "N/A"

            conn.close()

            return {
                'toplam_urun_sayisi': toplam_urun,
                'kritik_stok_altinda': kritik_stok,
                'sifir_stok': sifir_stok,
                'toplam_stok_miktari': float(toplam_stok_miktari),
                'en_fazla_stoklu_urun': en_fazla_stoklu,
                'en_az_stoklu_urun': en_az_stoklu
            }

        except Exception as e:
            logger.error(f"get_malzeme_stok_stats error: {e}")
            return {
                'toplam_urun_sayisi': 0,
                'kritik_stok_altinda': 0,
                'sifir_stok': 0,
                'toplam_stok_miktari': 0.0,
                'en_fazla_stoklu_urun': "N/A",
                'en_az_stoklu_urun': "N/A"
            }

    def get_malzeme_turleri(self):
        """MALZEME_STOK tablosundan benzersiz malzeme türlerini getirir"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT DISTINCT [MALZEME TÜRÜ] 
                FROM [GO3].[dbo].[MALZEME_STOK] 
                WHERE [MALZEME TÜRÜ] IS NOT NULL AND [MALZEME TÜRÜ] != ''
                ORDER BY [MALZEME TÜRÜ]
            """)

            rows = cursor.fetchall()
            turleri = [self.safe_decode_string(row[0]) for row in rows]

            conn.close()
            return turleri

        except Exception as e:
            logger.error(f"get_malzeme_turleri error: {e}")
            return []

    def get_markalar(self):
        """MALZEME_STOK tablosundan benzersiz markaları getirir"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT DISTINCT [MARKA] 
                FROM [GO3].[dbo].[MALZEME_STOK] 
                WHERE [MARKA] IS NOT NULL AND [MARKA] != ''
                ORDER BY [MARKA]
            """)

            rows = cursor.fetchall()
            markalar = [self.safe_decode_string(row[0]) for row in rows]

            conn.close()
            return markalar

        except Exception as e:
            logger.error(f"get_markalar error: {e}")
            return []

    def get_malzeme_stok_list(self, search_term=None, malzeme_turu=None, marka=None, stok_durumu=None, limit=None, max_records=10000):
        """MALZEME_STOK tablosundan stok listesini getirir - Optimize edilmiş"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # HEPsİ seçildiğinde bile maksimum kayıt sayısını sınırla
            effective_limit = limit if limit and limit != 'all' else max_records

            # Base query for MALZEME_STOK table - her zaman TOP kullan
            query = f"""
                SELECT TOP {effective_limit}
                       [MALZEME KODU],
                       [AÇIKLAMASI],
                       [MALZEME TÜRÜ],
                       [MARKA],
                       [SEYHAN],
                       [YÜREĞİR],
                       [MERSİN],
                       [TOPLAM],
                       [GRUP KODU],
                       [LOGICALREF]
                FROM [GO3].[dbo].[MALZEME_STOK]
                WHERE 1=1
            """

            params = []

            # Filtreleme
            if search_term:
                query += " AND ([MALZEME KODU] LIKE ? OR [AÇIKLAMASI] LIKE ?)"
                params.extend([f'%{search_term}%', f'%{search_term}%'])

            if malzeme_turu:
                query += " AND [MALZEME TÜRÜ] = ?"
                params.append(malzeme_turu)

            if marka:
                query += " AND [MARKA] = ?"
                params.append(marka)

            # Stok durumu filtresi
            if stok_durumu == 'stokta_var':
                query += " AND [TOPLAM] > 0"
            elif stok_durumu == 'stok_bitmis':
                query += " AND [TOPLAM] <= 0"

            # Sıralama
            query += " ORDER BY [MALZEME KODU]"

            cursor.execute(query, params)
            rows = cursor.fetchall()

            stok_listesi = []
            for row in rows:
                stok_listesi.append({
                    'MALZEME_KODU': self.safe_decode_string(row[0]),
                    'ACIKLAMASI': self.safe_decode_string(row[1]),
                    'MALZEME_TURU': self.safe_decode_string(row[2]),
                    'MARKA': self.safe_decode_string(row[3]),
                    'SEYHAN': float(row[4]) if row[4] else 0.0,
                    'YUREĞIR': float(row[5]) if row[5] else 0.0,
                    'MERSIN': float(row[6]) if row[6] else 0.0,
                    'TOPLAM': float(row[7]) if row[7] else 0.0,
                    'GRUP_KODU': self.safe_decode_string(row[8]),
                    'LOGICALREF': row[9],
                })

            conn.close()
            return stok_listesi

        except Exception as e:
            logger.error(f"get_malzeme_stok_list error: {e}")
            return []

    def get_fiyat_analizi_list(self, search_term=None, malzeme_turu=None, marka=None, stok_durumu=None, max_records=10000):
        """FIYATANALIZ tablosundan fiyat analizi listesini getirir - Optimize edilmiş"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            safe = self.safe_decode_string

            # Base query for FIYATANALIZ table - her zaman TOP kullan
            query = f"""
                SELECT TOP {max_records}
                       [LOGICALREF],
                       [MALZEME KODU],
                       [AÇIKLAMASI],
                       [MALZEME TÜRÜ],
                       [MARKA],
                       [GRUP KODU],
                       [TOPLAM],
                       [TANIMLI ALIŞ FİYATI],
                       [TANIMLI SATIŞ FİYATI],
                       [SON ALIŞ BİRİM NET],
                       [KARLILIK ORANI (%)]
                FROM [GO3].[dbo].[FIYATANALIZ]
                WHERE 1=1
            """

            params = []

            # Filtreleme
            if search_term:
                query += " AND ([MALZEME KODU] LIKE ? OR [AÇIKLAMASI] LIKE ?)"
                params.extend([f'%{search_term}%', f'%{search_term}%'])

            if malzeme_turu:
                query += " AND [MALZEME TÜRÜ] = ?"
                params.append(malzeme_turu)

            if marka:
                query += " AND [MARKA] = ?"
                params.append(marka)

            # Stok durumu filtresi
            if stok_durumu == 'stokta_var':
                query += " AND [TOPLAM] > 0"
            elif stok_durumu == 'stok_bitmis':
                query += " AND [TOPLAM] <= 0"
            # stok_durumu == 'hepsi' veya boş ise hiçbir filtreleme yapma

            # Sıralama
            query += " ORDER BY [MALZEME KODU]"

            cursor.execute(query, params)
            rows = cursor.fetchall()

            fiyat_listesi = []
            for row in rows:
                fiyat_listesi.append({
                    'LOGICALREF': row[0],
                    'MALZEME_KODU': safe(row[1]),
                    'ACIKLAMASI': safe(row[2]),
                    'MALZEME_TURU': safe(row[3]),
                    'MARKA': safe(row[4]),
                    'GRUP_KODU': safe(row[5]),
                    'TOPLAM': float(row[6]) if row[6] else 0.0,
                    'TANIMLI_ALIS_FIYATI': float(row[7]) if row[7] else 0.0,
                    'TANIMLI_SATIS_FIYATI': float(row[8]) if row[8] else 0.0,
                    'SON_ALIS_BIRIM_NET': float(row[9]) if row[9] else 0.0,
                    'KARLILIK_ORANI': float(row[10]) if row[10] else 0.0,
                })

            conn.close()
            return fiyat_listesi

        except Exception as e:
            logger.error(f"get_fiyat_analizi_list error: {e}")
            return []

    def get_fiyat_analizi_stats(self):
        """FIYATANALIZ tablosundan istatistikleri getirir"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Toplam ürün sayısı ve distinct count'lar için WHERE kullanma
            # (tüm tabloyu yansıtmalı)
            total_query = """
                SELECT 
                    COUNT(*) as toplam_urun_sayisi,
                    COUNT(DISTINCT [MARKA]) as toplam_marka_sayisi,
                    COUNT(DISTINCT [MALZEME TÜRÜ]) as toplam_turu_sayisi
                FROM [GO3].[dbo].[FIYATANALIZ]
            """
            cursor.execute(total_query)
            total_row = cursor.fetchone()

            # Fiyat ve karlılık için sadece fiyatı olan ürünleri filtrele
            price_query = """
                SELECT 
                    AVG([KARLILIK ORANI (%)]) as ortalama_karlilik,
                    MAX([TANIMLI SATIŞ FİYATI]) as en_yuksek_satis_fiyati,
                    MIN([TANIMLI SATIŞ FİYATI]) as en_dusuk_satis_fiyati
                FROM [GO3].[dbo].[FIYATANALIZ]
                WHERE [TANIMLI SATIŞ FİYATI] > 0
            """
            cursor.execute(price_query)
            price_row = cursor.fetchone()

            conn.close()

            if total_row:
                return {
                    'toplam_urun_sayisi': total_row[0] or 0,
                    'ortalama_karlilik': float(price_row[0]) if price_row[0] else 0.0,
                    'en_yuksek_satis_fiyati': float(price_row[1]) if price_row[1] else 0.0,
                    'en_dusuk_satis_fiyati': float(price_row[2]) if price_row[2] else 0.0,
                    'toplam_marka_sayisi': total_row[1] or 0,
                    'toplam_turu_sayisi': total_row[2] or 0,
                }
            else:
                return {
                    'toplam_urun_sayisi': 0,
                    'ortalama_karlilik': 0.0,
                    'en_yuksek_satis_fiyati': 0.0,
                    'en_dusuk_satis_fiyati': 0.0,
                    'toplam_marka_sayisi': 0,
                    'toplam_turu_sayisi': 0,
                }

            cursor.execute(query)
            row = cursor.fetchone()

            conn.close()

            if row:
                return {
                    'toplam_urun_sayisi': row[0] or 0,
                    'ortalama_karlilik': float(row[1]) if row[1] else 0.0,
                    'en_yuksek_satis_fiyati': float(row[2]) if row[2] else 0.0,
                    'en_dusuk_satis_fiyati': float(row[3]) if row[3] else 0.0,
                    'toplam_marka_sayisi': row[4] or 0,
                    'toplam_turu_sayisi': row[5] or 0,
                }
            else:
                return {
                    'toplam_urun_sayisi': 0,
                    'ortalama_karlilik': 0.0,
                    'en_yuksek_satis_fiyati': 0.0,
                    'en_dusuk_satis_fiyati': 0.0,
                    'toplam_marka_sayisi': 0,
                    'toplam_turu_sayisi': 0,
                }

        except Exception as e:
            logger.error(f"get_fiyat_analizi_stats error: {e}")
            return {
                'toplam_urun_sayisi': 0,
                'ortalama_karlilik': 0.0,
                'en_yuksek_satis_fiyati': 0.0,
                'en_dusuk_satis_fiyati': 0.0,
                'toplam_marka_sayisi': 0,
                'toplam_turu_sayisi': 0,
            }

    def get_fiyat_analizi_filter_options(self):
        """FIYATANALIZ tablosu için filtreleme seçeneklerini getirir"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            safe = self.safe_decode_string

            # Malzeme türleri
            cursor.execute(
                "SELECT DISTINCT [MALZEME TÜRÜ] FROM [GO3].[dbo].[FIYATANALIZ] WHERE [MALZEME TÜRÜ] IS NOT NULL ORDER BY [MALZEME TÜRÜ]")
            malzeme_turleri = [safe(row[0]) for row in cursor.fetchall()]

            # Markalar
            cursor.execute(
                "SELECT DISTINCT [MARKA] FROM [GO3].[dbo].[FIYATANALIZ] WHERE [MARKA] IS NOT NULL ORDER BY [MARKA]")
            markalar = [safe(row[0]) for row in cursor.fetchall()]

            conn.close()

            return {
                'malzeme_turleri': malzeme_turleri,
                'markalar': markalar
            }

        except Exception as e:
            logger.error(f"get_fiyat_analizi_filter_options error: {e}")
            return {
                'malzeme_turleri': [],
                'markalar': []
            }

    def get_malzeme_stok_filter_options(self):
        """MALZEME_STOK tablosu için filtreleme seçeneklerini getirir"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Malzeme türleri
            cursor.execute(
                "SELECT DISTINCT [MALZEME TÜRÜ] FROM [GO3].[dbo].[MALZEME_STOK] WHERE [MALZEME TÜRÜ] IS NOT NULL ORDER BY [MALZEME TÜRÜ]")
            malzeme_turleri = [self.safe_decode_string(
                row[0]) for row in cursor.fetchall()]

            # Markalar
            cursor.execute(
                "SELECT DISTINCT [MARKA] FROM [GO3].[dbo].[MALZEME_STOK] WHERE [MARKA] IS NOT NULL ORDER BY [MARKA]")
            markalar = [self.safe_decode_string(
                row[0]) for row in cursor.fetchall()]

            conn.close()

            return {
                'malzeme_turleri': malzeme_turleri,
                'markalar': markalar
            }

        except Exception as e:
            logger.error(f"get_malzeme_stok_filter_options error: {e}")
            return {
                'malzeme_turleri': [],
                'markalar': []
            }

    def get_cari_hareketleri(self, cari_kod, start_date=None, end_date=None):
        """Cari hesap hareketlerini getirir - TUMCARIHARETLER tablosundan"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Base query
            query = """
                SELECT [LOGICALREF], [TARİH], [FATURANO], [FATURA TÜRÜ], [CARİ KOD], 
                       [CARİ ÜNVAN], [AÇIKLAMA], [BORÇ], [ALACAK], [BANKA]
                FROM [GO3].[dbo].[TUMCARIHARETLER] 
                WHERE [CARİ KOD] = ?
            """

            params = [cari_kod]

            # Tarih filtresi ekle
            if start_date:
                query += " AND [TARİH] >= ?"
                params.append(start_date)

            if end_date:
                query += " AND [TARİH] <= ?"
                params.append(end_date)

            query += " ORDER BY [TARİH] DESC, [LOGICALREF] DESC"

            cursor.execute(query, params)
            rows = cursor.fetchall()

            hareketler = []
            for row in rows:
                hareket = {
                    'logicalref': row[0],
                    'tarih': row[1],
                    'faturano': self.safe_decode_string(row[2]),
                    'fatura_turu': self.safe_decode_string(row[3]),
                    'cari_kod': self.safe_decode_string(row[4]),
                    'cari_unvan': self.safe_decode_string(row[5]),
                    'aciklama': self.safe_decode_string(row[6]),
                    'borc': float(row[7]) if row[7] else 0.0,
                    'alacak': float(row[8]) if row[8] else 0.0,
                    'banka': self.safe_decode_string(row[9])
                }
                hareketler.append(hareket)

            conn.close()
            return hareketler

        except Exception as e:
            logger.error(f"get_cari_hareketleri error: {e}")
            return []

    def get_cari_analizi(self, cari_kod, start_date=None, end_date=None):
        """Cari hesap için detaylı analiz yapar"""
        try:
            hareketler = self.get_cari_hareketleri(
                cari_kod, start_date, end_date)

            if not hareketler:
                return None

            # Cari bilgilerini al
            cari_unvan = hareketler[0]['cari_unvan'] if hareketler else "Bilinmeyen Cari"

            # Analiz verileri
            analiz = {
                'cari_kod': cari_kod,
                'cari_unvan': cari_unvan,
                'toplam_hareket': len(hareketler),
                'toplam_borc': 0,
                'toplam_alacak': 0,
                'bakiye': 0,
                'fatura_turleri': {},
                'odeme_turleri': {
                    'nakit': 0,
                    'kredi_karti': 0,
                    'havale': 0,
                    'cek': 0,
                    'diger': 0
                },
                'son_hareket_tarihi': None,
                'ilk_hareket_tarihi': None
            }

            # Hareket analizleri
            for hareket in hareketler:
                # Toplam borç/alacak
                analiz['toplam_borc'] += hareket['borc']
                analiz['toplam_alacak'] += hareket['alacak']

                # Fatura türü analizi
                fatura_turu = hareket['fatura_turu']
                if fatura_turu not in analiz['fatura_turleri']:
                    analiz['fatura_turleri'][fatura_turu] = {
                        'adet': 0,
                        'borc_toplam': 0,
                        'alacak_toplam': 0
                    }

                analiz['fatura_turleri'][fatura_turu]['adet'] += 1
                analiz['fatura_turleri'][fatura_turu]['borc_toplam'] += hareket['borc']
                analiz['fatura_turleri'][fatura_turu]['alacak_toplam'] += hareket['alacak']

                # Ödeme türü analizi
                if 'Kredi Kartı' in fatura_turu:
                    analiz['odeme_turleri']['kredi_karti'] += hareket['alacak']
                elif 'Havale' in fatura_turu or 'Gelen Havale' in fatura_turu:
                    analiz['odeme_turleri']['havale'] += hareket['alacak']
                elif 'Çek' in fatura_turu:
                    analiz['odeme_turleri']['cek'] += hareket['alacak']
                elif 'Nakit' in fatura_turu or 'Tahsilat' in fatura_turu:
                    analiz['odeme_turleri']['nakit'] += hareket['alacak']
                else:
                    analiz['odeme_turleri']['diger'] += hareket['alacak']

                # Tarih analizleri
                hareket_tarihi = hareket['tarih']
                if hareket_tarihi:
                    if not analiz['son_hareket_tarihi'] or hareket_tarihi > analiz['son_hareket_tarihi']:
                        analiz['son_hareket_tarihi'] = hareket_tarihi
                    if not analiz['ilk_hareket_tarihi'] or hareket_tarihi < analiz['ilk_hareket_tarihi']:
                        analiz['ilk_hareket_tarihi'] = hareket_tarihi

            # Bakiye hesapla (Borç - Alacak)
            analiz['bakiye'] = analiz['toplam_borc'] - analiz['toplam_alacak']

            return analiz

        except Exception as e:
            logger.error(f"get_cari_analizi error: {e}")
            return None

    def get_cari_listesi(self, search_term=None):
        """Cari hesap listesini getirir"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
                SELECT DISTINCT [CARİ KOD], [CARİ ÜNVAN]
                FROM [GO3].[dbo].[TUMCARIHARETLER] 
                WHERE [CARİ KOD] IS NOT NULL AND [CARİ ÜNVAN] IS NOT NULL
            """

            params = []
            if search_term:
                query += " AND ([CARİ KOD] LIKE ? OR [CARİ ÜNVAN] LIKE ?)"
                params.extend([f'%{search_term}%', f'%{search_term}%'])

            query += " ORDER BY [CARİ KOD]"

            cursor.execute(query, params)
            rows = cursor.fetchall()

            cariler = []
            for row in rows:
                cari = {
                    'cari_kod': self.safe_decode_string(row[0]),
                    'cari_unvan': self.safe_decode_string(row[1])
                }
                cariler.append(cari)

            conn.close()
            return cariler

        except Exception as e:
            logger.error(f"get_cari_listesi error: {e}")
            return []

    def get_cari_vade_analizi(self, cari_kod, start_date=None, end_date=None):
        """
        Carinin ödeme vade alışkanlığı analizini getirir
        45 günlük standart vade ve FIFO (First In, First Out) mantığı ile
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # 1. Önce tüm hareketleri (faturaları ve ödemeleri) alalım
            query = """
                SELECT 
                    t.[CARİ KOD] as cari_kod,
                    t.[CARİ ÜNVAN] as cari_unvan,
                    t.[FATURANO] as fatura_no,
                    t.[TARİH] as tarih,
                    t.[BORÇ] as borc,
                    t.[ALACAK] as alacak,
                    t.[AÇIKLAMA] as aciklama,
                    ISNULL(t.[FATURA TÜRÜ], 'Genel') as fatura_turu,
                    CASE 
                        WHEN t.[BORÇ] > 0 THEN 'FATURA'
                        WHEN t.[ALACAK] > 0 THEN 'ODEME'
                        ELSE 'DİĞER'
                    END as hareket_tipi
                FROM [GO3].[dbo].[TUMCARIHARETLER] t
                WHERE t.[CARİ KOD] = ?
            """

            params = [cari_kod]

            if start_date:
                query += " AND t.[TARİH] >= ?"
                params.append(start_date)
            if end_date:
                query += " AND t.[TARİH] <= ?"
                params.append(end_date)

            query += " ORDER BY t.[TARİH] ASC, t.[FATURANO] ASC"

            cursor.execute(query, params)
            rows = cursor.fetchall()

            # Ham verileri işle
            hareketler = []
            for row in rows:
                hareket = {
                    'cari_kod': self.safe_decode_string(row[0]),
                    'cari_unvan': self.safe_decode_string(row[1]),
                    'fatura_no': self.safe_decode_string(row[2]),
                    'tarih': row[3],
                    'borc': float(row[4]) if row[4] else 0,
                    'alacak': float(row[5]) if row[5] else 0,
                    'aciklama': self.safe_decode_string(row[6]),
                    'fatura_turu': self.safe_decode_string(row[7]),
                    'hareket_tipi': self.safe_decode_string(row[8])
                }
                hareketler.append(hareket)

            # FIFO mantığı ile vade analizini hesapla
            vade_detaylari = self._calculate_fifo_vade_analizi(hareketler)

            # Özet istatistikleri hesapla
            vade_ozeti = self._calculate_vade_ozeti(vade_detaylari)

            conn.close()

            return {
                'vade_detaylari': vade_detaylari,
                'vade_ozeti': vade_ozeti
            }

        except Exception as e:
            logger.error(f"get_cari_vade_analizi error: {e}")
            return None

    def _calculate_fifo_vade_analizi(self, hareketler):
        """FIFO mantığı ile vade analizi hesaplar"""
        try:
            from datetime import datetime, timedelta

            # Faturaları ve ödemeleri ayır
            faturalar = []
            odemeler = []

            for hareket in hareketler:
                if hareket['hareket_tipi'] == 'FATURA' and hareket['borc'] > 0:
                    fatura = {
                        'fatura_no': hareket['fatura_no'],
                        'fatura_tarihi': hareket['tarih'],
                        'fatura_tutari': hareket['borc'],
                        'fatura_turu': hareket['fatura_turu'],
                        'vade_tarihi': hareket['tarih'] + timedelta(days=45),
                        'kalan_tutar': hareket['borc'],  # Ödenmemiş kalan
                        'odeme_detaylari': [],  # Bu faturayı kapatan ödemeler
                        'cari_kod': hareket['cari_kod'],
                        'cari_unvan': hareket['cari_unvan']
                    }
                    faturalar.append(fatura)

                elif hareket['hareket_tipi'] == 'ODEME' and hareket['alacak'] > 0:
                    odeme = {
                        'odeme_tarihi': hareket['tarih'],
                        'odeme_tutari': hareket['alacak'],
                        # Henüz kullanılmamış kısmı
                        'kalan_tutar': hareket['alacak'],
                        'aciklama': hareket['aciklama']
                    }
                    odemeler.append(odeme)

            # FIFO mantığı ile faturaları ödemelerle eşleştir
            for odeme in odemeler:
                for fatura in faturalar:
                    if odeme['kalan_tutar'] <= 0 or fatura['kalan_tutar'] <= 0:
                        continue

                    # Bu ödeme bu faturayı kapatabilir mi?
                    # Tarih tiplerini normalize et
                    odeme_tarihi = odeme['odeme_tarihi']
                    if hasattr(odeme_tarihi, 'date'):
                        odeme_tarihi = odeme_tarihi.date()

                    fatura_tarihi = fatura['fatura_tarihi']
                    if hasattr(fatura_tarihi, 'date'):
                        fatura_tarihi = fatura_tarihi.date()

                    vade_tarihi = fatura['vade_tarihi']
                    if hasattr(vade_tarihi, 'date'):
                        vade_tarihi = vade_tarihi.date()

                    if odeme_tarihi >= fatura_tarihi:
                        # Ödeme tutarı belirleme
                        odenen_tutar = min(
                            odeme['kalan_tutar'], fatura['kalan_tutar'])

                        # Ödeme detayını faturaya ekle
                        fatura['odeme_detaylari'].append({
                            'odeme_tarihi': odeme['odeme_tarihi'],
                            'odeme_tutari': odenen_tutar,
                            'vade_farki': (odeme_tarihi - vade_tarihi).days
                        })

                        # Kalan tutarları güncelle
                        fatura['kalan_tutar'] -= odenen_tutar
                        odeme['kalan_tutar'] -= odenen_tutar

            # Sonuçları formatla
            vade_detaylari = []
            for fatura in faturalar:
                if fatura['kalan_tutar'] <= 0:
                    # Tamamen ödenmiş fatura
                    son_odeme = max(
                        fatura['odeme_detaylari'], key=lambda x: x['odeme_tarihi'])
                    odeme_durumu = 'Ödendi'
                    odeme_tarihi = son_odeme['odeme_tarihi']
                    vade_farki_gun = son_odeme['vade_farki']
                else:
                    # Kısmen ödenmiş veya ödenmemiş fatura
                    if fatura['odeme_detaylari']:
                        odeme_durumu = f"Kısmen Ödendi (%{((fatura['fatura_tutari'] - fatura['kalan_tutar']) / fatura['fatura_tutari'] * 100):.1f})"
                        son_odeme = max(
                            fatura['odeme_detaylari'], key=lambda x: x['odeme_tarihi'])
                        odeme_tarihi = son_odeme['odeme_tarihi']
                        vade_farki_gun = son_odeme['vade_farki']
                    else:
                        odeme_durumu = 'Ödenmedi'
                        odeme_tarihi = None
                        # Vade tarihi tipini kontrol et ve uyumlu hale getir
                        if hasattr(fatura['vade_tarihi'], 'date'):
                            vade_tarihi = fatura['vade_tarihi'].date()
                        else:
                            vade_tarihi = fatura['vade_tarihi']
                        vade_farki_gun = (
                            datetime.now().date() - vade_tarihi).days

                # Gecikme kategorisi belirleme
                if vade_farki_gun <= 0:
                    gecikme_kategorisi = 'Zamanında'
                elif vade_farki_gun <= 15:
                    gecikme_kategorisi = '1-15 Gün Geç'
                elif vade_farki_gun <= 30:
                    gecikme_kategorisi = '16-30 Gün Geç'
                elif vade_farki_gun <= 60:
                    gecikme_kategorisi = '31-60 Gün Geç'
                else:
                    gecikme_kategorisi = '60+ Gün Geç'

                detay = {
                    'cari_kod': fatura['cari_kod'],
                    'cari_unvan': fatura['cari_unvan'],
                    'fatura_no': fatura['fatura_no'],
                    'fatura_tarihi': fatura['fatura_tarihi'],
                    'fatura_tutari': fatura['fatura_tutari'],
                    'fatura_turu': fatura['fatura_turu'],
                    'vade_tarihi': fatura['vade_tarihi'],
                    'odeme_tarihi': odeme_tarihi,
                    'odeme_durumu': odeme_durumu,
                    'vade_farki_gun': vade_farki_gun,
                    'gecikme_kategorisi': gecikme_kategorisi,
                    'kalan_tutar': fatura['kalan_tutar'],
                    'odenen_tutar': fatura['fatura_tutari'] - fatura['kalan_tutar'],
                    'odeme_sayisi': len(fatura['odeme_detaylari']),
                    'borc': fatura['fatura_tutari'],
                    'alacak': fatura['fatura_tutari'] - fatura['kalan_tutar']
                }
                vade_detaylari.append(detay)

            # Tarihe göre sırala (en yeni en üstte)
            vade_detaylari.sort(key=lambda x: x['fatura_tarihi'], reverse=True)

            return vade_detaylari

        except Exception as e:
            logger.error(f"_calculate_fifo_vade_analizi error: {e}")
            return []

    def _calculate_vade_ozeti(self, vade_detaylari):
        """Vade analizi özet istatistiklerini hesaplar"""
        if not vade_detaylari:
            return {}

        try:
            # Temel istatistikler
            toplam_fatura = len(vade_detaylari)
            odenen_faturalar = [
                v for v in vade_detaylari if v['odeme_durumu'] == 'Ödendi']
            odenmemis_faturalar = [
                v for v in vade_detaylari if v['odeme_durumu'] == 'Ödenmedi']

            # Ödeme performansı
            odeme_orani = (len(odenen_faturalar) /
                           toplam_fatura * 100) if toplam_fatura > 0 else 0

            # Gecikme kategorileri analizi
            gecikme_kategorileri = {}
            for detay in odenen_faturalar:  # Sadece ödenen faturaları analiz et
                kategori = detay['gecikme_kategorisi']
                if kategori not in gecikme_kategorileri:
                    gecikme_kategorileri[kategori] = {'adet': 0, 'tutar': 0}
                gecikme_kategorileri[kategori]['adet'] += 1
                gecikme_kategorileri[kategori]['tutar'] += detay['fatura_tutari']

            # Ortalama ödeme süresi (sadece ödenen faturalar)
            if odenen_faturalar:
                toplam_vade_farki = sum([v['vade_farki_gun']
                                        for v in odenen_faturalar])
                ortalama_odeme_suresi = toplam_vade_farki / \
                    len(odenen_faturalar)
            else:
                ortalama_odeme_suresi = 0

            # Zamanında ödeme oranı
            zamaninda_odenen = len(
                [v for v in odenen_faturalar if v['vade_farki_gun'] <= 0])
            zamaninda_odeme_orani = (
                zamaninda_odenen / len(odenen_faturalar) * 100) if odenen_faturalar else 0

            # Risk kategorisi belirleme
            if zamaninda_odeme_orani >= 80:
                risk_kategorisi = 'Düşük Risk'
                risk_renk = 'success'
            elif zamaninda_odeme_orani >= 60:
                risk_kategorisi = 'Orta Risk'
                risk_renk = 'warning'
            else:
                risk_kategorisi = 'Yüksek Risk'
                risk_renk = 'danger'

            # Tutar bazlı analizler
            toplam_fatura_tutari = sum(
                [v['fatura_tutari'] for v in vade_detaylari])
            odenen_tutar = sum([v['fatura_tutari'] for v in odenen_faturalar])
            odenmemis_tutar = sum([v['fatura_tutari']
                                  for v in odenmemis_faturalar])

            return {
                'toplam_fatura': toplam_fatura or 0,
                'odenen_fatura_sayisi': len(odenen_faturalar) if odenen_faturalar else 0,
                'odenmemis_fatura_sayisi': len(odenmemis_faturalar) if odenmemis_faturalar else 0,
                'odeme_orani': round(odeme_orani or 0, 2),
                'gecikme_kategorileri': gecikme_kategorileri or {},
                'ortalama_odeme_suresi': round(ortalama_odeme_suresi or 0, 1),
                'zamaninda_odeme_orani': round(zamaninda_odeme_orani or 0, 2),
                'risk_kategorisi': risk_kategorisi or 'Belirsiz',
                'risk_renk': risk_renk or '#808080',
                'toplam_fatura_tutari': toplam_fatura_tutari or 0,
                'odenen_tutar': odenen_tutar or 0,
                'odenmemis_tutar': odenmemis_tutar or 0,
                'tutar_odeme_orani': round((odenen_tutar / toplam_fatura_tutari * 100) if toplam_fatura_tutari and toplam_fatura_tutari > 0 else 0, 2)
            }

        except Exception as e:
            logger.error(f"_calculate_vade_ozeti error: {e}")
            return {}

    # --- Added: Fetch single tahsilat by ID without user filter ---
    def get_tahsilat_by_id_any(self, tahsilat_id: int):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            query = """
                SELECT TOP 1
                    Id,
                    CariKod,
                    CariUnvan,
                    Tutar,
                    Tarih,
                    Aciklama,
                    Durum,
                    Plasiyer,
                    Kullanici,
                    BANKAADI as Banka,
                    Taksit,
                    EvrakNo,
                    TahsilatTuru
                FROM [GO3].[dbo].[GunlukTahsilat_V]
                WHERE Id = ?
            """
            cursor.execute(query, (tahsilat_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                'Id': row[0],
                'CariKod': self.safe_decode_string(row[1]),
                'CariUnvan': self.safe_decode_string(row[2]),
                'Tutar': float(row[3]) if row[3] is not None else 0.0,
                'Tarih': row[4],
                'Aciklama': self.safe_decode_string(row[5]),
                'Durum': self.safe_decode_string(row[6]),
                'Plasiyer': self.safe_decode_string(row[7]),
                'Kullanici': self.safe_decode_string(row[8]),
                'Banka': self.safe_decode_string(row[9]),
                'Taksit': row[10],
                'EvrakNo': self.safe_decode_string(row[11]),
                'TahsilatTuru': self.safe_decode_string(row[12]) if row[12] is not None else None,
            }
        except Exception as e:
            logger.error(f"get_tahsilat_by_id_any error: {e}")
            return None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # --- Added: Update tahsilat record in TAHSILATTB with permission rules ---
    def update_tahsilat(self, tahsilat_id: int, username: str, tahsilat_turu=None, banka=None,
                        tutar=None, tarih=None, aciklama=None, taksit=None, evrak_no=None, cari_kod=None, force: bool = False):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Mevcut kaydı TAHSILATTB'den al (güncelleme için)
            cursor.execute("""
                SELECT TOP 1 Id, Kullanici, Durum
                FROM [GO3].[dbo].[TAHSILATTB]
                WHERE Id = ?
            """, (tahsilat_id,))
            row = cursor.fetchone()
            if not row:
                return {'success': False, 'error': 'Kayıt bulunamadı'}

            mevcut_kullanici = self.safe_decode_string(row[1]) if row[1] is not None else ''
            mevcut_durum = self.safe_decode_string(row[2]) if row[2] is not None else ''

            # Yetki kontrolü: FIRAT (force) her zaman, diğerleri sadece kendi kaydı ve İŞLENMEDİ
            if not force:
                if mevcut_kullanici.upper() != (username or '').upper():
                    return {'success': False, 'error': 'Bu kaydı güncelleme yetkiniz yok'}
                if mevcut_durum and mevcut_durum.upper() != 'İŞLENMEDİ':
                    return {'success': False, 'error': 'Sadece İŞLENMEDİ durumundaki kayıtlar güncellenebilir'}

            # Dinamik SET listesi hazırla
            set_clauses = []
            params = []
            # Cari güncelleme (CariID)
            if cari_kod is not None and str(cari_kod).strip() != '':
                try:
                    # LOGICALREF'i CLCARD tablosundan bul (CODE ile)
                    cursor.execute("""
                        SELECT TOP 1 LOGICALREF FROM [GO3].[dbo].[CLCARD] WHERE CODE = ?
                    """, (cari_kod.strip(),))
                    ref_row = cursor.fetchone()
                    if ref_row and ref_row[0]:
                        set_clauses.append("CariID = ?")
                        params.append(int(ref_row[0]))
                except Exception as ref_e:
                    # Sessiz atla; diğer alanlar güncellensin
                    logger.warning(f"CariID resolve failed for {cari_kod}: {ref_e}")

            if tahsilat_turu is not None and str(tahsilat_turu).strip() != '':
                set_clauses.append("TahsilatTuru = ?")
                params.append(tahsilat_turu)
            if banka is not None:
                set_clauses.append("Banka = ?")
                params.append(banka)
            if taksit is not None and str(taksit).strip() != '':
                try:
                    params.append(int(taksit))
                    set_clauses.append("Taksit = ?")
                except Exception:
                    pass
            if tutar is not None and str(tutar).strip() != '':
                try:
                    params.append(float(tutar))
                    set_clauses.append("Tutar = ?")
                except Exception:
                    pass
            if tarih is not None and str(tarih).strip() != '':
                set_clauses.append("Tarih = ?")
                params.append(tarih)
            if aciklama is not None:
                set_clauses.append("Aciklama = ?")
                params.append(aciklama)
            if evrak_no is not None:
                set_clauses.append("EvrakNo = ?")
                params.append(evrak_no)

            if not set_clauses:
                return {'success': True, 'message': 'Güncellenecek alan yok'}

            update_sql = f"""
                UPDATE [GO3].[dbo].[TAHSILATTB]
                SET {', '.join(set_clauses)}
                WHERE Id = ?
            """
            params.append(tahsilat_id)

            cursor.execute(update_sql, params)
            conn.commit()
            return {'success': True}
        except Exception as e:
            logger.error(f"update_tahsilat error: {e}")
            return {'success': False, 'error': str(e)}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def get_malzeme_satis_detay_all(self, baslangic_tarihi=None, bitis_tarihi=None, plasiyer=None, cari_kod=None, cari_unvan=None, malzeme_kodu=None, malzeme_aciklama=None):
        """DETAY tablosundan tüm malzeme satış detaylarını getirir - sayfalama olmadan - sadece dolu parametrelerle filtreleme"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Base query
            base_query = """
                  SELECT [TARİH], [MALZEME KODU], [AÇIKLAMASI], [MARKA], [MALZEME TÜRÜ],
                      [MİKTAR], [BİRİM], [BİRİM BRÜT], [BİRİM İNDİRİM], [BİRİM NET], [B2B], [FARK],
                      [TOPLAM İNDİRİM], [KDV TUTARI], [NET TOPLAM], [FATURA NO],
                      [CARİ KOD], [CARİ ÜNVAN], [PLASİYER], [BÖLGE]
                FROM [GO3].[dbo].[DETAY] 
                WHERE (TRCODE = 8 OR TRCODE = 7)
            """

            params = []

            # Sadece dolu parametreler için filtreleme koşulları ekle
            if baslangic_tarihi and baslangic_tarihi.strip():
                base_query += " AND [TARİH] >= ?"
                params.append(baslangic_tarihi)

            if bitis_tarihi and bitis_tarihi.strip():
                base_query += " AND [TARİH] <= ?"
                params.append(bitis_tarihi)

            if plasiyer and plasiyer.strip():
                base_query += " AND [PLASİYER] = ?"
                params.append(plasiyer)

            if cari_kod and cari_kod.strip():
                base_query += " AND [CARİ KOD] LIKE ?"
                params.append(f'%{cari_kod}%')

            if cari_unvan and cari_unvan.strip():
                base_query += " AND [CARİ ÜNVAN] LIKE ?"
                params.append(f'%{cari_unvan}%')

            if malzeme_kodu and malzeme_kodu.strip():
                base_query += " AND [MALZEME KODU] LIKE ?"
                params.append(f'%{malzeme_kodu}%')

            if malzeme_aciklama and malzeme_aciklama.strip():
                base_query += " AND [AÇIKLAMASI] LIKE ?"
                params.append(f'%{malzeme_aciklama}%')

            # Tüm kayıtları al - sayfalama yok
            main_query = base_query + " ORDER BY [TARİH] DESC"

            cursor.execute(main_query, params)
            rows = cursor.fetchall()

            # Sonuçları format et
            data = []
            for row in rows:
                item = {
                    'TARİH': row[0].strftime('%d.%m.%Y') if row[0] else '-',
                    'MALZEME_KODU': self.safe_decode_string(row[1]),
                    'AÇIKLAMASI': self.safe_decode_string(row[2]),
                    'MARKA': self.safe_decode_string(row[3]),
                    'MALZEME_TÜRÜ': self.safe_decode_string(row[4]),
                    'MİKTAR': float(row[5]) if row[5] else 0,
                    'BİRİM': self.safe_decode_string(row[6]),
                    'BİRİM_BRÜT': float(row[7]) if row[7] else 0,
                    'BİRİM_İNDİRİM': float(row[8]) if row[8] else 0,
                    'BİRİM_NET': float(row[9]) if row[9] else 0,
                    'B2B': float(row[10]) if row[10] else 0,
                    'FARK': float(row[11]) if row[11] else 0,
                    'TOPLAM_İNDİRİM': float(row[12]) if row[12] else 0,
                    'KDV_TUTARI': float(row[13]) if row[13] else 0,
                    'NET_TOPLAM': float(row[14]) if row[14] else 0,
                    'FATURA_NO': self.safe_decode_string(row[15]),
                    'CARİ_KOD': self.safe_decode_string(row[16]),
                    'CARİ_ÜNVAN': self.safe_decode_string(row[17]),
                    'PLASİYER': self.safe_decode_string(row[18]),
                    'BÖLGE': self.safe_decode_string(row[19])
                }
                data.append(item)

            conn.close()

            return {
                'data': data,
                'total_count': len(data),
                'total_pages': 1,
                'current_page': 1,
                'has_next': False,
                'has_previous': False
            }

        except Exception as e:
            logger.error(f"get_malzeme_satis_detay_all error: {e}")
            return {
                'data': [],
                'total_count': 0,
                'total_pages': 0,
                'current_page': 1,
                'has_next': False,
                'has_previous': False
            }

    def get_plasiyerler_list(self):
        """Mevcut plasiyerler listesini getirir"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
                SELECT DISTINCT [PLASİYER]
                FROM [GO3].[dbo].[DETAY] 
                WHERE [PLASİYER] IS NOT NULL AND [PLASİYER] != ''
                ORDER BY [PLASİYER]
            """

            cursor.execute(query)
            rows = cursor.fetchall()

            plasiyerler = [self.safe_decode_string(
                row[0]) for row in rows if row[0]]

            conn.close()
            return plasiyerler

        except Exception as e:
            logger.error(f"get_plasiyerler_list error: {e}")
            return []

    def get_stok_satis_analiz_data(self, search_term=None, malzeme_turu=None, marka=None,
                                   plasiyer=None, start_date=None, end_date=None, max_records=1000):
        """
        Stok Satış Analizi için DETAY tablosundan veri çeker
        TRCODE: 7,8 = Satış Faturası | 1,3 = İade Faturası
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Ana sorgu - TRCODE ile işlem tipi eklendi
            if max_records:
                base_query = f"""
                SELECT TOP {max_records}
                    [TARİH],
                    [MALZEME KODU],
                    [AÇIKLAMASI],
                    [MARKA],
                    [MALZEME TÜRÜ],
                    [MİKTAR],
                    [NET TOPLAM],
                    [CARİ KOD],
                    [CARİ ÜNVAN],
                    [PLASİYER],
                    [TRCODE],
                    CASE 
                        WHEN TRCODE IN (7, 8) THEN 'Satış'
                        WHEN TRCODE IN (1, 3) THEN 'İade'
                        ELSE 'Diğer'
                    END as [İŞLEM TİPİ]
                FROM [GO3].[dbo].[DETAY] 
                WHERE TRCODE IN (1, 3, 7, 8)
                """
            else:
                base_query = """
                SELECT 
                    [TARİH],
                    [MALZEME KODU],
                    [AÇIKLAMASI],
                    [MARKA],
                    [MALZEME TÜRÜ],
                    [MİKTAR],
                    [NET TOPLAM],
                    [CARİ KOD],
                    [CARİ ÜNVAN],
                    [PLASİYER],
                    [TRCODE],
                    CASE 
                        WHEN TRCODE IN (7, 8) THEN 'Satış'
                        WHEN TRCODE IN (1, 3) THEN 'İade'
                        ELSE 'Diğer'
                    END as [İŞLEM TİPİ]
                FROM [GO3].[dbo].[DETAY] 
                WHERE TRCODE IN (1, 3, 7, 8)
                """

            conditions = []
            params = []

            # Filtreleme koşulları
            if search_term:
                conditions.append(
                    "([AÇIKLAMASI] LIKE ? OR [MALZEME KODU] LIKE ?)")
                params.extend([f"%{search_term}%", f"%{search_term}%"])

            if malzeme_turu:
                conditions.append("[MALZEME TÜRÜ] = ?")
                params.append(malzeme_turu)

            if marka:
                conditions.append("[MARKA] = ?")
                params.append(marka)

            if plasiyer:
                conditions.append("[PLASİYER] = ?")
                params.append(plasiyer)

            if start_date:
                conditions.append("[TARİH] >= ?")
                params.append(start_date)

            if end_date:
                conditions.append("[TARİH] <= ?")
                params.append(end_date)

            # Koşulları ekle
            if conditions:
                base_query += " AND " + " AND ".join(conditions)

            # Sıralama ekle
            base_query += " ORDER BY [TARİH] DESC"

            logger.info(f"Executing query with {len(params)} parameters")
            cursor.execute(base_query, params)
            rows = cursor.fetchall()

            results = []
            for row in rows:
                result = {
                    'tarih': row[0] if row[0] else None,
                    'malzeme_kodu': self.safe_decode_string(row[1]),
                    'aciklamasi': self.safe_decode_string(row[2]),
                    'marka': self.safe_decode_string(row[3]),
                    'malzeme_turu': self.safe_decode_string(row[4]),
                    'miktar': float(row[5]) if row[5] else 0,
                    'net_toplam': float(row[6]) if row[6] else 0,
                    'cari_kod': self.safe_decode_string(row[7]),
                    'cari_unvan': self.safe_decode_string(row[8]),
                    'plasiyer': self.safe_decode_string(row[9]),
                    'trcode': row[10] if row[10] else 0,
                    'islem_tipi': self.safe_decode_string(row[11])
                }
                results.append(result)

            conn.close()
            logger.info(f"Retrieved {len(results)} records successfully")
            return results

        except Exception as e:
            logger.error(f"get_stok_satis_analiz_data error: {e}")
            return []

    def get_stok_satis_analiz_stats(self, search_term=None, malzeme_turu=None, marka=None,
                                    plasiyer=None, start_date=None, end_date=None):
        """
        Stok Satış Analizi için detaylı özet istatistikler
        TRCODE: 7,8 = Satış | 1,3 = İade
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Detaylı özet istatistikler sorgusu
            query = """
            SELECT 
                -- Genel İstatistikler
                COUNT(*) as toplam_kayit,
                COUNT(DISTINCT [MALZEME KODU]) as benzersiz_malzeme,
                COUNT(DISTINCT [CARİ KOD]) as benzersiz_musteri,
                COUNT(DISTINCT [PLASİYER]) as benzersiz_plasiyer,
                
                -- Satış İstatistikleri (TRCODE 7,8)
                COUNT(CASE WHEN TRCODE IN (7,8) THEN 1 END) as satis_adet,
                SUM(CASE WHEN TRCODE IN (7,8) THEN [MİKTAR] ELSE 0 END) as satis_miktar,
                SUM(CASE WHEN TRCODE IN (7,8) THEN [NET TOPLAM] ELSE 0 END) as satis_tutar,
                AVG(CASE WHEN TRCODE IN (7,8) THEN [NET TOPLAM] END) as satis_ortalama,
                
                -- İade İstatistikleri (TRCODE 1,3)
                COUNT(CASE WHEN TRCODE IN (1,3) THEN 1 END) as iade_adet,
                SUM(CASE WHEN TRCODE IN (1,3) THEN [MİKTAR] ELSE 0 END) as iade_miktar,
                SUM(CASE WHEN TRCODE IN (1,3) THEN [NET TOPLAM] ELSE 0 END) as iade_tutar,
                AVG(CASE WHEN TRCODE IN (1,3) THEN [NET TOPLAM] END) as iade_ortalama,
                
                -- Net Hesaplamalar (Satış - İade)
                SUM(CASE WHEN TRCODE IN (7,8) THEN [NET TOPLAM] ELSE 0 END) - 
                SUM(CASE WHEN TRCODE IN (1,3) THEN [NET TOPLAM] ELSE 0 END) as net_tutar,
                
                SUM(CASE WHEN TRCODE IN (7,8) THEN [MİKTAR] ELSE 0 END) - 
                SUM(CASE WHEN TRCODE IN (1,3) THEN [MİKTAR] ELSE 0 END) as net_miktar
                
            FROM [GO3].[dbo].[DETAY] 
            WHERE TRCODE IN (1, 3, 7, 8)
            """

            params = []

            # Filtreleme koşulları (aynı filtreleri uygula)
            if search_term:
                query += " AND ([AÇIKLAMASI] LIKE ? OR [MALZEME KODU] LIKE ?)"
                params.extend([f"%{search_term}%", f"%{search_term}%"])

            if malzeme_turu:
                query += " AND [MALZEME TÜRÜ] = ?"
                params.append(malzeme_turu)

            if marka:
                query += " AND [MARKA] = ?"
                params.append(marka)

            if plasiyer:
                query += " AND [PLASİYER] = ?"
                params.append(plasiyer)

            if start_date:
                query += " AND [TARİH] >= ?"
                params.append(start_date)

            if end_date:
                query += " AND [TARİH] <= ?"
                params.append(end_date)

            cursor.execute(query, params)
            row = cursor.fetchone()

            # Detaylı istatistikler
            satis_tutar = float(row[6]) if row[6] else 0
            iade_tutar = float(row[10]) if row[10] else 0
            net_tutar = float(row[14]) if row[14] else 0

            # İade oranı hesaplama
            iade_orani = (iade_tutar / satis_tutar *
                          100) if satis_tutar > 0 else 0

            stats = {
                # Genel
                'toplam_kayit': row[0] if row[0] else 0,
                'benzersiz_malzeme': row[1] if row[1] else 0,
                'benzersiz_musteri': row[2] if row[2] else 0,
                'benzersiz_plasiyer': row[3] if row[3] else 0,

                # Satış
                'satis_adet': row[4] if row[4] else 0,
                'satis_miktar': float(row[5]) if row[5] else 0,
                'satis_tutar': satis_tutar,
                'satis_ortalama': float(row[7]) if row[7] else 0,

                # İade
                'iade_adet': row[8] if row[8] else 0,
                'iade_miktar': float(row[9]) if row[9] else 0,
                'iade_tutar': iade_tutar,
                'iade_ortalama': float(row[11]) if row[11] else 0,

                # Net
                'net_tutar': net_tutar,
                'net_miktar': float(row[15]) if row[15] else 0,
                'iade_orani': iade_orani
            }

            conn.close()
            return stats

        except Exception as e:
            logger.error(f"get_stok_satis_analiz_stats error: {e}")
            return {}

    def get_top_satis_urunler(self, limit=10, start_date=None, end_date=None):
        """En çok satılan ürünleri getirir (TRCODE 7,8)"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = f"""
            SELECT TOP {limit}
                [MALZEME KODU],
                [AÇIKLAMASI],
                [MARKA],
                COUNT(*) as islem_sayisi,
                SUM([MİKTAR]) as toplam_miktar,
                SUM([NET TOPLAM]) as toplam_tutar
            FROM [GO3].[dbo].[DETAY]
            WHERE TRCODE IN (7, 8)
            """

            params = []
            if start_date:
                query += " AND [TARİH] >= ?"
                params.append(start_date)
            if end_date:
                query += " AND [TARİH] <= ?"
                params.append(end_date)

            query += """
            GROUP BY [MALZEME KODU], [AÇIKLAMASI], [MARKA]
            ORDER BY toplam_tutar DESC
            """

            cursor.execute(query, params)
            rows = cursor.fetchall()

            results = []
            for row in rows:
                results.append({
                    'malzeme_kodu': self.safe_decode_string(row[0]),
                    'aciklamasi': self.safe_decode_string(row[1]),
                    'marka': self.safe_decode_string(row[2]),
                    'islem_sayisi': row[3],
                    'toplam_miktar': float(row[4]) if row[4] else 0,
                    'toplam_tutar': float(row[5]) if row[5] else 0
                })

            conn.close()
            return results
        except Exception as e:
            logger.error(f"get_top_satis_urunler error: {e}")
            return []

    def get_top_iade_urunler(self, limit=10, start_date=None, end_date=None):
        """En çok iade edilen ürünleri getirir (TRCODE 1,3)"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = f"""
            SELECT TOP {limit}
                [MALZEME KODU],
                [AÇIKLAMASI],
                [MARKA],
                COUNT(*) as islem_sayisi,
                SUM([MİKTAR]) as toplam_miktar,
                SUM([NET TOPLAM]) as toplam_tutar
            FROM [GO3].[dbo].[DETAY]
            WHERE TRCODE IN (1, 3)
            """

            params = []
            if start_date:
                query += " AND [TARİH] >= ?"
                params.append(start_date)
            if end_date:
                query += " AND [TARİH] <= ?"
                params.append(end_date)

            query += """
            GROUP BY [MALZEME KODU], [AÇIKLAMASI], [MARKA]
            ORDER BY toplam_tutar DESC
            """

            cursor.execute(query, params)
            rows = cursor.fetchall()

            results = []
            for row in rows:
                results.append({
                    'malzeme_kodu': self.safe_decode_string(row[0]),
                    'aciklamasi': self.safe_decode_string(row[1]),
                    'marka': self.safe_decode_string(row[2]),
                    'islem_sayisi': row[3],
                    'toplam_miktar': float(row[4]) if row[4] else 0,
                    'toplam_tutar': float(row[5]) if row[5] else 0
                })

            conn.close()
            return results
        except Exception as e:
            logger.error(f"get_top_iade_urunler error: {e}")
            return []

    def get_plasiyer_performans(self, start_date=None, end_date=None):
        """Plasiyer bazında satış ve iade performansı"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
            SELECT 
                [PLASİYER],
                COUNT(CASE WHEN TRCODE IN (7,8) THEN 1 END) as satis_adet,
                SUM(CASE WHEN TRCODE IN (7,8) THEN [NET TOPLAM] ELSE 0 END) as satis_tutar,
                COUNT(CASE WHEN TRCODE IN (1,3) THEN 1 END) as iade_adet,
                SUM(CASE WHEN TRCODE IN (1,3) THEN [NET TOPLAM] ELSE 0 END) as iade_tutar,
                SUM(CASE WHEN TRCODE IN (7,8) THEN [NET TOPLAM] ELSE 0 END) - 
                SUM(CASE WHEN TRCODE IN (1,3) THEN [NET TOPLAM] ELSE 0 END) as net_tutar
            FROM [GO3].[dbo].[DETAY]
            WHERE TRCODE IN (1, 3, 7, 8) AND [PLASİYER] IS NOT NULL
            """

            params = []
            if start_date:
                query += " AND [TARİH] >= ?"
                params.append(start_date)
            if end_date:
                query += " AND [TARİH] <= ?"
                params.append(end_date)

            query += " GROUP BY [PLASİYER] ORDER BY net_tutar DESC"

            cursor.execute(query, params)
            rows = cursor.fetchall()

            results = []
            for row in rows:
                satis_tutar = float(row[2]) if row[2] else 0
                iade_tutar = float(row[4]) if row[4] else 0
                iade_orani = (iade_tutar / satis_tutar *
                              100) if satis_tutar > 0 else 0

                results.append({
                    'plasiyer': self.safe_decode_string(row[0]),
                    'satis_adet': row[1],
                    'satis_tutar': satis_tutar,
                    'iade_adet': row[3],
                    'iade_tutar': iade_tutar,
                    'net_tutar': float(row[5]) if row[5] else 0,
                    'iade_orani': iade_orani
                })

            conn.close()
            return results
        except Exception as e:
            logger.error(f"get_plasiyer_performans error: {e}")
            return []

# Global servis instance
    def get_malzeme_satinalma_detay(self, baslangic_tarihi='', bitis_tarihi='', plasiyer='', cari_kod='', cari_unvan='', malzeme_kodu='', malzeme_aciklama='', page=1, page_size=50):
        """STLINE ve STFICHE tablolarından satınalma detaylarını getirir - TRCODE=0 ve CARİ KOD LIKE '320.%' filtreli"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Filtreleme koşullarını hazırla
            # Satınalma işlemleri ve 320 ile başlayan cariler
            where_conditions = ["s.TRCODE = 0", "c.CODE LIKE '320.%'"]
            params = []

            if baslangic_tarihi:
                where_conditions.append("f.DATE_ >= ?")
                params.append(baslangic_tarihi)
            if bitis_tarihi:
                where_conditions.append("f.DATE_ <= ?")
                params.append(bitis_tarihi)
            if plasiyer:
                where_conditions.append("f.SALESMANREF = ?")
                params.append(plasiyer)
            if cari_kod:
                where_conditions.append("c.CODE LIKE ?")
                params.append(f"%{cari_kod}%")
            if cari_unvan:
                where_conditions.append("c.DEFINITION_ LIKE ?")
                params.append(f"%{cari_unvan}%")
            if malzeme_kodu:
                where_conditions.append("i.CODE LIKE ?")
                params.append(f"%{malzeme_kodu}%")
            if malzeme_aciklama:
                where_conditions.append("i.NAME LIKE ?")
                params.append(f"%{malzeme_aciklama}%")

            # Toplam kayıt sayısını al
            count_query = f"""
                SELECT COUNT(*) as total_count
                FROM [GO3].[dbo].[STLINE] s
                INNER JOIN [GO3].[dbo].[STFICHE] f ON s.STFICHEREF = f.LOGICALREF
                INNER JOIN [GO3].[dbo].[ITEMS] i ON s.STOCKREF = i.LOGICALREF
                INNER JOIN [GO3].[dbo].[CLCARD] c ON f.CLIENTREF = c.LOGICALREF
                WHERE {" AND ".join(where_conditions)}
            """

            cursor.execute(count_query, params)
            total_count = cursor.fetchone()[0]

            # Sayfalama için offset hesapla
            offset = (page - 1) * page_size

            # Ana sorgu - sayfalama ile
            query = f"""
                SELECT 
                    f.DATE_ as TARIH,
                    c.CODE as CARI_KOD,
                    c.DEFINITION_ as CARI_UNVAN,
                    i.CODE as MALZEME_KODU,
                    i.NAME as MALZEME_ACIKLAMA,
                    s.AMOUNT as MIKTAR,
                    s.UOMCODE as BIRIM,
                    s.PRICE as BIRIM_FIYAT,
                    s.TOTAL as TUTAR,
                    s.VATAMNT as KDV,
                    s.TOTALTL as TOPLAM
                FROM [GO3].[dbo].[STLINE] s
                INNER JOIN [GO3].[dbo].[STFICHE] f ON s.STFICHEREF = f.LOGICALREF
                INNER JOIN [GO3].[dbo].[ITEMS] i ON s.STOCKREF = i.LOGICALREF
                INNER JOIN [GO3].[dbo].[CLCARD] c ON f.CLIENTREF = c.LOGICALREF
                WHERE {" AND ".join(where_conditions)}
                ORDER BY f.DATE_ DESC, c.CODE, i.CODE
                OFFSET ? ROWS
                FETCH NEXT ? ROWS ONLY
            """

            # Sayfalama parametrelerini ekle
            params.extend([offset, page_size])

            cursor.execute(query, params)
            rows = cursor.fetchall()

            # Sonuçları formatlı şekilde hazırla
            result = []
            for row in rows:
                result.append({
                    'TARIH': row[0],
                    'CARI_KOD': self.safe_decode_string(row[1]),
                    'CARI_UNVAN': self.safe_decode_string(row[2]),
                    'MALZEME_KODU': self.safe_decode_string(row[3]),
                    'MALZEME_ACIKLAMA': self.safe_decode_string(row[4]),
                    'MIKTAR': float(row[5] if row[5] is not None else 0),
                    'BIRIM': self.safe_decode_string(row[6]),
                    'BIRIM_FIYAT': float(row[7] if row[7] is not None else 0),
                    'TUTAR': float(row[8] if row[8] is not None else 0),
                    'KDV': float(row[9] if row[9] is not None else 0),
                    'TOPLAM': float(row[10] if row[10] is not None else 0),
                })

            # Sayfalama bilgileri
            total_pages = (total_count + page_size - 1) // page_size
            has_next = page < total_pages
            has_previous = page > 1

            return {
                'data': result,
                'total_count': total_count,
                'total_pages': total_pages,
                'current_page': page,
                'has_next': has_next,
                'has_previous': has_previous
            }

        except Exception as e:
            logger.error(f"get_malzeme_satinalma_detay error: {e}")
            raise e

    def get_satinalma_ozet_stats(self):
        """Satınalma özet istatistiklerini getirir - TRCODE=0 ve CARİ KOD LIKE '320.%' filtreli"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Temel filtreler (satınalma işlemleri ve 320 ile başlayan cariler)
            base_conditions = "s.TRCODE = 0 AND c.CODE LIKE '320.%'"

            # Günlük istatistikler
            gunluk_query = f"""
                SELECT COUNT(*) as adet, ISNULL(SUM(s.TOTALTL), 0) as tutar
                FROM [GO3].[dbo].[STLINE] s
                INNER JOIN [GO3].[dbo].[STFICHE] f ON s.STFICHEREF = f.LOGICALREF
                INNER JOIN [GO3].[dbo].[CLCARD] c ON f.CLIENTREF = c.LOGICALREF
                WHERE {base_conditions} AND CAST(f.DATE_ AS DATE) = CAST(GETDATE() AS DATE)
            """

            # Haftalık istatistikler
            haftalik_query = f"""
                SELECT COUNT(*) as adet, ISNULL(SUM(s.TOTALTL), 0) as tutar
                FROM [GO3].[dbo].[STLINE] s
                INNER JOIN [GO3].[dbo].[STFICHE] f ON s.STFICHEREF = f.LOGICALREF
                INNER JOIN [GO3].[dbo].[CLCARD] c ON f.CLIENTREF = c.LOGICALREF
                WHERE {base_conditions} AND f.DATE_ >= DATEADD(day, -7, CAST(GETDATE() AS DATE))
            """

            # Aylık istatistikler
            aylik_query = f"""
                SELECT COUNT(*) as adet, ISNULL(SUM(s.TOTALTL), 0) as tutar
                FROM [GO3].[dbo].[STLINE] s
                INNER JOIN [GO3].[dbo].[STFICHE] f ON s.STFICHEREF = f.LOGICALREF
                INNER JOIN [GO3].[dbo].[CLCARD] c ON f.CLIENTREF = c.LOGICALREF
                WHERE {base_conditions} AND f.DATE_ >= DATEADD(day, -30, CAST(GETDATE() AS DATE))
            """

            # Plasiyer bazlı istatistikler
            plasiyer_query = f"""
                SELECT 
                    p.CODE as plasiyer_kod,
                    p.NAME as plasiyer_ad,
                    COUNT(*) as islem_adet,
                    ISNULL(SUM(s.TOTALTL), 0) as toplam_tutar
                FROM [GO3].[dbo].[STLINE] s
                INNER JOIN [GO3].[dbo].[STFICHE] f ON s.STFICHEREF = f.LOGICALREF
                INNER JOIN [GO3].[dbo].[CLCARD] c ON f.CLIENTREF = c.LOGICALREF
                INNER JOIN [GO3].[dbo].[SLSMAN] p ON f.SALESMANREF = p.LOGICALREF
                WHERE {base_conditions} AND f.DATE_ >= DATEADD(day, -30, CAST(GETDATE() AS DATE))
                GROUP BY p.CODE, p.NAME
                ORDER BY toplam_tutar DESC
            """

            # İstatistikleri çek
            cursor.execute(gunluk_query)
            gunluk = cursor.fetchone()

            cursor.execute(haftalik_query)
            haftalik = cursor.fetchone()

            cursor.execute(aylik_query)
            aylik = cursor.fetchone()

            cursor.execute(plasiyer_query)
            plasiyer_rows = cursor.fetchall()

            # Toplam istatistikler
            toplam_stats = {
                'gunluk_adet': gunluk[0] if gunluk else 0,
                'gunluk_tutar': float(gunluk[1]) if gunluk and gunluk[1] else 0,
                'haftalik_adet': haftalik[0] if haftalik else 0,
                'haftalik_tutar': float(haftalik[1]) if haftalik and haftalik[1] else 0,
                'aylik_adet': aylik[0] if aylik else 0,
                'aylik_tutar': float(aylik[1]) if aylik and aylik[1] else 0,
            }

            # Plasiyer verileri
            plasiyer_verileri = []
            for row in plasiyer_rows:
                plasiyer_verileri.append({
                    'plasiyer_kod': self.safe_decode_string(row[0]),
                    'plasiyer_ad': self.safe_decode_string(row[1]),
                    'islem_adet': row[2],
                    'toplam_tutar': float(row[3]) if row[3] else 0
                })

            return {
                'toplam_stats': toplam_stats,
                'plasiyer_verileri': plasiyer_verileri
            }

        except Exception as e:
            logger.error(f"get_satinalma_ozet_stats error: {e}")
            raise e

    def get_satinalma_listesi_paginated(self, page=1, page_size=100):
        """Satınalma listesini sayfalı olarak getirir - TRCODE=0 ve CARİ KOD LIKE '320.%' filtreli"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Temel filtreler
            where_conditions = ["s.TRCODE = 0", "c.CODE LIKE '320.%'"]

            # Toplam kayıt sayısı
            count_query = f"""
                SELECT COUNT(DISTINCT f.LOGICALREF) as total_count
                FROM [GO3].[dbo].[STFICHE] f
                INNER JOIN [GO3].[dbo].[STLINE] s ON f.LOGICALREF = s.STFICHEREF
                INNER JOIN [GO3].[dbo].[CLCARD] c ON f.CLIENTREF = c.LOGICALREF
                WHERE {" AND ".join(where_conditions)}
            """

            cursor.execute(count_query)
            total_count = cursor.fetchone()[0]

            # Sayfalama için offset hesapla
            offset = (page - 1) * page_size

            # Ana sorgu
            query = f"""
                SELECT DISTINCT
                    f.DATE_ as tarih,
                    f.FICHENO as fatura_no,
                    c.CODE as cari_kod,
                    c.DEFINITION_ as cari_unvan,
                    p.NAME as plasiyer,
                    (SELECT COUNT(*) FROM [GO3].[dbo].[STLINE] WHERE STFICHEREF = f.LOGICALREF) as kalem_sayisi,
                    (SELECT SUM(AMOUNT) FROM [GO3].[dbo].[STLINE] WHERE STFICHEREF = f.LOGICALREF) as toplam_miktar,
                    (SELECT SUM(TOTAL) FROM [GO3].[dbo].[STLINE] WHERE STFICHEREF = f.LOGICALREF) as ara_toplam,
                    (SELECT SUM(VATAMNT) FROM [GO3].[dbo].[STLINE] WHERE STFICHEREF = f.LOGICALREF) as kdv_toplam,
                    (SELECT SUM(TOTALTL) FROM [GO3].[dbo].[STLINE] WHERE STFICHEREF = f.LOGICALREF) as genel_toplam
                FROM [GO3].[dbo].[STFICHE] f
                INNER JOIN [GO3].[dbo].[STLINE] s ON f.LOGICALREF = s.STFICHEREF
                INNER JOIN [GO3].[dbo].[CLCARD] c ON f.CLIENTREF = c.LOGICALREF
                LEFT JOIN [GO3].[dbo].[SLSMAN] p ON f.SALESMANREF = p.LOGICALREF
                WHERE {" AND ".join(where_conditions)}
                ORDER BY f.DATE_ DESC
                OFFSET ? ROWS
                FETCH NEXT ? ROWS ONLY
            """

            # Sorguyu çalıştır
            cursor.execute(query, [offset, page_size])
            rows = cursor.fetchall()

            # Sonuçları formatlı şekilde hazırla
            result = []
            for row in rows:
                result.append({
                    'tarih': row[0],
                    'fatura_no': self.safe_decode_string(row[1]),
                    'cari_kod': self.safe_decode_string(row[2]),
                    'cari_unvan': self.safe_decode_string(row[3]),
                    'plasiyer': self.safe_decode_string(row[4]),
                    'kalem_sayisi': row[5],
                    'toplam_miktar': float(row[6] if row[6] is not None else 0),
                    'ara_toplam': float(row[7] if row[7] is not None else 0),
                    'kdv_toplam': float(row[8] if row[8] is not None else 0),
                    'genel_toplam': float(row[9] if row[9] is not None else 0)
                })

            # Sayfalama bilgileri
            total_pages = (total_count + page_size - 1) // page_size
            has_next = page < total_pages
            has_previous = page > 1

            pagination_info = {
                'total_count': total_count,
                'total_pages': total_pages,
                'current_page': page,
                'has_next': has_next,
                'has_previous': has_previous
            }

            return result, pagination_info

        except Exception as e:
            logger.error(f"get_satinalma_listesi_paginated error: {e}")
            raise e

    def get_alim_ozet_stats(self):
        """Alım özet istatistiklerini FATURA tablosundan tahsilat benzeri formatta döndürür - TRCODE=1 ve CARİ KOD LIKE '320.%' filtreli"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Plasiyer listesi - FATURA tablosundaki PLASİYER kolonundan
            plasiyer_list = ['ALİ', 'ATAKAN', 'AZİZ', 'EYÜP',
                             'SÜLEYMAN', 'YİĞİT', 'CAN', 'RECEP', 'HALİL']
            plasiyer_where = "(" + \
                ",".join([f"'{p}'" for p in plasiyer_list]) + ")"

            # Toplam alım istatistikleri - günlük, haftalık, aylık (aynı mantıkla)
            stats_query = f"""
            SELECT 
                'gunluk' as donem,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE) THEN 1 END) as adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE) THEN [TUTAR] ELSE 0 END) as toplam_tutar
            FROM [GO3].[dbo].[FATURA] 
            WHERE TRCODE=1
            AND [CARİ KOD] LIKE '320.%'
            AND [TARİH] >= DATEADD(month, -1, GETDATE())
            
            UNION ALL
            
            SELECT 
                'haftalik' as donem,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) >= DATEADD(day, -(DATEPART(WEEKDAY, GETDATE()) - 2), CAST(GETDATE() AS DATE)) THEN 1 END) as adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) >= DATEADD(day, -(DATEPART(WEEKDAY, GETDATE()) - 2), CAST(GETDATE() AS DATE)) THEN [TUTAR] ELSE 0 END) as toplam_tutar
            FROM [GO3].[dbo].[FATURA] 
            WHERE TRCODE=1
            AND [CARİ KOD] LIKE '320.%'
            
            UNION ALL
            
            SELECT 
                'aylik' as donem,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1) THEN 1 END) as adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1) THEN [TUTAR] ELSE 0 END) as toplam_tutar
            FROM [GO3].[dbo].[FATURA] 
            WHERE TRCODE=1
            AND [CARİ KOD] LIKE '320.%'
            """

            cursor.execute(stats_query)
            stats_rows = cursor.fetchall()

            # İstatistikleri organize et
            toplam_stats = {
                'gunluk_tutar': 0,
                'gunluk_adet': 0,
                'haftalik_tutar': 0,
                'haftalik_adet': 0,
                'aylik_tutar': 0,
                'aylik_adet': 0,
            }

            for row in stats_rows:
                donem = row[0]
                adet = row[1] or 0
                tutar = float(row[2] or 0)

                if donem == 'gunluk':
                    toplam_stats['gunluk_tutar'] = tutar
                    toplam_stats['gunluk_adet'] = adet
                elif donem == 'haftalik':
                    toplam_stats['haftalik_tutar'] = tutar
                    toplam_stats['haftalik_adet'] = adet
                elif donem == 'aylik':
                    toplam_stats['aylik_tutar'] = tutar
                    toplam_stats['aylik_adet'] = adet

            # Plasiyer bazlı detaylı istatistikler
            plasiyer_query = f"""
            SELECT 
                [PLASİYER] as plasiyer,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE) THEN 1 END) as gunluk_adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) = CAST(GETDATE() AS DATE) THEN [TUTAR] ELSE 0 END) as gunluk_tutar,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) >= DATEADD(day, -(DATEPART(WEEKDAY, GETDATE()) - 2), CAST(GETDATE() AS DATE)) THEN 1 END) as haftalik_adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) >= DATEADD(day, -(DATEPART(WEEKDAY, GETDATE()) - 2), CAST(GETDATE() AS DATE)) THEN [TUTAR] ELSE 0 END) as haftalik_tutar,
                COUNT(CASE WHEN CAST([TARİH] AS DATE) >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1) THEN 1 END) as aylik_adet,
                SUM(CASE WHEN CAST([TARİH] AS DATE) >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1) THEN [TUTAR] ELSE 0 END) as aylik_tutar
            FROM [GO3].[dbo].[FATURA] 
            WHERE TRCODE=1
            AND [CARİ KOD] LIKE '320.%'
            GROUP BY [PLASİYER]
            ORDER BY aylik_tutar DESC
            """

            cursor.execute(plasiyer_query)
            plasiyer_rows = cursor.fetchall()

            plasiyer_verileri = []
            for row in plasiyer_rows:
                plasiyer_data = {
                    'plasiyer': self.safe_decode_string(row[0]),
                    'satis': {  # Satış template'iyle uyumlu olması için
                        'gunluk_adet': row[1] or 0,
                        'gunluk_tutar': float(row[2] or 0),
                        'haftalik_adet': row[3] or 0,
                        'haftalik_tutar': float(row[4] or 0),
                        'aylik_adet': row[5] or 0,
                        'aylik_tutar': float(row[6] or 0),
                    }
                }
                plasiyer_verileri.append(plasiyer_data)

            return {
                'toplam_stats': toplam_stats,
                'plasiyer_verileri': plasiyer_verileri
            }

        except Exception as e:
            logger.error(f"get_alim_ozet_stats error: {e}")
            raise e

    def get_alim_listesi_paginated(self, page=1, page_size=100, plasiyer=None, baslangic_tarihi=None, bitis_tarihi=None, cari_kod=None, cari_unvan=None):
        """Sayfalama destekli alım listesi - FATURA tablosu TRCODE=1 ve CARİ KOD LIKE '320.%' filtreli"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Dinamik filtreler
            where_conditions = [
                "TRCODE=1",
                "[CARİ KOD] LIKE '320.%'"
            ]
            # Opsiyonel plasiyer filtresi (hem PLASİYER hem de PLASİYER KOD kolonlarını destekle)
            if plasiyer:
                where_conditions.append(
                    "(([PLASİYER] = ?) OR ([PLASİYER KOD] = ?))")
            # Opsiyonel tarih aralığı
            if baslangic_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) >= ?")
            if bitis_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) <= ?")
            # Opsiyonel cari filtreleri
            if cari_kod:
                where_conditions.append("[CARİ KOD] LIKE ?")
            if cari_unvan:
                where_conditions.append("[CARİ ÜNVAN] LIKE ?")

            # Toplam kayıt sayısı
            count_query = f"""
            SELECT COUNT(*) as total_count
            FROM [GO3].[dbo].[FATURA]
            WHERE {' AND '.join(where_conditions)}
            """

            params = []
            if plasiyer:
                params.extend([plasiyer, plasiyer])
            if baslangic_tarihi:
                params.append(baslangic_tarihi)
            if bitis_tarihi:
                params.append(bitis_tarihi)
            if cari_kod:
                params.append(f"%{cari_kod}%")
            if cari_unvan:
                params.append(f"%{cari_unvan}%")

            cursor.execute(count_query, params)
            total_count = cursor.fetchone()[0]

            # Sayfalama için offset hesapla
            offset = (page - 1) * page_size

            # Ana sorgu - FATURA tablosundan alım verileri (doğru kolon adları ile)
            query = f"""
            SELECT 
                [FATURAID],
                [TARİH],
                [FATURA NO],
                [CARİ KOD],
                [CARİ ÜNVAN],
                [PLASİYER],
                [İŞLEM TARİHİ],
                [TUTAR],
                [TUTAR] as NET_TOPLAM
            FROM [GO3].[dbo].[FATURA]
            WHERE {' AND '.join(where_conditions)}
            ORDER BY [TARİH] DESC, [FATURAID] DESC
            OFFSET ? ROWS
            FETCH NEXT ? ROWS ONLY
            """

            params = []
            if plasiyer:
                params.extend([plasiyer, plasiyer])
            if baslangic_tarihi:
                params.append(baslangic_tarihi)
            if bitis_tarihi:
                params.append(bitis_tarihi)
            if cari_kod:
                params.append(f"%{cari_kod}%")
            if cari_unvan:
                params.append(f"%{cari_unvan}%")
            params.extend([offset, page_size])

            cursor.execute(query, params)
            rows = cursor.fetchall()

            # Sonuçları formatla
            satis_listesi = []
            for row in rows:
                formatted_date = None
                if row[1]:  # TARİH
                    try:
                        date_obj = row[1]
                        if hasattr(date_obj, 'strftime'):
                            formatted_date = date_obj.strftime('%d.%m.%Y')
                        else:
                            formatted_date = str(row[1])[:10]
                    except:
                        formatted_date = str(row[1])[:10] if row[1] else None

                satis_item = {
                    'ID': row[0],  # FATURAID
                    'TARİH': row[1],
                    'FormattedDate': formatted_date,
                    'FATURA_NO': self.safe_decode_string(row[2]),
                    'CARİ_KOD': self.safe_decode_string(row[3]),
                    'CARİ_ÜNVAN': self.safe_decode_string(row[4]),
                    'CARİ_PLASİYER': self.safe_decode_string(row[5]),
                    'İŞLEM_TARİHİ': row[6],
                    'TUTAR': float(row[7]) if row[7] is not None else 0,
                    'NET_TOPLAM': float(row[8]) if row[8] is not None else 0
                }
                satis_listesi.append(satis_item)

            # Sayfalama bilgileri
            total_pages = (total_count + page_size - 1) // page_size
            has_previous = page > 1
            has_next = page < total_pages

            pagination_info = {
                'total_count': total_count,
                'total_pages': total_pages,
                'current_page': page,
                'page_size': page_size,
                'has_previous': has_previous,
                'has_next': has_next,
                'previous_page': page - 1 if has_previous else None,
                'next_page': page + 1 if has_next else None,
                'page_range': range(max(1, page - 2), min(total_pages + 1, page + 3))
            }

            return satis_listesi, pagination_info

        except Exception as e:
            logger.error(f"get_alim_listesi_paginated error: {e}")
            return [], {}

    def get_alim_listesi_all(self, baslangic_tarihi=None, bitis_tarihi=None, cari_kod=None, cari_unvan=None, sort_by='islem_tarihi_desc'):
        """Tüm alım listesi - sayfalama olmadan - sadece dolu parametrelerle filtreleme"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Dinamik filtreler
            where_conditions = [
                "TRCODE=1",
                "[CARİ KOD] LIKE '320.%'"
            ]

            # Sadece dolu parametrelerle filtreleme
            if baslangic_tarihi and baslangic_tarihi.strip():
                where_conditions.append(
                    f"CAST([TARİH] AS DATE) >= '{baslangic_tarihi}'")
            if bitis_tarihi and bitis_tarihi.strip():
                where_conditions.append(
                    f"CAST([TARİH] AS DATE) <= '{bitis_tarihi}'")
            if cari_kod and cari_kod.strip():
                where_conditions.append(
                    f"[CARİ KOD] LIKE '%{cari_kod.strip()}%'")
            if cari_unvan and cari_unvan.strip():
                where_conditions.append(
                    f"[CARİ ÜNVAN] LIKE '%{cari_unvan.strip()}%'")

            # Ana sorgu
            query = f"""
                SELECT 
                    [FATURAID],
                    [CARİ KOD] as CARİ_KOD,
                    [CARİ ÜNVAN] as CARİ_ÜNVAN,
                    [TARİH],
                    [İŞLEM TARİHİ] as İŞLEM_TARİHİ,
                    [FATURA NO] as FATURA_NO,
                    [TUTAR] as NET_TOPLAM,
                    [TUTAR],
                    [PLASİYER] as CARİ_PLASİYER,
                    [BÖLGE]
                FROM [GO3].[dbo].[FATURA]
                WHERE {" AND ".join(where_conditions)}
                ORDER BY {self._get_sort_clause(sort_by)}
            """

            cursor.execute(query)
            rows = cursor.fetchall()

            # Sonuçları formatlı şekilde hazırla
            result = []
            for row in rows:
                result.append({
                    'ID': row[0],
                    'CARİ_KOD': row[1],
                    'CARİ_ÜNVAN': row[2],
                    'TARİH': row[3],
                    'FormattedDate': row[3].strftime('%d.%m.%Y') if row[3] else '',
                    'İŞLEM_TARİHİ': row[4],
                    'FATURA_NO': row[5],
                    'NET_TOPLAM': row[6],
                    'TUTAR': row[7],
                    'CARİ_PLASİYER': row[8],
                    'BÖLGE': row[9]
                })

            return result

        except Exception as e:
            logger.error(f"get_alim_listesi_all error: {e}")
            return []

    def _get_sort_clause(self, sort_by):
        """Sıralama parametresine göre ORDER BY cümlesi döndürür"""
        sort_options = {
            'islem_tarihi_desc': '[İŞLEM TARİHİ] DESC',
            'islem_tarihi_asc': '[İŞLEM TARİHİ] ASC',
            'tarih_desc': '[TARİH] DESC',
            'tarih_asc': '[TARİH] ASC',
            'cari_kod_asc': '[CARİ_KOD] ASC',
            'cari_kod_desc': '[CARİ_KOD] DESC',
            'tutar_desc': '[TUTAR] DESC',
            'tutar_asc': '[TUTAR] ASC'
        }
        return sort_options.get(sort_by, '[İŞLEM TARİHİ] DESC')

    def get_malzeme_alim_detay(self, baslangic_tarihi='', bitis_tarihi='', plasiyer='', cari_kod='', cari_unvan='', malzeme_kodu='', malzeme_aciklama='', page=1, page_size=50):
        """Malzeme alım detaylarını getirir - DETAY tablosu TRCODE=1 ve CARİ KOD LIKE '320.%' filtreli"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Filtreleme koşulları - DETAY ve FATURA tablolarını JOIN ile birleştir
            where_conditions = ["f.TRCODE=1", "f.[CARİ KOD] LIKE '320.%'"]

            if baslangic_tarihi:
                where_conditions.append(
                    f"CAST(d.[TARİH] AS DATE) >= '{baslangic_tarihi}'")
            if bitis_tarihi:
                where_conditions.append(
                    f"CAST(d.[TARİH] AS DATE) <= '{bitis_tarihi}'")
            if plasiyer:
                where_conditions.append(
                    "(f.[PLASİYER] = ? OR f.[PLASİYER KOD] = ?)")
            if cari_kod:
                where_conditions.append(f"f.[CARİ KOD] LIKE '%{cari_kod}%'")
            if cari_unvan:
                where_conditions.append(
                    f"f.[CARİ ÜNVAN] LIKE '%{cari_unvan}%'")
            if malzeme_kodu:
                where_conditions.append(
                    f"d.[MALZEME KODU] LIKE '%{malzeme_kodu}%'")
            if malzeme_aciklama:
                where_conditions.append(
                    f"d.[AÇIKLAMASI] LIKE '%{malzeme_aciklama}%'")

            # Toplam kayıt sayısı
            count_query = f"""
            SELECT COUNT(*)
            FROM [GO3].[dbo].[DETAY] d
            INNER JOIN [GO3].[dbo].[FATURA] f ON d.[FATURAID] = f.[FATURAID]
            WHERE {' AND '.join(where_conditions)}
            """

            if plasiyer:
                cursor.execute(count_query, [plasiyer, plasiyer])
            else:
                cursor.execute(count_query)
            total_count = cursor.fetchone()[0]

            # Sayfalama için offset hesapla
            offset = (page - 1) * page_size

            # Ana sorgu - DETAY ve FATURA tablolarını JOIN ile birleştir
            query = f"""
            SELECT 
                d.[TARİH],
                d.[İŞLEM TARİHİ],
                d.[MALZEME KODU],
                d.[AÇIKLAMASI],
                d.[MARKA],
                d.[MALZEME TÜRÜ],
                d.[MİKTAR],
                d.[BİRİM],
                d.[BİRİM BRÜT],
                d.[BİRİM İNDİRİM],
                d.[BİRİM NET],
                d.[TOPLAM İNDİRİM],
                d.[KDV TUTARI],
                d.[NET TOPLAM],
                f.[CARİ KOD],
                f.[CARİ ÜNVAN],
                f.[İŞLEM TARİHİ]
            FROM [GO3].[dbo].[DETAY] d
            INNER JOIN [GO3].[dbo].[FATURA] f ON d.[FATURAID] = f.[FATURAID]
            WHERE {' AND '.join(where_conditions)}
            ORDER BY d.[TARİH] DESC, d.[DETAYID] DESC
            OFFSET ? ROWS
            FETCH NEXT ? ROWS ONLY
            """

            if plasiyer:
                cursor.execute(query, [plasiyer, plasiyer, offset, page_size])
            else:
                cursor.execute(query, [offset, page_size])
            rows = cursor.fetchall()

            # Sonuçları formatla
            data = []
            for row in rows:
                formatted_date = None
                if row[0]:  # TARİH
                    try:
                        date_obj = row[0]
                        if hasattr(date_obj, 'strftime'):
                            formatted_date = date_obj.strftime('%d.%m.%Y')
                        else:
                            formatted_date = str(row[0])[:10]
                    except:
                        formatted_date = str(row[0])[:10] if row[0] else None

                data.append({
                    'TARİH': formatted_date,
                    'İŞLEM_TARİHİ': row[1],
                    'MALZEME_KODU': self.safe_decode_string(row[2]),
                    'AÇIKLAMASI': self.safe_decode_string(row[3]),
                    'MARKA': self.safe_decode_string(row[4]),
                    'MALZEME_TÜRÜ': self.safe_decode_string(row[5]),
                    'MİKTAR': float(row[6]) if row[6] is not None else 0,
                    'BİRİM': self.safe_decode_string(row[7]),
                    'BİRİM_BRÜT': float(row[8]) if row[8] is not None else 0,
                    'BİRİM_İNDİRİM': float(row[9]) if row[9] is not None else 0,
                    'BİRİM_NET': float(row[10]) if row[10] is not None else 0,
                    'TOPLAM_İNDİRİM': float(row[11]) if row[11] is not None else 0,
                    'KDV_TUTARI': float(row[12]) if row[12] is not None else 0,
                    'NET_TOPLAM': float(row[13]) if row[13] is not None else 0,
                    'CARİ_KOD': self.safe_decode_string(row[14]),
                    'CARİ_ÜNVAN': self.safe_decode_string(row[15]),
                    'İŞLEM_TARİHİ_FATURA': row[16]
                })

            # Sayfalama bilgileri
            total_pages = (total_count + page_size - 1) // page_size
            has_next = page < total_pages
            has_previous = page > 1

            return {
                'data': data,
                'total_count': total_count,
                'total_pages': total_pages,
                'current_page': page,
                'has_next': has_next,
                'has_previous': has_previous
            }

        except Exception as e:
            logger.error(f"get_malzeme_alim_detay error: {e}")
            raise e

    def get_malzeme_alim_detay_all(self, baslangic_tarihi=None, bitis_tarihi=None, plasiyer=None, cari_kod=None, cari_unvan=None, malzeme_kodu=None, malzeme_aciklama=None):
        """DETAY tablosundan tüm malzeme alım detaylarını getirir - sayfalama olmadan - sadece dolu parametrelerle filtreleme"""
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            # Filtreleme koşulları - DETAY ve FATURA tablolarını JOIN ile birleştir
            where_conditions = ["f.TRCODE=1", "f.[CARİ KOD] LIKE '320.%'"]

            # Sadece dolu parametrelerle filtreleme
            if baslangic_tarihi and baslangic_tarihi.strip():
                where_conditions.append(
                    f"CAST(d.[TARİH] AS DATE) >= '{baslangic_tarihi}'")
            if bitis_tarihi and bitis_tarihi.strip():
                where_conditions.append(
                    f"CAST(d.[TARİH] AS DATE) <= '{bitis_tarihi}'")
            if plasiyer and plasiyer.strip():
                where_conditions.append(
                    f"(f.[PLASİYER] = '{plasiyer.strip()}' OR f.[PLASİYER KOD] = '{plasiyer.strip()}')")
            if cari_kod and cari_kod.strip():
                where_conditions.append(
                    f"f.[CARİ KOD] LIKE '%{cari_kod.strip()}%'")
            if cari_unvan and cari_unvan.strip():
                where_conditions.append(
                    f"f.[CARİ ÜNVAN] LIKE '%{cari_unvan.strip()}%'")
            if malzeme_kodu and malzeme_kodu.strip():
                where_conditions.append(
                    f"d.[MALZEME KODU] LIKE '%{malzeme_kodu.strip()}%'")
            if malzeme_aciklama and malzeme_aciklama.strip():
                where_conditions.append(
                    f"d.[AÇIKLAMASI] LIKE '%{malzeme_aciklama.strip()}%'")

            # Ana sorgu
            query = f"""
                SELECT 
                    d.[TARİH],
                    d.[MALZEME KODU] as MALZEME_KODU,
                    d.[AÇIKLAMASI] as AÇIKLAMASI,
                    d.[MARKA],
                    d.[MALZEME TÜRÜ] as MALZEME_TÜRÜ,
                    d.[MİKTAR],
                    d.[BİRİM],
                    d.[BİRİM BRÜT] as BİRİM_BRÜT,
                    d.[BİRİM İNDİRİM] as BİRİM_İNDİRİM,
                    d.[BİRİM NET] as BİRİM_NET,
                    d.[TOPLAM İNDİRİM] as TOPLAM_İNDİRİM,
                    d.[KDV TUTARI] as KDV_TUTARI,
                    d.[NET TOPLAM] as NET_TOPLAM,
                    f.[CARİ KOD] as CARİ_KOD,
                    f.[CARİ ÜNVAN] as CARİ_ÜNVAN,
                    f.[İŞLEM TARİHİ] as İŞLEM_TARİHİ_FATURA,
                    f.[BÖLGE] as BÖLGE
                FROM [GO3].[dbo].[DETAY] d
                INNER JOIN [GO3].[dbo].[FATURA] f ON d.[FATURAID] = f.[FATURAID]
                WHERE {" AND ".join(where_conditions)}
                ORDER BY d.[TARİH] DESC
            """

            cursor.execute(query)
            rows = cursor.fetchall()

            # Sonuçları formatlı şekilde hazırla
            data = []
            for row in rows:
                data.append({
                    'TARİH': row[0].strftime('%d.%m.%Y') if row[0] else '',
                    'MALZEME_KODU': self.safe_decode_string(row[1]),
                    'AÇIKLAMASI': self.safe_decode_string(row[2]),
                    'MARKA': self.safe_decode_string(row[3]),
                    'MALZEME_TÜRÜ': self.safe_decode_string(row[4]),
                    'MİKTAR': float(row[5]) if row[5] is not None else 0,
                    'BİRİM': self.safe_decode_string(row[6]),
                    'BİRİM_BRÜT': float(row[7]) if row[7] is not None else 0,
                    'BİRİM_İNDİRİM': float(row[8]) if row[8] is not None else 0,
                    'BİRİM_NET': float(row[9]) if row[9] is not None else 0,
                    'TOPLAM_İNDİRİM': float(row[10]) if row[10] is not None else 0,
                    'KDV_TUTARI': float(row[11]) if row[11] is not None else 0,
                    'NET_TOPLAM': float(row[12]) if row[12] is not None else 0,
                    'CARİ_KOD': self.safe_decode_string(row[13]),
                    'CARİ_ÜNVAN': self.safe_decode_string(row[14]),
                    'İŞLEM_TARİHİ_FATURA': row[15],
                    'BÖLGE': self.safe_decode_string(row[16])
                })

            return {
                'data': data,
                'total_count': len(data)
            }

        except Exception as e:
            logger.error(f"get_malzeme_alim_detay_all error: {e}")
            raise e

    def get_malzeme_satis_detay(self, baslangic_tarihi='', bitis_tarihi='', plasiyer='', cari_kod='', cari_unvan='', malzeme_kodu='', malzeme_aciklama='', page=1, page_size=50):
        """Malzeme satış detayları — DETAY + FATURA JOIN, parametreli filtrelerle."""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()

            where_conditions = ["f.TRCODE IN (7, 8)"]
            params = []

            if baslangic_tarihi and str(baslangic_tarihi).strip():
                where_conditions.append("CAST(d.[TARİH] AS DATE) >= ?")
                params.append(baslangic_tarihi)
            if bitis_tarihi and str(bitis_tarihi).strip():
                where_conditions.append("CAST(d.[TARİH] AS DATE) <= ?")
                params.append(bitis_tarihi)
            if plasiyer and str(plasiyer).strip():
                where_conditions.append(
                    f"UPPER(RTRIM(LTRIM({_FATURA_PLASIYER_CANON}))) = UPPER(RTRIM(LTRIM(?)))"
                )
                params.append(plasiyer)
            if cari_kod and str(cari_kod).strip():
                where_conditions.append("f.[CARİ KOD] LIKE ?")
                params.append(f"%{cari_kod}%")
            if cari_unvan and str(cari_unvan).strip():
                where_conditions.append("f.[CARİ ÜNVAN] LIKE ?")
                params.append(f"%{cari_unvan}%")
            if malzeme_kodu and str(malzeme_kodu).strip():
                where_conditions.append("d.[MALZEME KODU] LIKE ?")
                params.append(f"%{malzeme_kodu}%")
            if malzeme_aciklama and str(malzeme_aciklama).strip():
                where_conditions.append("d.[AÇIKLAMASI] LIKE ?")
                params.append(f"%{malzeme_aciklama}%")

            count_query = f"""
            SELECT COUNT(*)
            FROM [GO3].[dbo].[DETAY] d
            INNER JOIN [GO3].[dbo].[FATURA] f ON d.[FATURAID] = f.[FATURAID]
            WHERE {' AND '.join(where_conditions)}
            """
            cursor.execute(count_query, params)
            total_count = cursor.fetchone()[0]

            base_query = f"""
            SELECT
                d.[TARİH],
                d.[İŞLEM TARİHİ],
                d.[MALZEME KODU],
                d.[AÇIKLAMASI],
                d.[MARKA],
                d.[MALZEME TÜRÜ],
                d.[MİKTAR],
                d.[BİRİM],
                d.[BİRİM BRÜT],
                d.[BİRİM İNDİRİM],
                d.[BİRİM NET],
                ISNULL(d.[B2B], 0) AS [B2B],
                ISNULL(d.[FARK], 0) AS [FARK],
                d.[TOPLAM İNDİRİM],
                d.[KDV TUTARI],
                d.[NET TOPLAM],
                d.[FATURA NO],
                f.[CARİ KOD],
                f.[CARİ ÜNVAN],
                {_FATURA_PLASIYER_CANON} AS [PLASİYER_CANON],
                f.[BÖLGE],
                f.[İŞLEM TARİHİ]
            FROM [GO3].[dbo].[DETAY] d
            INNER JOIN [GO3].[dbo].[FATURA] f ON d.[FATURAID] = f.[FATURAID]
            WHERE {' AND '.join(where_conditions)}
            ORDER BY d.[TARİH] DESC, d.[DETAYID] DESC
            """

            current_page = 1
            total_pages = 1 if total_count else 0
            has_next = False
            has_previous = False
            query_params = list(params)

            if page_size is not None:
                safe_page = max(int(page or 1), 1)
                safe_page_size = max(int(page_size), 1)
                offset = (safe_page - 1) * safe_page_size
                total_pages = (total_count + safe_page_size - 1) // safe_page_size if total_count else 0
                has_next = safe_page < total_pages
                has_previous = safe_page > 1
                current_page = safe_page
                base_query += """
                OFFSET ? ROWS
                FETCH NEXT ? ROWS ONLY
                """
                query_params.extend([offset, safe_page_size])

            cursor.execute(base_query, query_params)
            rows = cursor.fetchall()

            data = []
            for row in rows:
                formatted_date = '-'
                if row[0]:
                    try:
                        formatted_date = row[0].strftime('%d.%m.%Y')
                    except AttributeError:
                        formatted_date = str(row[0])[:10]

                data.append({
                    'TARİH': formatted_date,
                    'İŞLEM_TARİHİ': row[1],
                    'MALZEME_KODU': self.safe_decode_string(row[2]),
                    'AÇIKLAMASI': self.safe_decode_string(row[3]),
                    'MARKA': self.safe_decode_string(row[4]),
                    'MALZEME_TÜRÜ': self.safe_decode_string(row[5]),
                    'MİKTAR': float(row[6]) if row[6] is not None else 0,
                    'BİRİM': self.safe_decode_string(row[7]),
                    'BİRİM_BRÜT': float(row[8]) if row[8] is not None else 0,
                    'BİRİM_İNDİRİM': float(row[9]) if row[9] is not None else 0,
                    'BİRİM_NET': float(row[10]) if row[10] is not None else 0,
                    'B2B': float(row[11]) if row[11] is not None else 0,
                    'FARK': float(row[12]) if row[12] is not None else 0,
                    'TOPLAM_İNDİRİM': float(row[13]) if row[13] is not None else 0,
                    'KDV_TUTARI': float(row[14]) if row[14] is not None else 0,
                    'NET_TOPLAM': float(row[15]) if row[15] is not None else 0,
                    'FATURA_NO': self.safe_decode_string(row[16]),
                    'CARİ_KOD': self.safe_decode_string(row[17]),
                    'CARİ_ÜNVAN': self.safe_decode_string(row[18]),
                    'PLASİYER': self.safe_decode_string(row[19]),
                    'BÖLGE': self.safe_decode_string(row[20]),
                    'İŞLEM_TARİHİ_FATURA': row[21],
                })

            return {
                'data': data,
                'total_count': total_count,
                'total_pages': total_pages,
                'current_page': current_page,
                'has_next': has_next,
                'has_previous': has_previous,
            }

        except Exception as e:
            logger.error(f"get_malzeme_satis_detay error: {e}")
            return {
                'data': [],
                'total_count': 0,
                'total_pages': 0,
                'current_page': 1,
                'has_next': False,
                'has_previous': False,
            }
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def get_tahsilat_raporu(self, baslangic_tarihi='', bitis_tarihi=''):
        """Klasik Tahsilat Raporu için plasiyer bazında tahsilat türleri verisi getirir"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Tarih filtreleme
            where_conditions = ["1=1"]
            params = []

            if baslangic_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) >= ?")
                params.append(baslangic_tarihi)

            if bitis_tarihi:
                where_conditions.append("CAST([TARİH] AS DATE) <= ?")
                params.append(bitis_tarihi)

            # Tahsilat raporu sorgusu
            query = f"""
                SELECT 
                    [PLASİYER],
                    [TAHSİLAT TÜRÜ],
                    [BANKA],
                    SUM([TUTAR]) as TOPLAM_TUTAR
                FROM [GO3].[dbo].[TAHSILAT_LOGO]
                WHERE [PLASİYER KOD] = 'PLS' AND {" AND ".join(where_conditions)}
                GROUP BY [PLASİYER], [TAHSİLAT TÜRÜ], [BANKA]
                ORDER BY [PLASİYER], [TAHSİLAT TÜRÜ], [BANKA]
            """

            cursor.execute(query, params)
            columns = [column[0] for column in cursor.description]
            results = []

            for row in cursor.fetchall():
                row_dict = dict(zip(columns, row))
                results.append(row_dict)

            return {
                'success': True,
                'data': results,
                'total_count': len(results)
            }

        except Exception as e:
            logger.error(f"get_tahsilat_raporu error: {e}")
            return {
                'success': False,
                'error': str(e),
                'data': []
            }

    def get_tum_cari_gecikmeleri(self):
        """
        Tüm carilerin geçikme tutarlarını hesaplar
        Vadesi geçen ödenmemiş fatura tutarlarının toplamı
        FIFO mantığı ile ödeme hesaplaması yaparak
        """
        try:
            from datetime import datetime, date, timedelta

            # Bugünün tarihi
            bugun = date.today()

            # Tüm carilerin hareketlerini al
            query = """
                SELECT 
                    [LOGICALREF],
                    [TARİH],
                    [FATURANO],
                    ISNULL([FATURA TÜRÜ], 'Genel') AS FATURA_TÜRÜ,
                    [CARİ KOD],
                    [CARİ ÜNVAN],
                    [AÇIKLAMA],
                    [BORÇ],
                    [ALACAK],
                    [BANKA],
                    ISNULL([PLASİYER], '') AS PLASIYER,
                    ISNULL([BÖLGE], '') AS BÖLGE
                FROM [GO3].[dbo].[TUMCARIHARETLER]
                WHERE ([BORÇ] > 0 OR [ALACAK] > 0)
                AND [CARİ KOD] IS NOT NULL
                AND [CARİ KOD] != ''
                ORDER BY [CARİ KOD], [TARİH] ASC, [FATURANO] ASC
            """

            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()

                # Carileri grupla ve FIFO hesaplama yap
                cari_gruplari = {}
                plasiyer_map = {}

                for row in rows:
                    logicalref = row[0]
                    tarih = row[1]
                    fatura_no = self.safe_decode_string(row[2])
                    fatura_turu = self.safe_decode_string(row[3])
                    cari_kod = self.safe_decode_string(row[4])
                    cari_unvan = self.safe_decode_string(row[5])
                    aciklama = self.safe_decode_string(row[6])
                    borc = float(row[7]) if row[7] else 0
                    alacak = float(row[8]) if row[8] else 0
                    banka = self.safe_decode_string(row[9])
                    plasiyer = self.safe_decode_string(row[10])
                    bolge = self.safe_decode_string(row[11])

                    if cari_kod not in cari_gruplari:
                        cari_gruplari[cari_kod] = {
                            'cari_unvan': cari_unvan,
                            'plasiyer': plasiyer,
                            'bolge': bolge,
                            'hareketler': []
                        }
                    else:
                        if not cari_gruplari[cari_kod].get('plasiyer') and plasiyer:
                            cari_gruplari[cari_kod]['plasiyer'] = plasiyer
                        if not cari_gruplari[cari_kod].get('bolge') and bolge:
                            cari_gruplari[cari_kod]['bolge'] = bolge

                    cari_gruplari[cari_kod]['hareketler'].append({
                        'logicalref': logicalref,
                        'fatura_no': fatura_no,
                        'tarih': tarih,
                        'borc': borc,
                        'alacak': alacak,
                        'aciklama': aciklama,
                        'fatura_turu': fatura_turu,
                        'banka': banka
                    })

                # Gerekli cariler için plasiyer/bölge bilgisini FATURA tablosundan da al
                if cari_gruplari:
                    cari_list = list(cari_gruplari.keys())
                    chunk_size = 200
                    mapping_cursor = conn.cursor()
                    for i in range(0, len(cari_list), chunk_size):
                        chunk = cari_list[i:i + chunk_size]
                        placeholders = ",".join(["?"] * len(chunk))
                        mapping_query = f"""
                            SELECT 
                                [CARİ KOD],
                                MAX(NULLIF([PLASİYER], '')) AS PLASIYER,
                                MAX(NULLIF([BÖLGE], '')) AS BOLGE
                            FROM [GO3].[dbo].[FATURA]
                            WHERE [CARİ KOD] IN ({placeholders})
                            GROUP BY [CARİ KOD]
                        """
                        mapping_cursor.execute(mapping_query, chunk)
                        for map_row in mapping_cursor.fetchall():
                            map_cari_kod = self.safe_decode_string(map_row[0])
                            map_plasiyer = self.safe_decode_string(
                                map_row[1]) if map_row[1] else ''
                            map_bolge = self.safe_decode_string(
                                map_row[2]) if map_row[2] else ''
                            plasiyer_map[map_cari_kod] = {
                                'plasiyer': map_plasiyer,
                                'bolge': map_bolge
                            }

                # Her cari için FIFO hesaplama yap
                results = []
                for cari_kod, cari_data in cari_gruplari.items():
                    gecikme_sonucu = self._calculate_cari_gecikme_fifo(
                        cari_data['hareketler'], bugun)

                    if gecikme_sonucu['gecikme_tutari'] > 0:
                        mapping_data = plasiyer_map.get(cari_kod, {})
                        plasiyer_value = cari_data.get(
                            'plasiyer') or mapping_data.get('plasiyer', '')
                        bolge_value = cari_data.get(
                            'bolge') or mapping_data.get('bolge', '')
                        results.append({
                            'cari_kod': cari_kod,
                            'cari_unvan': cari_data['cari_unvan'],
                            'gecikme_tutari': gecikme_sonucu['gecikme_tutari'],
                            'gecikme_fatura_sayisi': gecikme_sonucu['gecikme_fatura_sayisi'],
                            'en_eski_vade': gecikme_sonucu['en_eski_vade'],
                            'en_yeni_vade': gecikme_sonucu['en_yeni_vade'],
                            'gecikme_gun_sayisi': gecikme_sonucu['gecikme_gun_sayisi'],
                            'plasiyer': plasiyer_value,
                            'bolge': bolge_value
                        })

                # Geçikme tutarına göre sırala
                results.sort(key=lambda x: x['gecikme_tutari'], reverse=True)

                # Toplam istatistikler
                toplam_gecikme = sum([r['gecikme_tutari'] for r in results])
                toplam_cari_sayisi = len(results)
                toplam_fatura_sayisi = sum(
                    [r['gecikme_fatura_sayisi'] for r in results])

                return {
                    'success': True,
                    'data': results,
                    'toplam_gecikme': toplam_gecikme,
                    'toplam_cari_sayisi': toplam_cari_sayisi,
                    'toplam_fatura_sayisi': toplam_fatura_sayisi,
                    'hesaplama_tarihi': bugun.strftime('%d.%m.%Y %H:%M')
                }

        except Exception as e:
            logger.error(f"get_tum_cari_gecikmeleri error: {e}")
            return {
                'success': False,
                'error': str(e),
                'data': [],
                'toplam_gecikme': 0,
                'toplam_cari_sayisi': 0,
                'toplam_fatura_sayisi': 0,
                'hesaplama_tarihi': ''
            }

    def _calculate_cari_gecikme_fifo(self, hareketler, bugun):
        """
        Bir carinin FIFO mantığı ile geçikme hesaplaması
        """
        try:
            from datetime import timedelta

            # Faturaları ve ödemeleri ayır
            faturalar = []
            odemeler = []

            for hareket in hareketler:
                if hareket['borc'] > 0:  # Fatura
                    fatura = {
                        'fatura_no': hareket['fatura_no'],
                        'fatura_tarihi': hareket['tarih'],
                        'fatura_tutari': hareket['borc'],
                        'fatura_turu': hareket['fatura_turu'],
                        'vade_tarihi': hareket['tarih'] + timedelta(days=45),
                        'kalan_tutar': hareket['borc'],
                        'odenen_tutar': 0
                    }
                    faturalar.append(fatura)

                elif hareket['alacak'] > 0:  # Ödeme
                    odeme = {
                        'odeme_tarihi': hareket['tarih'],
                        'odeme_tutari': hareket['alacak'],
                        'kalan_tutar': hareket['alacak']
                    }
                    odemeler.append(odeme)

            # FIFO mantığı ile faturaları ödemelerle eşleştir
            for odeme in odemeler:
                for fatura in faturalar:
                    if odeme['kalan_tutar'] <= 0 or fatura['kalan_tutar'] <= 0:
                        continue

                    # Ödeme tutarı belirleme
                    odenen_tutar = min(
                        odeme['kalan_tutar'], fatura['kalan_tutar'])

                    # Tutarları güncelle
                    fatura['kalan_tutar'] -= odenen_tutar
                    fatura['odenen_tutar'] += odenen_tutar
                    odeme['kalan_tutar'] -= odenen_tutar

            # Vadesi geçen ve ödenmemiş faturaları bul
            gecikme_faturalar = []
            for fatura in faturalar:
                if fatura['kalan_tutar'] > 0 and fatura['vade_tarihi'].date() < bugun:
                    gecikme_faturalar.append(fatura)

            # Geçikme özetini hesapla
            if gecikme_faturalar:
                toplam_gecikme = sum([f['kalan_tutar']
                                     for f in gecikme_faturalar])
                en_eski_vade = min([f['vade_tarihi']
                                   for f in gecikme_faturalar])
                en_yeni_vade = max([f['vade_tarihi']
                                   for f in gecikme_faturalar])
                gecikme_gun_sayisi = (bugun - en_eski_vade.date()).days

                return {
                    'gecikme_tutari': toplam_gecikme,
                    'gecikme_fatura_sayisi': len(gecikme_faturalar),
                    'en_eski_vade': en_eski_vade.strftime('%d.%m.%Y'),
                    'en_yeni_vade': en_yeni_vade.strftime('%d.%m.%Y'),
                    'gecikme_gun_sayisi': gecikme_gun_sayisi
                }
            else:
                return {
                    'gecikme_tutari': 0,
                    'gecikme_fatura_sayisi': 0,
                    'en_eski_vade': '',
                    'en_yeni_vade': '',
                    'gecikme_gun_sayisi': 0
                }

        except Exception as e:
            logger.error(f"_calculate_cari_gecikme_fifo error: {e}")
            return {
                'gecikme_tutari': 0,
                'gecikme_fatura_sayisi': 0,
                'en_eski_vade': '',
                'en_yeni_vade': '',
                'gecikme_gun_sayisi': 0
            }

    def get_kredi_karti_bankalari(self):
        """Kredi kartı bankalarını LG_002_BANKACC tablosundan getir (CARDTYPE=5)"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
            SELECT [LOGICALREF], [CARDTYPE], [DEFINITION_], [BANKREF]
            FROM [GO3].[dbo].[LG_002_BANKACC]
            WHERE [ACTIVE] = 0 AND [CARDTYPE] = 5
            ORDER BY [DEFINITION_]
            """

            cursor.execute(query)
            columns = [column[0] for column in cursor.description]
            results = []

            for row in cursor.fetchall():
                row_dict = dict(zip(columns, row))
                # LOGICALREF'i ID olarak kullan, DEFINITION_'yi BANKAADI olarak ekle (mevcut kod uyumluluğu için)
                results.append({
                    'ID': row_dict.get('LOGICALREF'),
                    'BANKAADI': self.safe_decode_string(row_dict.get('DEFINITION_', '')),
                    'CARDTYPE': row_dict.get('CARDTYPE'),
                    'BANKREF': row_dict.get('BANKREF'),
                    'DEFINITION_': self.safe_decode_string(row_dict.get('DEFINITION_', ''))  # Mevcut kod uyumluluğu için
                })

            conn.close()
            logger.info(f"Kredi kartı bankaları getirildi (LG_002_BANKACC): {len(results)} adet")
            return results

        except Exception as e:
            logger.error(f"Kredi kartı bankaları getirme hatası: {e}")
            return []

    def get_havale_bankalari(self):
        """Banka havalesi bankalarını LG_002_BANKACC tablosundan getir (CARDTYPE=1)"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
            SELECT [LOGICALREF], [CARDTYPE], [DEFINITION_], [BANKREF]
            FROM [GO3].[dbo].[LG_002_BANKACC]
            WHERE [ACTIVE] = 0 AND [CARDTYPE] = 1
            ORDER BY [DEFINITION_]
            """

            cursor.execute(query)
            columns = [column[0] for column in cursor.description]
            results = []

            for row in cursor.fetchall():
                row_dict = dict(zip(columns, row))
                # LOGICALREF'i ID olarak kullan, DEFINITION_'yi BANKAADI olarak ekle (mevcut kod uyumluluğu için)
                results.append({
                    'ID': row_dict.get('LOGICALREF'),
                    'BANKAADI': self.safe_decode_string(row_dict.get('DEFINITION_', '')),
                    'CARDTYPE': row_dict.get('CARDTYPE'),
                    'BANKREF': row_dict.get('BANKREF'),
                    'DEFINITION_': self.safe_decode_string(row_dict.get('DEFINITION_', ''))  # Mevcut kod uyumluluğu için
                })

            conn.close()
            logger.info(f"Havale bankaları getirildi (LG_002_BANKACC): {len(results)} adet")
            return results

        except Exception as e:
            logger.error(f"Havale bankaları getirme hatası: {e}")
            return []

    def get_cari_listesi(self, search_term=None):
        """LG_002_CLCARD tablosundan cari bilgilerini getirir"""
        try:
            logger.info(f"Cari listesi getirme başlatılıyor. Search term: {search_term}")
            connection = self.get_connection()
            cursor = connection.cursor()
            
            # Base query
            query = """
                SELECT [LOGICALREF], [CODE], [DEFINITION_]
                FROM [GO3].[dbo].[LG_002_CLCARD]
                WHERE [ACTIVE] = 0
            """
            params = []
            
            # Search term varsa filtreleme ekle
            if search_term:
                # Türkçe karakterler ve büyük/küçük harf için CI_AI collation kullan
                query += " AND (([CODE] COLLATE Turkish_CI_AI LIKE ?) OR ([DEFINITION_] COLLATE Turkish_CI_AI LIKE ?))"
                search_pattern = f"%{search_term}%"
                params = [search_pattern, search_pattern]
                logger.info(f"Search pattern: {search_pattern}")
            
            query += " ORDER BY [CODE]"
            logger.info(f"Executing query: {query}")
            logger.info(f"Params: {params}")
            
            cursor.execute(query, params)
            results = cursor.fetchall()
            
            # Sonuçları dictionary listesi olarak döndür
            cari_listesi = []
            for row in results:
                cari_listesi.append({
                    'logicalref': row[0],
                    'code': row[1],
                    'definition': row[2],
                    'title': ''  # TITLE kolonu yok, boş string
                })
            
            logger.info(f"Cari listesi getirildi: {len(cari_listesi)} adet")
            return cari_listesi
            
        except Exception as e:
            logger.error(f"Cari listesi getirme hatası: {e}")
            return []

    def get_ambar_deger_raporu(self, arama=None, malzeme_turu=None, marka=None,
                               subeler=None, sirala='deger', yon='desc',
                               stok_durumu='hepsi', max_records=100):
        """[GO3].[dbo].[STOK_MALIYET_DETAYLI] view'ından detay grid verisini getirir.

        Tüm satırlar tek seferde çekilir; filtreleme / sıralama / KPI
        hesaplamaları Python tarafında yapılır.

        Parametreler
        ------------
        arama        : malzeme kodu veya açıklamada arama (case-insensitive)
        malzeme_turu : tam eşleşme filtresi
        marka        : tam eşleşme filtresi
        subeler      : ['SEYHAN', 'YUREGIR', 'MERSIN'] alt kümesi — seçilen
                       şubelerden en az birinde stok > 0 olan satırlar.
                       None/boş = Tüm Stoklar (filtre yok).
        sirala       : sıralama anahtarı (malzeme_kodu, aciklamasi,
                       malzeme_turu, marka, seyhan, yuregir, mersin, toplam,
                       son_alim_tarihi, son_birim_net, deger)
        yon          : 'asc' | 'desc'
        stok_durumu  : 'hepsi' (default) | 'stokta_var' | 'stok_bitmis'
                       'stokta_var'   → TOPLAM > 0
                       'stok_bitmis'  → TOPLAM <= 0
                       'hepsi' / None → filtre uygulanmaz
        max_records  : dönen satır sayısı üst sınırı (None = tümü)

        Dönüş:
            rows            : sıralanmış + kırpılmış satır listesi
            kpi             : {toplam_urun, toplam_stok, toplam_deger,
                               ortalama_birim_deger, marka_sayisi, tur_sayisi}
            subeler         : [{'kod','ad','stok','deger','pay'}] (3 şube)
            grid_toplam     : sayfada görünen satırların alt toplamı
                              (filtrelenmiş + kırpılmış)
                              {seyhan, yuregir, mersin, toplam, deger}
            grid_toplam_filtered
                             : filtreli TÜM satırların alt toplamı (footer)
            malzeme_turleri : dropdown seçenekleri (tüm veri üzerinden)
            markalar        : dropdown seçenekleri (tüm veri üzerinden)
            total_filtered  : filtre sonrası toplam satır sayısı
            truncated       : max_records nedeniyle kırpıldı mı
            top_markalar    : ilk 20 marka (tüm veri üzerinden),
                              [{ad, stok, deger, pay, bar_pct}]
            top_turler      : ilk 20 malzeme türü (tüm veri üzerinden),
                              [{ad, stok, deger, pay, bar_pct}]
        """
        empty = {
            'rows': [],
            'kpi': {
                'toplam_urun': 0,
                'toplam_stok': 0.0,
                'toplam_deger': 0.0,
                'ortalama_birim_deger': 0.0,
                'marka_sayisi': 0,
                'tur_sayisi': 0,
            },
            'subeler': [
                {'kod': 'SEYHAN', 'ad': 'Seyhan', 'stok': 0.0, 'deger': 0.0, 'pay': 0.0},
                {'kod': 'YUREGIR', 'ad': 'Yüreğir', 'stok': 0.0, 'deger': 0.0, 'pay': 0.0},
                {'kod': 'MERSIN', 'ad': 'Mersin', 'stok': 0.0, 'deger': 0.0, 'pay': 0.0},
            ],
            'grid_toplam': {'seyhan': 0.0, 'yuregir': 0.0, 'mersin': 0.0,
                            'toplam': 0.0, 'deger': 0.0},
            'kpi_filtered': {
                'toplam_urun': 0,
                'toplam_stok': 0.0,
                'toplam_deger': 0.0,
                'ortalama_birim_deger': 0.0,
                'marka_sayisi': 0,
                'tur_sayisi': 0,
            },
            'subeler_filtered': [
                {'kod': 'SEYHAN', 'ad': 'Seyhan', 'stok': 0.0, 'deger': 0.0, 'pay': 0.0},
                {'kod': 'YUREGIR', 'ad': 'Yüreğir', 'stok': 0.0, 'deger': 0.0, 'pay': 0.0},
                {'kod': 'MERSIN', 'ad': 'Mersin', 'stok': 0.0, 'deger': 0.0, 'pay': 0.0},
            ],
            'grid_toplam_filtered': {'seyhan': 0.0, 'yuregir': 0.0, 'mersin': 0.0,
                                     'toplam': 0.0, 'deger': 0.0},
            'malzeme_turleri': [],
            'markalar': [],
            'total_filtered': 0,
            'truncated': False,
            'top_markalar': [],
            'top_turler': [],
            'top_markalar_toplam': {'stok': 0.0, 'deger': 0.0, 'pay': 0.0},
            'top_turler_toplam': {'stok': 0.0, 'deger': 0.0, 'pay': 0.0},
        }

        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
                SELECT
                    [MALZEME KODU],
                    [AÇIKLAMASI],
                    [MALZEME TÜRÜ],
                    [MARKA],
                    [SEYHAN],
                    [YÜREĞİR],
                    [MERSİN],
                    [TOPLAM],
                    [SON ALIM TARİHİ],
                    [SON BİRİM NET],
                    [TOPLAM STOK DEĞERİ]
                FROM [GO3].[dbo].[STOK_MALIYET_DETAYLI]
            """
            cursor.execute(query)
            raw = cursor.fetchall()
            conn.close()

            def _to_float(val):
                if val is None or val == '':
                    return 0.0
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return 0.0

            def _to_str(val):
                if val is None:
                    return ''
                try:
                    return self.safe_decode_string(val)
                except Exception:
                    return str(val)

            # ---- Parametre normalizasyonu ----
            arama_n = (arama or '').strip().lower()
            tur_n = (malzeme_turu or '').strip()
            marka_n = (marka or '').strip()

            sube_alias = {
                'SEYHAN': 'seyhan',
                'YUREGIR': 'yuregir', 'YÜREĞİR': 'yuregir',
                'MERSIN': 'mersin', 'MERSİN': 'mersin',
            }
            if isinstance(subeler, str):
                subeler_raw = [s.strip() for s in subeler.split(',') if s.strip()]
            elif isinstance(subeler, (list, tuple, set)):
                subeler_raw = [str(s).strip() for s in subeler if s]
            else:
                subeler_raw = []
            sube_keys = []
            for s in subeler_raw:
                alias = sube_alias.get(s.upper())
                if alias and alias not in sube_keys:
                    sube_keys.append(alias)

            # ---- Tüm satırları dict'e çevir; dropdown seçenekleri tüm veri üzerinden ----
            all_rows = []
            tum_turler = set()
            tum_markalar = set()
            for row in raw:
                r = {
                    'malzeme_kodu': _to_str(row[0]),
                    'aciklamasi': _to_str(row[1]),
                    'malzeme_turu': _to_str(row[2]),
                    'marka': _to_str(row[3]),
                    'seyhan': _to_float(row[4]),
                    'yuregir': _to_float(row[5]),
                    'mersin': _to_float(row[6]),
                    'toplam': _to_float(row[7]),
                    'son_alim_tarihi': row[8],
                    'son_birim_net': _to_float(row[9]),
                    'toplam_deger': _to_float(row[10]),
                }
                if r['toplam_deger'] == 0 and r['toplam'] and r['son_birim_net']:
                    r['toplam_deger'] = r['toplam'] * r['son_birim_net']
                all_rows.append(r)
                if r['malzeme_turu']:
                    tum_turler.add(r['malzeme_turu'])
                if r['marka']:
                    tum_markalar.add(r['marka'])

            # ---- KPI / Şube kartları / Grid alt toplamları (FİLTRESİZ, tüm veri) ----
            # KPI'lar her zaman tüm tablo üzerinden hesaplanır; aktif filtrelerden
            # bağımsızdır. Aşağıda filtreli KPI'lar da üretilir, ancak response'da
            # kullanıcıya her zaman unfiltered (tüm veriler) gösterilir.
            unfiltered_toplam_stok = sum(r['toplam'] for r in all_rows)
            unfiltered_toplam_deger = sum(r['toplam_deger'] for r in all_rows)
            unfiltered_kpi = {
                'toplam_urun': len(all_rows),
                'toplam_stok': unfiltered_toplam_stok,
                'toplam_deger': unfiltered_toplam_deger,
                'ortalama_birim_deger': (unfiltered_toplam_deger / unfiltered_toplam_stok)
                    if unfiltered_toplam_stok > 0 else 0.0,
                'marka_sayisi': len({r['marka'] for r in all_rows if r['marka']}),
                'tur_sayisi': len({r['malzeme_turu'] for r in all_rows if r['malzeme_turu']}),
            }
            unfiltered_sube_stok = {'seyhan': 0.0, 'yuregir': 0.0, 'mersin': 0.0}
            unfiltered_sube_deger = {'seyhan': 0.0, 'yuregir': 0.0, 'mersin': 0.0}
            # Şube değeri: her satırda ilgili şubenin stokunu o satırın SON BİRİM NET
            # birim fiyatıyla çarpıp topluyoruz (yani şubeye özgü birim fiyat üzerinden).
            # Tek bir ağırlıklı-ortalama birim fiyatı şubelere dağıtılmaz; bu, view'da
            # her satır için yalnızca tek bir SON BİRİM NET olduğundan satır-bazlı doğru
            # yaklaşımdır ve SQL SUM(şube * SON BİRİM NET) ile bire bir örtüşür.
            for r in all_rows:
                birim = r['son_birim_net']
                for k in ('seyhan', 'yuregir', 'mersin'):
                    unfiltered_sube_stok[k] += r[k]
                    unfiltered_sube_deger[k] += r[k] * birim
            unfiltered_sube_deger_toplam = sum(unfiltered_sube_deger.values())
            unfiltered_subeler = []
            for kod, ad, key in (('SEYHAN', 'Seyhan', 'seyhan'),
                                 ('YUREGIR', 'Yüreğir', 'yuregir'),
                                 ('MERSIN', 'Mersin', 'mersin')):
                unfiltered_subeler.append({
                    'kod': kod,
                    'ad': ad,
                    'stok': unfiltered_sube_stok[key],
                    'deger': unfiltered_sube_deger[key],
                    'pay': (unfiltered_sube_deger[key] / unfiltered_sube_deger_toplam * 100.0)
                        if unfiltered_sube_deger_toplam > 0 else 0.0,
                })
            unfiltered_grid_toplam = {
                'seyhan': unfiltered_sube_stok['seyhan'],
                'yuregir': unfiltered_sube_stok['yuregir'],
                'mersin': unfiltered_sube_stok['mersin'],
                'toplam': unfiltered_toplam_stok,
                'deger': unfiltered_toplam_deger,
            }

            # ---- Stok durumu filtresi (TOPLAM kolonuna göre) ----
            stok_durumu_n = (stok_durumu or 'hepsi').strip().lower()
            if stok_durumu_n not in ('hepsi', 'stokta_var', 'stok_bitmis'):
                stok_durumu_n = 'hepsi'

            # ---- Filtreleme ----
            filtered = []
            for r in all_rows:
                if arama_n:
                    blob = f"{r['malzeme_kodu']} {r['aciklamasi']}".lower()
                    if arama_n not in blob:
                        continue
                if tur_n and r['malzeme_turu'] != tur_n:
                    continue
                if marka_n and r['marka'] != marka_n:
                    continue
                if sube_keys and not any(r[k] > 0 for k in sube_keys):
                    continue
                if stok_durumu_n == 'stokta_var' and (r['toplam'] or 0) <= 0:
                    continue
                if stok_durumu_n == 'stok_bitmis' and (r['toplam'] or 0) > 0:
                    continue
                filtered.append(r)

            # ---- Sıralama ----
            sort_key_map = {
                'malzeme_kodu': lambda x: (x['malzeme_kodu'] or '').lower(),
                'aciklamasi': lambda x: (x['aciklamasi'] or '').lower(),
                'malzeme_turu': lambda x: (x['malzeme_turu'] or '').lower(),
                'marka': lambda x: (x['marka'] or '').lower(),
                'seyhan': lambda x: x['seyhan'],
                'yuregir': lambda x: x['yuregir'],
                'mersin': lambda x: x['mersin'],
                'toplam': lambda x: x['toplam'],
                'son_alim_tarihi': lambda x: str(x['son_alim_tarihi'] or ''),
                'son_birim_net': lambda x: x['son_birim_net'],
                'maliyet': lambda x: x['toplam_deger'],
                'deger': lambda x: x['toplam_deger'],
            }
            sirala_n = (sirala or 'deger').strip().lower()
            if sirala_n not in sort_key_map:
                sirala_n = 'deger'
            yon_n = (yon or 'desc').strip().lower()
            if yon_n not in ('asc', 'desc'):
                yon_n = 'desc'
            filtered.sort(key=sort_key_map[sirala_n], reverse=(yon_n == 'desc'))

            # ---- Kırpma ----
            total_filtered = len(filtered)
            try:
                cap = int(max_records) if max_records else None
                if cap is not None and cap <= 0:
                    cap = None
            except (TypeError, ValueError):
                cap = None
            if cap is not None and total_filtered > cap:
                rows_out = filtered[:cap]
                truncated = True
            else:
                rows_out = filtered
                truncated = False

            # ---- KPI (filtrelenmiş tüm satırlar üzerinden) ----
            toplam_stok = sum(r['toplam'] for r in filtered)
            toplam_deger = sum(r['toplam_deger'] for r in filtered)
            kpi = {
                'toplam_urun': total_filtered,
                'toplam_stok': toplam_stok,
                'toplam_deger': toplam_deger,
                'ortalama_birim_deger': (toplam_deger / toplam_stok) if toplam_stok > 0 else 0.0,
                'marka_sayisi': len({r['marka'] for r in filtered if r['marka']}),
                'tur_sayisi': len({r['malzeme_turu'] for r in filtered if r['malzeme_turu']}),
            }

            # ---- Şube kartları (stok + değer + pay) ----
            sube_stok = {'seyhan': 0.0, 'yuregir': 0.0, 'mersin': 0.0}
            sube_deger = {'seyhan': 0.0, 'yuregir': 0.0, 'mersin': 0.0}
            for r in filtered:
                birim = r['son_birim_net']
                for k in ('seyhan', 'yuregir', 'mersin'):
                    sube_stok[k] += r[k]
                    sube_deger[k] += r[k] * birim
            sube_deger_toplam = sum(sube_deger.values())
            sube_kartlari = []
            for kod, ad, key in (('SEYHAN', 'Seyhan', 'seyhan'),
                                 ('YUREGIR', 'Yüreğir', 'yuregir'),
                                 ('MERSIN', 'Mersin', 'mersin')):
                sube_kartlari.append({
                    'kod': kod,
                    'ad': ad,
                    'stok': sube_stok[key],
                    'deger': sube_deger[key],
                    'pay': (sube_deger[key] / sube_deger_toplam * 100.0)
                        if sube_deger_toplam > 0 else 0.0,
                })

            # ---- Grid alt toplamları ----
            # grid_toplam: ekranda görünen (kırpılmış) satırların toplamı.
            # Bu sayede sayfa boyutu / pagination / sort değiştiğinde tfoot
            # daima o anki sayfadaki satırları yansıtır.
            page_seyhan = sum(r['seyhan'] for r in rows_out)
            page_yuregir = sum(r['yuregir'] for r in rows_out)
            page_mersin = sum(r['mersin'] for r in rows_out)
            page_toplam = sum(r['toplam'] for r in rows_out)
            page_deger = sum(r['toplam_deger'] for r in rows_out)
            grid_toplam = {
                'seyhan': page_seyhan,
                'yuregir': page_yuregir,
                'mersin': page_mersin,
                'toplam': page_toplam,
                'deger': page_deger,
            }
            # grid_toplam_filtered: filtreli TÜM satırlar (footer / özet amaçlı)
            grid_toplam_filtered = {
                'seyhan': sube_stok['seyhan'],
                'yuregir': sube_stok['yuregir'],
                'mersin': sube_stok['mersin'],
                'toplam': toplam_stok,
                'deger': toplam_deger,
            }

            # ---- Marka / Tür ilk 20 (tüm veri üzerinden, filtrelerden bağımsız) ----
            # Yeni Marka/Tür İlk 20 panelinin veri kaynağı; DETAYLI view'ından
            # Python'da türetilir, 160 satırlık STOK_MALIYET view'ına bağlı değildir.
            def _aggregate_top(group_field):
                agg = {}
                for r in all_rows:
                    ad = (r[group_field] or '').strip() or '(Belirtilmemiş)'
                    if ad not in agg:
                        agg[ad] = {'ad': ad, 'stok': 0.0, 'deger': 0.0}
                    agg[ad]['stok'] += r['toplam']
                    agg[ad]['deger'] += r['toplam_deger']
                items = sorted(
                    agg.values(),
                    key=lambda x: x['deger'],
                    reverse=True,
                )[:20]
                return items

            top_markalar_raw = _aggregate_top('marka')
            top_turler_raw = _aggregate_top('malzeme_turu')

            total_deger = unfiltered_toplam_deger
            max_marka = top_markalar_raw[0]['deger'] if top_markalar_raw else 0.0
            max_tur = top_turler_raw[0]['deger'] if top_turler_raw else 0.0

            def _build_top_list(items, max_deger):
                out = []
                for it in items:
                    deger = it['deger']
                    out.append({
                        'ad': it['ad'],
                        'stok': it['stok'],
                        'deger': deger,
                        'pay': (deger / total_deger * 100.0) if total_deger > 0 else 0.0,
                        'bar_pct': (deger / max_deger * 100.0) if max_deger > 0 else 0.0,
                    })
                return out

            top_markalar = _build_top_list(top_markalar_raw, max_marka)
            top_turler = _build_top_list(top_turler_raw, max_tur)

            top_markalar_toplam = {
                'stok': sum(m['stok'] for m in top_markalar),
                'deger': sum(m['deger'] for m in top_markalar),
                'pay': sum(m['pay'] for m in top_markalar),
            }
            top_turler_toplam = {
                'stok': sum(t['stok'] for t in top_turler),
                'deger': sum(t['deger'] for t in top_turler),
                'pay': sum(t['pay'] for t in top_turler),
            }

            return {
                'rows': rows_out,
                # KPI / şube kartları her zaman tüm veri üzerinden hesaplanır —
                # aktif filtrelerden etkilenmez.
                'kpi': unfiltered_kpi,
                'subeler': unfiltered_subeler,
                # grid_toplam artık sayfadaki (görünür) satırların toplamıdır.
                # Pagination / sayfa boyutu / sort değiştiğinde tfoot dinamik
                # olarak yeni sayfanın toplamını yansıtır.
                'grid_toplam': grid_toplam,
                # Filtreli değerler de ayrıca döndürülür (footer / debug amaçlı)
                'kpi_filtered': kpi,
                'subeler_filtered': sube_kartlari,
                'grid_toplam_filtered': grid_toplam_filtered,
                'malzeme_turleri': sorted(tum_turler),
                'markalar': sorted(tum_markalar),
                'total_filtered': total_filtered,
                'truncated': truncated,
                # Yeni: Marka / Tür ilk 20 panelleri için (tüm veri)
                'top_markalar': top_markalar,
                'top_turler': top_turler,
                'top_markalar_toplam': top_markalar_toplam,
                'top_turler_toplam': top_turler_toplam,
            }

        except Exception as e:
            logger.error(f"get_ambar_deger_raporu hatası: {e}", exc_info=True)
            return empty




    def get_plasiyer_prim_summary(self, plasiyer, start_date, end_date):
        """
        TAHSILAT_LOGO tablosundan plasiyerin prim hakedişini hesaplamak için tahsilat toplamlarını getirir.
        """
        query = """
        SELECT 
            [TAHSİLAT TÜRÜ] as Tur,
            SUM([TUTAR]) as Toplam
        FROM [GO3].[dbo].[TAHSILAT_LOGO]
        WHERE 
            UPPER([PLASİYER]) = UPPER(?)
            AND CAST([TARİH] AS DATE) BETWEEN ? AND ?
        GROUP BY [TAHSİLAT TÜRÜ]
        """
        
        try:
            results = self.execute_query(query, [plasiyer, start_date, end_date])
            
            summary = {
                'Nakit': 0.0,
                'Kredi Kartı': 0.0,
                'Havale': 0.0,
                'Çek': 0.0,
                'Senet': 0.0
            }
            
            for row in results:
                tur = str(row.get('Tur', '')).strip().upper()
                tutar = float(row.get('Toplam', 0))
                
                # Tür eşleştirme (Veritabanındaki değerlere göre)
                if 'NAKİT' in tur:
                    summary['Nakit'] += tutar
                elif 'KREDİ KARTI' in tur or 'POS' in tur:
                    summary['Kredi Kartı'] += tutar
                elif 'HAVALE' in tur or 'EFT' in tur:
                    summary['Havale'] += tutar
                elif 'ÇEK' in tur:
                    summary['Çek'] += tutar
                elif 'SENET' in tur:
                    summary['Senet'] += tutar
                    
            return summary
            
        except Exception as e:
            logger.error(f"get_plasiyer_prim_summary error: {e}")
            # return empty dict on error
            return {
                'Nakit': 0.0,
                'Kredi Kartı': 0.0,
                'Havale': 0.0,
                'Çek': 0.0,
                'Senet': 0.0
            }

    def get_plasiyer_prim_summary_from_gunluk(self, plasiyer, start_date, end_date):
        """
        Genel Görünüm dashboard ile aynı tahsilat kaynağı (GunlukTahsilat_V) kullanarak
        plasiyerin tahsilat türüne göre toplamlarını getirir. Prim hesaplaması bu toplamlarla yapılır.
        """
        query = """
        SELECT 
            [TahsilatTuru] as Tur,
            ISNULL(SUM(CAST([Tutar] AS DECIMAL(15,2))), 0) as Toplam
        FROM [GO3].[dbo].[GunlukTahsilat_V]
        WHERE 
            UPPER(RTRIM(LTRIM([Plasiyer]))) = UPPER(RTRIM(LTRIM(?)))
            AND CAST([Tarih] AS DATE) BETWEEN ? AND ?
        GROUP BY [TahsilatTuru]
        """
        try:
            results = self.execute_query(query, [plasiyer, start_date, end_date])
            summary = {
                'Nakit': 0.0,
                'Kredi Kartı': 0.0,
                'Havale': 0.0,
                'Çek': 0.0,
                'Senet': 0.0
            }
            for row in results:
                tur = str(row.get('Tur') or '').strip()
                tur_upper = tur.upper()
                tutar = float(row.get('Toplam', 0))
                if 'NAKIT' in tur_upper or 'NAKİT' in tur_upper:
                    summary['Nakit'] += tutar
                elif 'KREDİ' in tur_upper or 'KREDI' in tur_upper or 'POS' in tur_upper or 'KARTI' in tur_upper:
                    summary['Kredi Kartı'] += tutar
                elif 'HAVALE' in tur_upper or 'EFT' in tur_upper:
                    summary['Havale'] += tutar
                elif 'ÇEK' in tur_upper or 'CEK' in tur_upper:
                    summary['Çek'] += tutar
                elif 'SENET' in tur_upper:
                    summary['Senet'] += tutar
            return summary
        except Exception as e:
            logger.error(f"get_plasiyer_prim_summary_from_gunluk error: {e}")
            return {
                'Nakit': 0.0,
                'Kredi Kartı': 0.0,
                'Havale': 0.0,
                'Çek': 0.0,
                'Senet': 0.0
            }

    OM_SORT_KEYS = (
        'fatura_turu', 'malzeme_turu', 'malzeme_kodu', 'marka', 'aciklama',
        'fatura_durumu', 'toplam_miktar', 'agirlikli_ort_birim',
    )

    _OM_COL_DEFAULTS = {
        'fatura_turu': 'FATURA TÜRÜ',
        'malzeme_turu': 'MALZEME TÜRÜ',
        'malzeme_kodu': 'MALZEME KODU',
        'marka': 'MARKA',
        'aciklama': 'AÇIKLAMASI',
        'fatura_durumu': 'FATURA_DURUMU',
        'toplam_miktar': 'TOPLAM_MIKTAR',
        'agirlikli_ort_birim': 'AGIRLIKLI_ORT_BIRIM',
    }

    @staticmethod
    def _om_bracket_ident(name):
        if not name:
            return ''
        return '[' + str(name).replace(']', ']]') + ']'

    def _om_norm_col_key(self, s):
        """Sütun adını eşleştirme için normalize eder (Türkçe karakter → ASCII)."""
        if not s:
            return ''
        t = str(s).strip().lower()
        # maketrans anahtarları tek Unicode kod birimi olmalı ('i' + birleşik nokta geçersiz)
        tr = str.maketrans({
            'ı': 'i', 'İ': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's',
            'ö': 'o', 'ç': 'c', 'â': 'a', 'î': 'i', 'û': 'u',
        })
        t = t.translate(tr)
        # NFC'de bazen 'i' + U+0307 (combining dot); birleşik harfe indir
        t = t.replace('\u0307', '')
        return ''.join(c for c in t if c.isalnum())

    def _resolve_ortalama_maliyet_columns(self):
        """
        ORTALAMA_MALIYET görünümündeki gerçek sütun adlarını INFORMATION_SCHEMA ile bulur.
        View’de farklı yazım (Türkçe İ, boşluk, alt çizgi) olsa da filtre ve SELECT çalışır.
        """
        cached = getattr(self, '_om_col_resolved_cache', None)
        if cached is not None:
            return cached
        merged = dict(self._OM_COL_DEFAULTS)
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT COLUMN_NAME FROM [GO3].INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = N'dbo' AND TABLE_NAME = N'ORTALAMA_MALIYET'
                ORDER BY ORDINAL_POSITION
            """)
            db_cols = [row[0] for row in cur.fetchall() if row and row[0]]
            conn.close()
        except Exception as e:
            logger.warning(
                'ORTALAMA_MALIYET sütun çözümlemesi başarısız, varsayılan adlar: %s', e)
            self._om_col_resolved_cache = merged
            return merged

        found = {}
        for col in db_cols:
            nk = self._om_norm_col_key(col)
            if not nk:
                continue
            if 'malzemekodu' in nk or (nk.startswith('malzeme') and 'kodu' in nk):
                found.setdefault('malzeme_kodu', col)
            elif 'malzemeturu' in nk or (
                'malzeme' in nk and 'turu' in nk and 'kodu' not in nk
            ):
                found.setdefault('malzeme_turu', col)
            elif 'fatura' in nk and 'turu' in nk:
                found.setdefault('fatura_turu', col)
            elif 'marka' in nk:
                found.setdefault('marka', col)
            elif 'aciklama' in nk:
                found.setdefault('aciklama', col)
            elif 'toplam' in nk and 'miktar' in nk:
                found.setdefault('toplam_miktar', col)
            elif 'agirlikli' in nk and ('birim' in nk or 'ort' in nk):
                found.setdefault('agirlikli_ort_birim', col)
            elif 'agirlikli' in nk:
                found.setdefault('agirlikli_ort_birim', col)
            elif 'fatura' in nk and 'durum' in nk:
                found.setdefault('fatura_durumu', col)

        merged.update(found)
        self._om_col_resolved_cache = merged
        if found:
            logger.debug('ORTALAMA_MALIYET sütun eşlemesi: %s', merged)
        return merged

    def _om_q(self, logical):
        """Mantıksal alan adı → köşeli parantezli SQL tanımlayıcı."""
        c = self._resolve_ortalama_maliyet_columns()
        return self._om_bracket_ident(c.get(logical) or self._OM_COL_DEFAULTS[logical])

    @staticmethod
    def _om_norm_str_list(val):
        """Tek değer veya liste → boş olmayan string listesi."""
        if val is None:
            return []
        if isinstance(val, (list, tuple)):
            return [str(x).strip() for x in val if x is not None and str(x).strip() != '']
        s = str(val).strip()
        return [s] if s else []

    def get_ortalama_maliyet_filter_options(self):
        """Görünümden distinct değerler (çoklu seçim listeleri). Sütun adları DB’den çözülür."""
        tbl = '[GO3].[dbo].[ORTALAMA_MALIYET]'
        qf = self._om_q('fatura_turu')
        qm = self._om_q('malzeme_turu')
        qk = self._om_q('malzeme_kodu')
        qmarka = self._om_q('marka')
        qa = self._om_q('aciklama')
        out = {
            'fatura_turleri': [],
            'malzeme_turleri': [],
            'malzeme_kodlari': [],
            'markalar': [],
            'aciklamalar': [],
        }

        def fetch_col(sql):
            try:
                conn = self.get_connection()
                cur = conn.cursor()
                cur.execute(sql)
                rows = []
                for r in cur.fetchall():
                    if r[0] is None:
                        continue
                    cell = self._mssql_cell_to_str(r[0])
                    if cell and str(cell).strip():
                        rows.append(str(cell).strip())
                conn.close()
                return rows
            except Exception as e:
                logger.error(f'get_ortalama_maliyet_filter_options: {e}')
                return []

        out['fatura_turleri'] = fetch_col(
            f'SELECT DISTINCT {qf} AS v FROM {tbl} '
            f'WHERE {qf} IS NOT NULL ORDER BY {qf}'
        )
        out['malzeme_turleri'] = fetch_col(
            f'SELECT DISTINCT {qm} AS v FROM {tbl} '
            f'WHERE {qm} IS NOT NULL ORDER BY {qm}'
        )
        out['malzeme_kodlari'] = fetch_col(
            f'SELECT DISTINCT TOP (12000) {qk} AS v FROM {tbl} '
            f'WHERE {qk} IS NOT NULL ORDER BY {qk}'
        )
        out['markalar'] = fetch_col(
            f'SELECT DISTINCT TOP (3000) {qmarka} AS v FROM {tbl} '
            f'WHERE {qmarka} IS NOT NULL ORDER BY {qmarka}'
        )
        out['aciklamalar'] = fetch_col(
            f'SELECT DISTINCT TOP (4000) {qa} AS v FROM {tbl} '
            f'WHERE {qa} IS NOT NULL ORDER BY {qa}'
        )
        return out

    def _ortalama_maliyet_where_params(
        self,
        fatura_turu=None,
        malzeme_turu=None,
        malzeme_kodu=None,
        marka=None,
        aciklama=None,
        toplam_miktar_min=None,
        toplam_miktar_max=None,
    ):
        qf = self._om_q('fatura_turu')
        qm = self._om_q('malzeme_turu')
        qk = self._om_q('malzeme_kodu')
        qmarka = self._om_q('marka')
        qa = self._om_q('aciklama')
        qtm = self._om_q('toplam_miktar')
        where = ['1=1']
        params = []
        ft = self._om_norm_str_list(fatura_turu)
        if ft:
            ph = ','.join('?' * len(ft))
            where.append(f'{qf} IN ({ph})')
            params.extend(ft)
        mt = self._om_norm_str_list(malzeme_turu)
        if mt:
            ph = ','.join('?' * len(mt))
            where.append(f'{qm} IN ({ph})')
            params.extend(mt)
        mk = self._om_norm_str_list(malzeme_kodu)
        if mk:
            ph = ','.join('?' * len(mk))
            where.append(f'{qk} IN ({ph})')
            params.extend(mk)
        mr = self._om_norm_str_list(marka)
        if mr:
            ph = ','.join('?' * len(mr))
            where.append(f'{qmarka} IN ({ph})')
            params.extend(mr)
        ac = self._om_norm_str_list(aciklama)
        if ac:
            ph = ','.join('?' * len(ac))
            where.append(f'{qa} IN ({ph})')
            params.extend(ac)
        if toplam_miktar_min is not None:
            where.append(f'CAST({qtm} AS DECIMAL(28, 8)) >= ?')
            params.append(toplam_miktar_min)
        if toplam_miktar_max is not None:
            where.append(f'CAST({qtm} AS DECIMAL(28, 8)) <= ?')
            params.append(toplam_miktar_max)
        return where, params

    def get_ortalama_maliyet_paginated(
        self,
        page=1,
        page_size=50,
        sort_by='malzeme_kodu',
        sort_dir='asc',
        fatura_turu=None,
        malzeme_turu=None,
        malzeme_kodu=None,
        marka=None,
        aciklama=None,
        toplam_miktar_min=None,
        toplam_miktar_max=None,
    ):
        """[ORTALAMA_MALIYET] görünümü — filtre, sıralama, sayfalama."""
        try:
            page = max(1, int(page))
        except (TypeError, ValueError):
            page = 1
        try:
            if str(page_size).lower() == 'all':
                page_size = 1000000
            else:
                page_size = int(page_size)
                if page_size not in (25, 50, 100, 200, 1000000):
                    page_size = 50
        except (TypeError, ValueError):
            page_size = 50

        sort_key = sort_by if sort_by in self.OM_SORT_KEYS else 'malzeme_kodu'
        order_col = self._om_q(sort_key)
        order_dir = 'DESC' if str(sort_dir).lower() == 'desc' else 'ASC'

        where, params = self._ortalama_maliyet_where_params(
            fatura_turu, malzeme_turu, malzeme_kodu, marka, aciklama,
            toplam_miktar_min, toplam_miktar_max,
        )
        where_sql = ' AND '.join(where)
        base_from = 'FROM [GO3].[dbo].[ORTALAMA_MALIYET]'
        qf, qm, qk, qmarka, qa, qfd, qtm, qao = (
            self._om_q('fatura_turu'), self._om_q('malzeme_turu'),
            self._om_q('malzeme_kodu'), self._om_q('marka'), self._om_q('aciklama'),
            self._om_q('fatura_durumu'), self._om_q('toplam_miktar'), self._om_q('agirlikli_ort_birim'),
        )

        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            count_query = f'SELECT COUNT(*) AS c {base_from} WHERE {where_sql}'
            cursor.execute(count_query, params)
            total_count = int(cursor.fetchone()[0])

            offset = (page - 1) * page_size
            data_query = f"""
                SELECT {qf}, {qm}, {qk}, {qa}, {qmarka}, {qfd},
                       {qtm}, {qao}
                {base_from}
                WHERE {where_sql}
                ORDER BY {order_col} {order_dir}
                OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            """
            cursor.execute(data_query, params + [offset, page_size])
            raw = cursor.fetchall()
            conn.close()

            results = []
            for row in raw:
                results.append({
                    'fatura_turu': self._mssql_cell_to_str(row[0]),
                    'malzeme_turu': self._mssql_cell_to_str(row[1]),
                    'malzeme_kodu': self._mssql_cell_to_str(row[2]),
                    'aciklama': self._mssql_cell_to_str(row[3]),
                    'marka': self._mssql_cell_to_str(row[4]),
                    'fatura_durumu': self._mssql_cell_to_str(row[5]),
                    'toplam_miktar': float(row[6]) if row[6] is not None else None,
                    'agirlikli_ort_birim': float(row[7]) if row[7] is not None else None,
                })

            total_pages = (total_count + page_size - 1) // page_size if total_count else 0
            start_item = offset + 1 if total_count else 0
            end_item = min(offset + page_size, total_count) if total_count else 0

            pagination = {
                'total_count': total_count,
                'total_pages': total_pages,
                'current_page': page,
                'page_size': page_size,
                'has_next': page < total_pages if total_pages else False,
                'has_previous': page > 1,
                'next_page': page + 1 if page < total_pages else None,
                'previous_page': page - 1 if page > 1 else None,
                'start_item': start_item,
                'end_item': end_item,
            }
            return results, pagination
        except Exception as e:
            logger.error(f'get_ortalama_maliyet_paginated error: {e}')
            return [], {
                'total_count': 0,
                'total_pages': 0,
                'current_page': 1,
                'page_size': page_size,
                'has_next': False,
                'has_previous': False,
                'next_page': None,
                'previous_page': None,
                'start_item': 0,
                'end_item': 0,
            }

    def get_ortalama_maliyet_export_rows(
        self,
        sort_by='malzeme_kodu',
        sort_dir='asc',
        max_rows=50000,
        fatura_turu=None,
        malzeme_turu=None,
        malzeme_kodu=None,
        marka=None,
        aciklama=None,
        toplam_miktar_min=None,
        toplam_miktar_max=None,
    ):
        """Excel için aynı filtrelerle en fazla max_rows satır."""
        sort_key = sort_by if sort_by in self.OM_SORT_KEYS else 'malzeme_kodu'
        order_col = self._om_q(sort_key)
        order_dir = 'DESC' if str(sort_dir).lower() == 'desc' else 'ASC'
        where, params = self._ortalama_maliyet_where_params(
            fatura_turu, malzeme_turu, malzeme_kodu, marka, aciklama,
            toplam_miktar_min, toplam_miktar_max,
        )
        where_sql = ' AND '.join(where)
        base_from = 'FROM [GO3].[dbo].[ORTALAMA_MALIYET]'
        qf, qm, qk, qmarka, qa, qfd, qtm, qao = (
            self._om_q('fatura_turu'), self._om_q('malzeme_turu'),
            self._om_q('malzeme_kodu'), self._om_q('marka'), self._om_q('aciklama'),
            self._om_q('fatura_durumu'), self._om_q('toplam_miktar'), self._om_q('agirlikli_ort_birim'),
        )
        try:
            cap = min(max(1, int(max_rows)), 100000)
        except (TypeError, ValueError):
            cap = 50000
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            data_query = f"""
                SELECT {qf}, {qm}, {qk}, {qa}, {qmarka}, {qfd},
                       {qtm}, {qao}
                {base_from}
                WHERE {where_sql}
                ORDER BY {order_col} {order_dir}
                OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY
            """
            cursor.execute(data_query, params + [cap])
            raw = cursor.fetchall()
            conn.close()
            results = []
            for row in raw:
                results.append({
                    'fatura_turu': self._mssql_cell_to_str(row[0]),
                    'malzeme_turu': self._mssql_cell_to_str(row[1]),
                    'malzeme_kodu': self._mssql_cell_to_str(row[2]),
                    'aciklama': self._mssql_cell_to_str(row[3]),
                    'marka': self._mssql_cell_to_str(row[4]),
                    'fatura_durumu': self._mssql_cell_to_str(row[5]),
                    'toplam_miktar': float(row[6]) if row[6] is not None else None,
                    'agirlikli_ort_birim': float(row[7]) if row[7] is not None else None,
                })
            return results
        except Exception as e:
            logger.error(f'get_ortalama_maliyet_export_rows error: {e}')
            return []
    def insert_logo_kredi_karti_fisi(self, cari_ref, banka_ref, fis_no, belge_no, tarih, tutar, aciklama='Kredi kartı tahsilatı'):
        """
        Logo GO3 entegrasyonu: Kredi Kartı için 3 adımlı fiş kaydı.
        ADIM 1: LG_002_05_CLFICHE (fiş başlığı)
        ADIM 2: LG_002_05_CLFLINE (cari + banka satırı)
        ADIM 3: LG_002_05_PAYTRANS (ödeme/tahsilat hareketi)
        """
        from datetime import datetime

        # Tarih parse
        if isinstance(tarih, str):
            try:
                tarih_dt = datetime.strptime(tarih.split('.')[0], '%Y-%m-%d %H:%M:%S')
            except Exception:
                try:
                    tarih_dt = datetime.strptime(tarih, '%Y-%m-%d')
                except Exception:
                    tarih_dt = datetime.now()
        elif hasattr(tarih, 'year'):
            tarih_dt = tarih
        else:
            tarih_dt = datetime.now()

        tarih_str = tarih_dt.strftime('%Y-%m-%d %H:%M:%S')
        ay = tarih_dt.month
        yil = tarih_dt.year

        # FICHENO = frontend'deki "Fiş Numarası" alanından gelen değer (fis_no parametresi)
        # @FisNo (SQL örneğindeki) = form'daki Fiş Numarası alanı
        ficheno = str(fis_no)[:17] if fis_no else f"KK-{tarih_dt.strftime('%y%m%d%H%M')}"[:17]

        belge_no_str = str(belge_no)[:33] if belge_no else ''
        aciklama_str = str(aciklama)[:251] if aciklama else 'Kredi kartı tahsilatı'
        tutar_float = float(tutar)

        # ============================================================
        # ADIM 1: CLFICHE — Fiş başlığı (TRCODE=70, GENEXCTYP=3)
        # Logo standardı: CLCARDREF, BANKACCREF, BNACCREF = 0
        # ============================================================
        insert_clfiche_sql = """
        SET NOCOUNT ON;
        INSERT INTO [GO3].[dbo].[LG_002_05_CLFICHE]
        (
            [FICHENO], [DATE_], [TRCODE], [BRANCH], [DEPARTMENT],
            [GENEXP1], [DEBIT], [CREDIT], [REPDEBIT], [REPCREDIT],
            [CLCARDREF], [BANKACCREF], [BNACCREF], [STATUS],
            [CANCELLED], [CANCELLEDACC], [ACCOUNTED],
            [GENEXCTYP], [LINEEXCTYP], [RECSTATUS], [SITEID],
            [DEVIR], [AFFECTRISK], [POSTERMINALNUM],
            [HOUR_], [MINUTE_],
            [CAPIBLOCK_CREATEDBY], [CAPIBLOCK_CREADEDDATE],
            [CAPIBLOCK_CREATEDHOUR], [CAPIBLOCK_CREATEDMIN], [CAPIBLOCK_CREATEDSEC]
        )
        VALUES
        (
            ?, ?, 70, 0, 0,
            ?, 0, ?, 0, ?,
            0, 0, 0, 0,
            0, 0, 0,
            3, 0, 1, 0,
            0, 1, '005',
            ?, ?,
            1, ?,
            ?, ?, ?
        );
        SELECT SCOPE_IDENTITY();
        """

        # ============================================================
        # ADIM 2: CLFLINE — Tek satır, hem CLIENTREF hem BANKACCREF burada
        # SIGN=1 (Alacak), MODULENR=5
        # ============================================================
        insert_clfline_sql = """
        SET NOCOUNT ON;
        INSERT INTO [GO3].[dbo].[LG_002_05_CLFLINE]
        (
            [CLIENTREF], [BANKACCREF], [BNACCREF], [SOURCEFREF],
            [DATE_], [BRANCH], [DEPARTMENT], [MODULENR], [TRCODE], [LINENR], [TRANNO],
            [LINEEXP], [SIGN], [AMOUNT],
            [TRCURR], [TRRATE], [TRNET], [REPORTRATE], [REPORTNET], [BNLNTRNET],
            [STATUS], [CANCELLED], [ACCOUNTED], [TRGFLAG],
            [VATRATE], [VATAMOUNT], [RECSTATUS], [SITEID], [DEVIR],
            [AFFECTRISK], [MONTH_], [YEAR_]
        )
        VALUES
        (
            ?, ?, 0, ?,
            ?, 0, 0, 5, 70, 1, 1,
            ?, 1, ?,
            0, 1, ?, 1, ?, ?,
            0, 0, 0, 0,
            0, 0, 1, 0, 0,
            1, ?, ?
        );
        SELECT SCOPE_IDENTITY();
        """

        # ============================================================
        # ADIM 3: PAYTRANS — FICHEREF olarak CLFLINE'ın @LineRef'i verilir
        # PAYMENTTYPE=4 (Kredi Kartı)
        # ============================================================
        insert_paytrans_sql = """
        INSERT INTO [GO3].[dbo].[LG_002_05_PAYTRANS]
        (
            [CARDREF], [DATE_], [MODULENR], [SIGN], [FICHEREF], [FICHELINEREF], [TRCODE],
            [TOTAL], [PAID], [EARLYINTRATE], [LATELYINTRATE], [CROSSREF], [PAIDINCASH], [CANCELLED],
            [PROCDATE], [TRCURR], [TRRATE], [REPORTRATE], [MODIFIED], [REMINDLEV], [REMINDSENT],
            [CROSSCURR], [CROSSTOTAL], [DISCFLAG], [SITEID], [ORGLOGICREF], [WFSTATUS],
            [CLOSINGRATE], [DISCDUEDATE], [OPSTAT], [RECSTATUS], [INFIDX], [PAYNO], [DELAYTOTAL],
            [BANKACCREF], [PAYMENTTYPE], [CASHACCREF], [TRNET], [REPAYPLANREF]
        )
        VALUES
        (
            ?, ?, 5, 1, ?, 0, 70,
            ?, 0, 0, 0, 0, 0, 0,
            ?, 0, 0, 1, 0, 0, 0,
            0, 0, 0, 0, 0, 0,
            0, ?, 0, 0, 0, 1, 0,
            ?, 4, 0, 0, 0
        )
        """

        conn = self.get_connection()
        try:
            cursor = conn.cursor()

            now = datetime.now()
            hour_ = now.hour
            minute_ = now.minute
            capi_date = now.strftime('%Y-%m-%d 00:00:00.000')

            # ADIM 1: CLFICHE
            aciklama_str = str(aciklama) if aciklama else 'Kredi kartı tahsilatı'
            clfiche_params = [
                ficheno, tarih_str,
                aciklama_str, tutar_float, tutar_float,
                hour_, minute_,
                capi_date,
                hour_, minute_, now.second
            ]
            cursor.execute(insert_clfiche_sql, clfiche_params)
            fiche_row = cursor.fetchone()
            if not fiche_row or fiche_row[0] is None:
                raise Exception("CLFICHE INSERT failed — no LOGICALREF returned")
            fiche_ref = int(fiche_row[0])

            # ADIM 2: CLFLINE
            clfline_params = [
                int(cari_ref), int(banka_ref), fiche_ref,
                tarih_str,
                aciklama_str, tutar_float,
                tutar_float, tutar_float, tutar_float,
                ay, yil
            ]
            cursor.execute(insert_clfline_sql, clfline_params)
            clfline_row = cursor.fetchone()
            if not clfline_row or clfline_row[0] is None:
                raise Exception("CLFLINE INSERT failed — no LOGICALREF returned")
            line_ref = int(clfline_row[0])

            # ADIM 3: PAYTRANS (FICHEREF = line_ref, not fiche_ref!)
            paytrans_params = [
                int(cari_ref), tarih_str, line_ref,
                tutar_float,
                tarih_str,
                tarih_str,
                int(banka_ref)
            ]
            cursor.execute(insert_paytrans_sql, paytrans_params)

            conn.commit()
            logger.info(f"Logo KK fişi eklendi: FicheRef={fiche_ref}, LineRef={line_ref}, FisNo={ficheno}, Tutar={tutar_float}")
            return {'fiche_ref': fiche_ref, 'line_ref': line_ref}

        except Exception as e:
            conn.rollback()
            logger.error(f"insert_logo_kredi_karti_fisi error: {e}")
            raise e
        finally:
            conn.close()


    def insert_logo_banka_havalesi_fisi(self, cari_ref, banka_ref, fis_no, belge_no, tarih, tutar, aciklama='Banka havalesi tahsilatı'):
        """
        Logo GO3 entegrasyonu: Banka Havalesi (Gelen EFT) için 4 adımlı fiş kaydı.
        ADIM 1: LG_002_05_BNFICHE  — Banka fiş başlığı   (TRCODE=3, MODULENR=7)
        ADIM 2: LG_002_05_BNFLINE  — Banka fiş satırı    (TRCODE=3, MODULENR=7)
        ADIM 3: LG_002_05_CLFLINE  — Cari hesap hareketi (TRCODE=20, MODULENR=7)
        ADIM 4: LG_002_05_PAYTRANS — Ödeme/tahsilat takibi (TRCODE=20)
        """
        from datetime import datetime

        # Tarih parse
        if isinstance(tarih, str):
            try:
                tarih_dt = datetime.strptime(tarih.split('.')[0], '%Y-%m-%d %H:%M:%S')
            except Exception:
                try:
                    tarih_dt = datetime.strptime(tarih, '%Y-%m-%d')
                except Exception:
                    tarih_dt = datetime.now()
        elif hasattr(tarih, 'year'):
            tarih_dt = tarih
        else:
            tarih_dt = datetime.now()

        tarih_str = tarih_dt.strftime('%Y-%m-%d %H:%M:%S')
        ay = tarih_dt.month
        yil = tarih_dt.year

        ficheno = str(fis_no)[:17] if fis_no else f"BNK-{tarih_dt.strftime('%y%m%d%H%M')}"[:17]
        belge_no_str = str(belge_no)[:33] if belge_no else ''
        aciklama_str = str(aciklama)[:251] if aciklama else 'Banka havalesi tahsilatı'
        tutar_float = float(tutar)
        banka_ref_int = int(banka_ref)
        cari_ref_int = int(cari_ref)

        # ============================================================
        # ADIM 1: BNFICHE — Banka Fiş Başlığı (TRCODE=3 Gelen Havale/EFT)
        # ============================================================
        insert_bnfiche_sql = """
        SET NOCOUNT ON;
        INSERT INTO [GO3].[dbo].[LG_002_05_BNFICHE]
        (
            [FICHENO], [DATE_], [TRCODE], [MODULENR],
            [BRANCH], [DEPARMENT], [GENEXP1],
            [DEBITTOT], [CREDITTOT], [REPDEBIT], [REPCREDIT],
            [ACCOUNTED], [CANCELLED], [SIGN], [STATUS], [RECSTATUS],
            [CAPIBLOCK_CREATEDBY], [CAPIBLOCK_CREADEDDATE],
            [CAPIBLOCK_CREATEDHOUR], [CAPIBLOCK_CREATEDMIN], [CAPIBLOCK_CREATEDSEC],
            [CANCELLEDACC], [GENEXCTYP], [LINEEXCTYP], [FTIME]
        )
        VALUES
        (
            ?, ?, 3, 7,
            0, 0, ?,
            ?, 0, ?, 0,
            0, 0, 0, 0, 1,
            1, ?,
            ?, ?, ?,
            0, 3, 0, 320808538
        );
        SELECT SCOPE_IDENTITY();
        """

        # ============================================================
        # ADIM 2: BNFLINE — Banka Fiş Satırı (TRCODE=3)
        # ============================================================
        insert_bnfline_sql = """
        SET NOCOUNT ON;
        INSERT INTO [GO3].[dbo].[LG_002_05_BNFLINE]
        (
            [BANKREF], [BNACCREF], [CLIENTREF], [SOURCEFREF],
            [TRANSTYPE], [DATE_], [TRANSDUEDATE], [DEPARTMENT], [BRANCH],
            [SIGN], [TRCODE], [MODULENR], [LINENR],
            [TRANNO], [LINEEXP], [ACCOUNTED], [CANCELLED],
            [TRCURR], [AMOUNT], [TRRATE], [TRNET], [REPORTRATE], [REPORTNET],
            [BANKPROCTYPE], [COSTTYPE], [STATUS], [RECSTATUS], [TIME_],
            [CAPIBLOCK_CREATEDBY], [CAPIBLOCK_CREADEDDATE],
            [CAPIBLOCK_CREATEDHOUR], [CAPIBLOCK_CREATEDMIN], [CAPIBLOCK_CREATEDSEC],
            [LINEEXCTYP]
        )
        VALUES
        (
            ?, ?, ?, ?,
            1, ?, ?, 0, 0,
            0, 3, 7, 1,
            ?, ?, 0, 0,
            0, ?, 0, ?, 1, ?,
            2, 1, 0, 0, 320808538,
            1, ?,
            ?, ?, ?,
            0
        );
        SELECT SCOPE_IDENTITY();
        """

        # ============================================================
        # ADIM 3: CLFLINE — Cari Hesap Hareketi (TRCODE=20 Gelen Havale)
        # ============================================================
        insert_clfline_sql = """
        SET NOCOUNT ON;
        INSERT INTO [GO3].[dbo].[LG_002_05_CLFLINE]
        (
            [CLIENTREF], [BANKACCREF], [BNACCREF], [SOURCEFREF],
            [DATE_], [DOCDATE], [BRANCH], [DEPARTMENT], [MODULENR], [TRCODE], [TRANNO], [LINEEXP],
            [SIGN], [AMOUNT], [TRCURR], [TRRATE], [TRNET], [REPORTRATE], [REPORTNET], [BNLNTRNET],
            [STATUS], [RECSTATUS], [AFFECTRISK], [MONTH_], [YEAR_], [FTIME],
            [ACCOUNTED], [CANCELLED], [TRGFLAG], [DEVIR], [VATRATE], [VATAMOUNT], [SITEID], [LINEEXCTYP],
            [CAPIBLOCK_CREATEDBY], [CAPIBLOCK_CREADEDDATE],
            [CAPIBLOCK_CREATEDHOUR], [CAPIBLOCK_CREATEDMIN], [CAPIBLOCK_CREATEDSEC]
        )
        VALUES
        (
            ?, ?, ?, ?,
            ?, ?, 0, 0, 7, 20, ?, ?,
            1, ?, 0, 1, ?, 1, ?, ?,
            0, 1, 1, ?, ?, 320808538,
            0, 0, 0, 0, 0, 0, 0, 0,
            1, ?,
            ?, ?, ?
        );
        SELECT SCOPE_IDENTITY();
        """

        # ============================================================
        # ADIM 4: PAYTRANS — Ödeme/Tahsilat Takibi (TRCODE=20, PAYMENTTYPE=1 Havale)
        # ============================================================
        insert_paytrans_sql = """
        INSERT INTO [GO3].[dbo].[LG_002_05_PAYTRANS]
        (
            [CARDREF], [BANKACCREF], [DATE_], [MODULENR], [SIGN],
            [FICHEREF], [TRCODE], [TOTAL], [PROCDATE],
            [TRCURR], [TRRATE], [REPORTRATE],
            [PAYNO], [PAYMENTTYPE], [PAID], [CANCELLED]
        )
        VALUES
        (
            ?, ?, ?, 7, 1,
            ?, 20, ?, ?,
            0, 1, 1,
            1, 1, 0, 0
        )
        """

        conn = self.get_connection()
        try:
            cursor = conn.cursor()

            now = datetime.now()
            capi_date = now.strftime('%Y-%m-%d 00:00:00.000')

            # ADIM 1: BNFICHE
            bnfiche_params = [
                ficheno, tarih_str,
                aciklama_str,
                tutar_float, tutar_float,
                capi_date,
                now.hour, now.minute, now.second
            ]
            cursor.execute(insert_bnfiche_sql, bnfiche_params)
            bnfiche_row = cursor.fetchone()
            if not bnfiche_row or bnfiche_row[0] is None:
                raise Exception("BNFICHE INSERT failed — no LOGICALREF returned")
            bnfiche_ref = int(bnfiche_row[0])

            # ADIM 2: BNFLINE
            bnfline_params = [
                banka_ref_int, banka_ref_int, cari_ref_int, bnfiche_ref,
                tarih_str, tarih_str,
                ficheno, aciklama_str,
                tutar_float, tutar_float, tutar_float,
                capi_date,
                now.hour, now.minute, now.second
            ]
            cursor.execute(insert_bnfline_sql, bnfline_params)
            bnfline_row = cursor.fetchone()
            if not bnfline_row or bnfline_row[0] is None:
                raise Exception("BNFLINE INSERT failed — no LOGICALREF returned")
            bnfline_ref = int(bnfline_row[0])

            # ADIM 3: CLFLINE
            clfline_params = [
                cari_ref_int, banka_ref_int, banka_ref_int, bnfiche_ref,
                tarih_str, tarih_str,
                ficheno, aciklama_str,
                tutar_float, tutar_float, tutar_float, tutar_float,
                ay, yil,
                capi_date,
                now.hour, now.minute, now.second
            ]
            cursor.execute(insert_clfline_sql, clfline_params)
            clfline_row = cursor.fetchone()
            if not clfline_row or clfline_row[0] is None:
                raise Exception("CLFLINE INSERT failed — no LOGICALREF returned")
            clfline_ref = int(clfline_row[0])

            # ADIM 4: PAYTRANS (FICHEREF = clfline_ref, SQL ile aynı mantık)
            paytrans_params = [
                cari_ref_int, banka_ref_int, tarih_str,
                clfline_ref, tutar_float, tarih_str
            ]
            cursor.execute(insert_paytrans_sql, paytrans_params)

            conn.commit()
            logger.info(
                f"Logo Banka Havalesi fişi eklendi: BnFicheRef={bnfiche_ref}, "
                f"BnLineRef={bnfline_ref}, ClfLineRef={clfline_ref}, "
                f"FisNo={ficheno}, Tutar={tutar_float}"
            )
            return {
                'bnfiche_ref': bnfiche_ref,
                'bnfline_ref': bnfline_ref,
                'clfline_ref': clfline_ref
            }

        except Exception as e:
            conn.rollback()
            logger.error(f"insert_logo_banka_havalesi_fisi error: {e}")
            raise e
        finally:
            conn.close()

    def insert_logo_nakit_fisi(self, cari_ref, kasa_ref, fis_no, tarih, tutar, aciklama='Cari Nakit Tahsilat'):
        """
        Logo GO3 entegrasyonu: Nakit Kasa Tahsilatı için 4 adımlı fiş kaydı.
        ADIM 1: LG_002_05_KSLINES INSERT  — Kasa hareket satırı      (TRCODE=11)
        ADIM 2: LG_002_05_CLFLINE INSERT  — Cari hesap hareketi      (TRCODE=1,  MODULENR=10)
        ADIM 3: LG_002_05_KSLINES UPDATE  — TRANSREF çapraz bağlantı
        ADIM 4: LG_002_05_PAYTRANS INSERT — Ödeme/tahsilat takibi    (TRCODE=1,  MODULENR=10)
        """
        from datetime import datetime

        # Tarih parse
        if isinstance(tarih, str):
            try:
                tarih_dt = datetime.strptime(tarih.split('.')[0], '%Y-%m-%d %H:%M:%S')
            except Exception:
                try:
                    tarih_dt = datetime.strptime(tarih, '%Y-%m-%d')
                except Exception:
                    tarih_dt = datetime.now()
        elif hasattr(tarih, 'year'):
            tarih_dt = tarih
        else:
            tarih_dt = datetime.now()

        tarih_str = tarih_dt.strftime('%Y-%m-%d %H:%M:%S')
        ay = tarih_dt.month
        yil = tarih_dt.year

        ficheno = str(fis_no)[:17] if fis_no else f"NKT-{tarih_dt.strftime('%y%m%d%H%M')}"[:17]
        aciklama_str = str(aciklama)[:251] if aciklama else 'Cari Nakit Tahsilat'
        tutar_float = float(tutar)
        kasa_ref_int = int(kasa_ref)
        cari_ref_int = int(cari_ref)

        # ============================================================
        # ADIM 1: KSLINES INSERT — Kasa Hareket Satırı (TRCODE=11)
        # ============================================================
        insert_kslines_sql = """
        SET NOCOUNT ON;
        INSERT INTO [GO3].[dbo].[LG_002_05_KSLINES]
        (
            [CARDREF], [DATE_], [HOUR_], [MINUTE_], [TRCODE],
            [BRANCH], [DEPARTMENT], [FICHENO], [CUSTTITLE], [CUSTTITLE3], [AMOUNT],
            [REPORTRATE], [REPORTNET], [TRRATE], [TRNET], [TRCURR], [SIGN],
            [ACCOUNTED], [CANCELLED], [STATUS], [RECSTATUS], [TIME_],
            [CAPIBLOCK_CREATEDBY], [CAPIBLOCK_CREADEDDATE],
            [CAPIBLOCK_CREATEDHOUR], [CAPIBLOCK_CREATEDMIN], [CAPIBLOCK_CREATEDSEC],
            [CANCELLEDACC], [GENEXCTYP], [LINEEXCTYP], [AFFECTRISK], [REFLECTED]
        )
        VALUES
        (
            ?, ?, ?, ?, 11,
            0, 0, ?, ?, ?, ?,
            1, ?, 1, ?, 0, 0,
            0, 0, 0, 1, ?,
            1, ?,
            ?, ?, ?,
            0, 0, 0, 0, 0
        );
        SELECT SCOPE_IDENTITY();
        """

        # ============================================================
        # ADIM 2: CLFLINE INSERT — Cari Hesap Hareketi (TRCODE=1, MODULENR=10)
        # ============================================================
        insert_clfline_sql = """
        SET NOCOUNT ON;
        INSERT INTO [GO3].[dbo].[LG_002_05_CLFLINE]
        (
            [CLIENTREF], [SOURCEFREF], [DATE_], [DOCDATE],
            [BRANCH], [DEPARTMENT], [MODULENR], [TRCODE], [LINENR], [TRANNO], [LINEEXP],
            [SIGN], [AMOUNT], [TRCURR], [TRRATE], [TRNET], [REPORTRATE], [REPORTNET],
            [STATUS], [RECSTATUS], [AFFECTRISK], [MONTH_], [YEAR_], [FTIME],
            [ACCOUNTED], [CANCELLED], [TRGFLAG], [DEVIR], [VATRATE], [VATAMOUNT], [SITEID], [LINEEXCTYP],
            [CAPIBLOCK_CREATEDBY], [CAPIBLOCK_CREADEDDATE],
            [CAPIBLOCK_CREATEDHOUR], [CAPIBLOCK_CREATEDMIN], [CAPIBLOCK_CREATEDSEC]
        )
        VALUES
        (
            ?, ?, ?, ?,
            0, 0, 10, 1, 0, ?, ?,
            1, ?, 0, 1, ?, 1, ?,
            0, 0, 1, ?, ?, ?,
            0, 0, 0, 0, 0, 0, 0, 0,
            1, ?,
            ?, ?, ?
        );
        SELECT SCOPE_IDENTITY();
        """

        # ============================================================
        # ADIM 3: KSLINES UPDATE — TRANSREF çapraz bağlantı
        # ============================================================
        update_kslines_sql = """
        UPDATE [GO3].[dbo].[LG_002_05_KSLINES]
        SET TRANSREF = ?
        WHERE LOGICALREF = ?
        """

        # ============================================================
        # ADIM 4: PAYTRANS INSERT — Ödeme/Tahsilat Takibi (TRCODE=1, MODULENR=10)
        # ============================================================
        insert_paytrans_sql = """
        INSERT INTO [GO3].[dbo].[LG_002_05_PAYTRANS]
        (
            [CARDREF], [DATE_], [MODULENR], [SIGN], [FICHEREF], [TRCODE],
            [TOTAL], [PROCDATE], [TRCURR], [TRRATE], [REPORTRATE],
            [PAYNO], [PAYMENTTYPE], [PAID], [CANCELLED]
        )
        VALUES
        (
            ?, ?, 10, 1, ?, 1,
            ?, ?, 0, 1, 1,
            1, 1, 0, 0
        )
        """

        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            now = datetime.now()
            capi_date = now.strftime('%Y-%m-%d 00:00:00.000')

            # Cari unvan ve kodunu çek (KSLINES'ta CUSTTITLE, CUSTTITLE3 gerekiyor)
            cari_unvan = ''
            cari_kodu = ''
            try:
                cursor.execute(
                    "SELECT DEFINITION_, CODE FROM [GO3].[dbo].[LG_002_CLCARD] WHERE LOGICALREF = ?",
                    [cari_ref_int]
                )
                row = cursor.fetchone()
                if row:
                    cari_unvan = str(row[0] or '')[:200]
                    cari_kodu = str(row[1] or '')[:50]
            except Exception as e:
                logger.warning(f"insert_logo_nakit_fisi: cari bilgisi alınamadı: {e}")

            # TimeInt hesabı (SQL ile aynı formül)
            time_int = (now.hour * 16777216) + (now.minute * 65536) + (now.second * 256)

            # ADIM 1: KSLINES
            kslines_params = [
                kasa_ref_int, tarih_str, now.hour, now.minute,
                ficheno, cari_unvan, cari_kodu, tutar_float,
                tutar_float, tutar_float,
                time_int,
                capi_date,
                now.hour, now.minute, now.second
            ]
            cursor.execute(insert_kslines_sql, kslines_params)
            ks_row = cursor.fetchone()
            if not ks_row or ks_row[0] is None:
                raise Exception("KSLINES INSERT failed — no LOGICALREF returned")
            ks_line_ref = int(ks_row[0])

            # ADIM 2: CLFLINE
            clfline_params = [
                cari_ref_int, ks_line_ref, tarih_str, tarih_str,
                ficheno, aciklama_str,
                tutar_float, tutar_float, tutar_float,
                ay, yil, time_int,
                capi_date,
                now.hour, now.minute, now.second
            ]
            cursor.execute(insert_clfline_sql, clfline_params)
            clf_row = cursor.fetchone()
            if not clf_row or clf_row[0] is None:
                raise Exception("CLFLINE INSERT failed — no LOGICALREF returned")
            clf_line_ref = int(clf_row[0])

            # ADIM 3: KSLINES UPDATE — TRANSREF çapraz bağlantı
            cursor.execute(update_kslines_sql, [clf_line_ref, ks_line_ref])

            # ADIM 4: PAYTRANS
            paytrans_params = [
                cari_ref_int, tarih_str, ks_line_ref,
                tutar_float, tarih_str
            ]
            cursor.execute(insert_paytrans_sql, paytrans_params)

            conn.commit()
            logger.info(
                f"Logo Nakit fişi eklendi: KsLineRef={ks_line_ref}, "
                f"ClfLineRef={clf_line_ref}, FisNo={ficheno}, Tutar={tutar_float}"
            )
            return {
                'ks_line_ref': ks_line_ref,
                'clf_line_ref': clf_line_ref
            }

        except Exception as e:
            conn.rollback()
            logger.error(f"insert_logo_nakit_fisi error: {e}")
            raise e
        finally:
            conn.close()

    def get_next_fis_no(self, tahsilat_turu):


        """Tahsilat türüne göre sıradaki fiş numarasını üretir"""
        from datetime import datetime
        yil2 = datetime.now().strftime('%y')  # 2-digit year, e.g. '26'

        if tahsilat_turu == "Nakit":
            prefix = f"NKT{yil2}-"
        elif tahsilat_turu == "Kredi Kartı":
            prefix = f"KK{yil2}-"
        elif tahsilat_turu == "Banka Havalesi":
            prefix = f"BNK{yil2}-"
        else:
            return ""

        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Bu yıla ait prefix ile en son fiş numarasını bul
            query = """
            SELECT TOP 1 FisNo 
            FROM [GO3].[dbo].[TAHSILATTB] 
            WHERE FisNo LIKE ? 
            ORDER BY LEN(FisNo) DESC, FisNo DESC
            """
            cursor.execute(query, (prefix + "%",))
            row = cursor.fetchone()

            next_num = 1
            if row and row[0]:
                import re
                m = re.search(r'(\d+)$', str(row[0]))
                if m:
                    next_num = int(m.group(1)) + 1

            next_fis_no = f"{prefix}{str(next_num).zfill(5)}"

            conn.close()
            return next_fis_no

        except Exception as e:
            logger.error(f"get_next_fis_no error: {e}")
            return ""

    def get_stok_maliyet_dashboard(self):
        """[GO3].[dbo].[STOK_MALIYET] view'ından özet grid verisini getirir (~160 satır).

        Satırlar marka ASC sıralı döner; arama ve yeniden sıralama şablonda
        client-side yapılır. Ek olarak maliyete göre en yüksek 10 marka
        (mini bar paneli için) ve genel toplamlar hesaplanır.

        .. deprecated::
            2026-07-03: ``/ambar-deger-raporu/`` sayfası artık sadece
            ``STOK_MALIYET_DETAYLI`` view'ından besleniyor. Bu fonksiyon
            sayfadan çağrılmıyor; geriye dönük uyumluluk için bırakıldı.
            ``get_ambar_deger_raporu()`` üzerinden ``top_markalar`` /
            ``top_turler`` kullanılmalıdır.

        Dönüş:
            rows           : [{'marka','malzeme_turu','stok','maliyet','pay'}]
            toplam_stok    : tüm satırların stok toplamı
            toplam_maliyet : tüm satırların maliyet toplamı
            marka_sayisi   : benzersiz marka sayısı
            tur_sayisi     : benzersiz malzeme türü sayısı
            top_markalar   : [{'marka','stok','maliyet','pay','bar_pct'}] (ilk 10)
            pivot_marka    : [{'ad','stok','maliyet','pay','alt_sayi',
                               'detaylar': [{'ad','stok','maliyet','pay'}]}]
                             (marka A-Z; pay üst satırda genel toplam içindeki,
                              alt satırlarda grubun içindeki pay)
            pivot_tur      : aynı yapı, malzeme türü -> markalar
        """
        empty = {
            'rows': [],
            'toplam_stok': 0.0,
            'toplam_maliyet': 0.0,
            'marka_sayisi': 0,
            'tur_sayisi': 0,
            'top_markalar': [],
            'pivot_marka': [],
            'pivot_tur': [],
        }

        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
                SELECT [MARKA],
                       [MALZEME TÜRÜ],
                       [TOPLAM STOK MİKTARI],
                       [TOPLAM STOK MALİYETİ]
                FROM [GO3].[dbo].[STOK_MALIYET]
            """
            cursor.execute(query)
            raw = cursor.fetchall()
            conn.close()

            def _to_float(val):
                if val is None or val == '':
                    return 0.0
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return 0.0

            def _to_str(val):
                if val is None:
                    return ''
                try:
                    return self.safe_decode_string(val)
                except Exception:
                    return str(val)

            rows = []
            toplam_stok = 0.0
            toplam_maliyet = 0.0
            marka_set = set()
            tur_set = set()

            for row in raw:
                marka = _to_str(row[0])
                malzeme_turu = _to_str(row[1])
                stok = _to_float(row[2])
                maliyet = _to_float(row[3])
                rows.append({
                    'marka': marka,
                    'malzeme_turu': malzeme_turu,
                    'stok': stok,
                    'maliyet': maliyet,
                })
                toplam_stok += stok
                toplam_maliyet += maliyet
                if marka:
                    marka_set.add(marka)
                if malzeme_turu:
                    tur_set.add(malzeme_turu)

            for r in rows:
                r['pay'] = (r['maliyet'] / toplam_maliyet * 100.0) if toplam_maliyet > 0 else 0.0

            rows.sort(key=lambda r: ((r['marka'] or '').lower(), (r['malzeme_turu'] or '').lower()))

            # Top 10 marka (maliyete göre) — mini bar paneli için
            marka_agg = {}
            for r in rows:
                key = r['marka'] or '(Belirtilmemiş)'
                if key not in marka_agg:
                    marka_agg[key] = {'marka': key, 'stok': 0.0, 'maliyet': 0.0}
                marka_agg[key]['stok'] += r['stok']
                marka_agg[key]['maliyet'] += r['maliyet']

            top_markalar = sorted(
                marka_agg.values(), key=lambda m: m['maliyet'], reverse=True)[:10]
            max_maliyet = top_markalar[0]['maliyet'] if top_markalar else 0.0
            for m in top_markalar:
                m['pay'] = (m['maliyet'] / toplam_maliyet * 100.0) if toplam_maliyet > 0 else 0.0
                m['bar_pct'] = (m['maliyet'] / max_maliyet * 100.0) if max_maliyet > 0 else 0.0

            # Pivot gruplamaları: marka -> türler ve tür -> markalar
            def _build_pivot(group_field, detail_field):
                groups = {}
                for r in rows:
                    g_key = r[group_field] or '(Belirtilmemiş)'
                    d_key = r[detail_field] or '(Belirtilmemiş)'
                    grp = groups.setdefault(g_key, {
                        'ad': g_key, 'stok': 0.0, 'maliyet': 0.0, '_alt': {}})
                    grp['stok'] += r['stok']
                    grp['maliyet'] += r['maliyet']
                    alt = grp['_alt'].setdefault(d_key, {
                        'ad': d_key, 'stok': 0.0, 'maliyet': 0.0})
                    alt['stok'] += r['stok']
                    alt['maliyet'] += r['maliyet']

                pivot = []
                for grp in sorted(groups.values(), key=lambda g: g['ad'].lower()):
                    detaylar = sorted(
                        grp.pop('_alt').values(),
                        key=lambda d: d['maliyet'], reverse=True)
                    for d in detaylar:
                        d['pay'] = (d['maliyet'] / grp['maliyet'] * 100.0) if grp['maliyet'] > 0 else 0.0
                    grp['pay'] = (grp['maliyet'] / toplam_maliyet * 100.0) if toplam_maliyet > 0 else 0.0
                    grp['alt_sayi'] = len(detaylar)
                    grp['detaylar'] = detaylar
                    pivot.append(grp)
                return pivot

            pivot_marka = _build_pivot('marka', 'malzeme_turu')
            pivot_tur = _build_pivot('malzeme_turu', 'marka')

            return {
                'rows': rows,
                'toplam_stok': toplam_stok,
                'toplam_maliyet': toplam_maliyet,
                'marka_sayisi': len(marka_set),
                'tur_sayisi': len(tur_set),
                'top_markalar': top_markalar,
                'pivot_marka': pivot_marka,
                'pivot_tur': pivot_tur,
            }

        except Exception as e:
            logger.error(f"get_stok_maliyet_dashboard error: {e}", exc_info=True)
            return empty

    def get_stok_maliyet_treemap_data(self, filtre_marka=None, filtre_tur=None):
        """[GO3].[dbo].[STOK_MALIYET] view'ından treemap için hiyerarşik veri üretir.

        .. deprecated::
            2026-07-03: ``/ambar-deger-raporu/`` sayfası artık sadece
            ``STOK_MALIYET_DETAYLI`` view'ından besleniyor. Repo içinde
            başka çağıranı yok; ileride silinebilir.

        Sonuç yapısı:
            {
                'children': [
                    {
                        'marka': str,
                        'maliyet': float,
                        'stok': float,
                        'maliyet_pay': float,
                        'stok_pay': float,
                        'children': [
                            {'tur': str, 'maliyet': float, 'stok': float,
                             'maliyet_pay': float, 'stok_pay': float},
                            ...
                        ]
                    },
                    ...
                ],
                'total_maliyet': float,
                'total_stok': float,
            }

        Markalar maliyete göre azalan sıralı, türler de kendi markaları içinde
        maliyete göre azalan sıralı döner. ``filtre_marka`` ve ``filtre_tur`` ile
        veri önceden filtrelenebilir.
        """
        empty = {
            'children': [],
            'total_maliyet': 0.0,
            'total_stok': 0.0,
        }
        try:
            def _treemap_size_class(pay):
                if pay >= 35:
                    return 3
                if pay >= 14:
                    return 2
                return 1

            def _treemap_color(pay):
                if pay >= 30:
                    return '#0f172a'
                if pay >= 18:
                    return '#1e293b'
                if pay >= 10:
                    return '#334155'
                if pay >= 5:
                    return '#475569'
                return '#64748b'

            def _treemap_tur_color(pay):
                if pay >= 45:
                    return '#d4a017'
                if pay >= 25:
                    return '#e0b53a'
                if pay >= 12:
                    return '#c08a0f'
                return '#8a6a08'

            conn = self.get_connection()
            cursor = conn.cursor()

            query = """
                SELECT [MARKA],
                       [MALZEME TÜRÜ],
                       [TOPLAM STOK MİKTARI],
                       [TOPLAM STOK MALİYETİ]
                FROM [GO3].[dbo].[STOK_MALIYET]
            """

            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()

            def _to_float(val):
                if val is None or val == '':
                    return 0.0
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return 0.0

            def _to_str(val):
                if val is None:
                    return ''
                try:
                    return self.safe_decode_string(val)
                except Exception:
                    return str(val)

            filtre_marka_norm = (filtre_marka or '').strip()
            filtre_tur_norm = (filtre_tur or '').strip()

            data_rows = []
            for row in rows:
                marka = _to_str(row[0])
                malzeme_turu = _to_str(row[1])
                if filtre_marka_norm and marka != filtre_marka_norm:
                    continue
                if filtre_tur_norm and malzeme_turu != filtre_tur_norm:
                    continue
                data_rows.append({
                    'marka': marka,
                    'malzeme_turu': malzeme_turu,
                    'stok': _to_float(row[2]),
                    'maliyet': _to_float(row[3]),
                })

            marka_map = {}
            total_maliyet = 0.0
            total_stok = 0.0
            for r in data_rows:
                marka_key = r['marka'] or '(Belirtilmemiş)'
                tur_key = r['malzeme_turu'] or '(Belirtilmemiş)'
                if marka_key not in marka_map:
                    marka_map[marka_key] = {
                        'marka': marka_key,
                        'stok': 0.0,
                        'maliyet': 0.0,
                        'turler': {},
                    }
                marka_map[marka_key]['stok'] += r['stok']
                marka_map[marka_key]['maliyet'] += r['maliyet']
                if tur_key not in marka_map[marka_key]['turler']:
                    marka_map[marka_key]['turler'][tur_key] = {
                        'tur': tur_key,
                        'stok': 0.0,
                        'maliyet': 0.0,
                    }
                marka_map[marka_key]['turler'][tur_key]['stok'] += r['stok']
                marka_map[marka_key]['turler'][tur_key]['maliyet'] += r['maliyet']

                total_maliyet += r['maliyet']
                total_stok += r['stok']

            children = []
            for marka_key, m in marka_map.items():
                tur_children = []
                for tur_key, t in m['turler'].items():
                    tur_pay = (t['maliyet'] / m['maliyet'] * 100.0) if m['maliyet'] > 0 else 0.0
                    tur_children.append({
                        'tur': t['tur'],
                        'maliyet': t['maliyet'],
                        'stok': t['stok'],
                        'maliyet_pay': tur_pay,
                        'stok_pay': (t['stok'] / m['stok'] * 100.0)
                            if m['stok'] > 0 else 0.0,
                        'size_class': _treemap_size_class(tur_pay),
                        'tur_color': _treemap_tur_color(tur_pay),
                    })
                tur_children.sort(key=lambda x: x['maliyet'], reverse=True)
                marka_pay = (m['maliyet'] / total_maliyet * 100.0) if total_maliyet > 0 else 0.0
                children.append({
                    'marka': m['marka'],
                    'maliyet': m['maliyet'],
                    'stok': m['stok'],
                    'maliyet_pay': marka_pay,
                    'stok_pay': (m['stok'] / total_stok * 100.0) if total_stok > 0 else 0.0,
                    'size_class': _treemap_size_class(marka_pay),
                    'marka_color': _treemap_color(marka_pay),
                    'children': tur_children,
                })

            children.sort(key=lambda x: x['maliyet'], reverse=True)

            return {
                'children': children,
                'total_maliyet': total_maliyet,
                'total_stok': total_stok,
            }

        except Exception as e:
            logger.error(f"get_stok_maliyet_treemap_data error: {e}", exc_info=True)
            return empty



mssql_service = MSSQLService()
