from django import template
from django.contrib.auth.models import User
from ..models import KullaniciYetki
from ..services.navigation import (
    get_user_permission_map,
    has_menu_permission as shared_has_menu_permission,
)

register = template.Library()

@register.filter
def has_menu_permission(user, menu_name):
    """
    Kullanıcının belirli bir menüye erişim yetkisi olup olmadığını kontrol eder
    Ana menü yetkisi varsa veya alt menü yetkisi varsa ana menü görünür
    """
    return shared_has_menu_permission(user, menu_name)

@register.filter
def get_user_permissions(user):
    """
    Kullanıcının tüm menü yetkilerini döndürür
    Ana menü yetkisi varsa veya alt menü yetkisi varsa ana menü de dahil edilir
    """
    return get_user_permission_map(user)


@register.filter
def has_any_permission(user):
    """
    Kullanıcının herhangi bir menüye erişim yetkisi olup olmadığını kontrol eder
    """
    return any(get_user_permission_map(user).values())

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
