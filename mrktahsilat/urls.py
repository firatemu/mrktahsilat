from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse
from django.shortcuts import redirect

def favicon_view(request):
    return redirect('/static/images/favicon.ico')

urlpatterns = [
    path('favicon.ico', favicon_view),
    path('admin/', admin.site.urls),
    path('reports/', include('reports.urls')),
    # (modül, app_name) — tahsilat:… URL adlarının her ortamda çözülmesi için açık kayıt
    path('', include(('tahsilat.urls', 'tahsilat'))),
]

# Media files serving
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Static files serving
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
