# Tabbed Navigation Design — MRK OTOMOTİV

**Tarih:** 2026-09-28
**Durum:** Onaylandı
**Yazar:** Cursor Agent (kullanıcı tasarımı onayladıktan sonra)

## Amaç

Sol sidebar'daki menü öğelerinin "tarayıcı sekmesi" tarzında sekmeler olarak açılması. Aynı anda birden fazla sayfa açık kalabilir, kullanıcı sekmeler arasında hızlıca geçebilir, kapatabilir.

## Tasarım Kararları (Onaylı)

| Karar | Seçim |
|-------|-------|
| Desen | Tarayıcı tarzı yatay sekme barı (Chrome benzeri) |
| Yükleme | `fetch()` + HTML `<main>` injection |
| Persist | sessionStorage + SPA hissi |
| Özellikler | Kapatma (×) butonu + Aktif kapanınca komşuya geç |
| Tasarım | Mevcut brand kırmızı+lacivert (glassmorphism) |

## Mimari

```
┌─────────────────────────────────────────────────────┐
│  Navbar (hamburger + tarih + çıkış)                  │
├─────────────────────────────────────────────────────┤
│  ▌Dashboard ▌Tahsilatlarım ▌Tahsilat Listesi×▌    │ ← Sekme barı
├─────────────────────────────────────────────────────┤
│                                                      │
│  <Aktif sekmenin içeriği>                            │ ← #tab-content-root
│                                                      │
├─────────────────────────────────────────────────────┤
│  Footer                                              │
└─────────────────────────────────────────────────────┘
```

### Bileşenler

1. **`templates/base.html`** — sidebar linklerini yakalama; sekme barı + `#tab-content-root` container; CSS/JS include.
2. **`static/css/tab-manager.css`** — sekme barı, sekme görünümü, içerik alanı stilleri.
3. **`static/js/tab-manager.js`** — `TabManager` sınıfı (açma/kapama/aktif/persist/kısayollar).

### Sekme Veri Modeli

```js
{
  id: string,           // unique id (uuid benzeri)
  url: string,          // göreli URL
  title: string,        // sekme başlığı
  icon: string,         // bootstrap-icons class
  contentHTML: string,  // inject edilecek içerik HTML
  scriptText: string,   // sayfanın extra_js block içeriği
  styleText: string,    // sayfanın extra_css block içeriği
  active: boolean,
  scrollY: number       // scroll pozisyonu
}
```

### Persistence (sessionStorage)

