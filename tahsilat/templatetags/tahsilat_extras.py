from django import template
from django.contrib.auth.models import User
from ..models import KullaniciYetki

register = template.Library()

@register.filter
def has_menu_permission(user, menu_name):
    """
    Kullanıcının belirli bir menüye erişim yetkisi olup olmadığını kontrol eder
    Ana menü yetkisi varsa veya alt menü yetkisi varsa ana menü görünür
    """
    if not user or not user.is_authenticated:
        return False
    
    # FIRAT kullanıcısı her şeye erişebilir
    if user.username == 'FIRAT':
        return True
    
    # Ana menü yetkisi kontrolü
    try:
        yetki = KullaniciYetki.objects.get(kullanici=user, menu_adi=menu_name)
        if yetki.erisim_izni:
            return True
    except KullaniciYetki.DoesNotExist:
        pass
    
    # Alt menü yetkisi kontrolü - eğer ana menü yetkisi yoksa alt menüleri kontrol et
    alt_menu_mapping = {
        'muhasebe': ['muhasebe_yeni_tahsilat', 'muhasebe_tahsilat_listesi', 'muhasebe_klasik_rapor', 'muhasebe_gunluk_rapor'],
        'genel_gorunum': ['genel_dashboard', 'genel_tahsilatlar', 'genel_satislar', 'genel_alimlar', 'genel_ekstre', 'genel_cari_analiz', 'genel_cari_aylik_ozet'],
        'stok_yonetimi': ['stok_listesi', 'fiyat_analizi', 'stok_detayli_analiz', 'ortalama_maliyet'],
        'cari_yonetimi': ['cari_analiz', 'cari_vade_analizi', 'cari_gecikmeleri_detay'],
    }
    
    # Eğer bu ana menü için alt menü kontrolü yapılacaksa
    if menu_name in alt_menu_mapping:
        alt_menuler = alt_menu_mapping[menu_name]
        for alt_menu in alt_menuler:
            try:
                alt_yetki = KullaniciYetki.objects.get(kullanici=user, menu_adi=alt_menu)
                if alt_yetki.erisim_izni:
                    return True  # Alt menü yetkisi varsa ana menü de görünsün
            except KullaniciYetki.DoesNotExist:
                continue
    
    return False

@register.filter
def get_user_permissions(user):
    """
    Kullanıcının tüm menü yetkilerini döndürür
    Ana menü yetkisi varsa veya alt menü yetkisi varsa ana menü de dahil edilir
    """
    if not user or not user.is_authenticated:
        return {}
    
    # FIRAT kullanıcısı her şeye erişebilir
    if user.username == 'FIRAT':
        return {choice[0]: True for choice in KullaniciYetki.MENU_CHOICES}
    
    # Kullanıcının yetkilerini al
    yetkiler = KullaniciYetki.objects.filter(kullanici=user)
    permission_dict = {yetki.menu_adi: yetki.erisim_izni for yetki in yetkiler}
    
    # Alt menü yetkisi varsa ana menüyü de ekle
    alt_menu_mapping = {
        'muhasebe': ['muhasebe_yeni_tahsilat', 'muhasebe_tahsilat_listesi', 'muhasebe_klasik_rapor', 'muhasebe_gunluk_rapor'],
        'genel_gorunum': ['genel_dashboard', 'genel_tahsilatlar', 'genel_satislar', 'genel_alimlar', 'genel_ekstre', 'genel_cari_analiz', 'genel_cari_aylik_ozet'],
        'stok_yonetimi': ['stok_listesi', 'fiyat_analizi', 'stok_detayli_analiz', 'ortalama_maliyet'],
        'cari_yonetimi': ['cari_analiz', 'cari_vade_analizi', 'cari_gecikmeleri_detay'],
    }
    
    for ana_menu, alt_menuler in alt_menu_mapping.items():
        # Ana menü yetkisi yoksa ama alt menü yetkisi varsa ana menüyü ekle
        if ana_menu not in permission_dict:
            for alt_menu in alt_menuler:
                if alt_menu in permission_dict and permission_dict[alt_menu]:
                    permission_dict[ana_menu] = True
                    break
    
    return permission_dict


@register.filter
def has_any_permission(user):
    """
    Kullanıcının herhangi bir menüye erişim yetkisi olup olmadığını kontrol eder
    """
    if not user or not user.is_authenticated:
        return False
    
    # FIRAT kullanıcısı her şeye erişebilir
    if user.username == 'FIRAT':
        return True
    
    # Kullanıcının herhangi bir aktif yetkisi var mı kontrol et
    yetkiler = KullaniciYetki.objects.filter(kullanici=user, erisim_izni=True)
    
    if yetkiler.exists():
        return True
    
    # Alt menü yetkisi kontrolü
    alt_menu_mapping = {
        'muhasebe': ['muhasebe_yeni_tahsilat', 'muhasebe_tahsilat_listesi', 'muhasebe_klasik_rapor', 'muhasebe_gunluk_rapor'],
        'genel_gorunum': ['genel_dashboard', 'genel_tahsilatlar', 'genel_satislar', 'genel_alimlar', 'genel_ekstre', 'genel_cari_analiz', 'genel_cari_aylik_ozet'],
        'stok_yonetimi': ['stok_listesi', 'fiyat_analizi', 'stok_detayli_analiz', 'ortalama_maliyet'],
        'cari_yonetimi': ['cari_analiz', 'cari_vade_analizi', 'cari_gecikmeleri_detay'],
    }
    
    for ana_menu, alt_menuler in alt_menu_mapping.items():
        for alt_menu in alt_menuler:
            try:
                alt_yetki = KullaniciYetki.objects.get(kullanici=user, menu_adi=alt_menu)
                if alt_yetki.erisim_izni:
                    return True
            except KullaniciYetki.DoesNotExist:
                continue
    
    return False

@register.filter(name='get_item')
def get_item(dictionary, key):
    """Dictionary'den key ile değer alır, hem boşluklu hem boşluksuz key'leri dener"""
    try:
        if not dictionary:
            return None
        # Önce boşluklu key'i dene
        value = dictionary.get(key)
        if value is not None:
            return value
        # Boşluksuz alternatifi dene (boşlukları alt çizgi ile değiştir)
        alt_key = key.replace(' ', '_')
        return dictionary.get(alt_key)
    except Exception:
        return None


@register.filter
def format_tr_currency(value):
    """
    Sayıyı Türkçe formatta (1.234,56) döndürür
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value

    formatted = "{:,.2f}".format(number)
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")
