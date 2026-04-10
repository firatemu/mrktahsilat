from django import template
import locale
import math

register = template.Library()

@register.filter
def equals(value, arg):
    """
    Güvenli string karşılaştırması için filter
    """
    return str(value) == str(arg)

@register.filter
def safe_default(value, default=""):
    """
    Güvenli default değer için filter
    """
    return value if value is not None else default

@register.filter
def range_filter(value, arg):
    """
    Sayfalama için range filter
    """
    try:
        start = int(value)
        end = int(arg)
        return range(start, end + 1)
    except (ValueError, TypeError):
        return range(1, 2)

@register.filter
def currency_format(value):
    """
    Para birimi formatı - binlik ayracı ile TL sembolü
    Örnek: 8500 -> 8.500 ₺
    """
    try:
        # Değeri float'a çevir
        if value is None:
            value = 0
        
        # Float değerini integer'a çevir (ondalık kısmı atılsın)
        if isinstance(value, (int, float)):
            value = int(value)
        else:
            value = int(float(str(value)))
        
        # Binlik ayracı ile formatla ve TL sembolü ekle
        formatted = f"{value:,}".replace(',', '.')
        return f"{formatted} ₺"
    except (ValueError, TypeError):
        return "0"

@register.filter
def currency_display(value):
    """
    Para birimi görüntüleme - ₺ sembolü ile
    Örnek: 8500 -> ₺ 8.500
    """
    try:
        # Değeri float'a çevir
        if value is None:
            value = 0
        
        # Float değerini integer'a çevir (ondalık kısmı atılsın)
        if isinstance(value, (int, float)):
            value = int(value)
        else:
            value = int(float(str(value)))
        
        # Binlik ayracı ile formatla ve TL sembolü ekle
        formatted = f"{value:,}".replace(',', '.')
        return f"₺ {formatted}"
    except (ValueError, TypeError):
        return "₺ 0"

@register.filter
def currency(value):
    """
    Kısa versiyon - currency_display ile aynı
    Örnek: 8500 -> ₺ 8.500
    """
    return currency_display(value)

@register.filter
def currency_suffix(value):
    """
    Para birimi görüntüleme - sonda ₺ sembolü ile
    Örnek: 8500 -> 8.500 ₺
    """
    formatted = currency_format(value)
    return f"{formatted} ₺"

@register.filter
def number_format(value):
    """
    Sayı formatı - binlik ayracı ile (para birimi olmayan)
    Örnek: 1250 -> 1.250
    """
    try:
        if value is None:
            return "0"
        
        # Integer ise direkt formatla
        if isinstance(value, int):
            return f"{value:,}".replace(',', '.')
        
        # Float ise integer'a çevir (kusuratı at)
        if isinstance(value, float):
            return f"{int(value):,}".replace(',', '.')
        
        # String ise integer'a çevirmeye çalış
        return f"{int(float(str(value))):,}".replace(',', '.')
    except (ValueError, TypeError):
        return str(value)

@register.filter
def currency_clean(value):
    """
    Temiz para formatı - ₺ 40.436 şeklinde
    Örnek: 40436 -> ₺ 40.436
    """
    try:
        if value is None:
            value = 0
        
        # Float değerini integer'a çevir (ondalık kısmı atılsın)
        if isinstance(value, (int, float)):
            value = int(value)
        else:
            value = int(float(str(value)))
        
        # Binlik ayracı ile formatla
        formatted = f"{value:,}".replace(',', '.')
        return f"₺ {formatted}"
    except (ValueError, TypeError):
        return "₺ 0"

@register.filter
def currency_format_decimal(value, decimal_places=2):
    """
    Para birimi formatı - binlik ayracı ve ondalık kısmı ile
    Örnek: 8500.50 -> 8.500,50 ₺
    """
    try:
        if value is None:
            value = 0.0
        
        # Değeri float'a çevir
        if not isinstance(value, (int, float)):
            value = float(str(value))
        
        # Ondalık kısmı formatla
        decimal_format = f".{decimal_places}f"
        
        # Tam kısmı ve ondalık kısmı ayır
        parts = f"{value:{decimal_format}}".split('.')
        integer_part = int(float(parts[0]))
        decimal_part = parts[1] if len(parts) > 1 else '0' * decimal_places
        
        # Binlik ayracı ile formatla (tam kısım için)
        formatted_integer = f"{integer_part:,}".replace(',', '.')
        
        # Sonuç: 8.500,50 ₺ formatında
        return f"{formatted_integer},{decimal_part} ₺"
    except (ValueError, TypeError):
        return f"0,{'0' * decimal_places} ₺"

@register.filter
def om_qty_display(value):
    """
    Ortalama Maliyet — toplam miktar: tam sayı ise binlik ayraç; değilse TR ondalık (₺ yok).
    """
    if value is None:
        return '—'
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(v):
        return '—'
    if abs(v - round(v, 6)) < 1e-9:
        iv = int(round(v))
        return f"{iv:,}".replace(',', '.')
    s = f"{v:.6f}".rstrip('0').rstrip('.')
    if '.' not in s:
        try:
            iv = int(float(s))
            return f"{iv:,}".replace(',', '.')
        except ValueError:
            return s
    a, b = s.split('.', 1)
    try:
        ai = int(a)
        a_fmt = f"{ai:,}".replace(',', '.')
    except ValueError:
        a_fmt = a
    return f"{a_fmt},{b}"


@register.filter
def number_format_decimal(value, decimal_places=2):
    """
    Sayı formatı - binlik ayracı ve ondalık kısmı ile (para birimi olmayan)
    Örnek: 1250.75 -> 1.250,75
    """
    try:
        if value is None:
            return f"0,{'0' * decimal_places}"
        
        # Değeri float'a çevir
        if not isinstance(value, (int, float)):
            value = float(str(value))
        
        # Ondalık kısmı formatla
        decimal_format = f".{decimal_places}f"
        
        # Tam kısmı ve ondalık kısmı ayır
        parts = f"{value:{decimal_format}}".split('.')
        integer_part = int(float(parts[0]))
        decimal_part = parts[1] if len(parts) > 1 else '0' * decimal_places
        
        # Binlik ayracı ile formatla (tam kısım için)
        formatted_integer = f"{integer_part:,}".replace(',', '.')
        
        # Sonuç: 1.250,75 formatında
        return f"{formatted_integer},{decimal_part}"
    except (ValueError, TypeError):
        return f"0,{'0' * decimal_places}"