- Key: `mrk_tabs_state`
- Value: `JSON.stringify({ tabs: [...meta], activeId: '...' })`
- Sayfa yenileme (F5) → sekme listesi restore edilir; her sekme için `fetch()` ile içerik tekrar çekilir (orijinal URL'leriyle).
- Oturum kapatma → `sessionStorage.clear()`.

## Görsel Tasarım (Premium)

### Sekme Barı
- Yükseklik: **44px**, sticky top (navbar altında), `z-index: 1030`.
- Arka plan: `linear-gradient(180deg, #1f2937 0%, #111827 100%)` ile koyu + `backdrop-filter: blur(12px)`.
- Sol/sağ uçta fade-mask (overflow scroll için).

### Sekme (Pasif)
- Genişlik: min 140px, max 220px, esnek (1fr).
- Padding: `8px 12px`, gap 8px (icon + title + close).
- Renk: `#cbd5e6`, background: `rgba(255,255,255,0.04)`.
- Border-radius: `10px 10px 0 0` (üst köşeler yuvarlatılmış).
- Hover: background `rgba(255,255,255,0.10)`, transform translateY(-1px).
- Title `text-overflow: ellipsis`.

### Sekme (Aktif)
- Background: `#fff` (içerik alanı ile aynı seviyeye gelir).
- Bottom border: **3px solid #b53122** (brand kırmızı).
- Shadow: `0 -2px 8px rgba(181,49,34,0.18)`.
- Title rengi: `#0f172a` (koyu lacivert).
- Icon rengi: `#b53122`.

### Kapatma (×) Butonu
- 18x18 px, sağ uçta, **görünür ama pasif sekmede %60 opacity**.
- Hover: background `#dc2626`, color `#fff`, scale(1.1).
- Click: `event.stopPropagation()` ile sekmeyi kapat.

### Sekme Barı Sağ Uç (Global Aksiyonlar)
- `Tümünü Kapat` butonu (pasifse disabled).
- Icon-only, hover'da tooltip.

## Davranış

### Sekme Açma (Sidebar link tıklaması)
1. `click` event capture fazında yakalanır.
2. Hedef `.sidebar-nav-link` veya `.sidebar-dropdown-link` mi? → değilse normal link davranışı (escape).
3. `window.showGlobalLoading(pathname)` göster.
4. `fetch(url, { headers: { 'X-Tab-Request': '1' }})` ile HTML çek.
5. Yanıt parse edilir:
   - `<main>` içeriği alınır → `contentHTML`
   - `<title>` alınır → `title`
   - Herhangi bir `<link rel="stylesheet">` (statik olmayan) kaydedilir.
   - `<style>` içerikleri (extra_css) birle.
   - `<script src="...">` (CDN) — mevcut olduğu için atlanır.
   - `<script>` inline (extra_js) birle.
6. Aynı URL zaten sekmelerde varsa → o sekmeyi aktif yap (yeni açma).
7. Yeni sekme ise listeye ekle, aktif yap.
8. `#tab-content-root` içine `contentHTML` inject et.
9. Sayfanın `extra_js` scriptlerini `<script>` element olarak çalıştır.
10. `sessionStorage`'a persist et, loading gizle.

### Sekme Kapatma (×)
1. `event.stopPropagation()` ile tıklanan sekme tetiklenmez.
2. Sekme silinir.
3. Eğer silinen sekme aktif idiyse:
   - Sağ komşu varsa → sağdaki aktif
   - Yoksa soldaki aktif
   - Hiçbiri yoksa → aktif yok
4. `#tab-content-root` güncellenir.
5. Sekme barı yeniden render.
6. Persist.

### Aktif Sekme Değişimi (Tıklama)
- Pasif sekmeye tıklayınca → aktif yap, içeriği göster, scroll pozisyonunu geri yükle.

### Klavye Kısayolları
- `Ctrl+W` → aktif sekmeyi kapat.
- `Ctrl+Tab` → sıradaki sekmeye geç (sona gelince başa dön).
- `Ctrl+Shift+Tab` → önceki sekmeye geç.
- `Ctrl+1`–`Ctrl+9` → sıradaki sekmeye direkt geç.
- `Esc` → aktif sekmeyi kapat (modifier yoksa).

### Tarayıcı Geri/İleri
- `history.pushState` ile her sekme açılışında URL güncellenir, aktif sekmeyi değiştirmek `history.replaceState` ile.
- Tarayıcı geri/ileri tuşları sekme değiştirir (popstate event).

### Hata Yönetimi
- `fetch` hatası → toast mesajı: "Sayfa yüklenemedi, tam ekran moda geçiliyor" → 1.5s sonra `window.location.href = url`.
- 401/403 → tam ekran yenileme (yetki kontrolü için).

## Etkilenen Dosyalar

- **Değişen:** `templates/base.html` (sidebar link handler + sekme barı + root container + CSS/JS include).
- **Yeni:** `static/css/tab-manager.css`
- **Yeni:** `static/js/tab-manager.js`

## Uyumluluk Notları

- Mevcut sayfaların `{% block content %}` ve `{% block extra_js %}` yapısı korunur; sayfalarda değişiklik yok.
- `global-loading.js` entegrasyonu: `window.showGlobalLoading` ve `window.hideGlobalLoading` mevcut, sekme yüklemelerinde kullanılır.
- Form submit'leri sekme açmaz (aynı sayfa içinde).
- `target="_blank"` linkler etkilenmez.
- Dış linkler (farklı origin) etkilenmez.
- `data-no-tab` attribute'u olan linkler sekme açmaz (escape hook).

## Test Senaryoları

| # | Senaryo | Beklenen |
|---|---------|----------|
| 1 | Sidebar'dan 3 farklı sayfa aç | 3 sekme görünür, aktif olan vurgulu |
| 2 | Aktif sekmeyi × ile kapat | Komşu sekmeye geçer |
| 3 | F5 ile sayfa yenile | Sekmeler ve aktif sekme korunur |
| 4 | Oturumu kapat, tekrar aç | Taze başlar, sekme yok |
| 5 | Ctrl+W | Aktif sekmeyi kapatır |
| 6 | Ctrl+Tab | Sıradaki sekmeye geçer |
| 7 | 8+ sekme aç | Yatay scroll + fade-mask aktif |
| 8 | Bir sayfada normal link tıkla | Aynı sekmede açılır (yeni sekme değil) |
| 9 | `data-no-tab` link | Sekme açmaz, normal navigation |

## Riskler

- **JS-heavy sayfalar:** bazı sayfalar `DOMContentLoaded` event'i kullanıyor; sekme içeriği inject edildiğinde bu event zaten tetiklenmiş olur. **Çözüm:** `extra_js` script tag'leri her sekme yüklenmesinde tekrar çalıştırılır; mevcut sayfalar idempotent olmalı (çoğu zaten öyle).
- **CSRF:** fetch header'ında `X-CSRFToken` zaten meta'dan alınıyor; mevcut pattern.
- **Scroll restore:** her sekmenin scrollY'si saklanır.