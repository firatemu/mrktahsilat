"""Optional request debug middleware for targeted diagnostics."""
import logging

from django.conf import settings

logger = logging.getLogger('tahsilat')


class URLDebugMiddleware:
    """Logs request/response details only when explicitly enabled."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = getattr(settings, 'TAHSILAT_DEBUG_REQUESTS', False)

    def __call__(self, request):
        if not self.enabled:
            return self.get_response(request)

        path = getattr(request, 'path', 'unknown')
        method = getattr(request, 'method', 'unknown')
        user_info = 'anonymous'

        if hasattr(request, 'user') and getattr(request.user, 'is_authenticated', False):
            user_info = request.user.username

        logger.debug('Request start %s %s user=%s', method, path, user_info)
        response = self.get_response(request)
        logger.debug(
            'Request end %s %s status=%s',
            method,
            path,
            getattr(response, 'status_code', 'unknown'),
        )
        return response

