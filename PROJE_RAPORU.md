# MRK Tahsilat — Proje Teknik Raporu

Bu belge, **mrktahsilat** kod tabanının mimarisini, kullanılan teknolojileri, veri kaynaklarını ve işlevsel kapsamını teknik perspektiften özetler. Üretim ortamı: **mrktahsilat.com** (Django tabanlı kurumsal web uygulaması).

---

## 1. Genel bakış

| Öğe | Açıklama |
|-----|----------|
| **Ürün adı** | MRK OTOMOTİV tahsilat ve operasyon portalı (marka metinlerinde “MRK OTOMOTİV Sistemi”) |
| **Amaç** | Logo GO3 ERP verileriyle entegre tahsilat girişi, muhasebe listeleri, stok/cari/satış raporları, iç iletişim ve yardımcı yönetim modülleri |
| **Mimari stil** | Sunucu tarafı render (Django Templates), çok sayıda sayfa görünümü; kısmi AJAX/Fetch API kullanımı |
| **Kod ölçeği (yaklaşık)** | `tahsilat/views.py` ~8.800 satır, `tahsilat/mssql_service.py` ~8.200 satır; ~52 HTML şablonu; 20+ Django migration |

---

## 2. Teknoloji yığını

### 2.1 Çalışma zamanı ve çatı

| Bileşen | Sürüm / not |
|---------|-------------|
| **Python** | 3.10 (venv ile) |
| **Django** | 4.2.16 |
| **WSGI sunucusu** | Gunicorn 21.2.0 (tipik üretim kurulumu) |
| **Statik dosya** | WhiteNoise 6.5.0 (olası kullanım; ayarlara bağlı) |

### 2.2 Veri ve raporlama kütüphaneleri

| Paket | Kullanım alanı |
|-------|----------------|
| **pyodbc** 4.0.39 | Microsoft SQL Server bağlantısı (ODBC Driver 17 for SQL Server) |
| **pandas** 2.3.3, **openpyxl** 3.1.5 | Excel (.xlsx) dışa aktarma |
| **xlsxwriter** 3.2.9 | Excel üretimi (alternatif rapor akışları) |
| **reportlab** 4.0.4 | PDF üretimi |
| **Pillow** 10.4.0 | Görsel/evrak işlemleri |

### 2.3 HTTP / entegrasyon

| Paket | Kullanım |
|-------|----------|
| **requests** 2.32.5 | Harici webhook çağrıları (ör. n8n) |

### 2.4 Ön yüz (istemci)

- **HTML5** şablonları, **Bootstrap 5** ve **Bootstrap Icons** (`base.html` ve alt şablonlar)
- **JavaScript**: sayfa içi filtreler, tablo arama/sıralama, `fetch` ile CSRF korumalı AJAX (tahsilat güncelleme, chat vb.)
- **Türkçe** arayüz (`LANGUAGE_CODE = tr`, `TIME_ZONE = Europe/Istanbul`)

---

## 3. Proje yapısı

```
mrktahsilat/          # Django proje paketi
  settings.py         # Ana yapılandırma (.env + env_loader)
  urls.py             # Kök URL: admin, reports, tahsilat uygulaması
  env_loader.py       # .env okuma yardımcıları
  wsgi.py / asgi.py
tahsilat/             # Ana iş uygulaması
  views.py            # HTTP görünümleri (çok geniş)
  mssql_service.py    # ERP SQL soyutlaması (çok geniş)
  models.py           # SQLite ORM modelleri
  urls.py             # Uygulama URL’leri (app_name: tahsilat)
  authentication.py # MSSQL tabanlı kimlik doğrulama backend’i
  middleware.py       # İstek loglama (URLDebugMiddleware)
  templatetags/       # Özel şablon etiketleri (ör. menü yetkisi)
  migrations/
reports/              # Raporlar / n8n sohbet API’si
  views.py
  urls.py
templates/            # Genel şablonlar (base.html, uygulama altları)
static/               # CSS, JS, görseller
media/                # Kullanıcı yüklemeleri (evraklar vb.)
logs/                 # django.log
db.sqlite3            # Django varsayılan veritabanı (ORM)
.env                  # Gizli anahtarlar (repoda olmamalı)
```

---

## 4. Mimari ve veri katmanı

### 4.1 İki katmanlı veri modeli

1. **SQLite (`db.sqlite3`) — Django ORM**
   - Oturumlar (`SESSION_ENGINE`: veritabanı)
   - `User` (Django) — MSSQL ile giriş sonrası senkronize kullanıcı kayıtları
   - Uygulama modelleri: `TahsilatEvrak`, `GiderMasraf`, `KullaniciYetki`, `CariGeckme`, çek/senet ve diğer yardımcı tablolar (modeller `tahsilat/models.py` içinde tanımlı)

