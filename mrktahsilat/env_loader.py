"""
Environment variables loader for Django settings
Güvenlik için hassas bilgileri .env dosyasından yükler
"""
import os
import logging

logger = logging.getLogger(__name__)

def load_env_file(file_path='.env'):
    """
    .env dosyasını yükler ve environment variables'a ekler
    """
    try:
        if not os.path.exists(file_path):
            logger.warning(f".env dosyası bulunamadı: {file_path}")
            return False
            
        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                
                # Boş satırları ve yorumları atla
                if not line or line.startswith('#'):
                    continue
                    
                # KEY=VALUE formatını parse et
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Quotes varsa temizle
                    if (value.startswith('"') and value.endswith('"')) or \
                       (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    
                    # Environment variable olarak set et
                    os.environ[key] = value
                else:
                    logger.warning(f".env dosyası {line_num}. satırda format hatası: {line}")
        
        logger.info(f".env dosyası başarıyla yüklendi: {file_path}")
        return True
        
    except Exception as e:
        logger.error(f".env dosyası yüklenirken hata: {e}")
        return False

def get_env_var(key, default=None, required=False):
    """
    Environment variable'ı güvenli şekilde al
    """
    value = os.environ.get(key, default)
    
    if required and value is None:
        raise ValueError(f"Zorunlu environment variable bulunamadı: {key}")
    
    return value

def get_env_bool(key, default=False):
    """
    Environment variable'ı boolean olarak al
    """
    value = get_env_var(key, str(default)).lower()
    return value in ('true', '1', 'yes', 'on')

def get_env_list(key, default=None, separator=','):
    """
    Environment variable'ı liste olarak al
    """
    value = get_env_var(key)
    if value is None:
        return default or []
    
    return [item.strip() for item in value.split(separator) if item.strip()]

def get_env_int(key, default=None):
    """
    Environment variable'ı integer olarak al
    """
    value = get_env_var(key)
    if value is None:
        return default
    
    try:
        return int(value)
    except ValueError:
        logger.warning(f"Environment variable {key} integer değil: {value}")
        return default