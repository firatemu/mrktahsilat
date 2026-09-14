from django.http import JsonResponse
from django.shortcuts import redirect

from tahsilat.services.navigation import has_menu_permission, is_superuser_username


def yapay_zeka_access_denied_response(request, *, api=False):
    if api:
        return JsonResponse({'error': 'Bu işlem için yetkiniz yok.'}, status=403)
    return redirect('tahsilat:dashboard')


def user_has_yapay_zeka_permission(user):
    if not user or not user.is_authenticated:
        return False
    if is_superuser_username(user):
        return True
    return has_menu_permission(user, 'yapay_zeka')
