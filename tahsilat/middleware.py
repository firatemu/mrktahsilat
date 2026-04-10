"""
Debug middleware for URL resolution tracking
"""
import json
import logging

logger = logging.getLogger('tahsilat')

# Test if module is imported
try:
    with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
        f.write("MIDDLEWARE MODULE LOADED\n")
        f.flush()
except:
    pass

class URLDebugMiddleware:
    """Middleware to log URL resolution attempts"""
    
    def __init__(self, get_response):
        self.get_response = get_response
        # Log that middleware instance was created
        try:
            with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write("MIDDLEWARE INSTANCE CREATED\n")
                f.flush()
        except:
            pass

    def __call__(self, request):
        # Log the incoming request path (log all requests to debug)
        import time
        try:
            path = getattr(request, 'path', 'unknown')
            method = getattr(request, 'method', 'unknown')
            user_info = 'anonymous'
            try:
                if hasattr(request, 'user') and request.user.is_authenticated:
                    user_info = request.user.username
            except:
                pass
            
            # Log using Django logger first (more reliable)
            logger.info(f"MIDDLEWARE CALLED: {method} {path} user={user_info}")
            
            # Always log to debug file
            log_entry = f"[{time.time()}] {method} {path} user={user_info}\n"
            try:
                with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(log_entry)
                    f.flush()
            except Exception as e:
                logger.error(f"Failed to write debug log: {e}")
            
            # Also log structured data
            if 'muhasebe' in path or 'tahsilat-listesi' in path:
                log_data = {
                    'sessionId': 'debug-session',
                    'runId': 'run1',
                    'hypothesisId': 'E',
                    'location': 'middleware.py:__call__',
                    'message': 'Request received',
                    'data': {
                        'path': path,
                        'method': method,
                        'user': user_info,
                    },
                    'timestamp': int(time.time() * 1000)
                }
                try:
                    with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                        f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
                        f.flush()
                except Exception as e:
                    try:
                        logger.error(f"Failed to write JSON log: {e}")
                    except:
                        pass
        
        except Exception as e:
            try:
                logger.error(f"Middleware error: {e}")
                with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(f"MIDDLEWARE ERROR: {e}\n")
                    f.flush()
            except:
                pass
        
        try:
            response = self.get_response(request)
        except Exception as e:
            try:
                with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(f"GET_RESPONSE ERROR: {e}\n")
                    f.flush()
            except:
                pass
            raise
        
        # Log response status
        try:
            path = getattr(request, 'path', 'unknown')
            if response.status_code in [404, 302, 301] or 'muhasebe' in path or 'tahsilat-listesi' in path:
                log_data = {
                    'sessionId': 'debug-session',
                    'runId': 'run1',
                    'hypothesisId': 'E',
                    'location': 'middleware.py:__call__',
                    'message': 'Response',
                    'data': {
                        'path': path,
                        'status_code': response.status_code,
                        'location': getattr(response, 'url', None) if hasattr(response, 'url') else None,
                    },
                    'timestamp': int(time.time() * 1000)
                }
                with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
                    f.flush()
        except Exception as e:
            try:
                logger.error(f"Failed to log response: {e}")
            except:
                pass
        
        return response

