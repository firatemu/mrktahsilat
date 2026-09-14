"""Shared navigation and permission helpers."""

from django.shortcuts import redirect
from django.urls import reverse

from tahsilat.models import KullaniciYetki

SUPERUSER_USERNAMES = {'FIRAT'}

MENU_PRIORITY = [
    'dashboard',
    'muhasebe',
    'perakende',
    'satislar',
    'tahsilatlar',
    'tahsilatlarim',
    'gider_masraf',
    'genel_gorunum',
    'chat',
]

MENU_URL_NAMES = {
    'dashboard': 'tahsilat:dashboard',
    'chat': 'tahsilat:dashboard',
    'gider_masraf': 'tahsilat:gider_masraf_listesi',
    'genel_gorunum': 'tahsilat:genel_dashboard',
    'klasik_tahsilat_raporu': 'tahsilat:klasik_tahsilat_raporu',
    'muhasebe': 'tahsilat:muhasebe_yeni_tahsilat',
    'muhasebe_yeni_tahsilat': 'tahsilat:muhasebe_yeni_tahsilat',
    'perakende': 'tahsilat:perakende',
    'satislar': 'tahsilat:satislarim',
    'tahsilatlar': 'tahsilat:tahsilatlarim',
}

ALT_MENU_MAPPING = {
    'muhasebe': [
        'muhasebe_yeni_tahsilat',
        'muhasebe_tahsilat_listesi',
        'muhasebe_klasik_rapor',
        'muhasebe_gunluk_rapor',
        'kdv_raporu',
        'cek_senetler',
    ],
    'genel_gorunum': [
        'genel_dashboard',
        'genel_tahsilatlar',
        'genel_satislar',
        'genel_alimlar',
        'genel_ekstre',
        'genel_cari_analiz',
        'genel_cari_aylik_ozet',
        'plasiyer_prim',
    ],
    'hakedis_yonetimi': [
        'plasiyer_hedef_durumu',
        'hedeflerim',
        'hedef_belirleme',
    ],
    'stok_yonetimi': [
        'stok_listesi',
        'fiyat_analizi',
        'stok_detayli_analiz',
        'ortalama_maliyet',
    ],
    'cari_yonetimi': [
        'cari_bakiyeler',
        'cari_analiz',
        'cari_vade_analizi',
        'cari_gecikmeleri_detay',
    ],
}


def is_superuser_username(user):
    return bool(user and getattr(user, 'username', '').upper() in SUPERUSER_USERNAMES)


def get_user_permission_map(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return {}

    if is_superuser_username(user):
        return {choice[0]: True for choice in KullaniciYetki.MENU_CHOICES}

    yetkiler = KullaniciYetki.objects.filter(kullanici=user)
    permission_dict = {yetki.menu_adi: yetki.erisim_izni for yetki in yetkiler}

    for ana_menu, alt_menuler in ALT_MENU_MAPPING.items():
        if permission_dict.get(ana_menu):
            continue
        if any(permission_dict.get(alt_menu) for alt_menu in alt_menuler):
            permission_dict[ana_menu] = True

    return permission_dict


def has_menu_permission(user, menu_name):
    permissions = get_user_permission_map(user)
    return bool(permissions.get(menu_name))


def get_first_accessible_menu(user, priority=None):
    permissions = get_user_permission_map(user)
    if not permissions:
        return None

    priority = priority or MENU_PRIORITY
    for menu in priority:
        if permissions.get(menu):
            return menu

    for menu_name, has_access in permissions.items():
        if has_access:
            return menu_name
    return None


def get_redirect_url_for_menu(menu_name):
    url_name = MENU_URL_NAMES.get(menu_name, 'tahsilat:dashboard')
    return reverse(url_name)


def get_post_login_redirect_url(user, next_url=None):
    if next_url and next_url.startswith('/') and not next_url.startswith('//'):
        return next_url

    if is_superuser_username(user):
        return reverse('tahsilat:dashboard')

    first_menu = get_first_accessible_menu(user)
    if not first_menu:
        return reverse('tahsilat:dashboard')
    return get_redirect_url_for_menu(first_menu)


def redirect_to_first_accessible_page(user, fallback='tahsilat:login'):
    first_menu = get_first_accessible_menu(user)
    if not first_menu:
        return redirect(fallback)
    return redirect(get_redirect_url_for_menu(first_menu))