2. **Microsoft SQL Server — ERP veri ambarı**
   - Şema: **`[GO3].[dbo].*`**
   - Bağlantı: `settings.MSSQL_CONFIG` (.env: `MSSQL_SERVER`, `MSSQL_PORT`, `MSSQL_DATABASE`, `MSSQL_USERNAME`, `MSSQL_PASSWORD`, sürücü ve charset)
   - Erişim: `MSSQLService` sınıfı (`mssql_service.py`) üzerinden parametreli sorgular; Türkçe karakter ve encoding için `safe_decode_string`, `normalize_turkish_chars` vb.

### 4.2 Önemli MSSQL nesneleri (örnekler)

Uygulama kodunda geçen başlıca tablo/görünümler (tam liste `mssql_service.py` içinde genişler):

| Nesne | Rol |
|-------|-----|
| **GunlukTahsilat_V** | Tahsilat listeleri, dashboard özetleri, teslim durumu güncellemeleri |
| **TAHSILATTB** | Tahsilat kayıt ekleme/silme/güncelleme |
| **KULLANICITB** | Özel kimlik doğrulama (kullanıcı adı/şifre) |
| **FATURA**, **DETAY** | Satış/alım faturaları, malzeme detayları |
| **TUMCARIHARETLER** | Cari hareket / analiz |
| **MALZEME_STOK**, **MALZEME_STOK_PERFORMANS**, **FIYATANALIZ** | Stok ve fiyat raporları |
| **TAHSILAT_LOGO** / **TAHSILATLOGO** | Eski/alternatif tahsilat kaynakları |
| Logo tabloları (**LG_*** CLFICHE/CLFLINE vb.) | Tahsilat sonrası muhasebe entegrasyonu (insert akışları) |

### 4.3 Kimlik doğrulama

- **Birincil backend**: `MSSQLAuthenticationBackend` — `KULLANICITB` üzerinden doğrulama; başarılı girişte Django `User` oluşturma/güncelleme.
- **İkincil**: `ModelBackend` (Django admin / yerel süper kullanıcı).
- Oturum: çerez tabanlı; üretimde `SESSION_COOKIE_SECURE`, HSTS, `SECURE_PROXY_SSL_HEADER` (nginx arkasında HTTPS) ile sıkılaştırılmış yapı.

### 4.4 Yetkilendirme (iş kuralları)

- `KullaniciYetki` modeli: menü kodu + boolean erişim; şablonlarda `has_menu_permission` filtresi.
- Özel kullanıcılar (ör. FIRAT, SEZEN) için kod içi tam veya kısmi muafiyetler tanımlıdır (`views.py`, `tahsilat_extras.py`).

---

## 5. İşlevsel modüller (URL özeti)

Kök yapı: `path('', include(('tahsilat.urls', 'tahsilat')))` — tüm isimlendirilmiş URL’ler `tahsilat:` namespace’i ile kullanılır.

| Alan | Örnek yollar | Not |
|------|----------------|-----|
| Kimlik | `/login/`, `/logout/` | MSSQL + Django session |
| Dashboard | `/` | Özet ekranlar |
| Genel görünüm | `/genel-gorunum/...` | Dashboard, tahsilat/satış/alım, ekstre, cari analiz |
| Tahsilat | `/yeni-tahsilat/`, `/tahsilat-raporlari/...`, `/muhasebe/...` | Muhasebe tahsilat listesi, KDV, günlük rapor, düzenleme |
| Cari | `/cari-ekstre/`, `/cari-analiz/`, `/cari-vade-analizi/`, `/cari-gecikmeleri/` | Ekstre, vade, gecikme Excel |
| Stok | `/stok-yonetimi/`, `/stok-listesi/`, `/fiyat-analizi/`, `/stok-detayli-analiz/`, `/stok-yonetimi/ortalama-maliyet/` | Stok ve maliyet |
| Satış | `/satislarim/`, `/fatura-detay/<id>/` | Plasiyer/satış görünümleri |
| Gider | `/gider-masraf/...` | CRUD |
| Çek/Senet | `/cek-senetler/...` | Liste, import, durum güncelleme |
| LOGO aktarım | `/logo-transfer/` | Toplu aktarım arayüzü |
| Chat | `/chat/...` | Mesajlaşma, bildirim, durum |
| Plasiyer prim | `/plasiyer-prim/...` | Hesaplama ve kayıt |
| Dışa aktarma | `/export/plasiyer-performans-excel|pdf/` | Excel/PDF |
| Raporlar (alt uygulama) | `/reports/reports/chat/`, `/reports/api/n8n/chat-query/` | n8n webhook entegrasyonu |

