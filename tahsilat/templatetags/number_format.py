from django import template

register = template.Library()

def turkish_number_format(value, decimal_places=2):
    """Sayıyı Türk formatında (1.234.567,89) formatlar"""
    try:
        value = float(value)
        
        # Negatif sayı kontrolü
        is_negative = value < 0
        if is_negative:
            value = abs(value)
        
        # Ondalık kısmı al
        integer_part = int(value)
        decimal_part = value - integer_part
        
        # Binlik ayıraç ekle
        integer_str = str(integer_part)
        formatted_parts = []
        for i in range(len(integer_str), 0, -3):
            start = max(0, i - 3)
            formatted_parts.insert(0, integer_str[start:i])
        
        formatted = '.'.join(formatted_parts)
        
        # Ondalık kısmı ekle
        if decimal_places > 0:
            decimal_str = f"{decimal_part:.{decimal_places}f}".split('.')[1]
            formatted = f"{formatted},{decimal_str}"
        
        # Negatif işareti ekle
        if is_negative:
            formatted = f"-{formatted}"
        
        return formatted
    except (ValueError, TypeError):
        return value

@register.filter
def numberformat(value):
    """Sayıyı Türk formatında binlik ayıraç ile formatlar (2 ondalık)"""
    return turkish_number_format(value, 2)

@register.filter
def numberformat_int(value):
    """Sayıyı tam sayı olarak Türk formatında formatlar"""
    return turkish_number_format(value, 0)

