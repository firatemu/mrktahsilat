"""Utility functions for data type conversions and safety checks"""

def safe_decode_string(value, default=''):
    """Helper method to safely decode strings"""
    if value is None:
        return default
    elif isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    elif isinstance(value, str):
        return value
    else:
        return str(value)

def safe_int(value, default=0):
    """Helper method to safely convert to int"""
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default

def safe_float(value, default=0.0):
    """Helper method to safely convert to float"""
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError):
        return default

def safe_dict_get(d, key, default=None):
    """Helper method to safely get dictionary values"""
    return d.get(key, default) if d is not None else default