**Admin**: `/admin/` — Django yönetim arayüzü.

---

## 6. Entegrasyonlar

### 6.1 Logo GO3 (MSSQL)

- Okuma/yazma doğrudan SQL ile; ORM kullanılmaz.
- Tahsilat girişinde `TAHSILATTB` ve isteğe bağlı Logo fiş satırları (`insert_clfiche_and_clfline` akışı).
- Para birimi, tarih ve Türkçe metin alanları için encoding dikkatle yönetilir (`cp1254` / `utf-8` fallback).

### 6.2 n8n / otomasyon (`reports`)

- `N8N_WEBHOOK_URL`, `MRK_INTEGRATION_KEY` ortam değişkenleri ile korumalı POST.
- `csrf_exempt` + `@login_required` ile API uç noktası; üretimde anahtar zorunlu.

---

## 7. Güvenlik ve yapılandırma

### 7.1 Ortam değişkenleri (`.env`)

Zorunlu/önemli anahtarlar (örnek isimler):

- `SECRET_KEY`
- `MSSQL_*` (sunucu, port, veritabanı, kullanıcı, şifre, sürücü, charset)
- İsteğe bağlı: `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_*`, `CSRF_*`, `N8N_WEBHOOK_URL`, `MRK_INTEGRATION_KEY`

Yükleme: `mrktahsilat/env_loader.py` → `settings.py`.

### 7.2 Üretim sertleştirmesi

- `DEBUG = False`
- `ALLOWED_HOSTS`: alan adı ve IP
- `CSRF_TRUSTED_ORIGINS`: http/https varyantları
- HSTS, XSS filtre, içerik tipi sniffing koruması
- Oturum çerezi: `Secure` / `HttpOnly` / `SameSite` .env ile kontrol

### 7.3 Loglama

- `logs/django.log`: verbose formatter, `django` ve `tahsilat` loggers.
- `URLDebugMiddleware`: istek path ve kullanıcı bilgisi (dosyaya yazım denemeleri ortam izinlerine bağlı).

---

## 8. Ön yüz ve UX

- Ortak düzen: `templates/base.html` — yan menü, responsive, Bootstrap bileşenleri, yükleme göstergesi, CSRF meta.
- Sayfa özel CSS/JS: bloklar (`extra_css`, `extra_js`).
- Tablolarda istemci tarafı filtre/sıralama, CSV/Excel benzeri dışa aktarım (bazı sayfalarda UTF-8 BOM ile CSV).

---

## 9. Dağıtım notları

- **Statik toplama**: `STATIC_ROOT` = `staticfiles`; `collectstatic` ile.
- **Medya**: `MEDIA_ROOT` = `media` (evrak yüklemeleri).
- Tipik stack: **Nginx** (TLS, reverse proxy) + **Gunicorn** + bu Django projesi.
- ODBC: sunucuda **Microsoft ODBC Driver 17 for SQL Server** (veya ayarla uyumlu sürüm) kurulu olmalı.

---

## 10. Bakım ve teknik borç (gözlemler)

1. **Monolit görünümler**: `views.py` ve `mssql_service.py` çok büyük; modüllere bölünmesi (ör. `views/muhasebe.py`, `services/cari.py`) bakımı kolaylaştırır.
2. **Testler**: Otomatik test klasörü yapısı rapor kapsamında belirgin değil; kritik iş kuralları için birim/entegrasyon testi eklenebilir.
3. **Bağımlılık dosyası**: Depoda kök `requirements.txt` bulunmayabilir; üretim için `pip freeze > requirements.txt` sabitlemesi önerilir.
4. **Araç özel log yolları**: Bazı kod parçaları `/var/.cursor/debug.log` gibi sabit yollara yazmayı dener; izin yoksa sessizce başarısız olur — sunucuda ya kaldırılmalı ya da yapılandırılabilir hale getirilmelidir.

---

## 11. Sürüm ve belge

| Öğe | Değer |
|-----|--------|
| Belge | Proje teknik raporu (`PROJE_RAPORU.md`) |
| Kod tabanı kökü | `/var/www/mrktahsilat` |
| Django | 4.2.x |

Bu rapor, depodaki kaynak dosyaların incelenmesiyle oluşturulmuştur; üretim ortamındaki `.env`, reverse proxy ve süreç yöneticisi (systemd/supervisor) ayarları kuruluma göre değişebilir.

---

*Son güncelleme: kod tabanı taramasına dayalı statik dokümantasyon.*
