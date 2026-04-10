import io
import xlsxwriter
from datetime import datetime
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
import logging
import os
import json
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, HttpResponseNotFound, HttpResponseRedirect
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.csrf import csrf_protect, csrf_exempt
from django.db import models
from django.db.models import Q, Sum, Count
from django.utils import timezone
from datetime import date, datetime, timedelta
from django.conf import settings
from .mssql_service import mssql_service, MSSQLService
from .models import UploadedImage, TahsilatEvrak, GiderMasraf, KullaniciYetki, CariGeckme, Mesaj, KullaniciDurumu, LogoTransfer, SystemSettings
from django.core.paginator import Paginator

logger = logging.getLogger('tahsilat')

DEJAVU_REGULAR_FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
DEJAVU_BOLD_FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

def register_pdf_fonts():
    """Ensure DejaVu fonts are registered for PDF output"""
    try:
        registered = set(pdfmetrics.getRegisteredFontNames())
        if 'DejaVuSans' not in registered and DEJAVU_REGULAR_FONT_PATH:
            pdfmetrics.registerFont(TTFont('DejaVuSans', DEJAVU_REGULAR_FONT_PATH))
        if 'DejaVuSans-Bold' not in registered and DEJAVU_BOLD_FONT_PATH:
            pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', DEJAVU_BOLD_FONT_PATH))
    except Exception as e:
        logger.error(f'PDF font registration failed: {e}')
def login_view(request):
    """Kullanıcı giriş sayfası"""
    # Bazı Android cihazlarda session cookie redirect sırasında gönderilmediği için
    # authenticated kontrolünü kaldırdık. @login_required decorator zaten cookie yoksa
    # login'e yönlendirecek, burada tekrar redirect yapmak döngü oluşturabilir.
    # Sadece başarılı login sonrası redirect yapıyoruz.

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            # Session'ı explicit olarak kaydet - bazı Android cihazlarda cookie'nin gönderilmesi için gerekli
            request.session.modified = True
            request.session.save()
            messages.success(request, f'Hoş geldiniz, {username}!')

            # Bazı Android cihazlarda redirect sırasında cookie gönderilmediği için
            # doğrudan hedef sayfayı render ediyoruz (redirect yapmadan)
            from .models import KullaniciYetki
            
            # Kullanıcının yetkili olduğu ilk sayfayı belirle
            target_view = None
            redirect_url = '/'
            
            next_url = request.GET.get('next') or request.POST.get('next')
            if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                redirect_url = next_url
            elif user.username.upper() == 'FIRAT':
                target_view = 'dashboard'
                redirect_url = '/'
            else:
                user_permissions = KullaniciYetki.objects.filter(kullanici=user, erisim_izni=True)
                if user_permissions.exists():
                    # Yetkili olduğu ilk sayfayı bul (chat, dashboard gibi özel sayfalar hariç)
                    menu_priority = ['dashboard', 'muhasebe', 'perakende', 'satislar', 'tahsilatlar', 'tahsilatlarim', 'gider_masraf', 'genel_gorunum', 'chat']
                    first_permission = None
                    for menu in menu_priority:
                        perm = user_permissions.filter(menu_adi=menu).first()
                        if perm:
                            first_permission = perm
                            break
                    if not first_permission:
                        first_permission = user_permissions.first()
                    
                    if first_permission.menu_adi == 'perakende':
                        redirect_url = '/perakende/'
                    elif first_permission.menu_adi == 'satislar':
                        redirect_url = '/satislarim/'
                    elif first_permission.menu_adi == 'tahsilatlar':
                        redirect_url = '/tahsilat-raporlari/tahsilatlarim/'
                    elif first_permission.menu_adi == 'muhasebe' or first_permission.menu_adi == 'yeni_tahsilat':
                        redirect_url = '/muhasebe/yeni-tahsilat/'
                    elif first_permission.menu_adi == 'gider_masraf':
                        redirect_url = '/gider-masraf/'
                    elif first_permission.menu_adi == 'genel_gorunum':
                        redirect_url = '/genel-gorunum/dashboard/'
                    elif first_permission.menu_adi == 'dashboard':
                        target_view = 'dashboard'
                        redirect_url = '/'
                    elif first_permission.menu_adi == 'chat':
                        # Chat için dashboard'a yönlendir
                        redirect_url = '/'
                    else:
                        # Diğer yetkiler için dashboard'a yönlendir
                        redirect_url = '/'
            
            # Cookie'yi manuel olarak set eden response oluştur
            # Login başarılı sayfasını göster ve JavaScript ile yönlendir
            response = render(request, 'tahsilat/login.html', {
                'login_success': True,
                'redirect_url': redirect_url,
                'username': username,
                'session_key': request.session.session_key
            })
            
            # Cookie'yi manuel olarak set et
            if request.session.session_key:
                from django.utils.http import http_date
                max_age = settings.SESSION_COOKIE_AGE
                expires = http_date((timezone.now() + timedelta(seconds=max_age)).timestamp())
                cookie_value = request.session.session_key
                session_cookie_name = getattr(settings, 'SESSION_COOKIE_NAME', 'sessionid')
                response.set_cookie(
                    session_cookie_name, 
                    cookie_value,
                    max_age=max_age,
                    expires=expires,
                    path='/',
                    secure=settings.SESSION_COOKIE_SECURE,
                    httponly=settings.SESSION_COOKIE_HTTPONLY,
                    samesite=settings.SESSION_COOKIE_SAMESITE
                )
            
            return response
        else:
            messages.error(request, 'Kullanıcı adı veya şifre hatalı!')

    # GET request veya başarısız login durumunda template'i render et
    return render(request, 'tahsilat/login.html', {
        'login_success': False
    })


def check_session_view(request):
    """Session cookie'nin set edilip edilmediğini kontrol etmek için endpoint"""
    if request.user.is_authenticated:
        return JsonResponse({'authenticated': True, 'username': request.user.username})
    else:
        return JsonResponse({'authenticated': False}, status=401)


def logout_view(request):
    """Logout helper for URL routing (fixed simple redirect)."""
    try:
        logout(request)
    except Exception:
        pass
    return redirect('tahsilat:login')


@login_required
def dashboard(request):
    """Ana dashboard sayfası"""

    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    # Yetki kontrolü
    from .models import KullaniciYetki
    if request.user.username.upper() != 'FIRAT':
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='dashboard')
            if not yetki.erisim_izni:
                # Dashboard yetkisi yoksa, kullanıcının yetkili olduğu ilk sayfaya yönlendir
                user_permissions = KullaniciYetki.objects.filter(
                    kullanici=request.user, erisim_izni=True)
                if user_permissions.exists():
                    first_permission = user_permissions.first()
                    if first_permission.menu_adi == 'perakende':
                        return redirect('tahsilat:perakende')
                    elif first_permission.menu_adi == 'satislar':
                        return redirect('tahsilat:satislarim')
                    elif first_permission.menu_adi == 'tahsilatlar':
                        return redirect('tahsilat:tahsilatlarim')
                    elif first_permission.menu_adi == 'muhasebe':
                        return redirect('tahsilat:muhasebe_yeni_tahsilat')
                    elif first_permission.menu_adi == 'gider_masraf':
                        return redirect('tahsilat:gider_masraf_listesi')
                    elif first_permission.menu_adi == 'genel_gorunum':
                        return redirect('tahsilat:genel_dashboard')
                    elif first_permission.menu_adi == 'klasik_tahsilat_raporu':
                        return redirect('tahsilat:klasik_tahsilat_raporu')
                else:
                    return redirect('tahsilat:login')
        except KullaniciYetki.DoesNotExist:
            # FIRAT kontrolü zaten yapıldı, buraya gelirse yetki yok demektir
            # Kullanıcının yetkili olduğu ilk sayfaya yönlendir
            user_permissions = KullaniciYetki.objects.filter(
                kullanici=request.user, erisim_izni=True)
            if user_permissions.exists():
                first_permission = user_permissions.first()
                if first_permission.menu_adi == 'perakende':
                    return redirect('tahsilat:perakende')
                elif first_permission.menu_adi == 'satislar':
                    return redirect('tahsilat:satislarim')
                elif first_permission.menu_adi == 'tahsilatlar':
                    return redirect('tahsilat:tahsilatlarim')
                elif first_permission.menu_adi == 'muhasebe':
                    return redirect('tahsilat:muhasebe_yeni_tahsilat')
                elif first_permission.menu_adi == 'gider_masraf':
                    return redirect('tahsilat:gider_masraf_listesi')
                elif first_permission.menu_adi == 'genel_gorunum':
                    return redirect('tahsilat:genel_dashboard')
                elif first_permission.menu_adi == 'klasik_tahsilat_raporu':
                    return redirect('tahsilat:klasik_tahsilat_raporu')
            else:
                return redirect('tahsilat:login')

    # Session'dan plasiyer bilgisini al
    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)

    # Ay parametresini al
    selected_month = request.GET.get('month', None)

    # MSSQL'den istatistikleri al (ay filtresi ile)
    if selected_month and selected_month != 'current':
        # Ay filtresi ile istatistikleri al (tek ay için liste oluştur)
        month_number = selected_month.split('-')[1]  # "2025-10" -> "10"
        stats = mssql_service.get_tahsilat_stats_with_month_filter(plasiyer, [
                                                                   month_number])
        satis_stats = mssql_service.get_satis_stats_with_month_filter(plasiyer, [
                                                                      month_number])
    else:
        # Mevcut ay için istatistikleri al
        stats = mssql_service.get_tahsilat_stats(plasiyer)
        satis_stats = mssql_service.get_satis_stats(plasiyer)

    # Son tahsilatları al
    son_tahsilatlar = mssql_service.get_tahsilat_list(plasiyer, limit=8)

    # Son satışları al
    son_satislar = mssql_service.get_satis_list(plasiyer, limit=8)

    # En yüksek tahsilatlı carileri al
    top_cariler = mssql_service.get_top_cariler(plasiyer, limit=5)

    context = {
        'user': request.user,
        'user_data': user_data,
        'plasiyer': plasiyer,
        'stats': stats,
        'satis_stats': satis_stats,
        'son_tahsilatlar': son_tahsilatlar,
        'son_satislar': son_satislar,
        'top_cariler': top_cariler,
        'gunluk_toplam': stats.get('gunluk_tutar', 0),
        'gunluk_adet': stats.get('gunluk_adet', 0),
        'haftalik_toplam': stats.get('haftalik_tutar', 0),
        'haftalik_adet': stats.get('haftalik_adet', 0),
        'aylik_toplam': stats.get('aylik_tutar', 0),
        'aylik_adet': stats.get('aylik_adet', 0),
        # Satış istatistikleri
        'satis_gunluk_toplam': satis_stats.get('gunluk_tutar', 0),
        'satis_gunluk_adet': satis_stats.get('gunluk_adet', 0),
        'satis_haftalik_toplam': satis_stats.get('haftalik_tutar', 0),
        'satis_haftalik_adet': satis_stats.get('haftalik_adet', 0),
        'satis_aylik_toplam': satis_stats.get('aylik_tutar', 0),
        'satis_aylik_adet': satis_stats.get('aylik_adet', 0),
        # Ay filtresi
        'selected_month': selected_month,
    }

    return render(request, 'tahsilat/dashboard.html', context)


@login_required
def tahsilatlarim(request):
    """Tahsilatlarım sayfası - Pagination ile"""
    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)

    # Sayfa numarasını ve arama parametrelerini al
    try:
        page = int(request.GET.get('page', 1))
    except ValueError:
        page = 1

    cari_kod = request.GET.get('cari_kod', '').strip()
    cari_unvan = request.GET.get('cari_unvan', '').strip()
    tarih_filtresi = request.GET.get('tarih_filtresi', '').strip()  # all, today, week, month

    # Tarih aralığı parametreleri (YYYY-MM-DD)
    baslangic_tarihi = request.GET.get('baslangic_tarihi', '').strip()
    bitis_tarihi = request.GET.get('bitis_tarihi', '').strip()

    # Boş string'leri None'a çevir
    cari_kod = cari_kod if cari_kod else None
    cari_unvan = cari_unvan if cari_unvan else None
    tarih_filtresi = tarih_filtresi if tarih_filtresi else 'all'
    baslangic_tarihi = baslangic_tarihi if baslangic_tarihi else None
    bitis_tarihi = bitis_tarihi if bitis_tarihi else None

    # Tahsilatları pagination ile al (GunlukTahsilat_V tablosundan)
    tahsilatlar, pagination_info = mssql_service.get_tahsilat_list_paginated_gunluk(
        plasiyer=plasiyer,
        page=page,
        page_size=100,
        cari_kod=cari_kod,
        cari_unvan=cari_unvan,
        tarih_filtresi=tarih_filtresi,
        baslangic_tarihi=baslangic_tarihi,
        bitis_tarihi=bitis_tarihi
    )

    # Tüm kayıtların toplam tutarını hesapla (filtreleme ile birlikte)
    toplam_tutar = mssql_service.get_tahsilat_total_amount_gunluk(
        plasiyer=plasiyer,
        cari_kod=cari_kod,
        cari_unvan=cari_unvan,
        tarih_filtresi=tarih_filtresi,
        baslangic_tarihi=baslangic_tarihi,
        bitis_tarihi=bitis_tarihi
    )

    # Evrak dosyası varlığını kontrol et
    if tahsilatlar:
        for tahsilat in tahsilatlar:
            # Evrak dosyası kontrolü
            tahsilat_id = tahsilat.get('ID')

            logger.info(
                f"Tahsilat ID {tahsilat_id} için evrak kontrolü yapılıyor")

            # TahsilatEvrak modelinden evrak dosyalarını bul
            # NOT: Evrak görüntüleme için kullanıcı bazlı kısıtlama YOKTUR
            # Sadece tahsilat_id ile kontrol edilir, yükleyen kullanıcı kontrol edilmez
            if tahsilat_id:
                try:
                    tahsilat_evraklar = TahsilatEvrak.objects.filter(
                        tahsilat_id=tahsilat_id)
                    if tahsilat_evraklar.exists():
                        evrak = tahsilat_evraklar.first()  # İlk evrakı al
                        if evrak and evrak.evrak_dosyasi:
                            tahsilat['evrak_var'] = True
                            tahsilat['evrak_dosya'] = evrak.evrak_dosyasi.name if evrak.evrak_dosyasi else ''
                            tahsilat['evrak_url'] = evrak.evrak_dosyasi.url if evrak.evrak_dosyasi else ''
                            tahsilat['evrak_boyutu'] = evrak.get_file_size_mb(
                            ) if evrak else 0
                            tahsilat['evrak_tarih'] = evrak.yuklenen_tarih if evrak else None
                            logger.info(
                                f"Tahsilat ID {tahsilat_id} için evrak bulundu: {evrak.evrak_dosyasi.name if evrak.evrak_dosyasi else 'Dosya yok'}")
                        else:
                            tahsilat['evrak_var'] = False
                    else:
                        tahsilat['evrak_var'] = False
                        logger.info(
                            f"Tahsilat ID {tahsilat_id} için evrak bulunamadı")
                except Exception as e:
                    tahsilat['evrak_var'] = False
                    logger.error(
                        f"Tahsilat ID {tahsilat_id} evrak sorgulama hatası: {e}")
            else:
                tahsilat['evrak_var'] = False

    context = {
        'user': request.user,
        'user_data': user_data,
        'plasiyer': plasiyer,
        'tahsilatlar': tahsilatlar,
        'toplam_tutar': toplam_tutar,
        'pagination_info': pagination_info,
        'cari_kod': request.GET.get('cari_kod', ''),
        'cari_unvan': request.GET.get('cari_unvan', ''),
        'tarih_filtresi': tarih_filtresi,
        'baslangic_tarihi': request.GET.get('baslangic_tarihi', ''),
        'bitis_tarihi': request.GET.get('bitis_tarihi', ''),
    }

    return render(request, 'tahsilat/tahsilatlarim.html', context)


@login_required
def raporlar(request):
    """Tahsilat raporları ana sayfası"""
    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)

    context = {
        'user': request.user,
        'user_data': user_data,
        'plasiyer': plasiyer,
    }

    return render(request, 'tahsilat/raporlar.html', context)


@login_required
def yeni_tahsilat(request):
    """Yeni tahsilat girişi sayfası"""
    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)
    username_upper = request.user.username.upper()

    # Tahsilat türleri / banka listeleri (GET ve POST hata dönüşünde ortak)
    tahsilat_turleri = [
        'Nakit',
        'Kredi Kartı',
        'Banka Havalesi',
        'Çek',
        'Senet'
    ]
    kredi_karti_bankalari = [
        'YAPIKREDİ',
        'AKBANK',
        'FİNANSBANK',
        'DENİZBANK',
        'GARANTİ',
        'İŞBANKASI',
        'TEB',
        'VAKIFBANK',
        'MRKBANK',
        'ZİRAAT'
    ]
    havale_bankalari = [
        'YAPIKREDİ',
        'AKBANK',
        'FİNANSBANK',
        'DENİZBANK',
        'GARANTİ',
        'İŞBANKASI',
        'TEB',
        'VAKIFBANK',
        'GARANTİ ŞAHSİ',
        'İŞBANKASI ŞAHSİ',
        'ZİRAAT'
    ]

    if request.method == 'POST':
        # AJAX isteği kontrolü
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if not user_data.get('kullanici_adi'):
            try:
                kullanici_adi = mssql_service.get_kullanici_adi_from_mssql(
                    request.user.username
                )
                data = dict(request.session.get('mssql_user_data') or {})
                data['kullanici_adi'] = kullanici_adi
                request.session['mssql_user_data'] = data
                request.session.modified = True
                user_data = data
            except Exception:
                pass

        # Form verilerini al
        cari_hesap_code = request.POST.get('cari_hesap')  # Cari CODE
        evrak_no = request.POST.get('evrak_no')
        evrak_goruntu = request.FILES.get('evrak_goruntu')
        evrak_dosya = request.FILES.get('evrak_dosya')
        tahsilat_turu = request.POST.get('tahsilat_turu')
        banka = request.POST.get('banka')
        banka_id = request.POST.get('banka_id')  # BANKATB tablosundan gelen ID
        taksit = request.POST.get('taksit')
        tutar = request.POST.get('tutar')
        tarih = request.POST.get('tarih')
        aciklama = request.POST.get('aciklama', '')
        fis_no = request.POST.get('fis_no')

        # Evrak dosyası seçimi (kamera veya dosya seçiminden biri)
        evrak_file = evrak_goruntu if evrak_goruntu else evrak_dosya

        if evrak_file:
            logger.debug(f"Evrak: {evrak_file.name}, {evrak_file.size} byte")

        def save_evrak_file(file_obj, cari_code, tahsilat_id=None):
            """Evrak dosyasını sunucuya kaydeder ve dosya yolunu döndürür"""
            if not file_obj:
                return None

            try:
                # Evraklar dizinini oluştur (yoksa)
                evrak_dir = os.path.join(settings.MEDIA_ROOT, 'evraklar')
                os.makedirs(evrak_dir, exist_ok=True)

                # Dosya uzantısını al
                file_extension = os.path.splitext(file_obj.name)[1].lower()
                if not file_extension:
                    file_extension = '.jpg'  # Varsayılan uzantı

                # Benzersiz dosya adı oluştur
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                if tahsilat_id:
                    filename = f'tahsilat_{tahsilat_id}_{cari_code}_{timestamp}{file_extension}'
                else:
                    filename = f'tahsilat_{cari_code}_{timestamp}{file_extension}'

                file_path = os.path.join(evrak_dir, filename)

                # Dosyayı kaydet
                with open(file_path, 'wb+') as destination:
                    for chunk in file_obj.chunks():
                        destination.write(chunk)

                # Relative path döndür (media klasörüne göre)
                relative_path = os.path.join('evraklar', filename)

                logger.info(f'Evrak dosyası kaydedildi: {relative_path}')
                return relative_path

            except Exception as e:

                logger.error(f'Evrak dosyası kaydetme hatası: {e}')
                return None

        # Validasyon
        errors = []
        if not cari_hesap_code:
            errors.append('Cari hesap seçimi zorunludur.')
        if not tahsilat_turu:
            errors.append('Tahsilat türü seçimi zorunludur.')
        if not tutar:
            errors.append('Tutar girişi zorunludur.')
        if not tarih:
            errors.append('Tarih girişi zorunludur.')

        # Banka kontrolü (Kredi Kartı ve Banka Havalesi için zorunlu)
        if tahsilat_turu in ['Kredi Kartı', 'Banka Havalesi'] and not banka:
            errors.append(f'{tahsilat_turu} için banka seçimi zorunludur.')

        cari_logicalref = None
        if not errors and cari_hesap_code:
            cari_logicalref = mssql_service.resolve_cari_logicalref_yeni_tahsilat_post(
                cari_hesap_code, username_upper, request.user.username
            )
            if not cari_logicalref:
                errors.append(
                    'Seçilen cari hesap bulunamadı veya bu cari için yetkiniz yok.'
                )

        if errors:
            if is_ajax:
                return JsonResponse({'success': False, 'message': 'Form validasyon hatası', 'errors': errors})
            else:
                for error in errors:
                    messages.error(request, error)
        else:
            try:
                if cari_logicalref:
                    # Tarihi veritabanı formatına çevir (2023-01-02 00:00:00.000)
                    from datetime import datetime
                    tarih_formatted = datetime.strptime(
                        tarih, '%Y-%m-%d').strftime('%Y-%m-%d 00:00:00.000')

                    # Kullanıcı adı - MSSQL'deki gerçek kullanıcı adını kullan (Türkçe karakterler ve büyük/küçük harf korunarak)
                    # Önce session'dan kullanici_adi'yi al, yoksa plasiyer'i, son olarak MSSQL'den çek
                    kullanici = user_data.get('kullanici_adi') or user_data.get('plasiyer')
                    if not kullanici:
                        # Session'da yoksa MSSQL'den çek
                        kullanici = mssql_service.get_kullanici_adi_from_mssql(request.user.username)

                    # TAHSILATTB tablosuna kaydet
                    # banka_id varsa (BANKATB tablosundan gelen ID) kullan, yoksa banka adından hesapla
                    banka_id_param = None
                    if banka_id and str(banka_id).isdigit():
                        banka_id_param = int(banka_id)
                    elif banka and str(banka).isdigit():
                        # Eğer banka zaten bir ID ise
                        banka_id_param = int(banka)
                    
                    new_id = mssql_service.insert_tahsilat(
                        cari_id=cari_logicalref,
                        tahsilat_turu=tahsilat_turu,
                        banka=banka,
                        tutar=tutar,
                        tarih=tarih_formatted,
                        kullanici=kullanici,
                        aciklama=aciklama,
                        taksit=taksit,
                        evrak_no=evrak_no,
                        banka_id=banka_id_param,
                        fis_no=fis_no
                    )

                    # Evrak dosyası varsa TahsilatEvrak modeli ile kaydet
                    evrak_kaydedildi = False
                    if evrak_file:
                        try:
                            # TahsilatEvrak modeli ile kaydet
                            tahsilat_evrak = TahsilatEvrak(
                                tahsilat_id=new_id,
                                cari_kod=cari_hesap_code,
                                evrak_no=evrak_no,
                                evrak_dosyasi=evrak_file,
                                aciklama=aciklama or f"Tahsilat evrakı - {tahsilat_turu}",
                                yukleyen=request.user,
                                dosya_boyutu=evrak_file.size
                            )
                            tahsilat_evrak.save()

                            evrak_kaydedildi = True

                        except Exception as evrak_error:
                            logger.error(
                                f'Tahsilat ID {new_id} için evrak kaydetme hatası: {evrak_error}')

                    # Logo GO3 entegrasyonu (Kredi Kartı)
                    logo_entegrasyon_mesaj = ''
                    logo_setting, _ = SystemSettings.objects.get_or_create(key='LOGO_INTEGRATION_ENABLED', defaults={'value': True})
                    if tahsilat_turu == 'Kredi Kartı' and banka_id_param:
                        if logo_setting.value:
                            try:
                                logo_result = mssql_service.insert_logo_kredi_karti_fisi(
                                    cari_ref=cari_logicalref,
                                    banka_ref=banka_id_param,
                                    fis_no=fis_no,
                                    belge_no=evrak_no,
                                    tarih=tarih_formatted,
                                    tutar=tutar,
                                    aciklama=aciklama or 'Kredi kartı tahsilatı'
                                )
                                logo_entegrasyon_mesaj = f' Logo fişi oluşturuldu (Ref: {logo_result["fiche_ref"]}).'
                                logger.info(f'Logo KK fişi başarılı: TahsilatID={new_id}, FicheRef={logo_result["fiche_ref"]}')
                                # Logo başarılı, Durum güncelleniyor
                                mssql_service.update_tahsilat_durum(new_id)
                            except Exception as logo_err:
                                logger.error(f'Logo KK fiş hatası (TahsilatID={new_id}): {logo_err}')
                                logo_entegrasyon_mesaj = ' (Logo entegrasyonu başarısız, tahsilat kaydedildi.)'
                        else:
                            logo_entegrasyon_mesaj = ' (Logo entegrasyonu kapalı olduğundan Logo\'ya kayıt yapılmadı.)'

                    # Logo GO3 entegrasyonu (Banka Havalesi)
                    elif tahsilat_turu == 'Banka Havalesi' and banka_id_param:
                        if logo_setting.value:
                            try:
                                logo_result = mssql_service.insert_logo_banka_havalesi_fisi(
                                    cari_ref=cari_logicalref,
                                    banka_ref=banka_id_param,
                                    fis_no=fis_no,
                                    belge_no=evrak_no,
                                    tarih=tarih_formatted,
                                    tutar=tutar,
                                    aciklama=aciklama or 'Banka havalesi tahsilatı'
                                )
                                logo_entegrasyon_mesaj = (
                                    f' Logo fişi oluşturuldu '
                                    f'(BnFicheRef: {logo_result["bnfiche_ref"]}, '
                                    f'ClfLineRef: {logo_result["clfline_ref"]}).'
                                )
                                logger.info(
                                    f'Logo Banka Havalesi fişi başarılı: TahsilatID={new_id}, '
                                    f'BnFicheRef={logo_result["bnfiche_ref"]}, '
                                    f'ClfLineRef={logo_result["clfline_ref"]}'
                                )
                                # Logo başarılı, Durum güncelleniyor
                                mssql_service.update_tahsilat_durum(new_id)
                            except Exception as logo_err:
                                logger.error(f'Logo Banka Havalesi fiş hatası (TahsilatID={new_id}): {logo_err}')
                                logo_entegrasyon_mesaj = ' (Logo entegrasyonu başarısız, tahsilat kaydedildi.)'
                        else:
                            logo_entegrasyon_mesaj = ' (Logo entegrasyonu kapalı olduğundan Logo\'ya kayıt yapılmadı.)'

                    # Logo GO3 entegrasyonu (Nakit)
                    elif tahsilat_turu == 'Nakit':
                        if logo_setting.value:
                            # Kasa ref: banka_id alanından al; yoksa varsayılan kasa ref=1
                            kasa_ref_param = banka_id_param if banka_id_param else 1
                            try:
                                logo_result = mssql_service.insert_logo_nakit_fisi(
                                    cari_ref=cari_logicalref,
                                    kasa_ref=kasa_ref_param,
                                    fis_no=fis_no,
                                    tarih=tarih_formatted,
                                    tutar=tutar,
                                    aciklama=aciklama or 'Cari Nakit Tahsilat'
                                )
                                logo_entegrasyon_mesaj = (
                                    f' Logo fişi oluşturuldu '
                                    f'(KsLineRef: {logo_result["ks_line_ref"]}, '
                                    f'ClfLineRef: {logo_result["clf_line_ref"]}).'
                                )
                                logger.info(
                                    f'Logo Nakit fişi başarılı: TahsilatID={new_id}, '
                                    f'KsLineRef={logo_result["ks_line_ref"]}, '
                                    f'ClfLineRef={logo_result["clf_line_ref"]}'
                                )
                                # Logo başarılı, Durum güncelleniyor
                                mssql_service.update_tahsilat_durum(new_id)
                            except Exception as logo_err:
                                logger.error(f'Logo Nakit fiş hatası (TahsilatID={new_id}): {logo_err}')
                                logo_entegrasyon_mesaj = ' (Logo entegrasyonu başarısız, tahsilat kaydedildi.)'
                        else:
                            logo_entegrasyon_mesaj = ' (Logo entegrasyonu kapalı olduğundan Logo\'ya kayıt yapılmadı.)'


                    # Başarı mesajı (evrak bilgisi ile)
                    if evrak_kaydedildi:
                        success_msg = f'Tahsilat başarıyla kaydedildi! (ID: {new_id}) - Evrak dosyası da sunucuya kaydedildi.{logo_entegrasyon_mesaj}'
                    else:
                        success_msg = f'Tahsilat başarıyla kaydedildi! (ID: {new_id}){logo_entegrasyon_mesaj}'

                    if is_ajax:
                        return JsonResponse({
                            'success': True,
                            'message': success_msg,
                            'tahsilat_id': new_id,
                            'evrak_kaydedildi': evrak_kaydedildi
                        })
                    else:
                        messages.success(request, success_msg)
                        return redirect('tahsilat:yeni_tahsilat')

            except Exception as e:
                error_msg = f'Tahsilat kaydedilirken bir hata oluştu: {str(e)}'
                logger.error(f"Tahsilat kaydetme hatası: {e}")
                if is_ajax:
                    return JsonResponse({'success': False, 'message': error_msg})
                else:
                    messages.error(request, error_msg)

    # Sayfa gösterimi (GET veya POST validasyon hatası); cariler AJAX ile aranır
    cari_hesaplar = []

    if not user_data.get('kullanici_adi'):
        try:
            kullanici_adi = mssql_service.get_kullanici_adi_from_mssql(request.user.username)
            data = dict(request.session.get('mssql_user_data') or {})
            data['kullanici_adi'] = kullanici_adi
            request.session['mssql_user_data'] = data
            request.session.modified = True
            user_data = data
        except Exception:
            pass

    # Logo Ayarları
    logo_setting, _ = SystemSettings.objects.get_or_create(key='LOGO_INTEGRATION_ENABLED', defaults={'value': True})
    logo_integration_enabled = logo_setting.value

    # Taksit listesi (Template syntax hatasını önlemek için backend'den hazırla)
    taksit_list = []
    for t in range(1, 10):
        is_selected = False
        # edit_mode bu view'da henüz tam kullanılmıyor ama uyumluluk için ekliyoruz
        if t == 1:
            is_selected = True
        taksit_list.append({'val': t, 'selected_str': 'selected' if is_selected else ''})

    # JSON formatında edit verisi (JS lint hatasını önlemek için)
    edit_data_json = 'null'
    edit_mode = False
    tahsilat = None
    if edit_mode and tahsilat:
        edit_dict = {
            'cariKod': tahsilat.get('CariKod', ''),
            'cariUnvan': tahsilat.get('CariUnvan', ''),
            'tutar': str(tahsilat.get('Tutar', '0.00')),
            'tahsilatTuru': tahsilat.get('TahsilatTuru', ''),
            'taksit': str(tahsilat.get('Taksit', '')),
            'banka': tahsilat.get('Banka', ''),
            'bankaId': str(tahsilat.get('BankaID', ''))
        }
        edit_data_json = json.dumps(edit_dict)

    context = {
        'user': request.user,
        'user_data': user_data,
        'plasiyer': plasiyer,
        'cari_hesaplar': cari_hesaplar,
        'cari_search_scope': 'plasiyer',
        'tahsilat_turleri': tahsilat_turleri,
        'kredi_karti_bankalari': kredi_karti_bankalari,
        'havale_bankalari': havale_bankalari,
        'bugun': date.today().strftime('%Y-%m-%d'),
        'taksit_secenekleri': range(1, 10),
        'taksit_list': taksit_list,
        'logo_integration_enabled': logo_integration_enabled,
        'logo_checked_str': 'checked' if logo_integration_enabled else '',
        'logo_status_class': 'text-success' if logo_integration_enabled else 'text-danger',
        'logo_status_text': 'Açık' if logo_integration_enabled else 'Kapalı',
        'edit_mode': False,
        'edit_data_json': edit_data_json,
    }

    return render(request, 'tahsilat/yeni_tahsilat.html', context)


@login_required
@require_http_methods(["GET"])
def get_next_fis_no_ajax(request):
    """Tahsilat türüne göre sıradaki fiş numarasını döndürür"""
    tahsilat_turu = request.GET.get('tahsilat_turu', '').strip()
    if not tahsilat_turu:
        return JsonResponse({'success': False, 'message': 'Tahsilat türü belirtilmedi'})

    next_fis_no = mssql_service.get_next_fis_no(tahsilat_turu)
    return JsonResponse({'success': True, 'fis_no': next_fis_no})


@login_required
@require_POST
def toggle_logo_integration_ajax(request):
    """FIRAT kullanıcısı için Logo entegrasyonunu açıp kapatır."""
    if request.user.username.upper() != 'FIRAT':
        return JsonResponse({'success': False, 'message': 'Bu işlem için yetkiniz yok.'}, status=403)
        
    logo_setting, _ = SystemSettings.objects.get_or_create(key='LOGO_INTEGRATION_ENABLED', defaults={'value': True})
    new_value = not logo_setting.value
    logo_setting.value = new_value
    logo_setting.save()
    
    return JsonResponse({'success': True, 'enabled': new_value, 'message': 'Ayar güncellendi'})


@login_required
def ajax_search_cari_yeni_tahsilat(request):

    """Yeni tahsilat / muhasebe tahsilat formları için sunucu tarafı cari araması."""
    q = (request.GET.get('q') or '').strip()
    scope = (request.GET.get('scope') or 'plasiyer').strip()
    if scope not in ('plasiyer', 'muhasebe'):
        scope = 'plasiyer'
    if scope == 'muhasebe':
        allowed = request.user.username.upper() == 'FIRAT'
        if not allowed:
            try:
                yetki = KullaniciYetki.objects.get(
                    kullanici=request.user,
                    menu_adi='muhasebe_yeni_tahsilat',
                )
                allowed = yetki.erisim_izni
            except KullaniciYetki.DoesNotExist:
                allowed = False
        if not allowed:
            scope = 'plasiyer'

    username_upper = request.user.username.upper()
    rows = mssql_service.search_cari_yeni_tahsilat(
        q, username_upper, request.user.username, scope=scope, limit=45
    )
    results = []
    for r in rows:
        bak = r.get('BAKİYE')
        try:
            bak_num = float(bak) if bak is not None and bak != '' else None
        except (TypeError, ValueError):
            bak_num = None
        results.append({
            'CODE': r.get('CODE') or '',
            'DEFINITION_': r.get('DEFINITION_') or '',
            'SPECODE': r.get('SPECODE') or '',
            'BOLGE': r.get('BOLGE') or '',
            'LOGICALREF': r.get('LOGICALREF'),
            'BAKIYE': bak_num,
        })
    return JsonResponse({'success': True, 'results': results})


@login_required
def muhasebe_tahsilat_listesi(request):
    """Muhasebe > Tahsilat Listesi - Kullanıcı bazlı filtreleme ile"""
    # #region agent log
    import json
    log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'A', 'location': 'views.py:699', 'message': 'Function entry', 'data': {'user': request.user.username, 'authenticated': request.user.is_authenticated}, 'timestamp': int(timezone.now().timestamp() * 1000)}
    try:
        with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
    except: pass
    # #endregion

    # Alt menü yetki kontrolü
    # #region agent log
    log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'A', 'location': 'views.py:703', 'message': 'Before permission check', 'data': {'user': request.user.username}, 'timestamp': int(timezone.now().timestamp() * 1000)}
    try:
        with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
    except: pass
    # #endregion
    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    if request.user.username.upper() != 'FIRAT':
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='muhasebe_tahsilat_listesi')
            # #region agent log
            log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'A', 'location': 'views.py:706', 'message': 'Permission found', 'data': {'user': request.user.username, 'erisim_izni': yetki.erisim_izni}, 'timestamp': int(timezone.now().timestamp() * 1000)}
            try:
                with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
            except: pass
            # #endregion
            if not yetki.erisim_izni:
                # #region agent log
                log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'A', 'location': 'views.py:707', 'message': 'Redirect: no permission', 'data': {'user': request.user.username}, 'timestamp': int(timezone.now().timestamp() * 1000)}
                try:
                    with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                        f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
                except: pass
                # #endregion
                return redirect('tahsilat:dashboard')
        except KullaniciYetki.DoesNotExist:
            # #region agent log
            log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'A', 'location': 'views.py:709', 'message': 'Permission not found', 'data': {'user': request.user.username, 'is_firat': request.user.username == 'FIRAT'}, 'timestamp': int(timezone.now().timestamp() * 1000)}
            try:
                with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                    f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
            except: pass
            # #endregion
            # SEZEN kullanıcısı da erişebilir
            if request.user.username.upper() not in ['SEZEN']:
                # #region agent log
                log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'A', 'location': 'views.py:710', 'message': 'Redirect: not FIRAT or SEZEN', 'data': {'user': request.user.username}, 'timestamp': int(timezone.now().timestamp() * 1000)}
                try:
                    with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                        f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
                except: pass
                # #endregion
                return redirect('tahsilat:dashboard')

    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)
    current_user = request.user.username
    # #region agent log
    log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'D', 'location': 'views.py:712', 'message': 'After permission check, user data', 'data': {'user': current_user, 'has_user_data': bool(user_data), 'plasiyer': plasiyer}, 'timestamp': int(timezone.now().timestamp() * 1000)}
    try:
        with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
    except: pass
    # #endregion

    # Sayfa numarası ve sayfa başına kayıt
    try:
        page = int(request.GET.get('page', 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.GET.get('per_page', 50))
        if per_page not in (50, 100, 150, 200):
            per_page = 50
    except (ValueError, TypeError):
        per_page = 50

    # Filtre parametrelerini al (strip ile boşlukları temizle)
    tarih_filtresi = (request.GET.get('tarih_filtresi') or 'all').strip()
    tahsilat_turu = (request.GET.get('tahsilat_turu') or '').strip()
    teslim_durumu = (request.GET.get('teslim_durumu') or '').strip()
    kullanici = (request.GET.get('kullanici') or '').strip()

    # Yeni GET parametreleri (tarih aralığı ve ek filtreler)
    baslangic_tarihi = (request.GET.get('baslangic_tarihi') or '').strip()
    bitis_tarihi = (request.GET.get('bitis_tarihi') or '').strip()
    banka = (request.GET.get('banka') or '').strip()
    plasiyer_filter = (request.GET.get('plasiyer') or '').strip()

    # FIRAT ve SEZEN kullanıcıları hariç diğer kullanıcılar sadece kendi kayıtlarını görebilir
    if current_user.upper() not in ['FIRAT', 'SEZEN']:
        kullanici = current_user  # Kullanıcı filtresini zorla kendi kullanıcı adına ayarla

    # Excel export: aynı filtrelerle tüm kayıtları indir
    if request.GET.get('export') == 'excel':
        try:
            _, pagination_export = mssql_service.get_tahsilat_list_paginated_gunluk_all(
                page=1,
                page_size=1,
                cari_kod=None,
                cari_unvan=None,
                tarih_filtresi=tarih_filtresi,
                tahsilat_turu=tahsilat_turu,
                teslim_durumu=teslim_durumu,
                kullanici=kullanici,
                banka=banka,
                baslangic_tarihi=baslangic_tarihi,
                bitis_tarihi=bitis_tarihi,
                plasiyer_filter=plasiyer_filter,
            )
            export_total = pagination_export.get('total_count', 0)
            page_size_export = min(max(export_total, 1), 100000)
            export_list, _ = mssql_service.get_tahsilat_list_paginated_gunluk_all(
                page=1,
                page_size=page_size_export,
                cari_kod=None,
                cari_unvan=None,
                tarih_filtresi=tarih_filtresi,
                tahsilat_turu=tahsilat_turu,
                teslim_durumu=teslim_durumu,
                kullanici=kullanici,
                banka=banka,
                baslangic_tarihi=baslangic_tarihi,
                bitis_tarihi=bitis_tarihi,
                plasiyer_filter=plasiyer_filter,
            )
            if export_list:
                from io import BytesIO
                import pandas as pd
                from django.http import HttpResponse

                def _format_date(d):
                    if d is None:
                        return ''
                    if hasattr(d, 'strftime'):
                        return d.strftime('%d.%m.%Y')
                    return str(d)

                export_rows = []
                for row in export_list:
                    export_rows.append({
                        'ID': row.get('ID') or '',
                        'Tarih': _format_date(row.get('Tarih')),
                        'CariKod': (row.get('CariKod') or '').strip(),
                        'KOD': (row.get('Kod') or '').strip(),
                        'Cari Ünvan': (row.get('CariUnvan') or '').strip(),
                        'Tahsilat Türü': (row.get('TahsilatTuru') or '').strip(),
                        'Banka': (row.get('BANKAADI') or '').strip(),
                        'Taksit': row.get('Taksit'),
                        'Tutar': row.get('Tutar'),
                        'Plasiyer': (row.get('Plasiyer') or '').strip() or '-',
                        'Durum': (row.get('Durum') or '').strip(),
                        'Kullanıcı': (row.get('Kullanici') or '').strip() or '-',
                        'Ekleme Tarihi': _format_date(row.get('EklemeTarihi')),
                        'Teslim Durumu': (row.get('TeslimDurumu') or '').strip(),
                        'Evrak No': (row.get('EvrakNo') or '').strip() or '-',
                        'Açıklama': (row.get('Aciklama') or '').strip(),
                    })
                df = pd.DataFrame(export_rows)
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, sheet_name='Tahsilat Kayıtları', index=False)
                output.seek(0)
                response = HttpResponse(
                    output.getvalue(),
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
                response['Content-Disposition'] = (
                    'attachment; filename="tahsilat_kayitlari_%s.xlsx"'
                    % timezone.now().strftime('%Y%m%d_%H%M')
                )
                return response
        except Exception as export_error:
            logger.error(f"Muhasebe tahsilat listesi Excel export hatası: {export_error}")
            # Export hatasında normal sayfaya düş, kullanıcıya mesaj göstermek için context'e eklenebilir

    # Tahsilatları pagination ile al (kullanıcı bazlı filtreleme ile)
    # #region agent log
    log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'B', 'location': 'views.py:733', 'message': 'Before mssql_service call', 'data': {'page': page, 'kullanici': kullanici, 'tarih_filtresi': tarih_filtresi}, 'timestamp': int(timezone.now().timestamp() * 1000)}
    try:
        with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
    except: pass
    # #endregion
    try:
        tahsilatlar, pagination_info = mssql_service.get_tahsilat_list_paginated_gunluk_all(
        page=page,
        page_size=per_page,
        cari_kod=None,
        cari_unvan=None,
        tarih_filtresi=tarih_filtresi,
        tahsilat_turu=tahsilat_turu,
        teslim_durumu=teslim_durumu,
        kullanici=kullanici,
        banka=banka,
        baslangic_tarihi=baslangic_tarihi,
        bitis_tarihi=bitis_tarihi,
        plasiyer_filter=plasiyer_filter
    )
        # #region agent log
        log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'B', 'location': 'views.py:742', 'message': 'After mssql_service call', 'data': {'tahsilat_count': len(tahsilatlar) if tahsilatlar else 0, 'has_pagination': bool(pagination_info)}, 'timestamp': int(timezone.now().timestamp() * 1000)}
        try:
            with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
        except: pass
        # #endregion
    except Exception as e:
        # #region agent log
        log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'B', 'location': 'views.py:742', 'message': 'Exception in mssql_service call', 'data': {'error': str(e), 'error_type': type(e).__name__}, 'timestamp': int(timezone.now().timestamp() * 1000)}
        try:
            with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
        except: pass
        # #endregion
        raise

    # Filtrelenmiş kayıtların toplam tutarını hesapla
    # #region agent log
    log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'B', 'location': 'views.py:745', 'message': 'Before total amount call', 'data': {}, 'timestamp': int(timezone.now().timestamp() * 1000)}
    try:
        with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
    except: pass
    # #endregion
    try:
        toplam_tutar = mssql_service.get_tahsilat_total_amount_gunluk_all(
        cari_kod=None,
        cari_unvan=None,
        tarih_filtresi=tarih_filtresi,
        tahsilat_turu=tahsilat_turu,
        teslim_durumu=teslim_durumu,
        kullanici=kullanici,
        banka=banka,
        baslangic_tarihi=baslangic_tarihi,
        bitis_tarihi=bitis_tarihi,
        plasiyer_filter=plasiyer_filter
    )
        # #region agent log
        log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'B', 'location': 'views.py:752', 'message': 'After total amount call', 'data': {'toplam_tutar': float(toplam_tutar) if toplam_tutar else 0}, 'timestamp': int(timezone.now().timestamp() * 1000)}
        try:
            with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
        except: pass
        # #endregion
    except Exception as e:
        # #region agent log
        log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'B', 'location': 'views.py:752', 'message': 'Exception in total amount call', 'data': {'error': str(e), 'error_type': type(e).__name__}, 'timestamp': int(timezone.now().timestamp() * 1000)}
        try:
            with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
        except: pass
        # #endregion
        raise

    # Nakit tahsilat türünde TESLİM EDİLMEDİ durumundaki tutarların toplamını hesapla
    nakit_teslim_edilmedi_toplam = mssql_service.get_nakit_teslim_edilmedi_total()

    # Her kullanıcı için nakit tahsilat türünde TESLİM EDİLMEDİ durumundaki tutarların toplamını hesapla
    nakit_teslim_edilmedi_by_user = mssql_service.get_nakit_teslim_edilmedi_by_user()

    # Evrak dosyası varlığını kontrol et
    # NOT: Evrak görüntüleme için kullanıcı bazlı kısıtlama YOKTUR
    # Sayfayı görebilen tüm kullanıcılar evrak görüntüleyebilir
    if tahsilatlar:
        for tahsilat in tahsilatlar:
            # Evrak dosyası kontrolü - kullanıcı bazlı filtreleme yapılmaz
            tahsilat_id = tahsilat.get('ID')

            # TahsilatEvrak modelinden evrak dosyalarını bul
            # Sadece tahsilat_id ile kontrol edilir, yükleyen kullanıcı kontrol edilmez
            if tahsilat_id:
                try:
                    tahsilat_evraklar = TahsilatEvrak.objects.filter(
                        tahsilat_id=tahsilat_id)
                    if tahsilat_evraklar.exists():
                        evrak = tahsilat_evraklar.first()  # İlk evrakı al
                        if evrak and evrak.evrak_dosyasi:
                            tahsilat['evrak_var'] = True
                            tahsilat['evrak_dosya'] = evrak.evrak_dosyasi.name if evrak.evrak_dosyasi else ''
                            tahsilat['evrak_url'] = evrak.evrak_dosyasi.url if evrak.evrak_dosyasi else ''
                            tahsilat['evrak_boyutu'] = evrak.get_file_size_mb(
                            ) if evrak else 0
                            tahsilat['evrak_tarih'] = evrak.yuklenen_tarih if evrak else None
                        else:
                            tahsilat['evrak_var'] = False
                    else:
                        tahsilat['evrak_var'] = False
                except Exception as e:
                    tahsilat['evrak_var'] = False
            else:
                tahsilat['evrak_var'] = False

    # Pagination linkleri için GET parametreleri (page ve export hariç, per_page ekli)
    get_for_links = request.GET.copy()
    get_for_links.pop('page', None)
    get_for_links.pop('export', None)
    get_for_links['per_page'] = str(per_page)
    query_for_links = get_for_links.urlencode()
    # Sayfa başına seçenekleri için: page ve per_page hariç (filtreler kalır)
    get_base = request.GET.copy()
    get_base.pop('page', None)
    get_base.pop('per_page', None)
    get_base.pop('export', None)
    query_base = get_base.urlencode()

    # Şablonda güvenli kullanım için liste garantisi
    tahsilatlar = tahsilatlar if isinstance(tahsilatlar, list) else []
    context = {
        'user': request.user,
        'user_data': user_data,
        'plasiyer': plasiyer,
        'current_user': current_user,
        'tahsilatlar': tahsilatlar,
        'toplam_tutar': toplam_tutar,
        'pagination_info': pagination_info,
        'page_size': per_page,
        'query_for_links': query_for_links,
        'query_base': query_base,
        'tarih_filtresi': tarih_filtresi,
        'tahsilat_turu': tahsilat_turu,
        'teslim_durumu': teslim_durumu,
        'kullanici': kullanici,
        'baslangic_tarihi': baslangic_tarihi,
        'bitis_tarihi': bitis_tarihi,
        'banka': banka,
        'plasiyer_filter': plasiyer_filter,
        'nakit_teslim_edilmedi_toplam': nakit_teslim_edilmedi_toplam,
        'nakit_teslim_edilmedi_by_user': nakit_teslim_edilmedi_by_user,
        # Banka, plasiyer ve kullanıcı listeleri for template comboboxes
        'bankalar': mssql_service.get_unique_tahsilat_bankalar(),
        'plasiyerler': mssql_service.get_unique_plasiyerler(),
        'kullanicilar': mssql_service.get_unique_kullanicilar(),
    }
    try:
        result = render(request, 'tahsilat/muhasebe_tahsilat_listesi.html', context)
        # #region agent log
        log_data = {'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'C', 'location': 'views.py:805', 'message': 'After render success', 'data': {'status_code': result.status_code if hasattr(result, 'status_code') else 'N/A'}, 'timestamp': int(timezone.now().timestamp() * 1000)}
        try:
            with open('/var/.cursor/debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_data, ensure_ascii=False) + '\n')
        except: pass
        # #endregion
        return result
    except Exception as e:
        raise
@login_required
def csrf_debug(request):
    """CSRF token debug için"""
    if request.method == 'POST':
        return JsonResponse({
            'success': True,
            'message': 'CSRF token doğrulandı!',
            'csrf_token': request.META.get('CSRF_COOKIE', 'Yok'),
            'user': request.user.username
        })

    return JsonResponse({
        'csrf_token': request.META.get('CSRF_COOKIE', 'Yok'),
        'user': request.user.username,
        'method': request.method
    })


@login_required
def chat(request):
    """Mesajlaşma sayfası"""
    # Kullanıcıyı çevrimiçi olarak işaretle
    KullaniciDurumu.mark_user_online(request.user)

    # Çevrimdışı kullanıcıları temizle
    KullaniciDurumu.cleanup_offline_users()

    # Tüm kullanıcıları al (kendisi hariç)
    users = User.objects.exclude(id=request.user.id).order_by('username')

    # Kullanıcı durumlarını al
    user_statuses = {}
    for user in users:
        try:
            status = KullaniciDurumu.objects.get(kullanici=user)
            user_statuses[user.id] = status.cevrimici
        except KullaniciDurumu.DoesNotExist:
            # Eğer durum kaydı yoksa çevrimdışı olarak işaretle
            user_statuses[user.id] = False

    context = {
        'users': users,
        'user': request.user,
        'user_statuses': user_statuses,
    }

    return render(request, 'tahsilat/chat.html', context)


@login_required
def send_message(request):
    """Mesaj gönder"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Sadece POST istekleri kabul edilir'})

    try:
        # Kullanıcıyı çevrimiçi olarak işaretle
        KullaniciDurumu.mark_user_online(request.user)

        alici_id = request.POST.get('alici_id')
        mesaj = request.POST.get('mesaj', '').strip()

        if not alici_id or not mesaj:
            return JsonResponse({'success': False, 'message': 'Alıcı ve mesaj gerekli'})

        # Alıcıyı kontrol et
        try:
            alici = User.objects.get(id=alici_id)
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Alıcı bulunamadı'})

        # Mesajı kaydet
        mesaj_obj = Mesaj.objects.create(
            gonderen=request.user,
            alici=alici,
            mesaj=mesaj
        )

        return JsonResponse({
            'success': True,
            'message': 'Mesaj gönderildi',
            'mesaj_id': mesaj_obj.id
        })

    except Exception as e:
        logger.error(f"send_message error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def get_messages(request):
    """İki kullanıcı arasındaki mesajları getir"""
    try:
        user_id = request.GET.get('user_id')

        if not user_id:
            return JsonResponse({'success': False, 'message': 'Kullanıcı ID gerekli'})

        # Diğer kullanıcıyı kontrol et
        try:
            other_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Kullanıcı bulunamadı'})

        # İki kullanıcı arasındaki mesajları al
        messages = Mesaj.objects.filter(
            models.Q(gonderen=request.user, alici=other_user) |
            models.Q(gonderen=other_user, alici=request.user)
        ).order_by('olusturma_tarihi')

        # Mesajları formatla
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                'id': msg.id,
                'mesaj': msg.mesaj,
                'olusturma_tarihi': msg.olusturma_tarihi.isoformat(),
                'is_sent': msg.gonderen == request.user,
                'okundu': msg.okundu
            })

        return JsonResponse({
            'success': True,
            'messages': formatted_messages
        })

    except Exception as e:
        logger.error(f"get_messages error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def mark_messages_read(request):
    """Mesajları okundu olarak işaretle"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Sadece POST istekleri kabul edilir'})

    try:
        import json
        data = json.loads(request.body)
        user_id = data.get('user_id')

        if not user_id:
            return JsonResponse({'success': False, 'message': 'Kullanıcı ID gerekli'})

        # Diğer kullanıcıyı kontrol et
        try:
            other_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Kullanıcı bulunamadı'})

        # Bu kullanıcıdan gelen okunmamış mesajları işaretle
        updated_count = Mesaj.objects.filter(
            gonderen=other_user,
            alici=request.user,
            okundu=False
        ).update(
            okundu=True,
            okunma_tarihi=timezone.now()
        )

        return JsonResponse({
            'success': True,
            'updated_count': updated_count
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Geçersiz JSON verisi'})
    except Exception as e:
        logger.error(f"mark_messages_read error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def get_unread_counts(request):
    """Okunmamış mesaj sayılarını getir"""
    try:
        # Her kullanıcıdan gelen okunmamış mesaj sayısını al
        unread_counts = {}

        # Tüm kullanıcıları al (kendisi hariç)
        users = User.objects.exclude(id=request.user.id)

        for user in users:
            count = Mesaj.objects.filter(
                gonderen=user,
                alici=request.user,
                okundu=False
            ).count()

            if count > 0:
                unread_counts[str(user.id)] = count

        return JsonResponse({
            'success': True,
            'counts': unread_counts
        })

    except Exception as e:
        logger.error(f"get_unread_counts error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def clear_chat(request):
    """İki kullanıcı arasındaki tüm mesajları sil"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Sadece POST istekleri kabul edilir'})

    try:
        import json
        data = json.loads(request.body)
        user_id = data.get('user_id')

        if not user_id:
            return JsonResponse({'success': False, 'message': 'Kullanıcı ID gerekli'})

        # Diğer kullanıcıyı kontrol et
        try:
            other_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Kullanıcı bulunamadı'})

        # İki kullanıcı arasındaki tüm mesajları sil
        deleted_count = Mesaj.objects.filter(
            models.Q(gonderen=request.user, alici=other_user) |
            models.Q(gonderen=other_user, alici=request.user)
        ).delete()[0]

        return JsonResponse({
            'success': True,
            'deleted_count': deleted_count,
            'message': f'{deleted_count} mesaj silindi'
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Geçersiz JSON verisi'})
    except Exception as e:
        logger.error(f"clear_chat error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def update_user_status(request):
    """Kullanıcı durumunu güncelle"""
    try:
        # Kullanıcıyı çevrimiçi olarak işaretle
        KullaniciDurumu.mark_user_online(request.user)

        # Çevrimdışı kullanıcıları temizle
        KullaniciDurumu.cleanup_offline_users()

        return JsonResponse({
            'success': True,
            'message': 'Durum güncellendi'
        })

    except Exception as e:
        logger.error(f"update_user_status error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def get_chat_users(request):
    """Chat için kullanıcı listesi getir"""
    try:
        # Kullanıcıyı çevrimiçi olarak işaretle
        KullaniciDurumu.mark_user_online(request.user)

        # Tüm kullanıcıları al (kendisi hariç)
        users = User.objects.exclude(id=request.user.id).order_by('username')

        # Kullanıcı durumlarını ve okunmamış mesaj sayılarını al
        user_data = []
        for user in users:
            try:
                status = KullaniciDurumu.objects.get(kullanici=user)
                is_online = status.cevrimici
            except KullaniciDurumu.DoesNotExist:
                is_online = False

            # Okunmamış mesaj sayısı
            unread_count = Mesaj.objects.filter(
                alici=request.user,
                gonderen=user,
                okundu=False
            ).count()

            user_data.append({
                'id': user.id,
                'username': user.username,
                'is_online': is_online,
                'unread_count': unread_count
            })

        return JsonResponse({
            'success': True,
            'users': user_data
        })

    except Exception as e:
        logger.error(f"get_chat_users error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def get_chat_notifications(request):
    """Chat bildirimleri getir"""
    try:
        # Son 10 mesajı getir (okunmamış olanlar)
        recent_messages = Mesaj.objects.filter(
            alici=request.user,
            okundu=False
        ).select_related('gonderen').order_by('-olusturma_tarihi')[:10]

        notifications = []
        for message in recent_messages:
            notifications.append({
                'id': message.id,
                'sender': message.gonderen.username,
                'message': message.mesaj[:50] + ('...' if len(message.mesaj) > 50 else ''),
                'time': message.olusturma_tarihi.strftime('%H:%M'),
                'is_read': message.okundu
            })

        return JsonResponse({
            'success': True,
            'notifications': notifications
        })

    except Exception as e:
        logger.error(f"get_chat_notifications error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def mark_notification_read(request):
    """Bildirimi okundu olarak işaretle"""
    try:
        message_id = request.POST.get('message_id')

        if not message_id:
            return JsonResponse({'success': False, 'message': 'Mesaj ID gerekli'})

        try:
            message = Mesaj.objects.get(id=message_id, alici=request.user)
            message.mark_as_read()

            return JsonResponse({'success': True, 'message': 'Bildirim okundu olarak işaretlendi'})

        except Mesaj.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Mesaj bulunamadı'})

    except Exception as e:
        logger.error(f"mark_notification_read error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def search_messages(request):
    """Mesajlarda arama yap"""
    try:
        query = request.GET.get('q', '').strip()
        user_id = request.GET.get('user_id')

        if not query:
            return JsonResponse({'success': False, 'message': 'Arama terimi gerekli'})

        # Mesajları filtrele
        messages_query = Mesaj.objects.filter(
            Q(gonderen=request.user) | Q(alici=request.user),
            mesaj__icontains=query
        )

        # Belirli bir kullanıcı ile olan mesajları filtrele
        if user_id:
            try:
                other_user = User.objects.get(id=user_id)
                messages_query = messages_query.filter(
                    Q(gonderen=request.user, alici=other_user) |
                    Q(gonderen=other_user, alici=request.user)
                )
            except User.DoesNotExist:
                return JsonResponse({'success': False, 'message': 'Kullanıcı bulunamadı'})

        messages = messages_query.select_related(
            'gonderen', 'alici').order_by('-olusturma_tarihi')[:50]

        results = []
        for message in messages:
            results.append({
                'id': message.id,
                'sender': message.gonderen.username,
                'receiver': message.alici.username,
                'message': message.mesaj,
                'time': message.olusturma_tarihi.strftime('%d.%m.%Y %H:%M'),
                'is_sent': message.gonderen == request.user
            })

        return JsonResponse({
            'success': True,
            'results': results,
            'count': len(results)
        })

    except Exception as e:
        logger.error(f"search_messages error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def get_user_statuses(request):
    """Tüm kullanıcıların durumunu getir"""
    try:
        # Kullanıcıyı çevrimiçi olarak işaretle
        KullaniciDurumu.mark_user_online(request.user)

        # Tüm kullanıcıları al (kendisi hariç)
        users = User.objects.exclude(id=request.user.id)

        # Kullanıcı durumlarını al
        user_statuses = {}
        for user in users:
            try:
                status = KullaniciDurumu.objects.get(kullanici=user)
                user_statuses[str(user.id)] = status.cevrimici
            except KullaniciDurumu.DoesNotExist:
                user_statuses[str(user.id)] = False

        return JsonResponse({
            'success': True,
            'statuses': user_statuses
        })

    except Exception as e:
        logger.error(f"get_user_statuses error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def get_message_history(request):
    """Mesaj geçmişini getir"""
    try:
        user_id = request.GET.get('user_id')
        page = int(request.GET.get('page', 1))
        per_page = 20

        if not user_id:
            return JsonResponse({'success': False, 'message': 'Kullanıcı ID gerekli'})

        try:
            other_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Kullanıcı bulunamadı'})

        # Mesajları getir
        messages = Mesaj.objects.filter(
            Q(gonderen=request.user, alici=other_user) |
            Q(gonderen=other_user, alici=request.user)
        ).order_by('-olusturma_tarihi')

        # Sayfalama
        start = (page - 1) * per_page
        end = start + per_page
        page_messages = messages[start:end]

        results = []
        for message in page_messages:
            results.append({
                'id': message.id,
                'message': message.mesaj,
                'time': message.olusturma_tarihi.strftime('%d.%m.%Y %H:%M'),
                'is_sent': message.gonderen == request.user,
                'is_read': message.okundu
            })

        # Toplam sayfa sayısı
        total_messages = messages.count()
        total_pages = (total_messages + per_page - 1) // per_page

        return JsonResponse({
            'success': True,
            'messages': results,
            'pagination': {
                'current_page': page,
                'total_pages': total_pages,
                'total_messages': total_messages,
                'has_next': page < total_pages,
                'has_prev': page > 1
            }
        })

    except Exception as e:
        logger.error(f"get_message_history error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def update_teslim_durumu(request):
    """Seçili tahsilat kayıtlarının teslim durumunu günceller"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Sadece POST istekleri kabul edilir'})

    try:
        import json
        data = json.loads(request.body)
        tahsilat_ids = data.get('tahsilat_ids', [])
        new_status = data.get('new_status', '')

        if not tahsilat_ids or not new_status:
            return JsonResponse({'success': False, 'message': 'Tahsilat ID\'leri ve yeni durum gerekli'})

        # Geçerli durumları kontrol et
        valid_statuses = ['TESLİM EDİLDİ', 'HESAP GÖRÜLDÜ']
        if new_status not in valid_statuses:
            return JsonResponse({'success': False, 'message': 'Geçersiz durum'})

        # HESAP GÖRÜLDÜ işlemini sadece MERT, FIRAT ve SEZEN kullanıcıları yapabilir
        if new_status == 'HESAP GÖRÜLDÜ' and request.user.username.upper() not in ['MERT', 'FIRAT', 'SEZEN']:
            return JsonResponse({'success': False, 'message': 'Bu işlemi sadece MERT, FIRAT ve SEZEN kullanıcıları yapabilir'})

        # TESLİM EDİLDİ işlemi için kullanıcı yetki kontrolü
        if new_status == 'TESLİM EDİLDİ':
            current_user = request.user.username.upper()
            # FIRAT ve SEZEN kullanıcıları herkesin kayıtlarını teslim edildi yapabilir
            if current_user not in ['FIRAT', 'SEZEN']:
                # Diğer kullanıcılar sadece kendi kayıtlarını teslim edildi yapabilir
                # Seçili kayıtların kullanıcı bilgilerini kontrol et
                unauthorized_ids = mssql_service.check_tahsilat_user_permission(
                    tahsilat_ids, current_user)
                if unauthorized_ids:
                    return JsonResponse({
                        'success': False,
                        'message': f'Sadece kendi kayıtlarınızı teslim edildi yapabilirsiniz. Yetkisiz kayıt ID\'leri: {", ".join(map(str, unauthorized_ids))}'
                    })

        # MSSQL service ile durum güncelle
        updated_count = mssql_service.update_teslim_durumu_batch(
            tahsilat_ids, new_status)

        return JsonResponse({
            'success': True,
            'updated_count': updated_count,
            'message': f'{updated_count} kayıt başarıyla güncellendi'
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Geçersiz JSON verisi'})
    except Exception as e:
        logger.error(f"update_teslim_durumu error: {e}")
        return JsonResponse({'success': False, 'message': 'Sunucu hatası oluştu'})


@login_required
def update_logo_durumu(request):
    """Seçili tahsilat kayıtlarının Logo durumunu günceller"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Sadece POST istekleri kabul edilir'})

    try:
        import json
        data = json.loads(request.body)
        new_status = data.get('new_status', "LOGO'DA İŞLENDİ")
        
        # Hem tek ID hem de array formatını destekle (geriye dönük uyumluluk için)
        tahsilat_id = data.get('tahsilat_id', '')
        tahsilat_ids = data.get('tahsilat_ids', [])
        
        # ID listesini oluştur
        id_list = []
        if tahsilat_ids:
            # Array formatı
            id_list = tahsilat_ids if isinstance(tahsilat_ids, list) else [tahsilat_ids]
        elif tahsilat_id:
            # Tek ID formatı (geriye dönük uyumluluk)
            id_list = [tahsilat_id]
        
        if not id_list:
            return JsonResponse({'success': False, 'message': 'Tahsilat ID gerekli'})

        # MSSQL service ile durum güncelle
        updated_count = mssql_service.update_logo_durumu_batch(id_list, new_status)

        if updated_count == 0:
            return JsonResponse({
                'success': False,
                'message': 'Güncellenecek kayıt bulunamadı'
            })

        return JsonResponse({
            'success': True,
            'updated_count': updated_count,
            'message': f'{updated_count} kayıt başarıyla Logo\'da İşlendi olarak güncellendi'
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Geçersiz JSON verisi'})
    except Exception as e:
        logger.error(f"update_logo_durumu error: {e}")
        return JsonResponse({'success': False, 'message': f'Sunucu hatası: {str(e)}'})


@login_required
def muhasebe_yeni_tahsilat(request):
    """Muhasebe > Yeni Tahsilat: Tüm cariler listelenir (filtre yok)"""

    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    # Alt menü yetki kontrolü
    if request.user.username.upper() != 'FIRAT':
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='muhasebe_yeni_tahsilat')
            if not yetki.erisim_izni:
                return redirect('tahsilat:dashboard')
        except KullaniciYetki.DoesNotExist:
            return redirect('tahsilat:dashboard')

    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)

    # POST: yeni tahsilat kaydet
    if request.method == 'POST':
        # AJAX isteği kontrolü
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        
        try:
            from django.contrib import messages
            from .mssql_service import MSSQLService

            form = request.POST
            tahsilat_turu = form.get('tahsilat_turu') or ''
            cari_kod = form.get('cari_hesap') or ''
            banka = form.get('banka') or None
            banka_id = form.get('banka_id') or None  # BANKATB tablosundan gelen ID
            taksit = form.get('taksit') or None
            evrak_no = form.get('evrak_no') or None
            aciklama = form.get('aciklama') or None
            tarih = form.get('tarih') or ''
            tutar_raw = form.get('tutar') or ''

            # Zorunlu alanlar
            if not cari_kod or not tahsilat_turu or not tutar_raw:
                error_msg = 'Zorunlu alanlar eksik (Cari, Tür, Tutar).'
                if is_ajax:
                    return JsonResponse({'success': False, 'message': error_msg})
                else:
                    messages.error(request, error_msg)
                    return redirect('tahsilat:muhasebe_yeni_tahsilat')

            # Tutar parse
            try:
                tutar = float(str(tutar_raw).replace(',', '.'))
            except Exception:
                error_msg = 'Tutar sayısal olmalıdır.'
                if is_ajax:
                    return JsonResponse({'success': False, 'message': error_msg})
                else:
                    messages.error(request, error_msg)
                    return redirect('tahsilat:muhasebe_yeni_tahsilat')

            # Tarih formatı (YYYY-MM-DD HH:MM:SS.mmm)
            if tarih:
                tarih = f"{tarih} 00:00:00.000"

            ms = MSSQLService()
            cari_id = ms.get_cari_logicalref_by_code(cari_kod)
            if not cari_id:
                error_msg = 'Seçilen cari bulunamadı.'
                if is_ajax:
                    return JsonResponse({'success': False, 'message': error_msg})
                else:
                    messages.error(request, error_msg)
                    return redirect('tahsilat:muhasebe_yeni_tahsilat')

            # banka_id varsa (BANKATB tablosundan gelen ID) kullan
            banka_id_param = None
            if banka_id and str(banka_id).isdigit():
                banka_id_param = int(banka_id)
            elif banka and str(banka).isdigit():
                # Eğer banka zaten bir ID ise
                banka_id_param = int(banka)
            
            # Kullanıcı adı - MSSQL'deki gerçek kullanıcı adını kullan (Türkçe karakterler ve büyük/küçük harf korunarak)
            user_data = request.session.get('mssql_user_data', {})
            kullanici = user_data.get('kullanici_adi') or user_data.get('plasiyer')
            if not kullanici:
                # Session'da yoksa MSSQL'den çek
                kullanici = ms.get_kullanici_adi_from_mssql(request.user.username)
            
            new_id = ms.insert_tahsilat(
                cari_id=cari_id,
                tahsilat_turu=tahsilat_turu,
                banka=banka,
                tutar=tutar,
                tarih=tarih,
                kullanici=kullanici,
                aciklama=aciklama,
                taksit=taksit,
                evrak_no=evrak_no,
                banka_id=banka_id_param
            )

            success_msg = 'Tahsilat başarıyla kaydedildi.'
            if is_ajax:
                return JsonResponse({
                    'success': True,
                    'message': success_msg,
                    'tahsilat_id': new_id
                })
            else:
                messages.success(request, success_msg)
                return redirect('tahsilat:muhasebe_tahsilat_listesi')
        except Exception as e:
            logger.error(f"Yeni tahsilat kaydı hata: {e}")
            error_msg = f'Kayıt sırasında hata oluştu: {e}'
            if is_ajax:
                return JsonResponse({'success': False, 'message': error_msg})
            else:
                try:
                    messages.error(request, error_msg)
                except Exception:
                    pass
                return redirect('tahsilat:muhasebe_yeni_tahsilat')

    cari_hesaplar = []

    tahsilat_turleri = [
        'Nakit',
        'Kredi Kartı',
        'Banka Havalesi',
        'Çek',
        'Senet'
    ]

    kredi_karti_bankalari = [
        'YAPIKREDİ',
        'AKBANK',
        'FİNANSBANK',
        'DENİZBANK',
        'GARANTİ',
        'İŞBANKASI',
        'TEB',
        'VAKIFBANK',
        'MRKBANK',
        'ZİRAAT'
    ]

    havale_bankalari = [
        'YAPIKREDİ',
        'AKBANK',
        'FİNANSBANK',
        'DENİZBANK',
        'GARANTİ',
        'İŞBANKASI',
        'TEB',
        'VAKIFBANK',
        'GARANTİ ŞAHSİ',
        'İŞBANKASI ŞAHSİ',
        'ZİRAAT'
    ]

    # Bu sayfada sadece form gösterimi (kaydetme mevcut sayfadaki gibi olabilir/isteğe bağlı)
    # Logo Ayarları
    logo_setting, _ = SystemSettings.objects.get_or_create(key='LOGO_INTEGRATION_ENABLED', defaults={'value': True})
    logo_integration_enabled = logo_setting.value

    taksit_list = []
    for t in range(1, 10):
        is_selected = (t == 1)
        taksit_list.append({'val': t, 'selected_str': 'selected' if is_selected else ''})

    # JSON formatında edit verisi (JS lint hatasını önlemek için)
    edit_data_json = 'null'

    context = {
        'user': request.user,
        'user_data': user_data,
        'plasiyer': plasiyer,
        'cari_hesaplar': cari_hesaplar,
        'cari_search_scope': 'muhasebe',
        'tahsilat_turleri': tahsilat_turleri,
        'kredi_karti_bankalari': kredi_karti_bankalari,
        'havale_bankalari': havale_bankalari,
        'bugun': date.today().strftime('%Y-%m-%d'),
        'taksit_secenekleri': range(1, 10),
        'taksit_list': taksit_list,
        'edit_mode': False,
        'logo_integration_enabled': logo_integration_enabled,
        'logo_checked_str': 'checked' if logo_integration_enabled else '',
        'logo_status_class': 'text-success' if logo_integration_enabled else 'text-danger',
        'logo_status_text': 'Açık' if logo_integration_enabled else 'Kapalı',
        'edit_data_json': edit_data_json,
    }

    return render(request, 'tahsilat/yeni_tahsilat.html', context)
def muhasebe_tahsilat_duzenle(request, tahsilat_id):
    """Tahsilat düzenleme - FIRAT için her zaman serbest, diğerlerinde kayıt sahibi ve İŞLENMEDİ şartı"""
    force = (request.user.username.upper() == 'FIRAT')
    if request.method == 'POST':
        # Formdan gelen alanlar
        tahsilat_turu = request.POST.get('tahsilat_turu') or None
        cari_kod = request.POST.get('cari_hesap') or None
        banka = request.POST.get('banka') or None
        tutar = request.POST.get('tutar') or None
        tarih = request.POST.get('tarih') or None
        aciklama = request.POST.get('aciklama') or None
        taksit = request.POST.get('taksit') or None
        evrak_no = request.POST.get('evrak_no') or None

        # Tür dönüşümleri
        if tutar:
            try:
                tutar = float(str(tutar).replace(',', '.'))
            except Exception:
                tutar = None

        result = mssql_service.update_tahsilat(
            tahsilat_id,
            request.user.username,
            tahsilat_turu=tahsilat_turu,
            cari_kod=cari_kod,
            banka=banka,
            tutar=tutar,
            tarih=tarih,
            aciklama=aciklama,
            taksit=taksit,
            evrak_no=evrak_no,
            force=force,
        )
        if not result.get('success'):
            messages.error(request, result.get('error', 'Güncelleme başarısız'))
        else:
            messages.success(request, 'Tahsilat güncellendi')
        return redirect('tahsilat:muhasebe_tahsilat_listesi')

    # GET: mevcut kaydı getir ve mevcut formu (yeni_tahsilat) ile render et
    kayit = mssql_service.get_tahsilat_by_id_any(tahsilat_id)
    if not kayit:
        return HttpResponseNotFound('Tahsilat bulunamadı')

    # Evrak bilgisini ekle
    try:
        from .models import TahsilatEvrak
        tahsilat_evrak = TahsilatEvrak.objects.filter(tahsilat_id=tahsilat_id).first()
        if tahsilat_evrak and tahsilat_evrak.evrak_dosyasi:
            kayit['evrak_var'] = True
            kayit['evrak_url'] = tahsilat_evrak.evrak_dosyasi.url
            kayit['evrak_dosya'] = tahsilat_evrak.evrak_dosyasi.name
        else:
            kayit['evrak_var'] = False
            kayit['evrak_url'] = None
            kayit['evrak_dosya'] = None
    except Exception as e:
        logger.error(f"Evrak bilgisi alınırken hata: {e}")
        kayit['evrak_var'] = False
        kayit['evrak_url'] = None
        kayit['evrak_dosya'] = None

    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)
    cari_hesaplar = []
    tahsilat_turleri = [
        'Nakit',
        'Kredi Kartı',
        'Banka Havalesi',
        'Çek',
        'Senet'
    ]
    kredi_karti_bankalari = [
        'YAPIKREDİ', 'AKBANK', 'FİNANSBANK', 'DENİZBANK', 'GARANTİ',
        'İŞBANKASI', 'TEB', 'VAKIFBANK', 'MRKBANK', 'ZİRAAT'
    ]
    havale_bankalari = [
        'YAPIKREDİ', 'AKBANK', 'FİNANSBANK', 'DENİZBANK', 'GARANTİ',
        'İŞBANKASI', 'TEB', 'VAKIFBANK', 'GARANTİ ŞAHSİ', 'İŞBANKASI ŞAHSİ', 'ZİRAAT'
    ]

    # Debug: kayıt bilgilerini logla
    logger.info(f"muhasebe_tahsilat_duzenle - Tahsilat ID: {tahsilat_id}")
    logger.info(f"muhasebe_tahsilat_duzenle - Tutar: {kayit.get('Tutar')}")
    logger.info(f"muhasebe_tahsilat_duzenle - TahsilatTuru: {kayit.get('TahsilatTuru')}")
    logger.info(f"muhasebe_tahsilat_duzenle - EvrakNo: {kayit.get('EvrakNo')}")
    logger.info(f"muhasebe_tahsilat_duzenle - Evrak var: {kayit.get('evrak_var')}")

    # Logo Ayarları
    logo_setting, _ = SystemSettings.objects.get_or_create(key='LOGO_INTEGRATION_ENABLED', defaults={'value': True})
    logo_integration_enabled = logo_setting.value

    taksit_list = []
    taksit_val = str(kayit.get('Taksit') or '')
    for t in range(1, 10):
        is_selected = (str(t) == taksit_val)
        taksit_list.append({'val': t, 'selected_str': 'selected' if is_selected else ''})

    # JSON formatında edit verisi (JS lint hatasını önlemek için)
    edit_dict = {
        'cariKod': kayit.get('CariKod', ''),
        'cariUnvan': kayit.get('CariUnvan', ''),
        'tutar': str(kayit.get('Tutar', '0.00')),
        'tahsilatTuru': kayit.get('TahsilatTuru', ''),
        'taksit': str(kayit.get('Taksit', '')),
        'banka': kayit.get('Banka', ''),
        'bankaId': str(kayit.get('BankaID', ''))
    }
    edit_data_json = json.dumps(edit_dict)

    context = {
        'edit_mode': True,
        'tahsilat': kayit,
        'user': request.user,
        'user_data': user_data,
        'plasiyer': plasiyer,
        'cari_hesaplar': cari_hesaplar,
        'cari_search_scope': 'plasiyer',
        'tahsilat_turleri': tahsilat_turleri,
        'kredi_karti_bankalari': kredi_karti_bankalari,
        'havale_bankalari': havale_bankalari,
        'bugun': date.today().strftime('%Y-%m-%d'),
        'taksit_secenekleri': range(1, 10),
        'taksit_list': taksit_list,
        'logo_integration_enabled': logo_integration_enabled,
        'logo_checked_str': 'checked' if logo_integration_enabled else '',
        'logo_status_class': 'text-success' if logo_integration_enabled else 'text-danger',
        'logo_status_text': 'Açık' if logo_integration_enabled else 'Kapalı',
        'edit_data_json': edit_data_json,
    }
    return render(request, 'tahsilat/yeni_tahsilat.html', context)


@login_required
def cari_ekstre(request):
    """Cari Ekstre sayfası - CARIBAKIYE tablosundan veri listeler"""
    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)

    # GET parametrelerinden filtreleri al
    bolge_filter = request.GET.get('bolge', 'all')
    export_pdf = request.GET.get('export', '') == 'pdf'

    try:
        # Cari bakiye listesini al
        cari_bakiye_list = mssql_service.get_cari_bakiye_listesi(
            plasiyer, bolge_filter)

        # Benzersiz bölgeleri al (filtreleme için)
        bolgeler = mssql_service.get_unique_bolgeler(plasiyer)

        # PDF export işlemi
        if export_pdf:
            return generate_cari_ekstre_pdf(cari_bakiye_list, plasiyer, bolge_filter)

        # İstatistikler hesapla
        toplam_bakiye = sum(float(item.get('BAKİYE', 0) or 0)
                            for item in cari_bakiye_list)
        toplam_kayit = len(cari_bakiye_list)

        context = {
            'user': request.user,
            'user_data': user_data,
            'plasiyer': plasiyer,
            'cari_bakiye_list': cari_bakiye_list,
            'bolgeler': bolgeler,
            'selected_bolge': bolge_filter,
            'toplam_bakiye': toplam_bakiye,
            'toplam_kayit': toplam_kayit,
        }

        return render(request, 'tahsilat/cari_ekstre.html', context)

    except Exception as e:
        messages.error(
            request, 'Cari ekstre verilerini getirirken bir hata oluştu.')
        return redirect('tahsilat:dashboard')


@login_required
def genel_ekstre(request):
    """Genel Ekstre sayfası"""
    # Session'dan kullanıcı verilerini al
    user_data = request.session.get('mssql_user_data', {})

    # Eğer user_data yoksa boş dict olarak ayarla
    if not user_data:
        user_data = {}

    # Yetki kontrolü - FIRAT, SEZEN, SÜLEYMAN kullanıcıları veya genel_gorunum yetkisi olanlar
    username_upper = request.user.username.upper()
    has_access = False
    
    # FIRAT, SEZEN ve SÜLEYMAN kullanıcıları her zaman erişebilir
    if username_upper in ['FIRAT', 'SEZEN', 'SÜLEYMAN']:
        has_access = True
    else:
        # Diğer kullanıcılar için yetki kontrolü
        from .models import KullaniciYetki
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='genel_gorunum')
            if yetki.erisim_izni:
                has_access = True
        except KullaniciYetki.DoesNotExist:
            pass
    
    if not has_access:
        messages.error(
            request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
        # Boş context ile sayfayı render et
        try:
            from .mssql_service import MSSQLService
            mssql_service = MSSQLService()
            bolgeler = mssql_service.get_all_unique_bolgeler()
        except:
            bolgeler = []
        context = {
            'user': request.user,
            'user_data': user_data,
            'all_plasiyer_list': [],
            'plasiyer_bakiye_summary': [],
            'dashboard_totals': {'toplam_kayit': 0, 'toplam_bakiye': 0, 'pozitif_bakiye': 0, 'negatif_bakiye': 0, 'pozitif_kayit_sayisi': 0, 'negatif_kayit_sayisi': 0},
            'selected_plasiyer': '',
            'cari_bakiye_list': [],
            'bolgeler': bolgeler,
            'bolge_filter': 'all',
            'toplam_bakiye': 0,
            'toplam_kayit': 0,
        }
        return render(request, 'tahsilat/genel_ekstre.html', context)

    try:
        # Kullanıcı adını kontrol için al
        username_upper = request.user.username.upper()
        
        # MSSQL servis çağrıları
        from .mssql_service import MSSQLService
        mssql_service = MSSQLService()

        # Build a comprehensive plasiyer list from multiple MSSQL sources
        plasiyer_sources = []
        plasiyer_bakiye_summary = []
        try:
            # primary list (may be missing some names)
            primary = mssql_service.get_all_plasiyer_list()
            if primary:
                plasiyer_sources.extend(primary)
        except Exception:
            pass

        # Note: we will compute `plasiyer_bakiye_summary` later from CARIBAKIYE
        # rows filtered by CODE starting with '120.' so that the dashboard
        # matches the same CODE filter used for the main list.

        try:
            # distinct plasiyer names from tahsilat table
            unique = mssql_service.get_unique_plasiyerler()
            if unique:
                plasiyer_sources.extend(unique)
        except Exception:
            pass

        try:
            # fallback: plasiyer list from fatura (used elsewhere)
            fatura_list = mssql_service.get_plasiyer_list_from_fatura()
            if fatura_list:
                plasiyer_sources.extend(fatura_list)
        except Exception:
            pass

        # Normalize, dedupe (case-insensitive) and sort preserving readable names
        cleaned = []
        seen_upper = set()
        for item in plasiyer_sources:
            try:
                name = str(item).strip()
            except Exception:
                continue
            if not name:
                continue
            up = name.upper()
            if up not in seen_upper:
                seen_upper.add(up)
                cleaned.append(name)

        try:
            all_plasiyer_list = sorted(cleaned, key=lambda s: s.upper())
        except Exception:
            all_plasiyer_list = cleaned
        
        # Plasiyer dashboard toplamlarını hesapla
        dashboard_totals = {
            'toplam_kayit': sum([int(s.get('toplam_kayit') or 0) for s in plasiyer_bakiye_summary]),
            'toplam_bakiye': sum([float(s.get('toplam_bakiye') or 0) for s in plasiyer_bakiye_summary]),
            'pozitif_bakiye': sum([float(s.get('pozitif_bakiye') or 0) for s in plasiyer_bakiye_summary]),
            'negatif_bakiye': sum([float(s.get('negatif_bakiye') or 0) for s in plasiyer_bakiye_summary]),
            'pozitif_kayit_sayisi': sum([int(s.get('pozitif_kayit_sayisi') or 0) for s in plasiyer_bakiye_summary]),
            'negatif_kayit_sayisi': sum([int(s.get('negatif_kayit_sayisi') or 0) for s in plasiyer_bakiye_summary]),
        }

        # Filtreleme parametrelerini al
        selected_plasiyer = request.GET.get('plasiyer', '')
        bolge_filter = request.GET.get('bolge', 'all')

        # Export kontrolü
        export_pdf = request.GET.get('export', '') == 'pdf'
        export_excel = request.GET.get('export', '') == 'excel'
        cari_tipi = request.GET.get('cari_tipi', '')

        # Cari bakiye listesini al
        # Honor incoming plasiyer filter from query string (do not force empty)
        try:
            # Eğer belirli bir plasiyer seçildiyse, sadece o plasiyerin cari ekstrelerini getir
            if selected_plasiyer:
                logger.info(f"Genel ekstre - Plasiyer seçili: {selected_plasiyer}")
                cari_bakiye_list = mssql_service.get_cari_bakiye_by_plasiyer(
                    plasiyer=selected_plasiyer,
                    bolge=None if bolge_filter == 'all' else bolge_filter,
                    cari_tipi=None if not cari_tipi else cari_tipi
                ) or []
            else:
                # Plasiyer seçilmemişse, tüm plasiyerlerden veri çek
                cari_bakiye_list = mssql_service.get_cari_bakiye_all_plasiyers(
                    bolge=None if bolge_filter == 'all' else bolge_filter,
                    cari_tipi=None if not cari_tipi else cari_tipi
                ) or []

            logger.info(f"Genel ekstre - Cari bakiye listesi uzunluğu: {len(cari_bakiye_list)}")
            if cari_bakiye_list:
                logger.info(f"Genel ekstre - İlk kayıt: {cari_bakiye_list[0]}")

            # CODE ile başlayan filtre: TÜM kullanıcılar için sadece '120.' ile başlayan cari kodları göster
            try:
                cari_bakiye_list = [item for item in cari_bakiye_list if str(item.get('CODE', '')).startswith('120.')]
                logger.info(f"Genel ekstre - CODE startswith '120.' filtresi sonrası uzunluk: {len(cari_bakiye_list)} (kullanıcı: {username_upper})")
            except Exception as e:
                logger.error(f"Genel ekstre - CODE filter error: {e}")

        except Exception as e:
            logger.error(f"Genel ekstre - cari_bakiye_list fetch error: {e}")
            cari_bakiye_list = []

        # Export işlemleri
        if export_pdf and cari_bakiye_list:
            return generate_genel_ekstre_pdf(cari_bakiye_list, selected_plasiyer, bolge_filter, cari_tipi)
        elif export_excel and cari_bakiye_list:
            return generate_genel_ekstre_excel(cari_bakiye_list, selected_plasiyer, bolge_filter, cari_tipi)

        # Bölgeleri al - tüm plasiyerler için benzersiz bölgeleri getir
        bolgeler = mssql_service.get_all_unique_bolgeler()
        logger.info(f"Genel ekstre - alınan bölgeler: {bolgeler}")
        logger.info(f"Genel ekstre - bölgeler sayısı: {len(bolgeler)}")

        # ALİ plasiyeri için test
        if selected_plasiyer == 'ALİ':
            logger.info("Genel ekstre - ALİ plasiyeri için test verisi alınıyor...")
            ali_test_data = mssql_service.test_ali_plasiyer_data()

        # Toplam hesaplamalar (display list)
        toplam_bakiye = sum([float(item.get('BAKİYE') or 0) if item.get('BAKİYE') is not None else 0
                            for item in cari_bakiye_list])
        toplam_kayit = len(cari_bakiye_list)

        # Build plasiyer summary based on CARIBAKIYE rows
        # TÜM kullanıcılar için sadece '120.' ile başlayan cari kodları hesapla
        try:
            # Tüm kullanıcılar için 120. filtresi uygulanır
            summary_query = """
            SELECT
                [SPECODE] as plasiyer,
                COUNT(*) as toplam_kayit,
                ISNULL(SUM(CAST([BAKİYE] as DECIMAL(15,2))), 0) as toplam_bakiye,
                ISNULL(SUM(CASE WHEN CAST([BAKİYE] as DECIMAL(15,2)) > 0 THEN CAST([BAKİYE] as DECIMAL(15,2)) ELSE 0 END), 0) as pozitif_bakiye,
                ISNULL(SUM(CASE WHEN CAST([BAKİYE] as DECIMAL(15,2)) < 0 THEN CAST([BAKİYE] as DECIMAL(15,2)) ELSE 0 END), 0) as negatif_bakiye,
                COUNT(CASE WHEN CAST([BAKİYE] as DECIMAL(15,2)) > 0 THEN 1 END) as pozitif_kayit_sayisi,
                COUNT(CASE WHEN CAST([BAKİYE] as DECIMAL(15,2)) < 0 THEN 1 END) as negatif_kayit_sayisi
            FROM [GO3].[dbo].[CARIBAKIYE]
            WHERE [SPECODE] IS NOT NULL AND [SPECODE] != '' AND [CODE] LIKE '120.%'
            GROUP BY [SPECODE]
            ORDER BY toplam_bakiye DESC
            """
            plasiyer_bakiye_summary = mssql_service.execute_query(summary_query) or []
        except Exception as e:
            logger.error(f"Genel ekstre - plasiyer summary query error: {e}")
            plasiyer_bakiye_summary = []

        # Ensure all known plasiyers appear in the summary (fill zeros for missing)
        try:
            existing = { (str(s.get('plasiyer') or '').strip().upper()): s for s in plasiyer_bakiye_summary }
            for name in all_plasiyer_list:
                if not name:
                    continue
                key = name.strip().upper()
                if key not in existing:
                    plasiyer_bakiye_summary.append({
                        'plasiyer': name,
                        'toplam_kayit': 0,
                        'toplam_bakiye': 0,
                        'pozitif_bakiye': 0,
                        'negatif_bakiye': 0,
                        'pozitif_kayit_sayisi': 0,
                        'negatif_kayit_sayisi': 0,
                    })
        except Exception:
            pass

        # Ensure the dropdown/list of all plasiyers also includes any plasiyer
        # we found in the summary but not in the previously assembled list.
        try:
            existing_names = {n.strip().upper() for n in all_plasiyer_list if n}
            for s in plasiyer_bakiye_summary:
                name = str(s.get('plasiyer') or '').strip()
                if name and name.upper() not in existing_names:
                    all_plasiyer_list.append(name)
                    existing_names.add(name.upper())
            try:
                all_plasiyer_list = sorted(all_plasiyer_list, key=lambda s: s.upper())
            except Exception:
                pass
        except Exception:
            pass

        # Recompute dashboard totals from the summary we just built
        try:
            dashboard_totals = {
                'toplam_kayit': sum([int(float(s.get('toplam_kayit') or 0)) for s in plasiyer_bakiye_summary]),
                'toplam_bakiye': sum([float(s.get('toplam_bakiye') or 0) for s in plasiyer_bakiye_summary]),
                'pozitif_bakiye': sum([float(s.get('pozitif_bakiye') or 0) for s in plasiyer_bakiye_summary]),
                'negatif_bakiye': sum([float(s.get('negatif_bakiye') or 0) for s in plasiyer_bakiye_summary]),
                'pozitif_kayit_sayisi': sum([int(float(s.get('pozitif_kayit_sayisi') or 0)) for s in plasiyer_bakiye_summary]),
                'negatif_kayit_sayisi': sum([int(float(s.get('negatif_kayit_sayisi') or 0)) for s in plasiyer_bakiye_summary]),
            }
        except Exception:
            dashboard_totals = {'toplam_kayit': 0, 'toplam_bakiye': 0, 'pozitif_bakiye': 0, 'negatif_bakiye': 0, 'pozitif_kayit_sayisi': 0, 'negatif_kayit_sayisi': 0}

        context = {
            'user': request.user,
            'user_data': user_data,
            'all_plasiyer_list': all_plasiyer_list,
            'plasiyer_bakiye_summary': plasiyer_bakiye_summary,
            'dashboard_totals': dashboard_totals,
            'selected_plasiyer': selected_plasiyer,
            'cari_bakiye_list': cari_bakiye_list,
            'bolgeler': bolgeler,
            'bolge_filter': bolge_filter,
            'cari_tipi': cari_tipi,
            'toplam_bakiye': toplam_bakiye,
            'toplam_kayit': toplam_kayit,
        }

        return render(request, 'tahsilat/genel_ekstre.html', context)

    except Exception as e:
        logger.error(f"Genel ekstre hatası: {e}")
        messages.error(
            request, f'Genel ekstre verilerini getirirken bir hata oluştu: {str(e)}')

        # Hata durumunda boş context ile sayfayı render et
        try:
            from .mssql_service import MSSQLService
            mssql_service = MSSQLService()
            bolgeler = mssql_service.get_all_unique_bolgeler()
        except:
            bolgeler = []
        context = {
            'user': request.user,
            'user_data': user_data,
            'all_plasiyer_list': [],
            'plasiyer_bakiye_summary': [],
            'dashboard_totals': {'toplam_kayit': 0, 'toplam_bakiye': 0, 'pozitif_bakiye': 0, 'negatif_bakiye': 0, 'pozitif_kayit_sayisi': 0, 'negatif_kayit_sayisi': 0},
            'selected_plasiyer': '',
            'cari_bakiye_list': [],
            'bolgeler': bolgeler,
            'bolge_filter': 'all',
            'toplam_bakiye': 0,
            'toplam_kayit': 0,
        }
        return render(request, 'tahsilat/genel_ekstre.html', context)


def fatura_detay_ajax(request, fatura_id):
    """AJAX ile fatura detaylarını getirir"""
    if not request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'error': 'Sadece AJAX istekleri kabul edilir'}, status=400)

    try:
        detaylar = mssql_service.get_fatura_detaylari(fatura_id)

        return JsonResponse({
            'success': True,
            'detaylar': detaylar,
            'toplam_kalem': len(detaylar),
            'toplam_adet': sum(float(d.get('ADET', 0) or 0) for d in detaylar),
            'toplam_tutar': sum(float(d.get('NET TOPLAM', 0) or 0) for d in detaylar)
        })

    except Exception as e:
        return JsonResponse({'error': 'Detaylar yüklenirken hata oluştu'}, status=500)


# Test fonksiyonu güvenlik nedeniyle kaldırıldı


@login_required
def genel_gorunum(request):
    """Genel Görünüm - Ana sayfa (redirect to tahsilatlar)"""
    # Session'dan kullanıcı verilerini al
    user_data = request.session.get('mssql_user_data', {})
    departman = user_data.get('departman', '')

    # Eğer user_data yoksa boş dict olarak ayarla
    if not user_data:
        user_data = {}

    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    # Yetki kontrolü - sadece FIRAT kullanıcısı veya genel_gorunum yetkisi olanlar
    from .models import KullaniciYetki
    if request.user.username.upper() != 'FIRAT':
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='genel_gorunum')
            if not yetki.erisim_izni:
                messages.error(
                    request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
                # Boş context ile genel_gorunum.html template'ini render et
                context = {
                    'user_data': user_data,
                }
                return render(request, 'tahsilat/genel_gorunum.html', context)
        except KullaniciYetki.DoesNotExist:
            # FIRAT kontrolü zaten yapıldı, buraya gelirse yetki yok demektir
            messages.error(
                request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
            # Boş context ile genel_gorunum.html template'ini render et
            context = {
                'user_data': user_data,
            }
            return render(request, 'tahsilat/genel_gorunum.html', context)

    # Default olarak dashboard sayfasına yönlendir
    return redirect('tahsilat:genel_dashboard')


@login_required
def genel_dashboard(request):
    """Genel Dashboard - Tüm Plasiyerlerin Detaylı İstatistikleri"""

    logger.info('=== GENEL DASHBOARD START ===')

    # Custom authentication kontrolü
    user_data = request.session.get('mssql_user_data', {})
    logger.info(f'Session user_data: {user_data}')

    # Eğer user_data yoksa boş dict olarak ayarla
    if not user_data:
        user_data = {}
        logger.info('genel_dashboard: No user session data, using empty dict')

    departman = user_data.get('departman', '')
    username = user_data.get('kullanici_adi', request.user.username)

    logger.info(f'genel_dashboard: user={username}, departman={departman}')

    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    # Yetki kontrolü
    from .models import KullaniciYetki
    if request.user.username.upper() != 'FIRAT':
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='genel_gorunum')
            if not yetki.erisim_izni:
                messages.error(
                    request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
                # Boş context ile sayfayı render et
                context = {
                    'sayfa_baslik': 'Plasiyer Dashboard',
                    'sayfa_ikon': 'bi-speedometer2',
                    'plasiyer_data': [],
                    'toplam_satis': 0,
                    'toplam_tahsilat': 0,
                    'plasiyerler': [],
                    'user_data': user_data,
                    'selected_months': [],
                    
                }
                return render(request, 'tahsilat/genel_dashboard.html', context)
        except KullaniciYetki.DoesNotExist:
            messages.error(
                request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
            # Boş context ile sayfayı render et
            context = {
                'sayfa_baslik': 'Plasiyer Dashboard',
                'sayfa_ikon': 'bi-speedometer2',
                'plasiyer_data': [],
                'toplam_satis': 0,
                'toplam_tahsilat': 0,
                'plasiyerler': [],
                'user_data': user_data,
                'selected_months': [],
                
            }
            return render(request, 'tahsilat/genel_dashboard.html', context)

    # Filtreleme parametrelerini al
    selected_months = request.GET.getlist('selected_months')

    # Eğer hiç ay seçilmemişse, mevcut ayı varsayılan olarak seç
    if not selected_months:
        from datetime import datetime
        current_month = datetime.now().month
        selected_months = [str(current_month)]
        logger.info(
            f'Hiç ay seçilmemiş, mevcut ay ({current_month}) varsayılan olarak seçildi')

    logger.info(f'Filtreleme parametreleri - Seçili aylar: {selected_months}')

    try:
        logger.info('Starting MSSQL data fetch')
        mssql = MSSQLService()

        # Ay filtreleme parametrelerini service fonksiyonuna gönder (hem satış hem tahsilat için aynı)
        plasiyer_data = mssql.get_all_plasiyerler_stats(
            selected_months=selected_months if selected_months else None
        )
        logger.info(
            f'Data fetched successfully: {len(plasiyer_data["plasiyerler"])} plasiyerler')

        # Aylık satış ve tahsilat istatistiklerini al
        monthly_stats = mssql.get_monthly_satis_tahsilat_stats()
        
        # JSON formatına çevir (template'de JavaScript'te kullanmak için)
        import json
        monthly_stats_json = json.dumps(monthly_stats)

        context = {
            'sayfa_baslik': 'Genel Dashboard',
            'sayfa_ikon': 'bi-speedometer2',
            'plasiyer_data': plasiyer_data['plasiyerler'],
            'toplam_satis': plasiyer_data['toplam_satis'],
            'toplam_tahsilat': plasiyer_data['toplam_tahsilat'],
            'user_data': user_data,  # Navbar için user_data eklendi
            # Filtreleme için seçili ayları template'e gönder
            'selected_months': selected_months,
            # Aylık grafik verileri (JSON formatında)
            'monthly_stats_json': monthly_stats_json,
        }

        logger.info('Rendering genel_dashboard.html template')
        return render(request, 'tahsilat/genel_dashboard.html', context)

    except Exception as e:
        logger.error(f"Genel dashboard error: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        messages.error(request, f'Dashboard yüklenirken hata oluştu: {e}')
        # Hata durumunda boş context ile sayfayı render et
        context = {
            'sayfa_baslik': 'Plasiyer Dashboard',
            'sayfa_ikon': 'bi-speedometer2',
            'plasiyer_data': [],
            'toplam_satis': 0,
            'toplam_tahsilat': 0,
            'plasiyerler': [],
            'user_data': user_data,
            'selected_months': selected_months if 'selected_months' in locals() else [],
            'view_mode': view_mode if 'view_mode' in locals() else 'card',
        }
        return render(request, 'tahsilat/genel_dashboard.html', context)


@login_required
def genel_tahsilatlar(request):
    """Genel Görünüm - Tahsilatlar"""
    # Yetki kontrolü
    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    from .models import KullaniciYetki
    if request.user.username.upper() != 'FIRAT':
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='genel_gorunum')
            if not yetki.erisim_izni:
                return redirect('tahsilat:dashboard')
        except KullaniciYetki.DoesNotExist:
            return redirect('tahsilat:dashboard')

    # Session'dan kullanıcı verilerini al
    user_data = request.session.get('mssql_user_data', {})
    departman = user_data.get('departman', '')

    # Eğer user_data yoksa boş dict olarak ayarla
    if not user_data:
        user_data = {}

    # Ay parametresi işleme
    selected_months = request.GET.getlist('selected_months', [])
    logger.debug('Genel tahsilatlar selected_months: %s', selected_months)

    # Tahsilat listesi filtreleme parametreleri
    baslangic_tarihi = request.GET.get('baslangic_tarihi', '')
    bitis_tarihi = request.GET.get('bitis_tarihi', '')
    plasiyer_filter = request.GET.get('plasiyer', '')
    cari_kod_filter = request.GET.get('cari_kod', '')
    cari_unvan_filter = request.GET.get('cari_unvan', '')
    banka_filter = request.GET.get('banka', '')
    export_format = request.GET.get('export', '').lower()
    tab_param = request.GET.get('tab', '')
    # Özet sekmesi varsayılan: TAHSILAT_LOGO üzerindeki sayfalı liste + banka listesi atlanır
    load_list_tab = tab_param == 'liste' or bool(export_format)
    
    # Eğer tarih parametreleri boşsa, bugünün tarihini kullan
    from datetime import date
    today = date.today().strftime('%Y-%m-%d')
    if not baslangic_tarihi:
        baslangic_tarihi = today
    if not bitis_tarihi:
        bitis_tarihi = today
    
    logger.debug(
        'Tahsilat filtreleri - %s/%s plasiyer=%s load_list_tab=%s',
        baslangic_tarihi, bitis_tarihi, plasiyer_filter, load_list_tab,
    )

    try:
        tum_plasiyerler = mssql_service.get_plasiyer_list_from_fatura()
        if not tum_plasiyerler:
            tum_plasiyerler = ['EYÜP', 'ALİ', 'MERT', 'ATAKAN',
                               'AZİZ', 'YİĞİT', 'SÜLEYMAN', 'GÖRKEM', 'CAN', 'HASAN', 'NECATİ']
    except Exception as e:
        logger.error('Plasiyer listesi alınırken hata: %s', e)
        tum_plasiyerler = ['EYÜP', 'ALİ', 'MERT', 'ATAKAN',
                           'AZİZ', 'YİĞİT', 'SÜLEYMAN', 'GÖRKEM', 'CAN', 'HASAN', 'NECATİ']

    if load_list_tab:
        try:
            banka_listesi = mssql_service.get_all_unique_bankalar()
        except Exception as e:
            logger.error(f"Banka listesi alınırken hata: {e}")
            banka_listesi = []
    else:
        banka_listesi = []

    # Tahsilat özet istatistiklerini al (ay filtresi ile)
    try:
        ozet_data = mssql_service.get_tahsilat_ozet_stats(selected_months=selected_months)
        toplam_stats = ozet_data['toplam_stats']
        plasiyer_verileri = ozet_data['plasiyer_verileri']

        # Eksik olan plasiyer için boş veri ekle
        mevcut_plasiyerler = [pv['plasiyer'] for pv in plasiyer_verileri]

        for plasiyer in tum_plasiyerler:
            if plasiyer not in mevcut_plasiyerler:
                plasiyer_verileri.append({
                    'plasiyer': plasiyer,
                    'tahsilat': {
                        'gunluk_tutar': 0, 'gunluk_adet': 0,
                        'haftalik_tutar': 0, 'haftalik_adet': 0,
                        'aylik_tutar': 0, 'aylik_adet': 0,
                    }
                })

        # Ödeme türü bazında toplamlar (şimdilik dummy data)
        toplam_stats.update({
            'nakit_tutar': 99925.00,
            'havale_tutar': 859907.00,
            'kredi_karti_tutar': 3090035.00,
            'cek_tutar': 0.00,
            'senet_tutar': 520000.00,
        })

    except Exception as e:
        logger.error(f"Tahsilat özet verileri alınırken hata: {e}")
        import traceback
        traceback.print_exc()
        toplam_stats = {
            'gunluk_tutar': 0, 'gunluk_adet': 0,
            'haftalik_tutar': 0, 'haftalik_adet': 0,
            'aylik_tutar': 0, 'aylik_adet': 0,
            'nakit_tutar': 0, 'havale_tutar': 0,
            'kredi_karti_tutar': 0, 'cek_tutar': 0, 'senet_tutar': 0,
        }
        plasiyer_verileri = []

    # Her plasiyer için ödeme türü bazında dummy veriler ekle
    # Decimal import removed (unused)
    for pv in plasiyer_verileri:
        # Decimal'ı float'a çevir
        aylik_tutar = float(pv['tahsilat']['aylik_tutar'])
        pv['tahsilat']['nakit_tutar'] = aylik_tutar * 0.1  # %10 nakit
        pv['tahsilat']['havale_tutar'] = aylik_tutar * 0.3  # %30 havale
        pv['tahsilat']['kredi_karti_tutar'] = aylik_tutar * 0.5  # %50 kredi kartı
        pv['tahsilat']['cek_tutar'] = 0.0
        pv['tahsilat']['senet_tutar'] = aylik_tutar * 0.1  # %10 senet

    # Detaylı tahsilat listesini sayfalama ile al
    page = request.GET.get('page', 1)
    try:
        page = int(page)
    except (ValueError, TypeError):
        page = 1

    if load_list_tab:
        try:
            tahsilat_listesi, pagination_info = mssql_service.get_tahsilat_listesi_paginated(
                page=page, page_size=100,
                baslangic_tarihi=baslangic_tarihi,
                bitis_tarihi=bitis_tarihi,
                plasiyer_filter=plasiyer_filter,
                cari_kod_filter=cari_kod_filter,
                cari_unvan_filter=cari_unvan_filter,
                banka_filter=banka_filter
            )
            total_tutar = pagination_info.get('total_amount', 0)
        except Exception as e:
            logger.error(f"Tahsilat listesi alınırken hata: {e}")
            tahsilat_listesi = []
            pagination_info = {}
            total_tutar = 0
    else:
        tahsilat_listesi = []
        pagination_info = {
            'total_count': 0,
            'total_pages': 0,
            'current_page': 1,
            'page_size': 100,
            'has_previous': False,
            'has_next': False,
            'previous_page': None,
            'next_page': None,
            'page_range': range(1, 1),
            'total_amount': 0.0,
        }
        total_tutar = 0

    list_q = request.GET.copy()
    list_q['tab'] = 'liste'
    list_q.pop('page', None)
    list_q.pop('export', None)
    list_pagination_qs = list_q.urlencode()

    if export_format == 'excel':
        try:
            export_count = pagination_info.get('total_count', len(tahsilat_listesi)) if pagination_info else len(tahsilat_listesi)
            if export_count and export_count > len(tahsilat_listesi):
                export_list, _ = mssql_service.get_tahsilat_listesi_paginated(
                    page=1,
                    page_size=export_count,
                    baslangic_tarihi=baslangic_tarihi,
                    bitis_tarihi=bitis_tarihi,
                    plasiyer_filter=plasiyer_filter,
                    cari_kod_filter=cari_kod_filter,
                    cari_unvan_filter=cari_unvan_filter,
                    banka_filter=banka_filter
                )
            else:
                export_list = tahsilat_listesi

            if export_list:
                export_rows = []
                for row in export_list:
                    export_rows.append({
                        'Tarih': row.get('FormattedDate') or '',
                        'Plasiyer': row.get('Plasiyer') or '',
                        'Cari Kod': row.get('CariKod') or '',
                        'Cari Ünvan': row.get('CariUnvan') or '',
                        'Tahsilat Türü': row.get('TahsilatTuru') or '',
                        'Banka': row.get('BANKAADI') or '',
                        'Taksit': row.get('Taksit'),
                        'Tutar': row.get('Tutar'),
                        'Durum': row.get('Durum') or '',
                        'Evrak No': row.get('EvrakNo') or '',
                        'Açıklama': row.get('Aciklama') or '',
                    })

                import pandas as pd
                from io import BytesIO
                from django.http import HttpResponse

                df = pd.DataFrame(export_rows)
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, sheet_name='Tahsilatlar', index=False)

                output.seek(0)
                response = HttpResponse(
                    output.getvalue(),
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
                response['Content-Disposition'] = 'attachment; filename="detayli_tahsilat_listesi.xlsx"'
                return response
        except Exception as export_error:
            logger.error(f"Genel tahsilatlar excel export hatası: {export_error}")

    context = {
        'user': request.user,
        'user_data': user_data,
        'plasiyer_verileri': plasiyer_verileri,
        'toplam_stats': toplam_stats,
        'sayfa_baslik': 'Tahsilatlar',
        'tahsilat_listesi': tahsilat_listesi,
        'pagination_info': pagination_info,
        'sayfa_ikon': 'bi-cash-coin',
        'selected_months': selected_months,
        'plasiyer_listesi': tum_plasiyerler,
        'banka_listesi': banka_listesi,
        'banka_filter': banka_filter,
        'today': today,
        'total_tutar': total_tutar,
        'load_list_tab': load_list_tab,
        'list_pagination_qs': list_pagination_qs,
    }

    return render(request, 'tahsilat/genel_tahsilatlar.html', context)
@login_required
def genel_alimlar(request):
    """Genel Görünüm - Alımlar"""
    # Yetki kontrolü
    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    from .models import KullaniciYetki
    if request.user.username.upper() != 'FIRAT':
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='genel_gorunum')
            if not yetki.erisim_izni:
                return redirect('tahsilat:dashboard')
        except KullaniciYetki.DoesNotExist:
            return redirect('tahsilat:dashboard')

    # Session'dan kullanıcı verilerini al
    user_data = request.session.get('mssql_user_data', {})
    departman = user_data.get('departman', '')

    # Eğer user_data yoksa boş dict olarak ayarla
    if not user_data:
        user_data = {}

    # AJAX request for malzeme data
    if request.method == 'POST' and request.POST.get('action') in ('get_malzeme_alim', 'export_malzeme_alim_excel', 'export_malzeme_alim_pdf'):
        try:
            # Filtreleme parametreleri
            baslangic_tarihi = request.POST.get('baslangic_tarihi', '')
            bitis_tarihi = request.POST.get('bitis_tarihi', '')
            plasiyer = request.POST.get('plasiyer', '')
            cari_kod = request.POST.get('cari_kod', '')
            cari_unvan = request.POST.get('cari_unvan', '')
            malzeme_kodu = request.POST.get('malzeme_kodu', '')
            malzeme_aciklama = request.POST.get('malzeme_aciklama', '')
            per_page = request.POST.get('per_page', '100')
            page = int(request.POST.get('page', 1))

            # Sayfalama ayarları
            if per_page == 'all':
                page_size = None
            else:
                page_size = int(per_page)

            # İlk defa açıldığında (parametreler boşsa) bugünün tarihini varsayılan olarak ayarla
            from datetime import date
            today = date.today().strftime('%Y-%m-%d')

            # Eğer hiç tarih parametresi yoksa, bugünün tarihini ayarla
            if not baslangic_tarihi and not bitis_tarihi:
                baslangic_tarihi = today
                bitis_tarihi = today

            # Malzeme alım verilerini al - sayfalama kaldırıldı
            malzeme_data = mssql_service.get_malzeme_alim_detay_all(
                baslangic_tarihi=baslangic_tarihi,
                bitis_tarihi=bitis_tarihi,
                plasiyer=plasiyer,
                cari_kod=cari_kod,
                cari_unvan=cari_unvan,
                malzeme_kodu=malzeme_kodu,
                malzeme_aciklama=malzeme_aciklama
            )

            # Export işlemleri
            action = request.POST.get('action')
            if action in ('export_malzeme_alim_excel', 'export_malzeme_alim_pdf'):
                from io import BytesIO
                buffer = BytesIO()
                ts = datetime.now().strftime('%Y%m%d_%H%M')
                if action == 'export_malzeme_alim_excel':
                    import xlsxwriter
                    filename = f"malzeme_alim_{ts}.xlsx"
                    workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
                    ws = workbook.add_worksheet('Malzeme Alım')
                    headers = ['Tarih', 'Malzeme Kodu', 'Açıklama', 'Marka', 'Tür', 'Miktar', 'Birim', 'Birim Brüt', 'Birim İndirim',
                               'Birim Net', 'Toplam İndirim', 'KDV Tutarı', 'Net Toplam', 'Cari Kod', 'Cari Ünvan', 'İşlem Tarihi(Fatura)']
                    for c, h in enumerate(headers):
                        ws.write(0, c, h)
                    for r, item in enumerate(malzeme_data['data'], start=1):
                        ws.write(r, 0, item.get('TARİH'))
                        ws.write(r, 1, item.get('MALZEME_KODU'))
                        ws.write(r, 2, item.get('AÇIKLAMASI'))
                        ws.write(r, 3, item.get('MARKA'))
                        ws.write(r, 4, item.get('MALZEME_TÜRÜ'))
                        ws.write_number(r, 5, float(item.get('MİKTAR') or 0))
                        ws.write(r, 6, item.get('BİRİM'))
                        ws.write_number(r, 7, float(
                            item.get('BİRİM_BRÜT') or 0))
                        ws.write_number(r, 8, float(
                            item.get('BİRİM_İNDİRİM') or 0))
                        ws.write_number(r, 9, float(
                            item.get('BİRİM_NET') or 0))
                        ws.write_number(r, 10, float(
                            item.get('TOPLAM_İNDİRİM') or 0))
                        ws.write_number(r, 11, float(
                            item.get('KDV_TUTARI') or 0))
                        ws.write_number(r, 12, float(
                            item.get('NET_TOPLAM') or 0))
                        ws.write(r, 13, item.get('CARİ_KOD'))
                        ws.write(r, 14, item.get('CARİ_ÜNVAN'))
                        ws.write(r, 15, str(
                            item.get('İŞLEM_TARİHİ_FATURA') or ''))
                    workbook.close()
                    buffer.seek(0)
                    resp = HttpResponse(buffer.getvalue(
                    ), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
                    return resp
                else:
                    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
                    filename = f"malzeme_alim_{ts}.pdf"
                    pdf = SimpleDocTemplate(buffer, pagesize=A4)
                    data = [['Tarih', 'Malzeme Kodu', 'Açıklama', 'Marka', 'Tür', 'Miktar',
                             'Birim', 'Net Toplam', 'Cari Kod', 'Cari Ünvan', 'İşlem Tarihi(Fatura)']]
                    for item in malzeme_data['data']:
                        data.append([
                            item.get('TARİH'),
                            item.get('MALZEME_KODU'),
                            item.get('AÇIKLAMASI'),
                            item.get('MARKA'),
                            item.get('MALZEME_TÜRÜ'),
                            f"{float(item.get('MİKTAR') or 0):,.2f}",
                            item.get('BİRİM'),
                            f"{float(item.get('NET_TOPLAM') or 0):,.2f}",
                            item.get('CARİ_KOD'),
                            item.get('CARİ_ÜNVAN'),
                            str(item.get('İŞLEM_TARİHİ_FATURA') or '')
                        ])
                    tbl = Table(data)
                    tbl.setStyle(TableStyle([
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                    ]))
                    pdf.build([tbl])
                    buffer.seek(0)
                    resp = HttpResponse(buffer.getvalue(),
                                        content_type='application/pdf')
                    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
                    return resp

            return JsonResponse({
                'success': True,
                'data': malzeme_data['data'],
                'total_count': malzeme_data['total_count']
            })

        except Exception as e:
            logger.error(f"Malzeme alım verileri alınırken hata: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Veri alınırken hata oluştu: {str(e)}'
            })

    # GET request - Ana sayfa
    baslangic_tarihi = request.GET.get('baslangic_tarihi', '')
    bitis_tarihi = request.GET.get('bitis_tarihi', '')
    cari_kod = request.GET.get('cari_kod', '')
    cari_unvan = request.GET.get('cari_unvan', '')
    sort_by = request.GET.get('sort_by', 'islem_tarihi_desc')  # Varsayılan sıralama
    export = request.GET.get('export', '').lower()

    # İlk defa açıldığında (parametreler boşsa) bugünün tarihini varsayılan olarak ayarla
    from datetime import date
    today = date.today().strftime('%Y-%m-%d')

    # Eğer hiç tarih parametresi yoksa, bugünün tarihini ayarla
    if not baslangic_tarihi and not bitis_tarihi:
        baslangic_tarihi = today
        bitis_tarihi = today

    try:
        # Sayfalama kaldırıldı - tüm verileri getir
        alim_listesi = mssql_service.get_alim_listesi_all(
            baslangic_tarihi=baslangic_tarihi or None,
            bitis_tarihi=bitis_tarihi or None,
            cari_kod=cari_kod or None,
            cari_unvan=cari_unvan or None,
            sort_by=sort_by
        )

        # Export işlemleri
        if export in ('excel', 'pdf'):
            from io import BytesIO
            buffer = BytesIO()
            filename = f"alim_listesi_{datetime.now().strftime('%Y%m%d_%H%M')}.{ 'xlsx' if export=='excel' else 'pdf'}"

            if export == 'excel':
                import xlsxwriter
                workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
                worksheet = workbook.add_worksheet('Alim Listesi')
                headers = ['ID', 'Cari Kod', 'Cari Ünvan', 'Tarih',
                           'İşlem Tarihi', 'Fatura No', 'Net Toplam', 'Tutar']
                for col, h in enumerate(headers):
                    worksheet.write(0, col, h)
                for row_idx, f in enumerate(alim_listesi, start=1):
                    worksheet.write(row_idx, 0, f.get('ID'))
                    worksheet.write(row_idx, 1, f.get('CARİ_KOD'))
                    worksheet.write(row_idx, 2, f.get('CARİ_ÜNVAN'))
                    worksheet.write(row_idx, 3, f.get('FormattedDate'))
                    worksheet.write(row_idx, 4, str(
                        f.get('İŞLEM_TARİHİ') or ''))
                    worksheet.write(row_idx, 5, f.get('FATURA_NO'))
                    worksheet.write_number(
                        row_idx, 6, float(f.get('NET_TOPLAM') or 0))
                    worksheet.write_number(
                        row_idx, 7, float(f.get('TUTAR') or 0))
                workbook.close()
                buffer.seek(0)
                response = HttpResponse(buffer.getvalue(
                ), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                return response
            else:
                # PDF: basit tablo
                pdf = SimpleDocTemplate(buffer, pagesize=A4)
                data = [['ID', 'Cari Kod', 'Cari Ünvan', 'Tarih',
                         'İşlem Tarihi', 'Fatura No', 'Net Toplam', 'Tutar']]
                for f in alim_listesi:
                    data.append([
                        str(f.get('ID') or ''),
                        str(f.get('CARİ_KOD') or ''),
                        str(f.get('CARİ_ÜNVAN') or ''),
                        str(f.get('FormattedDate') or ''),
                        str(f.get('İŞLEM_TARİHİ') or ''),
                        str(f.get('FATURA_NO') or ''),
                        f"{float(f.get('NET_TOPLAM') or 0):,.2f}",
                        f"{float(f.get('TUTAR') or 0):,.2f}"
                    ])
                table = Table(data)
                table.setStyle(TableStyle([
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ]))
                pdf.build([table])
                buffer.seek(0)
                response = HttpResponse(
                    buffer.getvalue(), content_type='application/pdf')
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                return response
    except Exception as e:
        logger.error(f"Alım listesi alınırken hata: {e}")
        import traceback
        traceback.print_exc()
        alim_listesi = []
        pagination_info = {}

    # Plasiyerler listesini al (filtreleme için)
    try:
        plasiyerler = mssql_service.get_plasiyerler_list()
    except Exception as e:
        logger.error(f"Plasiyer listesi alınırken hata: {e}")
        plasiyerler = []

    context = {
        'user': request.user,
        'user_data': user_data,
        'sayfa_baslik': 'Alımlar',
        'satis_listesi': alim_listesi,  # Template uyumluluğu için
        'baslangic_tarihi': baslangic_tarihi,
        'bitis_tarihi': bitis_tarihi,
        'cari_kod': cari_kod,
        'cari_unvan': cari_unvan,
        'sort_by': sort_by,
        'sayfa_ikon': 'bi-cart-check',
        'plasiyerler': plasiyerler,
    }

    return render(request, 'tahsilat/genel_alimlar.html', context)


@login_required
def genel_satislar(request):
    """Genel Görünüm - Satışlar"""
    # Yetki kontrolü
    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    from .models import KullaniciYetki
    if request.user.username.upper() != 'FIRAT':
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='genel_gorunum')
            if not yetki.erisim_izni:
                return redirect('tahsilat:dashboard')
        except KullaniciYetki.DoesNotExist:
            return redirect('tahsilat:dashboard')

    # Session'dan kullanıcı verilerini al
    user_data = request.session.get('mssql_user_data', {})
    departman = user_data.get('departman', '')

    # Eğer user_data yoksa boş dict olarak ayarla
    if not user_data:
        user_data = {}

    # AJAX request for malzeme data
    if request.method == 'POST' and request.POST.get('action') in ('get_malzeme_satis', 'export_malzeme_satis_excel', 'export_malzeme_satis_pdf'):
        try:
            # Filtreleme parametreleri
            baslangic_tarihi = request.POST.get('baslangic_tarihi', '')
            bitis_tarihi = request.POST.get('bitis_tarihi', '')
            plasiyer = request.POST.get('plasiyer', '')
            cari_kod = request.POST.get('cari_kod', '')
            cari_unvan = request.POST.get('cari_unvan', '')
            malzeme_kodu = request.POST.get('malzeme_kodu', '')
            malzeme_aciklama = request.POST.get('malzeme_aciklama', '')
            per_page = request.POST.get('per_page', '100')
            page = int(request.POST.get('page', 1))

            # İlk defa açıldığında (parametreler boşsa) bugünün tarihini varsayılan olarak ayarla
            from datetime import date
            today = date.today().strftime('%Y-%m-%d')

            # Eğer hiç tarih parametresi yoksa, bugünün tarihini ayarla
            if not baslangic_tarihi and not bitis_tarihi:
                baslangic_tarihi = today
                bitis_tarihi = today

            # Sayfalama ayarları
            if per_page == 'all':
                page_size = None
            else:
                page_size = int(per_page)

            # Malzeme satış verilerini al - sayfalama olmadan tüm kayıtları getir
            malzeme_data = mssql_service.get_malzeme_satis_detay_all(
                baslangic_tarihi=baslangic_tarihi,
                bitis_tarihi=bitis_tarihi,
                plasiyer=plasiyer,
                cari_kod=cari_kod,
                cari_unvan=cari_unvan,
                malzeme_kodu=malzeme_kodu,
                malzeme_aciklama=malzeme_aciklama
            )

            # Export işlemleri
            action = request.POST.get('action')
            if action in ('export_malzeme_satis_excel', 'export_malzeme_satis_pdf'):
                from io import BytesIO
                import pandas as pd
                from django.http import HttpResponse
                from reportlab.lib.pagesizes import letter
                from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
                from reportlab.lib.styles import getSampleStyleSheet
                from reportlab.lib import colors
                from reportlab.lib.units import inch

                # DataFrame oluştur
                df = pd.DataFrame(malzeme_data['data'])

                if action == 'export_malzeme_satis_excel':
                    # Excel export
                    output = BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        df.to_excel(
                            writer, sheet_name='Malzeme Satış Detayları', index=False)

                    output.seek(0)
                    response = HttpResponse(
                        output.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    )
                    response['Content-Disposition'] = 'attachment; filename="malzeme_satis_detaylari.xlsx"'
                    return response

                elif action == 'export_malzeme_satis_pdf':
                    # PDF export
                    buffer = BytesIO()
                    doc = SimpleDocTemplate(buffer, pagesize=letter)
                    elements = []

                    # Başlık
                    styles = getSampleStyleSheet()
                    title = Paragraph(
                        "Malzeme Satış Detayları", styles['Title'])
                    elements.append(title)
                    elements.append(Spacer(1, 12))

                    # Tablo verilerini hazırla
                    table_data = [['Tarih', 'Cari Kod', 'Cari Unvan',
                                   'Malzeme Kodu', 'Malzeme Açıklama', 'Miktar', 'Birim Net', 'B2B', 'Fark', 'Tutar']]
                    for row in malzeme_data['data']:
                        table_data.append([
                            str(row.get('TARİH', '')),
                            str(row.get('CARİ_KOD', '')),
                            str(row.get('CARİ_ÜNVAN', '')),
                            str(row.get('MALZEME_KODU', '')),
                            str(row.get('AÇIKLAMASI', '')),
                            str(row.get('MİKTAR', '')),
                            str(row.get('BİRİM_NET', '')),
                            str(row.get('B2B', '')),
                            str(row.get('FARK', '')),
                            str(row.get('NET_TOPLAM', ''))
                        ])

                    # Tablo oluştur
                    table = Table(table_data)
                    table.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 14),
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                        ('GRID', (0, 0), (-1, -1), 1, colors.black)
                    ]))

                    elements.append(table)
                    doc.build(elements)

                    buffer.seek(0)
                    response = HttpResponse(
                        buffer.getvalue(), content_type='application/pdf')
                    response['Content-Disposition'] = 'attachment; filename="malzeme_satis_detaylari.pdf"'
                    return response

            # Normal AJAX response
            return JsonResponse({
                'success': True,
                'data': malzeme_data['data'],
                'total_count': malzeme_data['total_count']
            })

        except Exception as e:
            logger.error(f"Malzeme satış verileri alınırken hata: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Veri alınırken hata oluştu: {str(e)}'
            })

    # GET request - Ana sayfa
    baslangic_tarihi = request.GET.get('baslangic_tarihi', '')
    bitis_tarihi = request.GET.get('bitis_tarihi', '')
    cari_kod = request.GET.get('cari_kod', '')
    cari_unvan = request.GET.get('cari_unvan', '')
    plasiyer = request.GET.get('plasiyer', '')
    bolge = request.GET.get('bolge', '')
    e_belge_turu = request.GET.get('e_belge_turu', '')
    export = request.GET.get('export', '').lower()

    # İlk defa açıldığında (parametreler boşsa) bugünün tarihini varsayılan olarak ayarla
    from datetime import date
    today = date.today().strftime('%Y-%m-%d')

    # Eğer hiç tarih parametresi yoksa, bugünün tarihini ayarla
    if not baslangic_tarihi and not bitis_tarihi:
        baslangic_tarihi = today
        bitis_tarihi = today

    page = request.GET.get('page', 1)
    try:
        page = int(page)
    except (ValueError, TypeError):
        page = 1

    try:
        # Satış listesi al - sayfalama olmadan tüm kayıtları getir
        satis_listesi = mssql_service.get_satis_listesi_all(
            baslangic_tarihi=baslangic_tarihi or None,
            bitis_tarihi=bitis_tarihi or None,
            cari_kod=cari_kod or None,
            cari_unvan=cari_unvan or None,
            plasiyer=plasiyer or None,
            bolge=bolge or None,
            e_belge_turu=e_belge_turu or None
        )
    except Exception as e:
        logger.error(f"Satış listesi alınırken hata: {e}")
        satis_listesi = []

    # Plasiyer ve bölge seçeneklerini al
    try:
        plasiyer_options = mssql_service.get_plasiyer_list_from_fatura()
    except Exception as e:
        logger.error(f"Plasiyer listesi alınırken hata: {e}")
        plasiyer_options = []

    try:
        bolge_options = mssql_service.get_all_unique_bolgeler()
    except Exception as e:
        logger.error(f"Bölge listesi alınırken hata: {e}")
        bolge_options = []

    try:
        ebelge_turu_options = mssql_service.get_all_unique_ebelge_turleri()
    except Exception as e:
        logger.error(f"E-Belge Türü listesi alınırken hata: {e}")
        ebelge_turu_options = []

    context = {
        'user': request.user,
        'user_data': user_data,
        'sayfa_baslik': 'Satışlar',
        'satis_listesi': satis_listesi,
        'sayfa_ikon': 'bi-receipt',
        'baslangic_tarihi': baslangic_tarihi,
        'bitis_tarihi': bitis_tarihi,
        'cari_kod': cari_kod,
        'cari_unvan': cari_unvan,
        'plasiyer': plasiyer,
        'bolge': bolge,
        'e_belge_turu': e_belge_turu,
        'plasiyer_options': plasiyer_options,
        'bolge_options': bolge_options,
        'ebelge_turu_options': ebelge_turu_options,
    }

    if export in ('excel',):
        # Excel export
        import pandas as pd
        from io import BytesIO
        from django.http import HttpResponse

        df = pd.DataFrame(satis_listesi)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Satış Listesi', index=False)

        output.seek(0)
        resp = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        resp['Content-Disposition'] = 'attachment; filename="satis_listesi.xlsx"'
        return resp

    return render(request, 'tahsilat/genel_satislar.html', context)


@login_required
def genel_cari_analiz(request):
    """Genel Görünüm - Cari Genel Analiz"""
    # Yetki kontrolü
    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    from .models import KullaniciYetki
    if request.user.username.upper() != 'FIRAT':
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='genel_gorunum')
            if not yetki.erisim_izni:
                return redirect('tahsilat:dashboard')
        except KullaniciYetki.DoesNotExist:
            return redirect('tahsilat:dashboard')

    # Session'dan kullanıcı verilerini al
    user_data = request.session.get('mssql_user_data', {})
    departman = user_data.get('departman', '')

    # Eğer user_data yoksa boş dict olarak ayarla
    if not user_data:
        user_data = {}

    # AJAX request for export
    if request.method == 'POST' and request.POST.get('action') in ('export_excel', 'export_pdf'):
        try:
            # Filtreleme parametreleri
            baslangic_tarihi = request.POST.get('baslangic_tarihi', '')
            bitis_tarihi = request.POST.get('bitis_tarihi', '')
            plasiyer = request.POST.get('plasiyer', '')
            bolge = request.POST.get('bolge', '')

            # Verileri al
            cari_analiz_data = mssql_service.get_cari_genel_analiz(
                baslangic_tarihi=baslangic_tarihi or None,
                bitis_tarihi=bitis_tarihi or None,
                plasiyer=plasiyer or None,
                bolge=bolge or None
            )

            action = request.POST.get('action')
            if action == 'export_excel':
                # Excel export
                import pandas as pd
                from io import BytesIO
                from django.http import HttpResponse

                df = pd.DataFrame(cari_analiz_data)
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, sheet_name='Cari Genel Analiz', index=False)

                output.seek(0)
                response = HttpResponse(
                    output.getvalue(),
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
                response['Content-Disposition'] = 'attachment; filename="cari_genel_analiz.xlsx"'
                return response

            elif action == 'export_pdf':
                # PDF export
                from io import BytesIO
                from django.http import HttpResponse
                from reportlab.lib.pagesizes import letter, landscape
                from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
                from reportlab.lib.styles import getSampleStyleSheet
                from reportlab.lib import colors
                from reportlab.lib.units import inch

                buffer = BytesIO()
                doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
                elements = []

                # Başlık
                styles = getSampleStyleSheet()
                title = Paragraph("Cari Genel Analiz", styles['Title'])
                elements.append(title)
                elements.append(Spacer(1, 12))

                # Filtre bilgileri
                filter_text = f"Tarih: {baslangic_tarihi or 'Tümü'} - {bitis_tarihi or 'Tümü'}"
                if plasiyer:
                    filter_text += f" | Plasiyer: {plasiyer}"
                if bolge:
                    filter_text += f" | Bölge: {bolge}"
                filter_para = Paragraph(filter_text, styles['Normal'])
                elements.append(filter_para)
                elements.append(Spacer(1, 12))

                # Tablo verilerini hazırla
                table_data = [['Cari Kod', 'Cari Ünvan', 'Plasiyer', 'Bölge', 'Fatura Sayısı', 'Toplam Satış', 'Tahsilat Sayısı', 'Toplam Tahsilat']]
                for row in cari_analiz_data:
                    table_data.append([
                        str(row.get('cari_kod', '')),
                        str(row.get('cari_unvan', '')),
                        str(row.get('plasiyer', '')),
                        str(row.get('bolge', '')),
                        str(row.get('fatura_sayisi', 0)),
                        f"{row.get('toplam_satis', 0):,.2f}",
                        str(row.get('tahsilat_sayisi', 0)),
                        f"{row.get('toplam_tahsilat', 0):,.2f}"
                    ])

                # Tablo oluştur
                table = Table(table_data)
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black),
                    ('FONTSIZE', (0, 1), (-1, -1), 8),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ]))

                elements.append(table)
                doc.build(elements)

                buffer.seek(0)
                response = HttpResponse(
                    buffer.getvalue(), content_type='application/pdf')
                response['Content-Disposition'] = 'attachment; filename="cari_genel_analiz.pdf"'
                return response

        except Exception as e:
            logger.error(f"Cari analiz export hatası: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Export sırasında hata oluştu: {str(e)}'
            }, status=500)

    # GET request - Ana sayfa
    baslangic_tarihi = request.GET.get('baslangic_tarihi', '')
    bitis_tarihi = request.GET.get('bitis_tarihi', '')
    plasiyer = request.GET.get('plasiyer', '')
    bolge = request.GET.get('bolge', '')

    # İlk defa açıldığında (parametreler boşsa) bugünün tarihini varsayılan olarak ayarla
    from datetime import date
    today = date.today().strftime('%Y-%m-%d')

    # Eğer hiç tarih parametresi yoksa, bugünün tarihini ayarla
    if not baslangic_tarihi and not bitis_tarihi:
        baslangic_tarihi = today
        bitis_tarihi = today

    try:
        # Cari analiz verilerini al
        cari_analiz_data = mssql_service.get_cari_genel_analiz(
            baslangic_tarihi=baslangic_tarihi or None,
            bitis_tarihi=bitis_tarihi or None,
            plasiyer=plasiyer or None,
            bolge=bolge or None
        )
    except Exception as e:
        logger.error(f"Cari analiz verileri alınırken hata: {e}")
        cari_analiz_data = []

    # Plasiyer ve bölge listelerini al
    try:
        plasiyer_list = mssql_service.get_all_plasiyer_list()
        bolge_list = mssql_service.get_all_unique_bolgeler()
    except Exception as e:
        logger.error(f"Plasiyer/Bölge listesi alınırken hata: {e}")
        plasiyer_list = []
        bolge_list = []
    
    # Toplam hesaplamaları
    toplam_satis = sum(item.get('toplam_satis', 0) for item in cari_analiz_data)
    toplam_tahsilat = sum(item.get('toplam_tahsilat', 0) for item in cari_analiz_data)
    toplam_fatura_sayisi = sum(item.get('fatura_sayisi', 0) for item in cari_analiz_data)
    toplam_tahsilat_sayisi = sum(item.get('tahsilat_sayisi', 0) for item in cari_analiz_data)

    context = {
        'user': request.user,
        'user_data': user_data,
        'sayfa_baslik': 'Cari Genel Analiz',
        'cari_analiz_data': cari_analiz_data,
        'sayfa_ikon': 'bi-graph-up',
        'baslangic_tarihi': baslangic_tarihi,
        'bitis_tarihi': bitis_tarihi,
        'plasiyer': plasiyer,
        'bolge': bolge,
        'plasiyer_list': plasiyer_list,
        'bolge_list': bolge_list,
        'toplam_satis': toplam_satis,
        'toplam_tahsilat': toplam_tahsilat,
        'toplam_fatura_sayisi': toplam_fatura_sayisi,
        'toplam_tahsilat_sayisi': toplam_tahsilat_sayisi,
    }

    return render(request, 'tahsilat/genel_cari_analiz.html', context)


def _build_cari_aylik_ozet_data(baslangic_tarihi, bitis_tarihi):
    """Cari aylık özet için ay listesi ve satır verilerini üretir. (baslangic_tarihi, bitis_tarihi) YYYY-MM-DD formatında."""
    from datetime import datetime
    import calendar
    AY_ADLARI = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara']
    try:
        start = datetime.strptime(baslangic_tarihi or '', '%Y-%m-%d').date()
        end = datetime.strptime(bitis_tarihi or '', '%Y-%m-%d').date()
    except (ValueError, TypeError):
        start = date.today().replace(month=1, day=1)
        end = date.today()
    if start > end:
        start, end = end, start
    aylar = []
    y, m = start.year, start.month
    end_y, end_m = end.year, end.month
    while (y, m) <= (end_y, end_m):
        aylar.append({'yil': y, 'ay': m, 'label': f'{AY_ADLARI[m - 1]} {y}'})
        m += 1
        if m > 12:
            m, y = 1, y + 1

    aylik_satis = mssql_service.get_cari_aylik_satis(
        baslangic_tarihi=baslangic_tarihi or None,
        bitis_tarihi=bitis_tarihi or None
    )
    aylik_tahsilat = mssql_service.get_cari_aylik_tahsilat(
        baslangic_tarihi=baslangic_tarihi or None,
        bitis_tarihi=bitis_tarihi or None
    )
    bakiye_list = mssql_service.get_cari_bakiye_hepsi()

    satis_map = {(r['cari_kod'], r['yil'], r['ay']): r['toplam_satis'] for r in aylik_satis}
    tahsilat_map = {(r['cari_kod'], r['yil'], r['ay']): r['toplam_tahsilat'] for r in aylik_tahsilat}
    bakiye_map = {r['code']: r for r in bakiye_list}

    cari_codes = set()
    for r in aylik_satis:
        cari_codes.add(r['cari_kod'])
    for r in aylik_tahsilat:
        cari_codes.add(r['cari_kod'])
    for r in bakiye_list:
        cari_codes.add(r['code'])
    cari_codes = sorted([c for c in cari_codes if c])

    unvan_from_satis = {r['cari_kod']: r['cari_unvan'] for r in aylik_satis}
    unvan_from_tahsilat = {r['cari_kod']: r['cari_unvan'] for r in aylik_tahsilat}

    rows = []
    for cari_kod in cari_codes:
        bakiye_info = bakiye_map.get(cari_kod, {})
        cari_unvan = (
            bakiye_info.get('definition_') or
            unvan_from_satis.get(cari_kod) or
            unvan_from_tahsilat.get(cari_kod) or
            ''
        )
        row = {
            'cari_kod': cari_kod,
            'cari_unvan': cari_unvan,
            'guncel_bakiye': bakiye_info.get('bakiye', 0),
            'borc': bakiye_info.get('borc', 0),
            'alacak': bakiye_info.get('alacak', 0),
        }
        toplam_satis = 0.0
        toplam_tahsilat = 0.0
        for a in aylar:
            key_satis = (cari_kod, a['yil'], a['ay'])
            key_label = f"{a['label']}_satis"
            satis_val = satis_map.get(key_satis, 0)
            tahsilat_val = tahsilat_map.get(key_satis, 0)
            row[key_label] = satis_val
            row[f"{a['label']}_tahsilat"] = tahsilat_val
            toplam_satis += satis_val
            toplam_tahsilat += tahsilat_val
        row['toplam_satis'] = toplam_satis
        row['toplam_tahsilat'] = toplam_tahsilat
        rows.append(row)
    return aylar, rows


@login_required
def genel_cari_aylik_ozet(request):
    """Genel Görünüm - Cari Aylık Özet: ay ay satış/tahsilat + güncel bakiye."""
    from .models import KullaniciYetki
    if request.user.username.upper() != 'FIRAT':
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='genel_gorunum')
            if not yetki.erisim_izni:
                return redirect('tahsilat:dashboard')
        except KullaniciYetki.DoesNotExist:
            return redirect('tahsilat:dashboard')

    user_data = request.session.get('mssql_user_data', {}) or {}

    if request.method == 'POST' and request.POST.get('action') == 'export_excel':
        try:
            baslangic_tarihi = request.POST.get('baslangic_tarihi', '')
            bitis_tarihi = request.POST.get('bitis_tarihi', '')
            aylar, rows = _build_cari_aylik_ozet_data(baslangic_tarihi, bitis_tarihi)
            import pandas as pd
            from io import BytesIO
            export_rows = []
            for r in rows:
                rec = {'Cari Kod': r['cari_kod'], 'Cari Ünvan': r['cari_unvan']}
                for a in aylar:
                    rec[f"{a['label']} Satış"] = r.get(f"{a['label']}_satis", 0)
                    rec[f"{a['label']} Tahsilat"] = r.get(f"{a['label']}_tahsilat", 0)
                rec['Güncel Bakiye'] = r.get('guncel_bakiye', 0)
                rec['Toplam Satış'] = r.get('borc', 0)
                rec['Toplam Tahsilat'] = r.get('alacak', 0)
                export_rows.append(rec)
            df = pd.DataFrame(export_rows)
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Cari Aylık Özet', index=False)
            output.seek(0)
            response = HttpResponse(
                output.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = 'attachment; filename="cari_aylik_ozet.xlsx"'
            return response
        except Exception as e:
            logger.error(f"Cari aylık özet export hatası: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Export sırasında hata oluştu: {str(e)}'
            }, status=500)

    today = date.today().strftime('%Y-%m-%d')
    yil_basi = date.today().replace(month=1, day=1).strftime('%Y-%m-%d')
    baslangic_tarihi = request.GET.get('baslangic_tarihi', yil_basi)
    bitis_tarihi = request.GET.get('bitis_tarihi', today)
    if not baslangic_tarihi:
        baslangic_tarihi = yil_basi
    if not bitis_tarihi:
        bitis_tarihi = today

    try:
        aylar, rows = _build_cari_aylik_ozet_data(baslangic_tarihi, bitis_tarihi)
    except Exception as e:
        logger.error(f"Cari aylık özet veri hatası: {e}")
        aylar, rows = [], []

    context = {
        'user': request.user,
        'user_data': user_data,
        'sayfa_baslik': 'Cari Aylık Özet',
        'sayfa_ikon': 'bi-calendar-month',
        'aylar': aylar,
        'cari_aylik_rows': rows,
        'baslangic_tarihi': baslangic_tarihi,
        'bitis_tarihi': bitis_tarihi,
    }
    return render(request, 'tahsilat/genel_cari_aylik_ozet.html', context)


@login_required
@require_http_methods(["POST"])
def tahsilat_sil_ajax(request):
    """AJAX tahsilat silme - Sadece İŞLENMEDİ durumdaki kayıtlar silinebilir"""
    try:
        tahsilat_id = request.POST.get('tahsilat_id')
        # MSSQL'deki kullanıcı adı ile eşleştir
        kullanici = request.user.username.upper()

        if not tahsilat_id:
            return JsonResponse({'success': False, 'error': 'Tahsilat ID gerekli'})

        # Tahsilatı sil - kullanıcı adını direkt kullan
        result = mssql_service.delete_tahsilat(tahsilat_id, kullanici)

        logger.info(
            f"Tahsilat silme işlemi - ID: {tahsilat_id}, Kullanıcı: {kullanici}, Sonuç: {result}")

        return JsonResponse(result)

    except Exception as e:
        logger.error(f"tahsilat_sil_ajax error: {e}")
        return JsonResponse({
            'success': False,
            'error': f'Sunucu hatası: {str(e)}'
        })


@login_required
def stok_listesi(request):
    """Stok Listesi sayfası - MALZEME_STOK tablosundan veri listeler"""
    # Session'dan plasiyer bilgisini al
    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)

    # Filtreleme parametreleri
    arama = request.GET.get('arama', '')
    malzeme_turu = request.GET.get('malzeme_turu', '')
    marka = request.GET.get('marka', '')
    stok_durumu = request.GET.get('stok_durumu', '')

    # MALZEME_STOK tablosundan stok verilerini al
    malzeme_stok_list = mssql_service.get_malzeme_stok_list(
        search_term=arama if arama else None,
        malzeme_turu=malzeme_turu if malzeme_turu else None,
        marka=marka if marka else None,
        stok_durumu=stok_durumu if stok_durumu else None,
        max_records=10000  # HEPsİ seçeneği için maksimum limite
    )

    # Sayfalama sistemi
    has_filters = bool(arama or malzeme_turu or marka or stok_durumu)
    per_page_param = request.GET.get('per_page', '100')

    # HEPsİ seçeneği kontrolü
    if per_page_param == 'all' or has_filters:
        # Filtreleme varsa veya HEPsİ seçilmişse tüm sonuçları göster
        page_obj = malzeme_stok_list
        paginator = None
        per_page = 'all' if per_page_param == 'all' else len(malzeme_stok_list)
    else:
        # Sayfa başına gösterim sayısı (varsayılan 100)
        try:
            per_page = int(per_page_param)
        except (ValueError, TypeError):
            per_page = 100

        if len(malzeme_stok_list) == 0:
            page_obj = malzeme_stok_list
            paginator = None
        else:
            paginator = Paginator(malzeme_stok_list, per_page)
            page_number = request.GET.get('page', 1)
            try:
                page_obj = paginator.get_page(page_number)
            except:
                page_obj = paginator.get_page(1)

    # Filtreleme için gerekli listeler (MALZEME_STOK tablosundan)
    filter_options = mssql_service.get_malzeme_stok_filter_options()

    context = {
        'user': request.user,
        'user_data': user_data,
        'plasiyer': plasiyer,
        'malzeme_stok_list': page_obj,
        'malzeme_turleri': filter_options.get('malzeme_turleri', []),
        'markalar': filter_options.get('markalar', []),
        'arama': arama,
        'malzeme_turu': malzeme_turu,
        'marka': marka,
        'stok_durumu': stok_durumu,
        'has_filters': has_filters,
        'paginator': paginator,
        'per_page': per_page,
    }

    return render(request, 'tahsilat/stok_listesi.html', context)


@login_required
def stok_yonetimi(request):
    """Stok Yönetimi sayfası - MALZEME_STOK tablosundan veri listeler"""
    # Excel export kontrolü
    if request.GET.get('export') == 'excel':
        return export_stok_excel(request)

    # Session'dan plasiyer bilgisini al
    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)

    # Filtreleme parametreleri
    arama = request.GET.get('arama', '')
    malzeme_turu = request.GET.get('malzeme_turu', '')
    marka = request.GET.get('marka', '')
    stok_durumu = request.GET.get('stok_durumu', '')

    # MALZEME_STOK tablosundan stok verilerini al
    malzeme_stok_list = mssql_service.get_malzeme_stok_list(
        search_term=arama if arama else None,
        malzeme_turu=malzeme_turu if malzeme_turu else None,
        marka=marka if marka else None,
        stok_durumu=stok_durumu if stok_durumu else None,
        max_records=10000  # HEPsİ seçeneği için maksimum limite
    )

    # Sayfalama sistemi
    has_filters = bool(arama or malzeme_turu or marka or stok_durumu)
    per_page_param = request.GET.get('per_page', '100')

    # HEPsİ seçeneği kontrolü
    if per_page_param == 'all' or has_filters:
        # Filtreleme varsa veya HEPsİ seçilmişse tüm sonuçları göster
        page_obj = malzeme_stok_list
        paginator = None
        per_page = 'all' if per_page_param == 'all' else len(malzeme_stok_list)
    else:
        # Sayfa başına gösterim sayısı (varsayılan 100)
        try:
            per_page = int(per_page_param)
        except (ValueError, TypeError):
            per_page = 100

        if len(malzeme_stok_list) == 0:
            page_obj = malzeme_stok_list
            paginator = None
        else:
            paginator = Paginator(malzeme_stok_list, per_page)
            page_number = request.GET.get('page', 1)
            try:
                page_obj = paginator.get_page(page_number)
            except:
                page_obj = paginator.get_page(1)

    # Filtreleme için gerekli listeler (MALZEME_STOK tablosundan)
    filter_options = mssql_service.get_malzeme_stok_filter_options()

    context = {
        'user': request.user,
        'user_data': user_data,
        'plasiyer': plasiyer,
        'malzeme_stok_list': page_obj,
        'malzeme_turleri': filter_options.get('malzeme_turleri', []),
        'markalar': filter_options.get('markalar', []),
        'arama': arama,
        'malzeme_turu': malzeme_turu,
        'marka': marka,
        'stok_durumu': stok_durumu,
        'has_filters': has_filters,
        'paginator': paginator,
        'per_page': per_page,
    }

    return render(request, 'tahsilat/stok_yonetimi.html', context)


def _parse_om_decimal_param(val):
    if val is None or str(val).strip() == '':
        return None
    try:
        return float(str(val).strip().replace(',', '.'))
    except ValueError:
        return None


OM_MAX_IN_LIST = 500  # SQL IN parametre sınırına yaklaşmamak için


def _om_getlist_capped(request, key, cap=OM_MAX_IN_LIST):
    raw = request.GET.getlist(key)
    out, seen = [], set()
    for x in raw:
        s = (x or '').strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= cap:
            break
    return out


def _ortalama_maliyet_filter_pairs(
    uygula, per_page,
    fatura_list, malzeme_tur_list, kod_list, marka_list,
    toplam_miktar_min, toplam_miktar_max,
):
    """Sıralama parametreleri hariç (tablo başlığı linkleri için)."""
    from urllib.parse import urlencode
    pairs = [('per_page', str(per_page))]
    if uygula:
        pairs.insert(0, ('uygula', '1'))
    for x in fatura_list:
        pairs.append(('fatura_turu', x))
    for x in malzeme_tur_list:
        pairs.append(('malzeme_turu', x))
    for x in kod_list:
        pairs.append(('malzeme_kodu', x))
    for x in marka_list:
        pairs.append(('marka', x))
    if toplam_miktar_min is not None:
        pairs.append(('toplam_miktar_min', str(toplam_miktar_min)))
    if toplam_miktar_max is not None:
        pairs.append(('toplam_miktar_max', str(toplam_miktar_max)))
    return urlencode(pairs)


def _ortalama_maliyet_url_pairs(
    uygula, sort_by, sort_dir, per_page,
    fatura_list, malzeme_tur_list, kod_list, marka_list,
    toplam_miktar_min, toplam_miktar_max,
):
    from urllib.parse import urlencode
    base = _ortalama_maliyet_filter_pairs(
        uygula, per_page, fatura_list, malzeme_tur_list, kod_list, marka_list,
        toplam_miktar_min, toplam_miktar_max,
    )
    sort_part = urlencode([('sort', sort_by), ('dir', sort_dir)])
    return f'{base}&{sort_part}' if base else sort_part


def _user_can_access_ortalama_maliyet(user):
    """Ambar raporu gibi: ayrı yetki veya Stok Listesi yetkisi; FIRAT her zaman."""
    if not user.is_authenticated:
        return False
    if user.username.upper() == 'FIRAT':
        return True
    for menu_adi in ('ortalama_maliyet', 'stok_listesi'):
        try:
            y = KullaniciYetki.objects.get(
                kullanici=user, menu_adi=menu_adi)
            if y.erisim_izni:
                return True
        except KullaniciYetki.DoesNotExist:
            continue
    return False


@login_required
def ortalama_maliyet(request):
    """Stok Yönetimi > Ortalama Maliyet — ORTALAMA_MALIYET görünümü (ilk açılışta sorgu yok)."""

    if not _user_can_access_ortalama_maliyet(request.user):
        messages.error(
            request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
        return redirect('tahsilat:dashboard')

    user_data = request.session.get('mssql_user_data', {})
    if not user_data:
        user_data = {}

    om_filter_opts = mssql_service.get_ortalama_maliyet_filter_options()

    uygula = request.GET.get('uygula') == '1'
    fatura_list = _om_getlist_capped(request, 'fatura_turu')
    malzeme_tur_list = _om_getlist_capped(request, 'malzeme_turu')
    malzeme_kod_list = _om_getlist_capped(request, 'malzeme_kodu')
    marka_list = _om_getlist_capped(request, 'marka')
    toplam_miktar_min = _parse_om_decimal_param(request.GET.get('toplam_miktar_min'))
    toplam_miktar_max = _parse_om_decimal_param(request.GET.get('toplam_miktar_max'))
    sort_by = (request.GET.get('sort') or 'malzeme_kodu').strip() or 'malzeme_kodu'
    sort_dir = (request.GET.get('dir') or 'asc').strip().lower()
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'asc'

    try:
        page = int(request.GET.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)

    per_page_param = request.GET.get('per_page', '50')
    if per_page_param == 'all':
        per_page = 'all'
    else:
        try:
            per_page = int(per_page_param)
            if per_page not in (25, 50, 100, 200):
                per_page = 50
        except (TypeError, ValueError):
            per_page = 50

    filter_kwargs = {
        'fatura_turu': fatura_list or None,
        'malzeme_turu': malzeme_tur_list or None,
        'malzeme_kodu': malzeme_kod_list or None,
        'marka': marka_list or None,
        'toplam_miktar_min': toplam_miktar_min,
        'toplam_miktar_max': toplam_miktar_max,
    }

    if uygula and request.GET.get('export') == 'excel':
        try:
            import pandas as pd
            from io import BytesIO

            export_rows = mssql_service.get_ortalama_maliyet_export_rows(
                sort_by=sort_by,
                sort_dir=sort_dir,
                max_rows=50000,
                **filter_kwargs,
            )
            if not export_rows:
                messages.warning(request, 'Dışa aktarılacak kayıt bulunamadı.')
                rq = _ortalama_maliyet_url_pairs(
                    True, sort_by, sort_dir, per_page,
                    fatura_list, malzeme_tur_list, malzeme_kod_list, marka_list,
                    toplam_miktar_min, toplam_miktar_max,
                )
                return redirect(f'{request.path}?{rq}')

            df = pd.DataFrame([{
                'Fatura Türü': r['fatura_turu'],
                'Malzeme Türü': r['malzeme_turu'],
                'Malzeme Kodu': r['malzeme_kodu'],
                'Açıklaması': r['aciklama'],
                'Marka': r['marka'],
                'Fatura Durumu': r['fatura_durumu'],
                'Toplam Miktar': r['toplam_miktar'],
                'Ağırlıklı Ort. Birim': r['agirlikli_ort_birim'],
            } for r in export_rows])
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Ortalama Maliyet', index=False)
            output.seek(0)
            response = HttpResponse(
                output.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            response['Content-Disposition'] = (
                'attachment; filename="ortalama_maliyet_%s.xlsx"'
                % timezone.now().strftime('%Y%m%d_%H%M')
            )
            return response
        except Exception as ex:
            logger.error(f'ortalama_maliyet Excel export: {ex}')
            messages.error(request, 'Excel oluşturulurken hata oluştu.')
            return redirect('tahsilat:ortalama_maliyet')

    rows = []
    pagination = {
        'total_count': 0,
        'total_pages': 0,
        'current_page': 1,
        'page_size': per_page,
        'has_next': False,
        'has_previous': False,
        'next_page': None,
        'previous_page': None,
        'start_item': 0,
        'end_item': 0,
    }
    if uygula:
        rows, pagination = mssql_service.get_ortalama_maliyet_paginated(
            page=page,
            page_size=per_page,
            sort_by=sort_by,
            sort_dir=sort_dir,
            **filter_kwargs,
        )

    om_query = _ortalama_maliyet_url_pairs(
        uygula, sort_by, sort_dir, per_page,
        fatura_list, malzeme_tur_list, malzeme_kod_list, marka_list,
        toplam_miktar_min, toplam_miktar_max,
    )
    om_base = _ortalama_maliyet_filter_pairs(
        uygula, per_page, fatura_list, malzeme_tur_list, malzeme_kod_list, marka_list,
        toplam_miktar_min, toplam_miktar_max,
    )

    context = {
        'user': request.user,
        'user_data': user_data,
        'uygula': uygula,
        'rows': rows,
        'pagination': pagination,
        'om_filter_opts': om_filter_opts,
        'sel_fatura_turu': fatura_list,
        'sel_malzeme_turu': malzeme_tur_list,
        'sel_malzeme_kodu': malzeme_kod_list,
        'sel_marka': marka_list,
        'toplam_miktar_min': request.GET.get('toplam_miktar_min', '') or '',
        'toplam_miktar_max': request.GET.get('toplam_miktar_max', '') or '',
        'sort_by': sort_by,
        'sort_dir': sort_dir,
        'per_page': per_page,
        'om_query': om_query,
        'om_base': om_base,
    }
    return render(request, 'tahsilat/ortalama_maliyet.html', context)


@login_required
def fiyat_analizi(request):
    """Fiyat Analizi sayfası - FIYATANALIZ tablosundan veri listeler"""
    # Excel export kontrolü
    if request.GET.get('export') == 'excel':
        return export_fiyat_analizi_excel(request)

    # Session'dan plasiyer bilgisini al
    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)

    # Filtreleme parametreleri
    arama = request.GET.get('arama', '')
    malzeme_turu = request.GET.get('malzeme_turu', '')
    marka = request.GET.get('marka', '')
    stok_durumu = request.GET.get('stok_durumu', '')
    
    # FIYATANALIZ tablosundan fiyat analizi verilerini al
    fiyat_analizi_list = mssql_service.get_fiyat_analizi_list(
        search_term=arama if arama else None,
        malzeme_turu=malzeme_turu if malzeme_turu else None,
        marka=marka if marka else None,
        stok_durumu=stok_durumu if stok_durumu else None,
        max_records=10000  # HEPsİ seçeneği için maksimum limit
    )
    
    # Sayfalama sistemi
    has_filters = bool(arama or malzeme_turu or marka or stok_durumu)
    per_page_param = request.GET.get('per_page', '100')

    # HEPsİ seçeneği kontrolü
    if per_page_param == 'all' or has_filters:
        # Filtreleme varsa veya HEPsİ seçilmişse tüm sonuçları göster
        page_obj = fiyat_analizi_list
        paginator = None
        per_page = 'all' if per_page_param == 'all' else len(
            fiyat_analizi_list)
    else:
        # Sayfa başına gösterim sayısı (varsayılan 100)
        try:
            per_page = int(per_page_param)
        except (ValueError, TypeError):
            per_page = 100

        if len(fiyat_analizi_list) == 0:
            page_obj = fiyat_analizi_list
            paginator = None
        else:
            paginator = Paginator(fiyat_analizi_list, per_page)
            page_number = request.GET.get('page', 1)
            try:
                page_obj = paginator.get_page(page_number)
            except:
                page_obj = paginator.get_page(1)

    # Filtreleme için gerekli listeler (FIYATANALIZ tablosundan)
    filter_options = mssql_service.get_fiyat_analizi_filter_options()

    context = {
        'user': request.user,
        'user_data': user_data,
        'plasiyer': plasiyer,
        'fiyat_analizi_list': page_obj,
        'malzeme_turleri': filter_options.get('malzeme_turleri', []),
        'markalar': filter_options.get('markalar', []),
        'arama': arama,
        'malzeme_turu': malzeme_turu,
        'marka': marka,
        'stok_durumu': stok_durumu,
        'has_filters': has_filters,
        'paginator': paginator,
        'per_page': per_page,
    }

    return render(request, 'tahsilat/stok_listesi.html', context)
@login_required
def cari_analiz(request):
    """Cari Analiz sayfası - TUMCARIHARETLER tablosundan analiz"""
    # Session'dan plasiyer bilgisini al
    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)

    # Form parametreleri - URL'den gelenler varsa kullan, yoksa boş
    cari_kod = request.GET.get('cari_kod', '') or ''
    start_date = request.GET.get('start_date', '') or ''
    end_date = request.GET.get('end_date', '') or ''
    per_page = request.GET.get('per_page', '100')
    
    # Tüm cari hesapları getir (client-side arama için)
    cari_hesaplar = mssql_service.get_cari_hesaplar_all()
    
    # Cari listesi - sadece seçili cari varsa gösterim için
    cariler = []
    if cari_kod:
        # Seçili cariyi bulmak için arama yap
        cariler_raw = mssql_service.get_cari_listesi(cari_kod)
        for cari in cariler_raw:
            if isinstance(cari, dict):
                c_kod = cari.get('cari_kod', '').strip()
                c_unvan = cari.get('cari_unvan', '').strip()
                if c_kod and c_unvan:
                    cariler.append({
                        'cari_kod': c_kod,
                        'cari_unvan': c_unvan,
                    })

    # Seçilen cari analizi
    cari_analizi = None
    cari_hareketleri = []

    if cari_kod:
        # Tarih formatını düzenle
        start_date_obj = None
        end_date_obj = None

        if start_date:
            try:
                start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
            except ValueError:
                pass

        if end_date:
            try:
                end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
            except ValueError:
                pass

        # Cari analizi ve hareketleri getir
        cari_analizi = mssql_service.get_cari_analizi(
            cari_kod, start_date_obj, end_date_obj)
        cari_hareketleri = mssql_service.get_cari_hareketleri(
            cari_kod, start_date_obj, end_date_obj)

        # Debug bilgisi
        logger.info(f"Cari kod: {cari_kod}")
        logger.info(f"Cari analizi: {cari_analizi is not None}")
        logger.info(
            f"Cari hareketleri sayısı: {len(cari_hareketleri) if cari_hareketleri else 0}")

        # Gelişmiş analiz verileri ekle
        # (enrich_cari_analysis fonksiyonu şu an kullanılmıyor)

        # Pagination sistemi
        toplam_hareket_sayisi = len(
            cari_hareketleri) if cari_hareketleri else 0

        # Sayfa başına gösterim sayısı
        try:
            per_page_int = int(
                per_page) if per_page != 'all' else toplam_hareket_sayisi
        except (ValueError, TypeError):
            per_page_int = 100

        # Hareketleri pagination ile sınırla
        if per_page == 'all':
            paginated_hareketler = cari_hareketleri
            paginator = None
        else:
            paginator = Paginator(cari_hareketleri, per_page_int)
            page_number = request.GET.get('page', 1)
            try:
                page_obj = paginator.get_page(page_number)
                paginated_hareketler = page_obj.object_list
            except:
                paginated_hareketler = cari_hareketleri[:per_page_int]
                page_obj = None
    else:
        toplam_hareket_sayisi = 0
        paginated_hareketler = []
        paginator = None
        page_obj = None

    # Risk analizi - Düzeltilmiş bakiye yorumlama
    risk_analizi = {
        'seviye': 'düşük',
        'renk': 'success',
        'mesaj': 'Normal cari hareket durumu',
        'oneriler': []
    }

    if cari_analizi:
        bakiye = cari_analizi.get('bakiye', 0)
        son_hareket = cari_analizi.get('son_hareket_tarihi')

        # Bakiye durumu kontrolü (Pozitif bakiye = Müşteri borçlu, Negatif bakiye = Müşteri alacaklı)
        if bakiye > 10000:
            risk_analizi['seviye'] = 'yüksek'
            risk_analizi['renk'] = 'danger'
            risk_analizi['mesaj'] = f'Yüksek borç bakiyesi: {bakiye:,.2f} TL'
            risk_analizi['oneriler'].extend([
                'Acil tahsilat planı yapılmalı',
                'Müşteri ile ödeme planı görüşülmeli',
                'Kredi limitini gözden geçirin',
                'Teminat durumunu kontrol edin'
            ])
        elif bakiye > 5000:
            risk_analizi['seviye'] = 'orta'
            risk_analizi['renk'] = 'warning'
            risk_analizi['mesaj'] = f'Takip edilmesi gereken borç: {bakiye:,.2f} TL'
            risk_analizi['oneriler'].extend([
                'Düzenli takip yapılmalı',
                'Haftalık durum kontrolü',
                'Yeni satışlarda dikkatli olun'
            ])
        elif bakiye > 1000:
            risk_analizi['seviye'] = 'düşük'
            risk_analizi['renk'] = 'info'
            risk_analizi['mesaj'] = f'Normal borç seviyesi: {bakiye:,.2f} TL'
            risk_analizi['oneriler'].append('Normal takip süreci devam etsin')
        elif bakiye < -1000:
            risk_analizi['seviye'] = 'pozitif'
            risk_analizi['renk'] = 'success'
            risk_analizi['mesaj'] = f'Müşteri alacaklı: {abs(bakiye):,.2f} TL'
            risk_analizi['oneriler'].extend([
                'Kredili satış imkanı değerlendirilebilir',
                'Müşteri sadakati yüksek',
                'Ek hizmet teklifleri sunulabilir'
            ])
        else:
            risk_analizi['mesaj'] = 'Bakiye dengelenmiş durumda'

        # Son hareket tarihi kontrolü
        if son_hareket:
            from datetime import timedelta
            # son_hareket hem date hem datetime olabilir, güvenli şekilde date'e çevir
            if hasattr(son_hareket, 'date'):
                son_hareket_date = son_hareket.date()
            else:
                son_hareket_date = son_hareket
            gun_farki = (datetime.now().date() - son_hareket_date).days

            if gun_farki > 90 and bakiye > 1000:
                if risk_analizi['seviye'] != 'yüksek':
                    risk_analizi['seviye'] = 'yüksek'
                    risk_analizi['renk'] = 'danger'
                risk_analizi['mesaj'] += f' (Son hareket: {gun_farki} gün önce)'
                risk_analizi['oneriler'].extend([
                    'Acil müşteri iletişimi gerekli',
                    'Borç yapılandırma değerlendirilmeli',
                    'Hukuki süreç değerlendirilebilir'
                ])
            elif gun_farki > 60 and bakiye > 1000:
                if risk_analizi['seviye'] == 'düşük':
                    risk_analizi['seviye'] = 'orta'
                    risk_analizi['renk'] = 'warning'
                risk_analizi['oneriler'].append(
                    'Müşteri ile iletişime geçilmeli')
            elif gun_farki > 30:
                risk_analizi['oneriler'].append(
                    f'Son hareket {gun_farki} gün önce, takip edin')

    context = {
        'user': request.user,
        'user_data': user_data,
        'plasiyer': plasiyer,
        'cari_hesaplar': cari_hesaplar,  # Tüm cari hesapları (client-side arama için)
        'cariler': cariler,  # Seçili cari için
        'cari_kod': cari_kod,  # URL'den gelen değer
        'start_date': start_date,  # URL'den gelen değer
        'end_date': end_date,  # URL'den gelen değer
        'per_page': per_page,
        'cari_analizi': cari_analizi,
        'cari_hareketleri': paginated_hareketler,
        'toplam_hareket_sayisi': toplam_hareket_sayisi,
        'paginator': paginator,
        'page_obj': page_obj if 'page_obj' in locals() else None,
        'risk_analizi': risk_analizi,
    }

    return render(request, 'tahsilat/cari_analiz.html', context)


@login_required
def cari_vade_analizi(request):
    """Carinin ödeme vade alışkanlığı analiz sayfası"""
    # Debug: Log the request details
    logger.info(
        f"Vade analizi sayfası açıldı. User: {request.user.username if request.user.is_authenticated else 'Anonymous'}")
    logger.info(
        f"Request META - HTTP_HOST: {request.META.get('HTTP_HOST', 'N/A')}")
    logger.info(
        f"Request META - SERVER_NAME: {request.META.get('SERVER_NAME', 'N/A')}")

    try:
        # Kullanıcı bilgilerini al
        user_data = mssql_service.get_kullanici_bilgileri(
            request.user.username)
        if not user_data:
            messages.error(request, "Kullanıcı bilgileri bulunamadı!")
            return redirect('tahsilat:login')

        plasiyer = user_data.get('PLASIYER_KOD')

        # Cari listesini al - tüm cariler
        search_term = request.GET.get('search', '')
        cariler = mssql_service.get_cari_listesi(search_term)
        # Limit kaldırıldı, tüm cariler gösterilecek

        # Seçili cari ve tarih aralığı
        cari_kod = request.GET.get('cari_kod', '')
        start_date = request.GET.get('start_date', '')
        end_date = request.GET.get('end_date', '')

        # Varsayılan tarih aralığı - 2025 yılı başından bugüne
        if not start_date:
            start_date = '2025-01-01'
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')

        vade_analizi = None
        if cari_kod:
            # Vade analizini getir
            vade_analizi = mssql_service.get_cari_vade_analizi(
                cari_kod, start_date, end_date)

            if vade_analizi:
                # Grafik verileri hazırla (şimdilik boş dict, ileride eklenebilir)
                vade_analizi['grafik_verileri'] = {
                    'gecikme_pie': None,
                    'aylik_trend': None
                }

        context = {
            'user': request.user,
            'user_data': user_data,
            'plasiyer': plasiyer,
            'cariler': cariler,
            'cari_kod': cari_kod,
            'search_term': search_term,
            'start_date': start_date,
            'end_date': end_date,
            'vade_analizi': vade_analizi,
        }

        return render(request, 'tahsilat/cari_vade_analizi.html', context)

    except Exception as e:
        logger.error(f"Vade analizi error: {e}")
        messages.error(request, f"Vade analizi hatası: {str(e)}")
        return redirect('tahsilat:dashboard')


def _get_filtered_cari_gecikme_queryset(plasiyer_list=None, bolge_list=None, min_gecikme_gun=None, max_gecikme_gun=None):
    """
    Filters CariGeckme queryset with optional plasiyer and bolge selections.
    """
    queryset = CariGeckme.get_latest_data()

    if plasiyer_list:
        queryset = queryset.filter(plasiyer__in=plasiyer_list)

    if bolge_list:
        queryset = queryset.filter(bolge__in=bolge_list)

    if min_gecikme_gun is not None:
        queryset = queryset.filter(gecikme_gun_sayisi__gte=min_gecikme_gun)

    if max_gecikme_gun is not None:
        queryset = queryset.filter(gecikme_gun_sayisi__lte=max_gecikme_gun)

    return queryset.order_by('-gecikme_tutari')


@login_required
def cari_bakiyeler(request):
    """Cari Bakiyeler sayfası - CARIHESAPEKSTRE tablosundan veri listeler"""
    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    if request.user.username.upper() != 'FIRAT':
        # Yetki kontrolü
        from .models import KullaniciYetki
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='cari_bakiyeler')
            if not yetki.erisim_izni:
                messages.error(request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
                return redirect('tahsilat:dashboard')
        except KullaniciYetki.DoesNotExist:
            messages.error(request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
            return redirect('tahsilat:dashboard')

    user_data = request.session.get('mssql_user_data', {})
    
    try:
        # SQL sorgusu - tüm cari bakiyeleri getir
        query = """
        SELECT  [CARİ KOD]
              ,[CARİ ÜNVAN]
              ,[PLASİYER]  
              ,[BÖLGE]
              ,[PLASİYER KOD]
              ,[BORÇ]
              ,[ALACAK]
              ,[BAKİYE]
        FROM [GO3].[dbo].[CARIHESAPEKSTRE] 
        ORDER BY [BAKİYE] DESC
        """
        
        cari_bakiyeler_list = mssql_service.execute_query(query) or []
        
        # Toplam hesaplamalar - hem boşluklu hem boşluksuz key'leri kontrol et
        
        toplam_borc = sum([float(item.get('BORÇ') or item.get('BORC') or 0) for item in cari_bakiyeler_list])
        toplam_alacak = sum([float(item.get('ALACAK') or 0) for item in cari_bakiyeler_list])
        toplam_bakiye = sum([float(item.get('BAKİYE') or item.get('BAKIYE') or 0) for item in cari_bakiyeler_list])
        toplam_kayit = len(cari_bakiyeler_list)
        
        # Pozitif ve negatif bakiye sayıları
        pozitif_bakiye_sayisi = len([item for item in cari_bakiyeler_list if float(item.get('BAKİYE') or item.get('BAKIYE') or 0) > 0])
        negatif_bakiye_sayisi = len([item for item in cari_bakiyeler_list if float(item.get('BAKİYE') or item.get('BAKIYE') or 0) < 0])
        sifir_bakiye_sayisi = len([item for item in cari_bakiyeler_list if float(item.get('BAKİYE') or item.get('BAKIYE') or 0) == 0])
        
        context = {
            'user': request.user,
            'user_data': user_data,
            'cari_bakiyeler_list': cari_bakiyeler_list,
            'toplam_bakiye': toplam_bakiye,
            'toplam_borc': toplam_borc,
            'toplam_alacak': toplam_alacak,
            'toplam_kayit': toplam_kayit,
            'pozitif_bakiye_sayisi': pozitif_bakiye_sayisi,
            'negatif_bakiye_sayisi': negatif_bakiye_sayisi,
            'sifir_bakiye_sayisi': sifir_bakiye_sayisi,
        }
        
        return render(request, 'tahsilat/cari_bakiyeler.html', context)
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        logger.error(f"Cari bakiyeler sayfası hatası: {e}\n{error_detail}")
        print(f"CARI_BAKIYELER ERROR: {e}")
        print(f"TRACEBACK: {error_detail}")
        
        # Hata durumunda bile boş liste ile sayfayı göster
        context = {
            'user': request.user,
            'user_data': request.session.get('mssql_user_data', {}),
            'cari_bakiyeler_list': [],
            'toplam_bakiye': 0,
            'toplam_borc': 0,
            'toplam_alacak': 0,
            'toplam_kayit': 0,
            'pozitif_bakiye_sayisi': 0,
            'negatif_bakiye_sayisi': 0,
            'sifir_bakiye_sayisi': 0,
            'error_message': f'Veriler getirilirken bir hata oluştu: {str(e)}'
        }
        return render(request, 'tahsilat/cari_bakiyeler.html', context)


@login_required
def cari_hareketler(request, cari_kod):
    """Cari Hesap Hareketleri sayfası - CARIHAREKETLER tablosundan veri listeler"""
    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    if request.user.username.upper() != 'FIRAT':
        # Yetki kontrolü
        from .models import KullaniciYetki
        try:
            yetki = KullaniciYetki.objects.get(
                kullanici=request.user, menu_adi='cari_bakiyeler')
            if not yetki.erisim_izni:
                messages.error(request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
                return redirect('tahsilat:cari_bakiyeler')
        except KullaniciYetki.DoesNotExist:
            messages.error(request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
            return redirect('tahsilat:cari_bakiyeler')

    user_data = request.session.get('mssql_user_data', {})
    
    # PDF export kontrolü
    export_pdf = request.GET.get('export', '') == 'pdf'
    
    try:
        # SQL sorgusu - seçili cari koduna göre filtrele (en eski tarihten başlayarak)
        query = """
        SELECT 
              [TARİH]
              ,[FATURANO]
              ,[FATURA TÜRÜ]
              ,[CARİ KOD]
              ,[CARİ ÜNVAN]
              ,[AÇIKLAMA]
              ,[BORÇ]
              ,[ALACAK]
        FROM [GO3].[dbo].[CARIHAREKETLER]
        WHERE [CARİ KOD] = ?
        ORDER BY [TARİH] ASC
        """
        
        hareketler_list = mssql_service.execute_query(query, (cari_kod,)) or []
        
        # Cari ünvanı al (ilk kayıttan)
        cari_unvan = hareketler_list[0].get('CARİ ÜNVAN') or hareketler_list[0].get('CARİ_ÜNVAN') if hareketler_list else 'Bilinmeyen Cari'
        
        # Yürüyen bakiye hesaplama (en eski tarihten başlayarak)
        yuruyen_bakiye = 0.0
        for item in hareketler_list:
            borc = float(item.get('BORÇ') or item.get('BORC') or 0)
            alacak = float(item.get('ALACAK') or 0)
            yuruyen_bakiye = yuruyen_bakiye + borc - alacak
            item['YURUYEN_BAKIYE'] = yuruyen_bakiye
        
        # Toplam hesaplamalar
        toplam_borc = sum([float(item.get('BORÇ') or item.get('BORC') or 0) for item in hareketler_list])
        toplam_alacak = sum([float(item.get('ALACAK') or 0) for item in hareketler_list])
        toplam_bakiye = toplam_borc - toplam_alacak
        toplam_kayit = len(hareketler_list)
        
        # PDF export işlemi
        if export_pdf and hareketler_list:
            return generate_cari_hareketler_pdf(hareketler_list, cari_kod, cari_unvan, toplam_borc, toplam_alacak, toplam_bakiye, toplam_kayit)
        
        # Listeyi ters çevir (en yeni tarih önce görünsün)
        hareketler_list = list(reversed(hareketler_list))
        
        context = {
            'user': request.user,
            'user_data': user_data,
            'hareketler_list': hareketler_list,
            'cari_kod': cari_kod,
            'cari_unvan': cari_unvan,
            'toplam_bakiye': toplam_bakiye,
            'toplam_borc': toplam_borc,
            'toplam_alacak': toplam_alacak,
            'toplam_kayit': toplam_kayit,
            'sayfa_baslik': 'Cari Hesap Hareketleri',
            'sayfa_ikon': 'bi-file-text',
            'error_message': None,
        }
        
        return render(request, 'tahsilat/cari_hareketler.html', context)
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        logger.error(f"Cari hareketler sayfası hatası: {e}\n{error_detail}")
        print(f"CARI_HAREKETLER ERROR: {e}")
        print(f"TRACEBACK: {error_detail}")
        
        # Hata durumunda bile boş liste ile sayfayı göster
        context = {
            'user': request.user,
            'user_data': request.session.get('mssql_user_data', {}),
            'hareketler_list': [],
            'cari_kod': cari_kod,
            'cari_unvan': 'Bilinmeyen Cari',
            'toplam_bakiye': 0,
            'toplam_borc': 0,
            'toplam_alacak': 0,
            'toplam_kayit': 0,
            'error_message': f'Veriler getirilirken bir hata oluştu: {str(e)}',
            'sayfa_baslik': 'Cari Hesap Hareketleri',
            'sayfa_ikon': 'bi-file-text',
        }
        return render(request, 'tahsilat/cari_hareketler.html', context)


@login_required
def cari_gecikmeleri(request):
    """Cari Geçiklemeleri sayfası - Tüm carilerin geçikme tutarları"""

    # Yetki kontrolü
    from .models import KullaniciYetki
    try:
        yetki = KullaniciYetki.objects.get(
            kullanici=request.user, menu_adi='cari_gecikmeleri')
        if not yetki.erisim_izni:
            return redirect('tahsilat:dashboard')
    except KullaniciYetki.DoesNotExist:
        if request.user.username != 'FIRAT':
            return redirect('tahsilat:dashboard')

    # Session'da mssql_user_data ayarla
    if 'mssql_user_data' not in request.session:
        request.session['mssql_user_data'] = {'departman': 'admin'}

    # AJAX isteği kontrolü
    if request.method == 'POST' and request.POST.get('action') == 'hesapla':
        try:
            # Eski verileri temizle
            CariGeckme.clear_old_data()

            # Geçikme verilerini hesapla
            gecikme_data = mssql_service.get_tum_cari_gecikmeleri()

            if gecikme_data['success']:
                # Yeni verileri veritabanına kaydet
                for item in gecikme_data['data']:
                    CariGeckme.objects.create(
                        cari_kod=item['cari_kod'],
                        cari_unvan=item['cari_unvan'],
                        plasiyer=item.get('plasiyer') or None,
                        bolge=item.get('bolge') or None,
                        gecikme_tutari=item['gecikme_tutari'],
                        gecikme_fatura_sayisi=item['gecikme_fatura_sayisi'],
                        en_eski_vade=datetime.strptime(
                            item['en_eski_vade'], '%d.%m.%Y').date(),
                        en_yeni_vade=datetime.strptime(
                            item['en_yeni_vade'], '%d.%m.%Y').date(),
                        gecikme_gun_sayisi=item['gecikme_gun_sayisi']
                    )

                return JsonResponse({
                    'success': True,
                    'data': gecikme_data['data'],
                    'toplam_gecikme': gecikme_data['toplam_gecikme'],
                    'toplam_cari_sayisi': gecikme_data['toplam_cari_sayisi'],
                    'toplam_fatura_sayisi': gecikme_data['toplam_fatura_sayisi'],
                    'hesaplama_tarihi': gecikme_data['hesaplama_tarihi']
                })
            else:
                return JsonResponse({
                    'success': False,
                    'error': gecikme_data.get('error', 'Bilinmeyen hata')
                })
        except Exception as e:
            logger.error(f"Cari geçikmeleri hesaplama hatası: {e}")
            return JsonResponse({
                'success': False,
                'error': str(e)
            })

    # Filtre parametrelerini al
    selected_plasiyer = [
        p for p in request.GET.getlist('plasiyer') if p and p.strip()]
    selected_bolgeler = [
        b for b in request.GET.getlist('bolge') if b and b.strip()]

    gecikme_gun_min = request.GET.get('gecikme_gun_min')
    gecikme_gun_max = request.GET.get('gecikme_gun_max')

    try:
        gecikme_gun_min_val = int(gecikme_gun_min) if gecikme_gun_min not in (None, '') else None
    except ValueError:
        gecikme_gun_min_val = None

    try:
        gecikme_gun_max_val = int(gecikme_gun_max) if gecikme_gun_max not in (None, '') else None
    except ValueError:
        gecikme_gun_max_val = None

    # İlk sayfa yüklemesi - veritabanından veri al
    tum_gecikme_kaydi = CariGeckme.get_latest_data()
    gecikme_data = _get_filtered_cari_gecikme_queryset(
        selected_plasiyer, selected_bolgeler, gecikme_gun_min_val, gecikme_gun_max_val)

    stats = gecikme_data.aggregate(
        toplam_gecikme=Sum('gecikme_tutari'),
        toplam_cari_sayisi=Count('id'),
        toplam_fatura_sayisi=Sum('gecikme_fatura_sayisi')
    )

    toplam_gecikme = float(stats.get('toplam_gecikme') or 0)
    toplam_cari_sayisi = int(stats.get('toplam_cari_sayisi') or 0)
    toplam_fatura_sayisi = int(stats.get('toplam_fatura_sayisi') or 0)

    # Verileri template formatına çevir
    gecikme_list = []
    for item in gecikme_data:
        gecikme_list.append({
            'cari_kod': item.cari_kod,
            'cari_unvan': item.cari_unvan,
            'plasiyer': item.plasiyer or '',
            'bolge': item.bolge or '',
            'gecikme_tutari': float(item.gecikme_tutari),
            'gecikme_fatura_sayisi': item.gecikme_fatura_sayisi,
            'en_eski_vade': item.en_eski_vade.strftime('%d.%m.%Y'),
            'en_yeni_vade': item.en_yeni_vade.strftime('%d.%m.%Y'),
            'gecikme_gun_sayisi': item.gecikme_gun_sayisi
        })

    # Son hesaplama tarihini al
    hesaplama_tarihi = ''
    if tum_gecikme_kaydi.exists():
        hesaplama_tarihi = tum_gecikme_kaydi.first().hesaplama_tarihi.strftime('%d.%m.%Y %H:%M')

    plasiyer_options = sorted(set([
        p for p in CariGeckme.objects.exclude(
            plasiyer__isnull=True).exclude(plasiyer__exact='').values_list('plasiyer', flat=True)
    ]))
    bolge_options = sorted(set([
        b for b in CariGeckme.objects.exclude(
            bolge__isnull=True).exclude(bolge__exact='').values_list('bolge', flat=True)
    ]))

    context = {
        'mssql_user_data': {'departman': 'admin'},
        'gecikme_data': gecikme_list,
        'toplam_gecikme': toplam_gecikme,
        'toplam_cari_sayisi': toplam_cari_sayisi,
        'toplam_fatura_sayisi': toplam_fatura_sayisi,
        'hesaplama_tarihi': hesaplama_tarihi,
        'plasiyer_options': plasiyer_options,
        'bolge_options': bolge_options,
        'selected_plasiyer': selected_plasiyer,
        'selected_bolgeler': selected_bolgeler,
        'selected_gecikme_gun_min': gecikme_gun_min_val if gecikme_gun_min_val is not None else '',
        'selected_gecikme_gun_max': gecikme_gun_max_val if gecikme_gun_max_val is not None else ''
    }

    return render(request, 'tahsilat/cari_gecikmeleri.html', context)

    return render(request, 'tahsilat/stok_satis_analiz.html', context)


@login_required
def cari_gecikmeleri_excel(request):
    """Cari Geçikmeleri Excel export"""
    selected_plasiyer = [
        p for p in request.GET.getlist('plasiyer') if p and p.strip()]
    selected_bolgeler = [
        b for b in request.GET.getlist('bolge') if b and b.strip()]

    gecikme_gun_min = request.GET.get('gecikme_gun_min')
    gecikme_gun_max = request.GET.get('gecikme_gun_max')

    try:
        gecikme_gun_min_val = int(gecikme_gun_min) if gecikme_gun_min not in (None, '') else None
    except ValueError:
        gecikme_gun_min_val = None

    try:
        gecikme_gun_max_val = int(gecikme_gun_max) if gecikme_gun_max not in (None, '') else None
    except ValueError:
        gecikme_gun_max_val = None

    queryset = _get_filtered_cari_gecikme_queryset(
        selected_plasiyer, selected_bolgeler, gecikme_gun_min_val, gecikme_gun_max_val)

    rows = []
    for item in queryset:
        rows.append({
            'Cari Kod': item.cari_kod,
            'Cari Ünvan': item.cari_unvan,
            'Plasiyer': item.plasiyer or '',
            'Bölge': item.bolge or '',
            'Geçikme Tutarı': float(item.gecikme_tutari),
            'Fatura Sayısı': item.gecikme_fatura_sayisi,
            'En Eski Vade': item.en_eski_vade.strftime('%d.%m.%Y'),
            'En Yeni Vade': item.en_yeni_vade.strftime('%d.%m.%Y'),
            'Geçikme Günü': item.gecikme_gun_sayisi,
        })

    columns = [
        'Cari Kod',
        'Cari Ünvan',
        'Plasiyer',
        'Bölge',
        'Geçikme Tutarı',
        'Fatura Sayısı',
        'En Eski Vade',
        'En Yeni Vade',
        'Geçikme Günü',
    ]

    import pandas as pd

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
        df.to_excel(writer, index=False, sheet_name='Cari Geçikmeleri')

    output.seek(0)
    filename = f"cari_gecikmeleri_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def satislarim(request):
    """Satışlarım - FATURA listesini sayfalama ve filtrelerle gösterir"""
    user_data = request.session.get('mssql_user_data', {})
    plasiyer = user_data.get('plasiyer', request.user.username)

    try:
        page = int(request.GET.get('page', '1') or 1)
    except Exception:
        page = 1
    page_size = int(request.GET.get('page_size', '100') or 100)

    baslangic_tarihi = request.GET.get('baslangic_tarihi') or None
    bitis_tarihi = request.GET.get('bitis_tarihi') or None
    cari_kod = request.GET.get('cari_kod') or None
    cari_unvan = request.GET.get('cari_unvan') or None

    satislar, pagination = mssql_service.get_satis_listesi_paginated(
        page=page,
        page_size=page_size,
        plasiyer=plasiyer,
        baslangic_tarihi=baslangic_tarihi,
        bitis_tarihi=bitis_tarihi,
        cari_kod=cari_kod,
        cari_unvan=cari_unvan,
    )

    # Debug logging for troubleshooting empty results
    logger.info(f"satislarim view params: plasiyer={plasiyer!r}, page={page}, page_size={page_size}, baslangic={baslangic_tarihi}, bitis={bitis_tarihi}, cari_kod={cari_kod}, cari_unvan={cari_unvan}")

    # --- Dashboard summary: overall total and totals by region ---
    try:
        full_satis_list = mssql_service.get_satis_listesi_all(
            baslangic_tarihi=baslangic_tarihi or None,
            bitis_tarihi=bitis_tarihi or None,
            cari_kod=cari_kod or None,
            cari_unvan=cari_unvan or None,
            plasiyer=plasiyer or None,
        )

        total_amount = 0.0
        region_map = {}
        for s in (full_satis_list or []):
            amt = float(s.get('NET_TOPLAM') or 0)
            total_amount += amt
            region = s.get('BÖLGE') or s.get('Bölge') or 'Bilinmiyor'
            region_map.setdefault(region, {'amount': 0.0, 'count': 0})
            region_map[region]['amount'] += amt
            region_map[region]['count'] += 1

        # Convert to sorted list for template (largest amounts first)
        satis_by_region_list = sorted(
            [{'region': r, 'amount': v['amount'], 'count': v['count']} for r, v in region_map.items()],
            key=lambda x: x['amount'], reverse=True
        )

        satis_summary = {
            'total_amount': total_amount,
            'total_count': len(full_satis_list or []),
        }
        # Debug: log computed summary for troubleshooting
        logger.info(f"satislarim summary computed: total_amount={total_amount}, total_count={len(full_satis_list or [])}")
        logger.info(f"satislarim by-region sample: {satis_by_region_list[:5]}")
    except Exception as e:
        logger.error(f"satislarim summary calc error: {e}")
        satis_summary = {'total_amount': 0.0, 'total_count': 0}
        satis_by_region_list = []

    context = {
        'user': request.user,
        'plasiyer': plasiyer,
        'satislar': satislar or [],
        'pagination': pagination or {},
        # Backwards-compatible keys expected by template
        'faturalar': satislar or [],
        'pagination_info': pagination or {},
        'satis_summary': satis_summary,
        'satis_by_region_list': satis_by_region_list,
        'filters': {
            'baslangic_tarihi': baslangic_tarihi or '',
            'bitis_tarihi': bitis_tarihi or '',
            'cari_kod': cari_kod or '',
            'cari_unvan': cari_unvan or '',
            'page_size': page_size,
        }
    }
    return render(request, 'tahsilat/satislarim.html', context)


@login_required
def klasik_tahsilat_raporu(request):
    """Klasik Tahsilat Raporu sayfası

    - baslangic_tarihi ve bitis_tarihi GET parametrelerini okur (YYYY-MM-DD)
    - Parametreler boşsa ikisini de bugüne ayarlar
    - MSSQL'den rapor verilerini çekip şablonun beklediği yapılara dönüştürür
    """
    from datetime import date

    baslangic_tarihi = (request.GET.get('baslangic_tarihi') or '').strip()
    bitis_tarihi = (request.GET.get('bitis_tarihi') or '').strip()

    # Varsayılan: bugün
    today_str = date.today().strftime('%Y-%m-%d')
    if not baslangic_tarihi and not bitis_tarihi:
        baslangic_tarihi = today_str
        bitis_tarihi = today_str
    elif baslangic_tarihi and not bitis_tarihi:
        bitis_tarihi = baslangic_tarihi
    elif bitis_tarihi and not baslangic_tarihi:
        baslangic_tarihi = bitis_tarihi

    # Rapor verilerini çek
    rapor_resp = mssql_service.get_tahsilat_raporu(
        baslangic_tarihi=baslangic_tarihi,
        bitis_tarihi=bitis_tarihi,
    )

    # Şablonun beklediği yapılar
    plasiyer_verileri = {}
    plasiyer_listesi = []
    toplam_verileri = {
        'Nakit Tahsilat': 0.0,
        'Gelen Havale': 0.0,
        'Kredi Kartı Fişi': 0.0,
        'Çek Girişi': 0.0,
        'Senet Girişi': 0.0,
        'TOPLAM': 0.0,
    }

    banka_verileri = {}
    banka_listesi = []
    banka_toplam_verileri = {
        'Kredi Kartı Fişi': 0.0,
        'Gelen Havale': 0.0,
        'TOPLAM': 0.0,
    }

    if rapor_resp.get('success'):
        rows = rapor_resp.get('data', [])
        for row in rows:
            plasiyer = row.get('PLASİYER') or ''
            tur = row.get('TAHSİLAT TÜRÜ') or ''
            banka = row.get('BANKA') or ''
            tutar = float(row.get('TOPLAM_TUTAR') or 0)

            # Plasiyer bazlı
            if plasiyer not in plasiyer_verileri:
                plasiyer_verileri[plasiyer] = {
                    'Nakit Tahsilat': 0.0,
                    'Gelen Havale': 0.0,
                    'Kredi Kartı Fişi': 0.0,
                    'Çek Girişi': 0.0,
                    'Senet Girişi': 0.0,
                    'TOPLAM': 0.0,
                }
                plasiyer_listesi.append(plasiyer)

            # Tür eşlemesi doğrudan isimlerle yapılır
            if tur in plasiyer_verileri[plasiyer]:
                plasiyer_verileri[plasiyer][tur] += tutar
                toplam_verileri[tur] += tutar
            plasiyer_verileri[plasiyer]['TOPLAM'] += tutar
            toplam_verileri['TOPLAM'] += tutar

            # Banka bazlı (sadece kredi kartı ve havale ilgileniyoruz)
            if banka:
                if banka not in banka_verileri:
                    banka_verileri[banka] = {
                        'Kredi Kartı Fişi': 0.0,
                        'Gelen Havale': 0.0,
                        'TOPLAM': 0.0,
                    }
                    banka_listesi.append(banka)

                if tur in ('Kredi Kartı Fişi', 'Gelen Havale'):
                    banka_verileri[banka][tur] += tutar
                    banka_verileri[banka]['TOPLAM'] += tutar
                    banka_toplam_verileri[tur] += tutar
                    banka_toplam_verileri['TOPLAM'] += tutar

        # Sıralamalar
        plasiyer_listesi = sorted(plasiyer_listesi)
        banka_listesi = sorted(banka_listesi)

    # Yardımcı paneller (tarih aralığı ile)
    try:
        nakit_teslim_edilmedi_toplam = mssql_service.get_nakit_teslim_edilmedi_total_with_date(
            baslangic_tarihi=baslangic_tarihi,
            bitis_tarihi=bitis_tarihi,
        )
        nakit_teslim_edilmedi_by_user = mssql_service.get_nakit_teslim_edilmedi_by_user_with_date(
            baslangic_tarihi=baslangic_tarihi,
            bitis_tarihi=bitis_tarihi,
        )
    except Exception:
        nakit_teslim_edilmedi_toplam = 0.0
        nakit_teslim_edilmedi_by_user = []

    # Gider/Masraf için şimdilik boş veri (entegrasyon eklenince doldurulacak)
    gider_masraf_toplam = 0.0
    gider_masraf_by_user = []

    export_format = request.GET.get('export', '').lower()

    if export_format == 'pdf':
        return generate_klasik_tahsilat_pdf(
            baslangic_tarihi,
            bitis_tarihi,
            plasiyer_listesi,
            plasiyer_verileri,
            toplam_verileri,
            banka_listesi,
            banka_verileri,
            banka_toplam_verileri,
            nakit_teslim_edilmedi_toplam,
            nakit_teslim_edilmedi_by_user
        )
    elif export_format == 'excel':
        return generate_klasik_tahsilat_excel(
            baslangic_tarihi,
            bitis_tarihi,
            plasiyer_listesi,
            plasiyer_verileri,
            toplam_verileri,
            banka_listesi,
            banka_verileri,
            banka_toplam_verileri,
            nakit_teslim_edilmedi_toplam,
            nakit_teslim_edilmedi_by_user
        )

    context = {
        'baslangic_tarihi': baslangic_tarihi,
        'bitis_tarihi': bitis_tarihi,
        'plasiyer_listesi': plasiyer_listesi,
        'plasiyer_verileri': plasiyer_verileri,
        'toplam_verileri': toplam_verileri,
        'banka_listesi': banka_listesi,
        'banka_verileri': banka_verileri,
        'banka_toplam_verileri': banka_toplam_verileri,
        'nakit_teslim_edilmedi_toplam': nakit_teslim_edilmedi_toplam,
        'nakit_teslim_edilmedi_by_user': nakit_teslim_edilmedi_by_user,
        'gider_masraf_toplam': gider_masraf_toplam,
        'gider_masraf_by_user': gider_masraf_by_user,
    }

    return render(request, 'tahsilat/klasik_tahsilat_raporu.html', context)


@login_required
def muhasebe_gunluk_rapor(request):
    """Muhasebe > Günlük Rapor: Plasiyer bazında günlük satış, tahsilat ve gider özetleri"""
    from datetime import date

    # Date range filter (defaults to today)
    start_date = (request.GET.get('start_date') or request.GET.get('tarih') or '').strip()
    end_date = (request.GET.get('end_date') or '').strip()
    if not start_date:
        start_date = date.today().strftime('%Y-%m-%d')
    if not end_date:
        end_date = start_date
    # label used in PDF/title
    rapor_tarihi = start_date if start_date == end_date else f"{start_date} - {end_date}"

    # FIRAT kullanıcısı süper kullanıcı - tüm sayfalara erişim yetkisi var
    # Yetki kontrolü (alt menü)
    if request.user.username.upper() != 'FIRAT':
        try:
            yetki = KullaniciYetki.objects.get(kullanici=request.user, menu_adi='muhasebe_gunluk_rapor')
            if not yetki.erisim_izni:
                return redirect('tahsilat:dashboard')
        except KullaniciYetki.DoesNotExist:
            return redirect('tahsilat:dashboard')

    # Verileri çek (MSSQLService üzerinde mevcut yardımcıları kullanarak)
    # Satışlar: GunlukTahsilat_V yerine satış görünümü yoksa 0 döndürürüz; ileride detaylandırılır
    from .mssql_service import MSSQLService
    mssql_service = None
    try:
        mssql_service = MSSQLService()
    except Exception:
        mssql_service = None

    if mssql_service:
        try:
            nakit_teslim_edilmedi_toplam = mssql_service.get_nakit_teslim_edilmedi_total_with_date(
                baslangic_tarihi=start_date,
                bitis_tarihi=end_date,
            )
            nakit_teslim_edilmedi_by_user = mssql_service.get_nakit_teslim_edilmedi_by_user_with_date(
                baslangic_tarihi=start_date,
                bitis_tarihi=end_date,
            )
        except Exception:
            nakit_teslim_edilmedi_toplam = 0.0
            nakit_teslim_edilmedi_by_user = []
    else:
        nakit_teslim_edilmedi_toplam = 0.0
        nakit_teslim_edilmedi_by_user = []

    # Detay listeleri
    if mssql_service:
        try:
            tahsilatlar_detay = mssql_service.get_tahsilatlar_by_date(
                baslangic_tarihi=start_date,
                bitis_tarihi=end_date,
            )
        except Exception:
            tahsilatlar_detay = []
    else:
        tahsilatlar_detay = []

    if mssql_service:
        try:
            satislar_detay = mssql_service.get_satislar_by_date(
                baslangic_tarihi=start_date,
                bitis_tarihi=end_date,
            )
        except Exception:
            satislar_detay = []
    else:
        satislar_detay = []

    # Satış ve tahsilat detaylarını şablonda güvenle kullanılacak şekilde normalize et
    tahsilatlar_list = []
    for t in tahsilatlar_detay:
        if t is None:
            continue
        try:
            tahsilatlar_list.append({
                'tarih': t.get('Tarih') or t.get('TARİH') or t.get('tarih'),
                'plasiyer': t.get('Plasiyer') or t.get('PLASİYER') or t.get('plasiyer') or t.get('Kullanici') or t.get('KULLANICI'),
                'kullanici': t.get('Kullanici') or t.get('KULLANICI') or t.get('kullanici') or t.get('KullaniciAdi') or t.get('KULLANICI_ADI') or '',
                'cari_kod': t.get('CariKod') or t.get('CARİ KOD') or t.get('CARI_KOD') or t.get('cari_kod'),
                'cari_unvan': t.get('CariUnvan') or t.get('CARİ ÜNVAN') or t.get('CARI_UNVAN') or t.get('cari_unvan'),
                'tahsilat_turu': t.get('TahsilatTuru') or t.get('TAHSİLAT TÜRÜ') or t.get('TAHSILAT_TURU') or t.get('tahsilat_turu'),
                'banka': t.get('BANKAADI') or t.get('Banka') or t.get('BANKA') or t.get('banka'),
                'taksit': t.get('Taksit') or t.get('TAKSİT') or t.get('TAKSIT') or t.get('taksit'),
                'tutar': float(t.get('Tutar') or t.get('TUTAR') or t.get('NET_TOPLAM') or t.get('tutar') or 0),
                'teslim_durumu': t.get('TeslimDurumu') or t.get('TESLİM DURUMU') or t.get('TESLIM_DURUMU') or t.get('teslim_durumu'),
                'aciklama': t.get('Aciklama') or t.get('AÇIKLAMA') or t.get('aciklama') or '',
            })
        except Exception:
            continue

    satislar_list = []
    for s in satislar_detay:
        if s is None:
            continue
        try:
            satislar_list.append({
                'tarih': s.get('TARİH') or s.get('Tarih'),
                'plasiyer': s.get('SATAN_KİŞİ') or s.get('PLASİYER') or s.get('Plasiyer'),
                'cari_kod': s.get('CARİ KOD') or s.get('CARI_KOD') or s.get('CARİ_KOD'),
                'cari_unvan': s.get('CARİ ÜNVAN') or s.get('CARI_UNVAN') or s.get('CARİ_ÜNVAN'),
                'fatura_no': s.get('FATURA NO') or s.get('FATURA_NO'),
                'fatura_turu': s.get('FATURA TÜRÜ') or s.get('FATURA_TÜRÜ'),
                'net_toplam': float(s.get('NET_TOPLAM') or s.get('TUTAR') or 0),
                'bolge': s.get('BÖLGE') or s.get('BOLGE'),
            })
        except Exception:
            continue

    # Toplam sözlükleri
    plasiyer_satis_toplam = {}
    plasiyer_tahsilat_toplam = {}
    plasiyer_gider_toplam = {}

    # Tahsilat toplamları (plasiyer bazında)
    for t in tahsilatlar_list:
        p = t.get('plasiyer') or 'Bilinmeyen'
        tutar = float(t.get('tutar') or 0)
        plasiyer_tahsilat_toplam[p] = plasiyer_tahsilat_toplam.get(p, 0.0) + tutar

    # Satış toplamları (plasiyer/satan kişi bazında)
    for s in satislar_detay:
        p = s.get('SATAN_KİŞİ') or s.get('PLASİYER') or 'Bilinmeyen'
        tutar = float(s.get('NET_TOPLAM') or s.get('TUTAR') or 0)
        plasiyer_satis_toplam[p] = plasiyer_satis_toplam.get(p, 0.0) + tutar

    # Tüm plasiyerleri toparla ve tablo satırlarını üret
    plasiyer_set = set(list(plasiyer_satis_toplam.keys()) + list(plasiyer_tahsilat_toplam.keys()) + list(plasiyer_gider_toplam.keys()))
    plasiyer_listesi = sorted(plasiyer_set)

    satirlar = []
    toplam_satis = 0.0
    toplam_tahsilat = 0.0
    toplam_gider = 0.0
    
    for p in plasiyer_listesi:
        s_val = float(plasiyer_satis_toplam.get(p, 0.0) or 0.0)
        t_val = float(plasiyer_tahsilat_toplam.get(p, 0.0) or 0.0)
        g_val = float(plasiyer_gider_toplam.get(p, 0.0) or 0.0)
        satirlar.append({
            'plasiyer': p,
            'satis': s_val,
            'tahsilat': t_val,
            'gider': g_val,
        })
        toplam_satis += s_val
        toplam_tahsilat += t_val
        toplam_gider += g_val

    export_format = request.GET.get('export', '').lower()

    if export_format == 'pdf':
        return generate_muhasebe_gunluk_pdf(
            rapor_tarihi,
            satirlar,
            tahsilatlar_list,
            satislar_list,
            nakit_teslim_edilmedi_toplam
        )

    context = {
        'tarih': rapor_tarihi,
        'start_date': start_date,
        'end_date': end_date,
        'satirlar': satirlar,
        'toplam_satis': toplam_satis,
        'toplam_tahsilat': toplam_tahsilat,
        'toplam_gider': toplam_gider,
        'nakit_teslim_edilmedi_toplam': nakit_teslim_edilmedi_toplam,
        'tahsilatlar_detay': tahsilatlar_list,
        'nakit_teslim_edilmedi_by_user': nakit_teslim_edilmedi_by_user,
        'satislar_detay': satislar_list,
    }

    return render(request, 'tahsilat/muhasebe_gunluk_rapor.html', context)


@login_required
def gider_masraf_listesi(request):
    """Gider Masraf Listesi - Ana sayfa"""
    # Placeholder data - gerçek veritabanı bağlantısı eklenebilir
    gider_listesi = [
        {
            'id': 1,
            'tarih': '2025-10-23',
            'aciklama': 'Ofis kirası',
            'tutar': 5000.00,
            'durum': 'Onaylandı'
        },
        {
            'id': 2,
            'tarih': '2025-10-22',
            'aciklama': 'Elektrik faturası',
            'tutar': 850.50,
            'durum': 'Beklemede'
        },
        {
            'id': 3,
            'tarih': '2025-10-21',
            'aciklama': 'İnternet aboneliği',
            'tutar': 120.00,
            'durum': 'Onaylandı'
        }
    ]

    # İstatistikler
    toplam_gider = sum(item['tutar'] for item in gider_listesi)
    onaylanan_gider = sum(
        item['tutar'] for item in gider_listesi if item['durum'] == 'Onaylandı')
    beklemede_gider = sum(
        item['tutar'] for item in gider_listesi if item['durum'] == 'Beklemede')

    context = {
        'gider_listesi': gider_listesi,
        'toplam_gider': toplam_gider,
        'onaylanan_gider': onaylanan_gider,
        'beklemede_gider': beklemede_gider,
        'gider_sayisi': len(gider_listesi)
    }
    return render(request, 'tahsilat/gider_masraf_listesi.html', context)


@login_required
def gider_masraf_ekle(request):
    """Yeni Gider Masraf Ekle"""
    if request.method == 'POST':
        # Form verilerini al
        tarih = request.POST.get('tarih')
        aciklama = request.POST.get('aciklama')
        tutar = request.POST.get('tutar')
        kategori = request.POST.get('kategori')

        # Basit validasyon
        if tarih and aciklama and tutar:
            # Burada gerçek veritabanına kayıt işlemi yapılacak
            # Şimdilik başarı mesajı göster
            messages.success(request, 'Gider masraf başarıyla eklendi!')
            return redirect('tahsilat:gider_masraf_listesi')
        else:
            messages.error(request, 'Lütfen tüm alanları doldurun!')

    context = {
        'kategoriler': [
            {'id': 'kira', 'ad': 'Kira'},
            {'id': 'elektrik', 'ad': 'Elektrik'},
            {'id': 'su', 'ad': 'Su'},
            {'id': 'internet', 'ad': 'İnternet'},
            {'id': 'telefon', 'ad': 'Telefon'},
            {'id': 'yakit', 'ad': 'Yakıt'},
            {'id': 'bakim', 'ad': 'Bakım'},
            {'id': 'diger', 'ad': 'Diğer'}
        ]
    }
    return render(request, 'tahsilat/gider_masraf_ekle.html', context)
@login_required
def gider_masraf_guncelle(request, gider_id):
    """Gider Masraf Güncelle"""
    if request.method == 'POST':
        # Form verilerini al
        tarih = request.POST.get('tarih')
        aciklama = request.POST.get('aciklama')
        tutar = request.POST.get('tutar')
        kategori = request.POST.get('kategori')

        # Basit validasyon
        if tarih and aciklama and tutar:
            # Burada gerçek veritabanında güncelleme işlemi yapılacak
            messages.success(
                request, f'Gider masraf #{gider_id} başarıyla güncellendi!')
            return redirect('tahsilat:gider_masraf_listesi')
        else:
            messages.error(request, 'Lütfen tüm alanları doldurun!')

    # Mevcut gider bilgilerini getir (şimdilik placeholder)
    gider_data = {
        'id': gider_id,
        'tarih': '2025-10-23',
        'aciklama': 'Örnek gider masrafı',
        'tutar': 1000.00,
        'kategori': 'kira'
    }

    context = {
        'gider': gider_data,
        'kategoriler': [
            {'id': 'kira', 'ad': 'Kira'},
            {'id': 'elektrik', 'ad': 'Elektrik'},
            {'id': 'su', 'ad': 'Su'},
            {'id': 'internet', 'ad': 'İnternet'},
            {'id': 'telefon', 'ad': 'Telefon'},
            {'id': 'yakit', 'ad': 'Yakıt'},
            {'id': 'bakim', 'ad': 'Bakım'},
            {'id': 'diger', 'ad': 'Diğer'}
        ]
    }
    return render(request, 'tahsilat/gider_masraf_guncelle.html', context)


@login_required
def gider_masraf_sil(request, gider_id):
    """Gider Masraf Sil"""
    if request.method == 'POST':
        # Burada gerçek veritabanından silme işlemi yapılacak
        messages.success(
            request, f'Gider masraf #{gider_id} başarıyla silindi!')
        return redirect('tahsilat:gider_masraf_listesi')

    # Mevcut gider bilgilerini getir (şimdilik placeholder)
    gider_data = {
        'id': gider_id,
        'tarih': '2025-10-23',
        'aciklama': 'Örnek gider masrafı',
        'tutar': 1000.00
    }

    context = {
        'gider': gider_data
    }
    return render(request, 'tahsilat/gider_masraf_listesi.html', context)


@login_required
def perakende(request):
    """Perakende sayfasını mevcut tasarımla aktif eder ve veri doldurur."""
    from datetime import datetime, timedelta
    import os

    # Tarih filtreleri (varsayılan: son 90 gün)
    baslangic_tarihi = request.GET.get('baslangic_tarihi', '')
    bitis_tarihi = request.GET.get('bitis_tarihi', '')
    if not bitis_tarihi:
        bitis_dt = datetime.today()
        bitis_tarihi = bitis_dt.strftime('%Y-%m-%d')
    else:
        bitis_dt = datetime.strptime(bitis_tarihi, '%Y-%m-%d')
    if not baslangic_tarihi:
        baslangic_dt = bitis_dt - timedelta(days=90)
        baslangic_tarihi = baslangic_dt.strftime('%Y-%m-%d')

    # Perakende cari listesi: settings veya env'den, yoksa MSSQL'den son hareketlere göre 3 cari
    from django.conf import settings
    perakende_cari_kodlari = getattr(settings, 'PERAKENDE_CARILER', []) or os.environ.get('PERAKENDE_CARILER', '')
    if isinstance(perakende_cari_kodlari, str):
        perakende_cari_kodlari = [c.strip() for c in perakende_cari_kodlari.split(',') if c.strip()]

    cari_verileri = {}

    try:
        # Eğer tanımlı cari yoksa, satış hareketlerinden son 3 cariyi seç
        if not perakende_cari_kodlari:
            res = mssql_service.get_malzeme_satis_detay(
                baslangic_tarihi=baslangic_tarihi,
                bitis_tarihi=bitis_tarihi,
                page=1,
                page_size=100
            )
            rows = res.get('data', [])
            seen = []
            for r in rows:
                code = r.get('CARİ_KOD') or r.get('CARİ KOD') or r.get('CARİ_KODU')
                if code and code not in seen:
                    seen.append(code)
                if len(seen) >= 3:
                    break
            perakende_cari_kodlari = seen

        # Her cari için satış listesini çek ve özetleri hazırla
        for cari_kod in perakende_cari_kodlari:
            res = mssql_service.get_malzeme_satis_detay(
                baslangic_tarihi=baslangic_tarihi,
                bitis_tarihi=bitis_tarihi,
                cari_kod=cari_kod,
                page=1,
                page_size=200
            )
            satislar = res.get('data', [])
            toplam_satis = 0.0
            cari_adi = ''
            for s in satislar:
                try:
                    toplam_satis += float(s.get('NET_TOPLAM') or 0)
                except Exception:
                    pass
                if not cari_adi:
                    cari_adi = s.get('CARİ_ÜNVAN') or s.get('CARİ ÜNVAN') or s.get('CARI_UNVAN') or ''

            # Tahsilatlar: şu an için boş; entegrasyon eklenebilir
            tahsilatlar = []
            toplam_tahsilat = 0.0

            cari_verileri[cari_kod] = {
                'cari_adi': cari_adi,
                'satislar': satislar,
                'tahsilatlar': tahsilatlar,
                'toplam_satis': toplam_satis,
                'toplam_tahsilat': toplam_tahsilat,
                'satis_sayisi': len(satislar),
                'tahsilat_sayisi': len(tahsilatlar),
            }
    except Exception as e:
        logger.error(f"perakende data load error: {e}")

    context = {
        'baslangic_tarihi': baslangic_tarihi,
        'bitis_tarihi': bitis_tarihi,
        'perakende_cari_kodlari': perakende_cari_kodlari,
        'cari_verileri': cari_verileri,
    }
    return render(request, 'tahsilat/perakende.html', context)


@login_required
def logo_transfer(request):
    """Placeholder view for logo_transfer - to be implemented"""
    context = {
        'message': 'Bu özellik henüz geliştirilmektedir.'
    }
    return render(request, 'tahsilat/placeholder.html', context)


@login_required
def logo_transfer_ajax(request):
    """Placeholder view for logo_transfer_ajax - to be implemented"""
    return JsonResponse({'success': False, 'message': 'Bu özellik henüz geliştirilmektedir.'})


@login_required
def yetkilendirme(request):
    """Kullanıcı yetkilendirme sayfası"""
    try:
        # POST işlemi - yetki güncelleme
        if request.method == 'POST':
            kullanici_id = request.POST.get('kullanici_id')
            menu_adi = request.POST.get('menu_adi')
            erisim_izni = request.POST.get('erisim_izni') == 'true'
            
            try:
                from django.contrib.auth.models import User
                from .models import KullaniciYetki
                
                kullanici = User.objects.get(id=kullanici_id)
                
                # Yetkiyi güncelle veya oluştur
                yetki, created = KullaniciYetki.objects.get_or_create(
                    kullanici=kullanici,
                    menu_adi=menu_adi,
                    defaults={'erisim_izni': erisim_izni, 'olusturan': request.user}
                )
                
                if not created:
                    yetki.erisim_izni = erisim_izni
                    yetki.save()
                
                return JsonResponse({'success': True, 'message': 'Yetki başarıyla güncellendi!'})
                
            except Exception as e:
                logger.error(f"Yetki güncelleme hatası: {e}")
                return JsonResponse({'success': False, 'message': f'Yetki güncellenirken hata oluştu: {e}'})
        
        # GET işlemi - sayfa gösterimi
        # Tek kaynak: modeldeki hiyerarşi (yeni menüler yetkilendirmede otomatik görünsün)
        from .models import KullaniciYetki
        menu_hierarchy = KullaniciYetki.get_menu_hierarchy()

        # Kullanıcıları MSSQL'den çek
        from .mssql_service import MSSQLService
        mssql_service = MSSQLService()
        
        # MSSQL'den tüm kullanıcıları çek
        mssql_kullanicilar = []
        mssql_username_map = {}
        django_user_map = {}  # MSSQL username -> Django User mapping
        
        try:
            conn = mssql_service.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT [ID], [KullaniciAdi], [Departman] FROM [GO3].[dbo].[KULLANICITB] ORDER BY [KullaniciAdi]')
            rows = cursor.fetchall()
            conn.close()
            
            # Django User modelinden kullanıcıları al (yetki kontrolü için)
            # Hem normalize edilmiş hem de normalize edilmemiş username'leri dictionary'ye ekle
            from django.contrib.auth.models import User
            django_users = {}
            for u in User.objects.all():
                # Normalize edilmiş username ile ekle (karşılaştırma için)
                normalized_username = mssql_service.normalize_turkish_chars(u.username.upper())
                django_users[normalized_username] = u
                # Normalize edilmemiş username ile de ekle (gerçek kullanıcı adı için)
                django_users[u.username] = u
            
            # MSSQL kullanıcılarını işle
            for row in rows:
                raw_username = row[1] if row[1] else ''
                if not raw_username:
                    continue
                    
                # Gerçek kullanıcı adını decode et (Türkçe karakterler ve büyük/küçük harf korunarak)
                # Veritabanındaki haliyle (büyük/küçük harf ve Türkçe karakterler dahil) kullan
                real_username = mssql_service.safe_decode_string_preserve_turkish(raw_username).strip()
                
                if real_username:
                    # Normalize edilmiş username -> gerçek kullanıcı adı mapping'i oluştur
                    normalized = mssql_service.normalize_turkish_chars(real_username)
                    mssql_username_map[normalized] = real_username
                    
                    # MSSQL kullanıcı bilgilerini ekle
                    mssql_kullanicilar.append({
                        'id': row[0],
                        'username': real_username,
                        'normalized_username': normalized,
                        'departman': mssql_service.safe_decode_string(row[2]) if row[2] else '',
                    })
                    
                    # Django User ile eşleştir
                    # Önce normalize edilmiş username ile, sonra gerçek username ile dene
                    django_user = django_users.get(normalized) or django_users.get(real_username)
                    if django_user:
                        django_user_map[normalized] = django_user
                        django_user_map[real_username] = django_user
        except Exception as e:
            logger.error(f"MSSQL kullanıcıları çekilirken hata: {e}")
        
        # Kullanıcıları MSSQL'den gelen sıraya göre sırala
        kullanicilar = []
        for mssql_user in mssql_kullanicilar:
            normalized = mssql_user['normalized_username']
            django_user = django_user_map.get(normalized)
            if django_user:
                # Django User varsa kullan
                kullanicilar.append(django_user)
            else:
                # Django User yoksa, sadece görüntüleme için bir mock user oluştur
                # Ancak yetki ataması yapılamaz
                from django.contrib.auth.models import User
                mock_user = type('MockUser', (), {
                    'id': None,
                    'username': mssql_user['username'],
                    'is_superuser': False,
                    'first_name': mssql_user['departman'],
                })()
                kullanicilar.append(mock_user)
        
        # Kullanıcı yetkilerini al
        kullanici_yetkileri = {}
        try:
            from .models import KullaniciYetki
            for kullanici in kullanicilar:
                # Mock user ise yetki yok
                if not hasattr(kullanici, 'id') or kullanici.id is None:
                    kullanici_yetkileri[kullanici.username] = {}
                    continue
                    
                yetkiler = {}
                permissions = KullaniciYetki.objects.filter(kullanici=kullanici)
                for perm in permissions:
                    yetkiler[perm.menu_adi] = perm.erisim_izni
                kullanici_yetkileri[kullanici.id] = yetkiler
        except ImportError:
            # KullaniciYetki modeli yoksa boş dict
            pass
        
        context = {
            'menu_hierarchy': menu_hierarchy,
            'kullanicilar': kullanicilar,
            'kullanici_yetkileri': kullanici_yetkileri,
            'mssql_username_map': mssql_username_map,  # Gerçek kullanıcı adları için mapping
        }
        
        return render(request, 'tahsilat/yetkilendirme.html', context)
        
    except Exception as e:
        logger.error(f"Yetkilendirme sayfası hatası: {e}")
        messages.error(request, f'Yetkilendirme sayfası yüklenirken hata oluştu: {e}')
        return render(request, 'tahsilat/yetkilendirme.html', {
            'menu_hierarchy': {},
            'kullanicilar': [],
            'kullanici_yetkileri': {},
        })


@login_required
def sync_users(request):
    """Kullanıcıları senkronize et (placeholder)"""
    try:
        if request.method == 'POST':
            # Basit senkronizasyon işlemi
            from django.contrib.auth.models import User
            
            # Mevcut kullanıcı sayısını al
            user_count = User.objects.count()
            
            return JsonResponse({
                'success': True, 
                'message': f'Kullanıcı senkronizasyonu tamamlandı. Toplam {user_count} kullanıcı mevcut.'
            })
        else:
            return JsonResponse({'success': False, 'message': 'Sadece POST istekleri kabul edilir.'})
            
    except Exception as e:
        logger.error(f"Kullanıcı senkronizasyonu hatası: {e}")
        return JsonResponse({'success': False, 'message': f'Senkronizasyon hatası: {e}'})


@login_required
def get_plasiyer_regions(request):
    """AJAX ile plasiyerin bölgelerini getirir"""
    try:
        plasiyer = request.GET.get('plasiyer', '')
        cari_tipi = request.GET.get('cari_tipi', '')
        
        if not plasiyer:
            return JsonResponse({'success': False, 'message': 'Plasiyer gerekli'})
        
        from .mssql_service import MSSQLService
        mssql_service = MSSQLService()
        
        # Plasiyerin bölgelerini al
        regions = mssql_service.get_unique_bolgeler(plasiyer)
        
        # Cari tipi filtresi uygula
        if cari_tipi:
            filtered_regions = []
            for region in regions:
                # Bu bölgede bu cari tipinden veri var mı kontrol et
                test_data = mssql_service.get_cari_bakiye_by_plasiyer(plasiyer, region, cari_tipi)
                if test_data:
                    filtered_regions.append(region)
            regions = filtered_regions
        
        return JsonResponse({
            'success': True,
            'regions': regions
        })
        
    except Exception as e:
        logger.error(f"get_plasiyer_regions error: {e}")
        return JsonResponse({'success': False, 'message': 'Bölge verileri alınamadı'})


@login_required
def get_all_regions(request):
    """AJAX ile tüm bölgeleri getirir"""
    try:
        from .mssql_service import MSSQLService
        mssql_service = MSSQLService()
        
        regions = mssql_service.get_all_unique_bolgeler()
        
        return JsonResponse({
            'success': True,
            'regions': regions
        })
        
    except Exception as e:
        logger.error(f"get_all_regions error: {e}")
        return JsonResponse({'success': False, 'message': 'Bölge verileri alınamadı'})


@login_required
@require_http_methods(["GET"])
def get_banka_listesi(request):
    """Kredi Kartı veya Banka Havalesi için BANKATB tablosundan banka listesini döndürür."""
    try:
        tahsilat_turu = request.GET.get('tahsilat_turu', '').strip()

        if tahsilat_turu == 'Kredi Kartı':
            bankalar = mssql_service.get_kredi_karti_bankalari() or []
        elif tahsilat_turu == 'Banka Havalesi':
            bankalar = mssql_service.get_havale_bankalari() or []
        else:
            return JsonResponse({'success': False, 'message': 'Geçersiz tahsilat türü'})

        normalized = []
        for b in bankalar:
            if isinstance(b, dict):
                # BANKATB tablosundan gelen veriler: ID, BANKAADI, TURU
                # BANKAADI alanını öncelikli olarak kullan
                banka_adi = b.get('BANKAADI', '') or b.get('DEFINITION_', '') or b.get('definition', '') or b.get('name', '')
                banka_id = b.get('ID')
            else:
                banka_adi = b[0] if len(b) > 0 else ''
                banka_id = b[1] if len(b) > 1 else None
            
            if banka_adi:
                normalized.append({
                    'ID': str(banka_id) if banka_id else '',
                    'BANKAADI': str(banka_adi).strip(),  # BANKAADI alanını kullan
                    'DEFINITION_': str(banka_adi).strip(),  # Mevcut frontend uyumluluğu için
                    'TURU': b.get('TURU', '') if isinstance(b, dict) else ''
                })

        return JsonResponse({'success': True, 'bankalar': normalized})
    except Exception as e:
        logger.error(f"get_banka_listesi error: {e}")
        return JsonResponse({'success': False, 'message': 'Banka listesi yüklenemedi'})

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
        return str(value)


def generate_cari_ekstre_pdf(cari_bakiye_list, plasiyer, bolge_filter):
    """Cari ekstre için PDF oluşturur (basit tablo halinde)"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from django.http import HttpResponse
        import io
        from datetime import datetime

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)

        # Ensure DejaVu fonts are registered for Turkish characters
        try:
            register_pdf_fonts()
        except Exception:
            pass

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], alignment=1, fontSize=14, textColor=colors.darkblue, fontName='DejaVuSans-Bold')

        title_text = "CARI EKSTRE RAPORU"
        if plasiyer:
            title_text += f" - {plasiyer}"
        if bolge_filter and bolge_filter != 'all':
            title_text += f" ({bolge_filter} Bölgesi)"

        title = Paragraph(title_text, title_style)
        normal_style = styles['Normal']
        normal_style.fontName = 'DejaVuSans'
        tarih = Paragraph(f"Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y %H:%M')}", normal_style)

        # helper: insert soft-breaks into long words so Paragraph can wrap them
        def insert_soft_breaks(text, maxlen=30):
            if not text:
                return ''
            parts = []
            for token in text.split(' '):
                if len(token) > maxlen:
                    # insert zero-width space every maxlen chars
                    new = '\u200b'.join([token[i:i+maxlen] for i in range(0, len(token), maxlen)])
                    parts.append(new)
                else:
                    parts.append(token)
            return ' '.join(parts)

        # Build table data with Paragraphs to allow word-wrapping
        data = [['Cari Kod', 'Cari Ünvan', 'Bakiye', 'Bölge']]
        normal_para_style = styles['Normal']
        normal_para_style.fontName = 'DejaVuSans'
        normal_para_style.fontSize = 8
        # prefer CJK wrapping to allow breaks at inserted zwsp
        normal_para_style.wordWrap = 'CJK'
        for item in cari_bakiye_list:
            bakiye_value = item.get('BAKİYE')
            if bakiye_value is not None and bakiye_value != '':
                bakiye_str = turkish_number_format(bakiye_value, 2) + ' ₺'
            else:
                bakiye_str = '-'

            definition = item.get('DEFINITION_', '') or item.get('DEFINITION') or item.get('CariUnvan', '')
            # allow wrapping: insert soft breaks into long tokens
            safe_definition = insert_soft_breaks(definition, maxlen=30)
            def_para = Paragraph(safe_definition, normal_para_style)
            bakiye_para = Paragraph(bakiye_str, ParagraphStyle('right', parent=styles['Normal'], alignment=2, fontName='DejaVuSans', fontSize=8))

            data.append([
                Paragraph(item.get('CODE', ''), normal_para_style),
                def_para,
                bakiye_para,
                Paragraph(item.get('BOLGE', '-'), normal_para_style)
            ])

        # Compute column widths dynamically based on available document width
        usable_width = doc.width
        # ratios for columns: code, definition, bakiye, bolge
        ratios = [0.12, 0.62, 0.14, 0.12]
        colWidths = [usable_width * r for r in ratios]
        table = Table(data, colWidths=colWidths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTNAME', (0, 0), (-1, 0), 'DejaVuSans-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), 'DejaVuSans'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('ALIGN', (2, 1), (2, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ]))

        elements = [title, Spacer(1, 6), tarih, Spacer(1, 12), table]
        doc.build(elements)

        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type='application/pdf')
        filename = f"cari_ekstre_{plasiyer or 'tum'}_{bolge_filter}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    except Exception as e:
        logger.error(f"Cari ekstre PDF oluşturma hatası: {e}")
        return HttpResponse("PDF oluşturulurken hata oluştu.", status=500)

def generate_cari_hareketler_pdf(hareketler_list, cari_kod, cari_unvan, toplam_borc, toplam_alacak, toplam_bakiye, toplam_kayit):
    """Cari Hesap Hareketleri için PDF oluşturur - A4 formatında"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from django.http import HttpResponse
        import io
        from datetime import datetime

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1*cm, leftMargin=1*cm, topMargin=1*cm, bottomMargin=2*cm)

        # Ensure DejaVu fonts are registered for Turkish characters
        try:
            register_pdf_fonts()
        except Exception:
            pass

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], alignment=1, fontSize=16, textColor=colors.HexColor('#667eea'), fontName='DejaVuSans-Bold', spaceAfter=6)
        subtitle_style = ParagraphStyle('Subtitle', parent=styles['Normal'], alignment=1, fontSize=12, textColor=colors.HexColor('#495057'), fontName='DejaVuSans', spaceAfter=3)
        info_style = ParagraphStyle('Info', parent=styles['Normal'], alignment=1, fontSize=9, textColor=colors.HexColor('#6c757d'), fontName='DejaVuSans', spaceAfter=10)

        # Başlık
        title = Paragraph("CARI HESAP HAREKETLERİ", title_style)
        subtitle = Paragraph(f"{cari_unvan}", subtitle_style)
        cari_kod_info = Paragraph(f"Cari Kod: {cari_kod}", info_style)
        tarih_info = Paragraph(f"Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y %H:%M')}", info_style)

        # Tablo başlıkları
        data = [['Tarih', 'Fatura No', 'Fatura Türü', 'Borç', 'Alacak', 'BAKİYE']]

        # Tablo verileri (en eski tarihten başlayarak - PDF'de kronolojik sıra)
        normal_para_style = ParagraphStyle('Normal', parent=styles['Normal'], fontName='DejaVuSans', fontSize=7, wordWrap='CJK')
        right_para_style = ParagraphStyle('Right', parent=styles['Normal'], alignment=2, fontName='DejaVuSans', fontSize=7)
        
        for item in hareketler_list:
            tarih = item.get('TARİH')
            if tarih:
                if isinstance(tarih, datetime):
                    tarih_str = tarih.strftime('%d.%m.%Y')
                else:
                    tarih_str = str(tarih)
            else:
                tarih_str = '-'
            
            fatura_no = item.get('FATURANO') or item.get('FATURA_NO') or '-'
            fatura_turu = item.get('FATURA TÜRÜ') or item.get('FATURA_TÜRÜ') or '-'
            
            borc = float(item.get('BORÇ') or item.get('BORC') or 0)
            alacak = float(item.get('ALACAK') or 0)
            yuruyen_bakiye = float(item.get('YURUYEN_BAKIYE') or 0)
            
            borc_str = turkish_number_format(borc, 2) + ' ₺' if borc > 0 else '-'
            alacak_str = turkish_number_format(alacak, 2) + ' ₺' if alacak > 0 else '-'
            bakiye_str = turkish_number_format(yuruyen_bakiye, 2) + ' ₺'
            
            data.append([
                Paragraph(tarih_str, normal_para_style),
                Paragraph(str(fatura_no), normal_para_style),
                Paragraph(str(fatura_turu), normal_para_style),
                Paragraph(borc_str, right_para_style),
                Paragraph(alacak_str, right_para_style),
                Paragraph(bakiye_str, right_para_style),
            ])

        # Toplam satırı
        toplam_borc_str = turkish_number_format(toplam_borc, 2) + ' ₺'
        toplam_alacak_str = turkish_number_format(toplam_alacak, 2) + ' ₺'
        toplam_bakiye_str = turkish_number_format(toplam_bakiye, 2) + ' ₺'
        
        total_style = ParagraphStyle('Total', parent=styles['Normal'], fontName='DejaVuSans-Bold', fontSize=8)
        total_right_style = ParagraphStyle('TotalRight', parent=styles['Normal'], alignment=2, fontName='DejaVuSans-Bold', fontSize=8)
        
        data.append([
            Paragraph('TOPLAM', total_style),
            Paragraph('', normal_para_style),
            Paragraph('', normal_para_style),
            Paragraph(toplam_borc_str, total_right_style),
            Paragraph(toplam_alacak_str, total_right_style),
            Paragraph(toplam_bakiye_str, total_right_style),
        ])

        # Kolon genişlikleri
        usable_width = doc.width
        ratios = [0.15, 0.20, 0.20, 0.15, 0.15, 0.15]
        colWidths = [usable_width * r for r in ratios]
        
        table = Table(data, colWidths=colWidths, repeatRows=1)
        table.setStyle(TableStyle([
            # Başlık satırı
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'DejaVuSans-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            
            # Veri satırları
            ('FONTNAME', (0, 1), (-1, -2), 'DejaVuSans'),
            ('FONTSIZE', (0, 1), (-1, -2), 7),
            ('ALIGN', (3, 1), (5, -2), 'RIGHT'),  # Borç, Alacak, BAKİYE sağa hizalı
            
            # Toplam satırı
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f8f9fa')),
            ('FONTNAME', (0, -1), (-1, -1), 'DejaVuSans-Bold'),
            ('FONTSIZE', (0, -1), (-1, -1), 8),
            ('ALIGN', (3, -1), (5, -1), 'RIGHT'),
            ('LINEABOVE', (0, -1), (-1, -1), 1, colors.grey),
            
            # Grid
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))

        elements = [title, subtitle, cari_kod_info, tarih_info, Spacer(1, 0.3*cm), table]
        doc.build(elements)

        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type='application/pdf')
        filename = f"cari_hareketler_{cari_kod.replace('.', '_')}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response

    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        logger.error(f"Cari hareketler PDF oluşturma hatası: {e}\n{error_detail}")
        return HttpResponse("PDF oluşturulurken hata oluştu.", status=500)

def generate_genel_ekstre_pdf(cari_bakiye_list, plasiyer, bolge_filter, cari_tipi):
    """Genel ekstre PDF oluşturur - Modern, dinamik ve A4 formatına uygun"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, KeepTogether
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch, cm
        from django.http import HttpResponse
        from reportlab.platypus.frames import Frame
        from reportlab.platypus.doctemplate import PageTemplate, BaseDocTemplate
        import io
        from datetime import datetime

        # PDF buffer oluştur - A4 formatı için optimize edilmiş marjlar
        buffer = io.BytesIO()
        
        # A4 boyutları: 210mm x 297mm (595.27 x 841.89 points)
        # Modern marjlar: üst/alt 1.5cm, sağ/sol 1.2cm
        doc = SimpleDocTemplate(
            buffer, 
            pagesize=A4,
            rightMargin=cm*1.2,
            leftMargin=cm*1.2,
            topMargin=cm*1.5,
            bottomMargin=cm*1.5
        )

        # Ensure DejaVu fonts are registered for Turkish characters
        try:
            register_pdf_fonts()
        except Exception:
            pass

        # Stil tanımları - Modern ve okunaklı
        styles = getSampleStyleSheet()
        
        # Başlık stili - Büyük ve göze çarpan
        title_style = ParagraphStyle(
            'MainTitle',
            parent=styles['Heading1'],
            fontSize=16,
            spaceAfter=10,
            spaceBefore=0,
            alignment=1,  # Center
            textColor=colors.HexColor('#1a1a2e'),
            fontName='DejaVuSans-Bold',
            leading=19
        )
        
        # Alt başlık stili
        subtitle_style = ParagraphStyle(
            'SubTitle',
            parent=styles['Normal'],
            fontSize=9,
            spaceAfter=5,
            alignment=1,
            textColor=colors.HexColor('#4a5568'),
            fontName='DejaVuSans'
        )
        
        # Normal metin stili
        normal_style = ParagraphStyle(
            'NormalText',
            parent=styles['Normal'],
            fontSize=7,
            fontName='DejaVuSans',
            leading=9,
            textColor=colors.HexColor('#2d3748')
        )
        
        # Özet bilgi stili
        summary_style = ParagraphStyle(
            'Summary',
            parent=styles['Normal'],
            fontSize=8,
            fontName='DejaVuSans-Bold',
            leading=10,
            textColor=colors.HexColor('#2d3748')
        )

        # Başlık oluştur
        title_text = "GENEL EKSTRE RAPORU"
        title = Paragraph(title_text, title_style)
        
        # Alt başlık bilgileri
        subtitle_parts = []
        if plasiyer:
            subtitle_parts.append(f"Plasiyer: {plasiyer}")
        if bolge_filter and bolge_filter != 'all':
            subtitle_parts.append(f"Bölge: {bolge_filter}")
        if cari_tipi:
            cari_tipi_text = "Tedarikçiler" if cari_tipi == 'tedarikci' else "Müşteriler"
            subtitle_parts.append(f"Tip: {cari_tipi_text}")
        subtitle_parts.append(f"Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        
        subtitle = Paragraph(" | ".join(subtitle_parts), subtitle_style) if subtitle_parts else None

        # İstatistikler hesapla
        toplam_kayit = len(cari_bakiye_list)
        toplam_bakiye = sum([float(item.get('BAKİYE') or 0) for item in cari_bakiye_list])
        pozitif_bakiye = sum([float(item.get('BAKİYE') or 0) for item in cari_bakiye_list if float(item.get('BAKİYE') or 0) > 0])
        negatif_bakiye = sum([float(item.get('BAKİYE') or 0) for item in cari_bakiye_list if float(item.get('BAKİYE') or 0) < 0])
        pozitif_kayit = len([item for item in cari_bakiye_list if float(item.get('BAKİYE') or 0) > 0])
        negatif_kayit = len([item for item in cari_bakiye_list if float(item.get('BAKİYE') or 0) < 0])

        # Özet bilgiler tablosu
        summary_data = [
            ['Toplam Kayıt', f"{toplam_kayit:,}", 'Pozitif Bakiye', turkish_number_format(pozitif_bakiye, 2) + ' ₺'],
            ['Pozitif Kayıt', f"{pozitif_kayit:,}", 'Negatif Bakiye', turkish_number_format(negatif_bakiye, 2) + ' ₺'],
            ['Negatif Kayıt', f"{negatif_kayit:,}", 'Toplam Bakiye', turkish_number_format(toplam_bakiye, 2) + ' ₺']
        ]
        
        summary_table_data = []
        for row in summary_data:
            summary_table_data.append([
                Paragraph(str(row[0]), summary_style),
                Paragraph(str(row[1]), normal_style),
                Paragraph(str(row[2]), summary_style),
                Paragraph(str(row[3]), summary_style)
            ])
        
        summary_table = Table(summary_table_data, colWidths=[doc.width*0.25, doc.width*0.25, doc.width*0.25, doc.width*0.25])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f7fafc')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
        ]))

        # Tablo başlıkları - Optimize edilmiş
        header = [
            'Cari Kod',
            'Cari Ünvan',
            'Plasiyer',
            'Bölge',
            'Satış T.',
            'S.G.Süre',
            'Tahsilat T.',
            'T.G.Süre',
            'Bakiye'
        ]
        
        # Font boyutları - A4'e uygun
        header_font_size = 6.5
        data_font_size = 6
        
        # Tablo stil tanımları
        header_style = ParagraphStyle(
            'HeaderStyle',
            parent=styles['Normal'],
            fontName='DejaVuSans-Bold',
            fontSize=header_font_size,
            leading=header_font_size + 2,
            alignment=1,  # Center
            textColor=colors.white,
            wordWrap='CJK'
        )
        
        data_style = ParagraphStyle(
            'DataStyle',
            parent=styles['Normal'],
            fontName='DejaVuSans',
            fontSize=data_font_size,
            leading=data_font_size + 2,
            textColor=colors.HexColor('#2d3748'),
            wordWrap='CJK'
        )
        
        right_align_style = ParagraphStyle(
            'RightAlign',
            parent=data_style,
            alignment=2  # Right
        )

        # Tablo verileri oluştur
        table_data = []
        # Başlık satırı
        header_row = [Paragraph(str(h), header_style) for h in header]
        table_data.append(header_row)
        
        # Veri satırları
        for item in cari_bakiye_list:
            # Bakiye formatı
            bakiye_value = item.get('BAKİYE')
            if bakiye_value is not None and bakiye_value != '':
                try:
                    bakiye_float = float(bakiye_value)
                    bakiye_str = turkish_number_format(bakiye_float, 2) + ' ₺'
                    # Pozitif/negatif renklendirme için stil
                    if bakiye_float > 0:
                        bakiye_para = Paragraph(bakiye_str, ParagraphStyle('BakiyePos', parent=right_align_style, textColor=colors.HexColor('#c53030')))
                    elif bakiye_float < 0:
                        bakiye_para = Paragraph(bakiye_str, ParagraphStyle('BakiyeNeg', parent=right_align_style, textColor=colors.HexColor('#2f855a')))
                    else:
                        bakiye_para = Paragraph(bakiye_str, right_align_style)
                except:
                    bakiye_para = Paragraph('-', right_align_style)
            else:
                bakiye_para = Paragraph('-', right_align_style)
            
            # Tarih formatları
            tarihs = item.get('TARIHS')
            tarihs_text = tarihs.strftime('%d.%m.%Y') if tarihs and hasattr(tarihs, 'strftime') else '-'
            
            tariht = item.get('TARIHT')
            tariht_text = tariht.strftime('%d.%m.%Y') if tariht and hasattr(tariht, 'strftime') else '-'
            
            # Geçen süre formatları
            satis_gecen = f"{item.get('SATISGECENSURE', '')}" if item.get('SATISGECENSURE') else '-'
            tahsilat_gecen = f"{item.get('TAHSILATGECENSURE', '')}" if item.get('TAHSILATGECENSURE') else '-'
            
            # Satır oluştur
            row = [
                Paragraph(str(item.get('CODE', '') or '-'), data_style),
                Paragraph(str(item.get('DEFINITION_', '') or '-'), data_style),
                Paragraph(str(item.get('SPECODE', '') or '-'), data_style),
                Paragraph(str(item.get('BOLGE', '') or '-'), data_style),
                Paragraph(tarihs_text, data_style),
                Paragraph(satis_gecen, ParagraphStyle('Center', parent=data_style, alignment=1)),
                Paragraph(tariht_text, data_style),
                Paragraph(tahsilat_gecen, ParagraphStyle('Center', parent=data_style, alignment=1)),
                bakiye_para
            ]
            table_data.append(row)

        # Kolon genişlikleri - A4'e optimize
        # A4 genişliği: 595.27 points, marjlar sonrası: ~520 points
        col_widths = [
            doc.width * 0.12,  # Cari Kod
            doc.width * 0.30,  # Cari Ünvan
            doc.width * 0.10,  # Plasiyer
            doc.width * 0.08,  # Bölge
            doc.width * 0.08,  # Satış T.
            doc.width * 0.06,  # S.G.Süre
            doc.width * 0.08,  # Tahsilat T.
            doc.width * 0.06,  # T.G.Süre
            doc.width * 0.12   # Bakiye
        ]
        
        # Tablo oluştur
        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        
        # Modern tablo stili
        table.setStyle(TableStyle([
            # Başlık satırı
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'DejaVuSans-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), header_font_size),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
            
            # Veri satırları
            ('FONTNAME', (0, 1), (-1, -1), 'DejaVuSans'),
            ('FONTSIZE', (0, 1), (-1, -1), data_font_size),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (4, 1), (4, -1), 'CENTER'),  # Satış Tarihi
            ('ALIGN', (5, 1), (5, -1), 'CENTER'),  # Satış Geçen Süre
            ('ALIGN', (6, 1), (6, -1), 'CENTER'),  # Tahsilat Tarihi
            ('ALIGN', (7, 1), (7, -1), 'CENTER'),  # Tahsilat Geçen Süre
            
            # Grid ve border
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('LINEBELOW', (0, 0), (-1, 0), 2, colors.HexColor('#4a5568')),
            
            # Padding
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 1), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
            
            # Alternatif satır renkleri
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7fafc')]),
            
            # Hover efekti benzeri - ilk veri satırında örnek
            ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#edf2f7')),
        ]))

        # PDF elemanları oluştur
        elements = []
        
        # Başlık ve alt başlık
        elements.append(title)
        if subtitle:
            elements.append(subtitle)
        
        elements.append(Spacer(1, 12))
        
        # Özet bilgiler
        elements.append(KeepTogether(summary_table))
        elements.append(Spacer(1, 12))
        
        # Ana tablo
        elements.append(table)
        
        # PDF oluştur
        doc.build(elements, onFirstPage=lambda canvas, doc: None,
                  onLaterPages=lambda canvas, doc: None)

        # Response oluştur
        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type='application/pdf')
        filename = f"genel_ekstre_{plasiyer or 'tum'}_{bolge_filter}_{cari_tipi}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response

    except Exception as e:
        logger.error(f"Genel ekstre PDF oluşturma hatası: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return HttpResponse("PDF oluşturulurken hata oluştu.", status=500)


def generate_genel_ekstre_excel(cari_bakiye_list, plasiyer, bolge_filter, cari_tipi):
    """Genel ekstre Excel oluşturur"""
    try:
        import pandas as pd
        from django.http import HttpResponse
        from datetime import datetime
        import io

        # DataFrame oluştur
        data = []
        for item in cari_bakiye_list:
            # Bakiye formatı - Türk formatında (1.234,56)
            bakiye_value = item.get('BAKİYE')
            if bakiye_value is not None and bakiye_value != '':
                bakiye_formatted = turkish_number_format(bakiye_value, 2)
            else:
                bakiye_formatted = ''
            
            data.append({
                'Cari Kod': item.get('CODE', ''),
                'Cari Ünvan': item.get('DEFINITION_', ''),
                'Plasiyer': item.get('SPECODE', ''),
                'Satış Tarih': item.get('TARIHS', '').strftime('%d.%m.%Y') if item.get('TARIHS') else '',
                'Satış Geçen Süre': f"{item.get('SATISGECENSURE', '')} gün" if item.get('SATISGECENSURE') else '',
                'Tahsilat Tarih': item.get('TARIHT', '').strftime('%d.%m.%Y') if item.get('TARIHT') else '',
                'Tahsilat Geçen Süre': f"{item.get('TAHSILATGECENSURE', '')} gün" if item.get('TAHSILATGECENSURE') else '',
                'Bakiye': bakiye_formatted,
                'Bölge': item.get('BOLGE', '')
            })

        df = pd.DataFrame(data)

        # Excel buffer oluştur
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Genel Ekstre', index=False)
            
            # Worksheet'i al ve formatla
            worksheet = writer.sheets['Genel Ekstre']
            
            # Sütun genişliklerini ayarla
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width

        output.seek(0)

        # Response oluştur
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        filename = f"genel_ekstre_{plasiyer or 'tum'}_{bolge_filter}_{cari_tipi}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response

    except Exception as e:
        logger.error(f"Excel oluşturma hatası: {e}")
        return HttpResponse("Excel oluşturulurken hata oluştu.", status=500)


def export_plasiyer_performans_excel(request):
    """Plasiyer Performans Tablosu Excel Export"""
    try:
        # Filtreleme parametrelerini al - genel_dashboard ile aynı mantık
        selected_months = request.GET.getlist('selected_months', [])
        
        # Eğer hiç ay seçilmemişse, mevcut ayı varsayılan olarak seç (genel_dashboard ile aynı)
        if not selected_months:
            from datetime import datetime
            current_month = datetime.now().month
            selected_months = [str(current_month)]
        
        # MSSQL servisinden veri al - genel_dashboard ile aynı şekilde
        mssql_service = MSSQLService()
        plasiyer_data = mssql_service.get_all_plasiyerler_stats(
            selected_months=selected_months if selected_months else None
        )
        
        # Excel dosyası oluştur
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output)
        worksheet = workbook.add_worksheet('Plasiyer Performans')
        
        # Stil tanımlamaları
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#f8f9fa',
            'border': 1,
            'align': 'center',
            'valign': 'vcenter'
        })
        
        title_format = workbook.add_format({
            'bold': True,
            'font_size': 14,
            'bg_color': '#e9ecef',
            'border': 1,
            'align': 'center',
            'valign': 'vcenter'
        })
        
        number_format = workbook.add_format({
            'num_format': '#,##0.00',
            'border': 1,
            'align': 'right'
        })
        
        currency_format = workbook.add_format({
            'num_format': '#,##0.00 ₺',
            'border': 1,
            'align': 'right'
        })
        
        # Başlık satırları
        worksheet.merge_range('A1:G1', 'PLASİYER PERFORMANS TABLOSU', title_format)
        
        # Tarih bilgisi
        tarih_str = datetime.now().strftime('%d.%m.%Y %H:%M')
        worksheet.write('A2', f'Rapor Tarihi: {tarih_str}')
        
        # Filtreleme bilgisi
        if selected_months:
            ay_isimleri = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
                          'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']
            secilen_aylar = [ay_isimleri[int(ay)-1] for ay in selected_months]
            worksheet.write('A3', f'Filtrelenen Aylar: {", ".join(secilen_aylar)}')
        
        # Tablo başlıkları
        row = 5
        worksheet.write(row, 0, 'Plasiyer', header_format)
        worksheet.write(row, 1, 'Tahsilat - Günlük', header_format)
        worksheet.write(row, 2, 'Tahsilat - Haftalık', header_format)
        worksheet.write(row, 3, 'Tahsilat - Aylık', header_format)
        worksheet.write(row, 4, 'Satış - Günlük', header_format)
        worksheet.write(row, 5, 'Satış - Haftalık', header_format)
        worksheet.write(row, 6, 'Satış - Aylık', header_format)
        
        # Veri satırları
        row = 6
        for plasiyer in plasiyer_data['plasiyerler']:
            worksheet.write(row, 0, plasiyer['plasiyer'], header_format)
            worksheet.write(row, 1, float(plasiyer['tahsilat']['gunluk_tutar']), currency_format)
            worksheet.write(row, 2, float(plasiyer['tahsilat']['haftalik_tutar']), currency_format)
            worksheet.write(row, 3, float(plasiyer['tahsilat']['aylik_tutar']), currency_format)
            worksheet.write(row, 4, float(plasiyer['satis']['gunluk_tutar']), currency_format)
            worksheet.write(row, 5, float(plasiyer['satis']['haftalik_tutar']), currency_format)
            worksheet.write(row, 6, float(plasiyer['satis']['aylik_tutar']), currency_format)
            row += 1
        
        # Toplam satırı
        worksheet.write(row, 0, 'TOPLAM', title_format)
        worksheet.write(row, 1, float(plasiyer_data['toplam_tahsilat']['gunluk_tutar']), currency_format)
        worksheet.write(row, 2, float(plasiyer_data['toplam_tahsilat']['haftalik_tutar']), currency_format)
        worksheet.write(row, 3, float(plasiyer_data['toplam_tahsilat']['aylik_tutar']), currency_format)
        worksheet.write(row, 4, float(plasiyer_data['toplam_satis']['gunluk_tutar']), currency_format)
        worksheet.write(row, 5, float(plasiyer_data['toplam_satis']['haftalik_tutar']), currency_format)
        worksheet.write(row, 6, float(plasiyer_data['toplam_satis']['aylik_tutar']), currency_format)
        
        # Sütun genişlikleri
        worksheet.set_column('A:A', 15)
        worksheet.set_column('B:G', 18)
        
        workbook.close()
        output.seek(0)
        
        # Response oluştur
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
        filename = f'plasiyer_performans_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Exception as e:
        logger.error(f"Plasiyer performans Excel export hatası: {e}")
        return HttpResponse("Excel oluşturulurken hata oluştu.", status=500)
def export_plasiyer_performans_pdf(request):
    """Plasiyer Performans Tablosu PDF Export"""
    try:
        # Filtreleme parametrelerini al - genel_dashboard ile aynı mantık
        selected_months = request.GET.getlist('selected_months', [])
        
        # Eğer hiç ay seçilmemişse, mevcut ayı varsayılan olarak seç (genel_dashboard ile aynı)
        if not selected_months:
            from datetime import datetime
            current_month = datetime.now().month
            selected_months = [str(current_month)]
        
        # MSSQL servisinden veri al - genel_dashboard ile aynı şekilde
        mssql_service = MSSQLService()
        plasiyer_data = mssql_service.get_all_plasiyerler_stats(
            selected_months=selected_months if selected_months else None
        )
        
        # PDF oluştur
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        story = []
        
        # Stil tanımlamaları
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            spaceAfter=30,
            alignment=TA_CENTER
        )
        
        # Başlık
        story.append(Paragraph("PLASİYER PERFORMANS TABLOSU", title_style))
        
        # Tarih bilgisi
        tarih_str = datetime.now().strftime('%d.%m.%Y %H:%M')
        story.append(Paragraph(f"<b>Rapor Tarihi:</b> {tarih_str}", styles['Normal']))
        
        # Filtreleme bilgisi
        if selected_months:
            ay_isimleri = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
                          'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']
            secilen_aylar = [ay_isimleri[int(ay)-1] for ay in selected_months]
            story.append(Paragraph(f"<b>Filtrelenen Aylar:</b> {', '.join(secilen_aylar)}", styles['Normal']))
        
        story.append(Spacer(1, 20))
        
        # Tablo verileri
        table_data = [['Plasiyer', 'Tahsilat - Günlük', 'Tahsilat - Haftalık', 'Tahsilat - Aylık',
                      'Satış - Günlük', 'Satış - Haftalık', 'Satış - Aylık']]
        
        for plasiyer in plasiyer_data['plasiyerler']:
            row = [
                plasiyer['plasiyer'],
                f"{float(plasiyer['tahsilat']['gunluk_tutar']):,.2f} ₺",
                f"{float(plasiyer['tahsilat']['haftalik_tutar']):,.2f} ₺",
                f"{float(plasiyer['tahsilat']['aylik_tutar']):,.2f} ₺",
                f"{float(plasiyer['satis']['gunluk_tutar']):,.2f} ₺",
                f"{float(plasiyer['satis']['haftalik_tutar']):,.2f} ₺",
                f"{float(plasiyer['satis']['aylik_tutar']):,.2f} ₺"
            ]
            table_data.append(row)
        
        # Toplam satırı
        toplam_row = [
            'TOPLAM',
            f"{float(plasiyer_data['toplam_tahsilat']['gunluk_tutar']):,.2f} ₺",
            f"{float(plasiyer_data['toplam_tahsilat']['haftalik_tutar']):,.2f} ₺",
            f"{float(plasiyer_data['toplam_tahsilat']['aylik_tutar']):,.2f} ₺",
            f"{float(plasiyer_data['toplam_satis']['gunluk_tutar']):,.2f} ₺",
            f"{float(plasiyer_data['toplam_satis']['haftalik_tutar']):,.2f} ₺",
            f"{float(plasiyer_data['toplam_satis']['aylik_tutar']):,.2f} ₺"
        ]
        table_data.append(toplam_row)
        
        # Tablo oluştur
        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        story.append(table)
        
        # PDF oluştur
        doc.build(story)
        buffer.seek(0)
        
        # Response oluştur
        response = HttpResponse(buffer.read(), content_type='application/pdf')
        filename = f'plasiyer_performans_{datetime.now().strftime("%Y%m%d_%H%M")}.pdf'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Exception as e:
        logger.error(f"Plasiyer performans PDF export hatası: {e}")
        return HttpResponse("PDF oluşturulurken hata oluştu.", status=500)


def generate_muhasebe_gunluk_pdf(rapor_tarihi, satirlar, tahsilatlar_list, satislar_list, nakit_teslim_edilmedi_toplam):
    """Muhasebe Günlük Rapor PDF oluşturma fonksiyonu"""
    # Güvenli None kontrolü
    if tahsilatlar_list is None:
        tahsilatlar_list = []
    if satislar_list is None:
        satislar_list = []
    if satirlar is None:
        satirlar = []
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import cm, mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT
        
        register_pdf_fonts()

        buffer = io.BytesIO()
        # Yatay (landscape) A4 sayfa - tablolar için daha geniş alan
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), 
                               rightMargin=1*cm, leftMargin=1*cm,
                               topMargin=1*cm, bottomMargin=1*cm)
        
        story = []
        styles = getSampleStyleSheet()
        for style in styles.byName.values():
            style.fontName = 'DejaVuSans'

        # Başlık stili
        title_style = styles['Heading1']
        title_style.fontName = 'DejaVuSans-Bold'
        title_style.alignment = TA_CENTER
        styles['Heading2'].fontName = 'DejaVuSans-Bold'
        
        # Başlık
        story.append(Paragraph("MUHASEBE GÜNLÜK RAPOR", title_style))
        story.append(Paragraph(f"<b>Rapor Tarihi:</b> {rapor_tarihi}", styles['Normal']))
        story.append(Spacer(1, 20))
        
        # Plasiyer Özet Tablosu
        story.append(Paragraph("<b>Plasiyer Özeti</b>", styles['Heading2']))
        story.append(Spacer(1, 10))
        
        plasiyer_data = [['Plasiyer', 'Satış Toplamı', 'Tahsilat Toplamı', 'Gider Toplamı']]
        for row in satirlar:
            plasiyer_data.append([
                row['plasiyer'],
                f"{float(row['satis']):,.2f} ₺",
                f"{float(row['tahsilat']):,.2f} ₺",
                f"{float(row['gider']):,.2f} ₺"
            ])
        
        # Toplam satırını ekle
        toplam_satis = sum(float(row.get('satis', 0)) for row in satirlar)
        toplam_tahsilat = sum(float(row.get('tahsilat', 0)) for row in satirlar)
        toplam_gider = sum(float(row.get('gider', 0)) for row in satirlar)
        plasiyer_data.append([
            'TOPLAM',
            f"{toplam_satis:,.2f} ₺",
            f"{toplam_tahsilat:,.2f} ₺",
            f"{toplam_gider:,.2f} ₺"
        ])
        
        plasiyer_table = Table(plasiyer_data, colWidths=[4*cm, 5*cm, 5*cm, 5*cm])
        plasiyer_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#17a2b8')),
            ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'DejaVuSans-Bold'),
            ('FONTNAME', (0, 1), (-2, -1), 'DejaVuSans'),
            ('FONTNAME', (0, -1), (-1, -1), 'DejaVuSans-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
            ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#34495e')),
            ('LINEABOVE', (0, -1), (-1, -1), 1.5, colors.HexColor('#17a2b8')),
            ('ROWBACKGROUNDS', (0, 1), (-2, -2), [colors.white, colors.HexColor('#ecf0f1')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(plasiyer_table)
        story.append(Spacer(1, 20))
        
        # Nakit Teslim Edilmedi
        story.append(Paragraph(f"<b>Nakit Teslim Edilmedi:</b> {float(nakit_teslim_edilmedi_toplam):,.2f} ₺", styles['Normal']))
        story.append(Spacer(1, 20))
        
        # Tahsilatlar Tablosu
        if tahsilatlar_list is not None and tahsilatlar_list:
            story.append(Paragraph(f"<b>Tahsilatlar ({rapor_tarihi})</b>", styles['Heading2']))
            story.append(Spacer(1, 10))
            
            # Include 'Kullanıcı' column and ensure taksit=0 is displayed (don't treat 0 as falsy)
            # Landscape A4 = 29.7cm genişlik, 2cm margin = 27.7cm kullanılabilir alan
            tahsilat_data = [['Gün', 'Plasiyer', 'Kullanıcı', 'Cari Kod', 'Cari Ünvan', 'Tür', 'Banka', 'Taksit', 'Tutar', 'Durum']]
            for t in tahsilatlar_list:
                # None kontrolü
                if t is None:
                    continue
                # Sadece gün numarasını al
                tarih_obj = t.get('tarih')
                if tarih_obj and hasattr(tarih_obj, 'day'):
                    gun = str(tarih_obj.day)
                elif tarih_obj:
                    tarih_str = str(tarih_obj)
                    gun = tarih_str[-2:] if len(tarih_str) >= 2 else tarih_str
                else:
                    gun = '-'
                
                # Cari ünvanı kısalt (max 25 karakter)
                cari_unvan_raw = t.get('cari_unvan') or ''
                cari_unvan = str(cari_unvan_raw)[:25] if cari_unvan_raw else '-'
                if cari_unvan_raw and len(str(cari_unvan_raw)) > 25:
                    cari_unvan += '...'
                
                # Teslim durumunu kısalt
                teslim = t.get('teslim_durumu') or '-'
                if teslim == 'TESLİM EDİLDİ':
                    teslim = 'TE'
                elif teslim == 'HESAP GÖRÜLDÜ':
                    teslim = 'HG'
                elif teslim == 'BEKLİYOR':
                    teslim = 'BK'
                else:
                    teslim = str(teslim)[:2] if teslim and teslim != '-' else '-'
                
                # Plasiyer, kullanıcı, tahsilat_turu, banka için güvenli slice
                plasiyer_raw = t.get('plasiyer') or '-'
                plasiyer = str(plasiyer_raw)[:8] if plasiyer_raw else '-'
                
                kullanici_raw = t.get('kullanici') or '-'
                kullanici = str(kullanici_raw)[:8] if kullanici_raw else '-'
                
                tahsilat_turu_raw = t.get('tahsilat_turu') or '-'
                tahsilat_turu = str(tahsilat_turu_raw)[:12] if tahsilat_turu_raw else '-'
                
                banka_raw = t.get('banka') or '-'
                banka = str(banka_raw)[:10] if banka_raw else '-'
                
                tahsilat_data.append([
                    gun,
                    plasiyer,
                    kullanici,
                    t.get('cari_kod') or '-',
                    cari_unvan,
                    tahsilat_turu,
                    banka,
                    (str(t.get('taksit')) if (t.get('taksit') is not None and t.get('taksit') != '') else '-'),
                    f"{float(t.get('tutar', 0) or 0):,.2f} ₺",
                    teslim
                ])
            # Kolon genişlikleri: Gün(1) + Plasiyer(2) + Kullanıcı(2) + CariKod(2.5) + CariUnvan(5.5) + Tür(2.5) + Banka(2.5) + Taksit(1) + Tutar(2.5) + Durum(1) = 22.5cm
            tahsilat_table = Table(tahsilat_data, colWidths=[1*cm, 2*cm, 2*cm, 2.5*cm, 5.5*cm, 2.5*cm, 2.5*cm, 1*cm, 2.5*cm, 1*cm])
            tahsilat_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('ALIGN', (0, 1), (0, -1), 'CENTER'),  # Gün
                ('ALIGN', (1, 1), (2, -1), 'LEFT'),    # Plasiyer, Kullanıcı
                ('ALIGN', (3, 1), (4, -1), 'LEFT'),    # Cari Kod, Cari Ünvan
                ('ALIGN', (5, 1), (7, -1), 'CENTER'),  # Tür, Banka, Taksit
                ('ALIGN', (8, 1), (8, -1), 'RIGHT'),   # Tutar
                ('ALIGN', (9, 1), (9, -1), 'CENTER'),  # Durum
                ('FONTNAME', (0, 0), (-1, 0), 'DejaVuSans-Bold'),
                ('FONTNAME', (0, 1), (-1, -1), 'DejaVuSans'),
                ('FONTSIZE', (0, 0), (-1, 0), 8),
                ('FONTSIZE', (0, 1), (-1, -1), 7),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 2),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
                ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#2c3e50')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#ecf0f1')]),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            story.append(tahsilat_table)
            story.append(PageBreak())
        
        # Satışlar Tablosu
        if satislar_list is not None and satislar_list:
            story.append(Paragraph(f"<b>Satışlar ({rapor_tarihi})</b>", styles['Heading2']))
            story.append(Spacer(1, 10))
            
            satis_data = [['Tarih', 'Plasiyer', 'Cari Kod', 'Cari Ünvan', 'Fatura No', 'Fatura Türü', 'Net Tutar', 'Bölge']]
            for s in satislar_list:
                # None kontrolü
                if s is None:
                    continue
                tarih_obj = s.get('tarih')
                if tarih_obj and hasattr(tarih_obj, 'strftime'):
                    tarih_str = tarih_obj.strftime('%Y-%m-%d')
                else:
                    tarih_str = str(tarih_obj) if tarih_obj else '-'
                
                # Cari ünvanı kısalt (max 28 karakter)
                cari_unvan_raw = s.get('cari_unvan') or ''
                cari_unvan = str(cari_unvan_raw)[:28] if cari_unvan_raw else '-'
                if cari_unvan_raw and len(str(cari_unvan_raw)) > 28:
                    cari_unvan += '...'
                
                # Fatura türü kısalt
                fatura_turu_raw = s.get('fatura_turu') or '-'
                fatura_turu = str(fatura_turu_raw)
                if 'Toptan satış faturası' in fatura_turu:
                    fatura_turu = 'Toptan Satış'
                elif 'Perakende' in fatura_turu:
                    fatura_turu = 'Perakende'
                fatura_turu = fatura_turu[:15] if fatura_turu else '-'
                
                # Plasiyer ve bölge için güvenli slice
                plasiyer_raw = s.get('plasiyer') or '-'
                plasiyer = str(plasiyer_raw)[:10] if plasiyer_raw else '-'
                
                bolge_raw = s.get('bolge') or '-'
                bolge = str(bolge_raw)[:10] if bolge_raw else '-'
                
                satis_data.append([
                    tarih_str,
                    plasiyer,
                    s.get('cari_kod') or '-',
                    cari_unvan,
                    s.get('fatura_no') or '-',
                    fatura_turu,
                    f"{float(s.get('net_toplam', 0) or 0):,.2f} ₺",
                    bolge
                ])
            
            # Kolon genişlikleri: Tarih(2.2) + Plasiyer(2) + CariKod(2.5) + CariUnvan(6) + FaturaNo(4) + Tür(2.5) + Net(2.8) + Bölge(2) = 24cm
            satis_table = Table(satis_data, colWidths=[2.2*cm, 2*cm, 2.5*cm, 6*cm, 4*cm, 2.5*cm, 2.8*cm, 2*cm])
            satis_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#27ae60')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('ALIGN', (0, 1), (0, -1), 'CENTER'),  # Tarih
                ('ALIGN', (1, 1), (1, -1), 'LEFT'),    # Plasiyer
                ('ALIGN', (2, 1), (3, -1), 'LEFT'),    # Cari Kod, Cari Ünvan
                ('ALIGN', (4, 1), (5, -1), 'LEFT'),    # Fatura No, Tür
                ('ALIGN', (6, 1), (6, -1), 'RIGHT'),   # Net Tutar
                ('ALIGN', (7, 1), (7, -1), 'CENTER'),  # Bölge
                ('FONTNAME', (0, 0), (-1, 0), 'DejaVuSans-Bold'),
                ('FONTNAME', (0, 1), (-1, -1), 'DejaVuSans'),
                ('FONTSIZE', (0, 0), (-1, 0), 8),
                ('FONTSIZE', (0, 1), (-1, -1), 7),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 2),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
                ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#27ae60')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#ecf0f1')]),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            story.append(satis_table)
        
        # PDF oluştur
        doc.build(story)
        buffer.seek(0)
        
        # Response oluştur
        response = HttpResponse(buffer.read(), content_type='application/pdf')
        filename = f'muhasebe_gunluk_rapor_{rapor_tarihi.replace("-", "")}_{datetime.now().strftime("%H%M")}.pdf'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Exception as e:
        logger.error(f"Muhasebe günlük rapor PDF export hatası: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return HttpResponse(f"PDF oluşturulurken hata oluştu: {str(e)}", status=500)


def generate_klasik_tahsilat_pdf(baslangic_tarihi, bitis_tarihi, plasiyer_listesi, plasiyer_verileri, 
                                  toplam_verileri, banka_listesi, banka_verileri, banka_toplam_verileri,
                                  nakit_teslim_edilmedi_toplam, nakit_teslim_edilmedi_by_user):
    """Klasik Tahsilat Raporu PDF oluşturma fonksiyonu"""
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import cm, mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT
        
        register_pdf_fonts()

        buffer = io.BytesIO()
        # Yatay (landscape) A4 sayfa - tablolar için daha geniş alan
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), 
                               rightMargin=1*cm, leftMargin=1*cm,
                               topMargin=1*cm, bottomMargin=1*cm)
        
        story = []
        styles = getSampleStyleSheet()
        for style in styles.byName.values():
            style.fontName = 'DejaVuSans'

        # Başlık stili
        title_style = styles['Heading1']
        title_style.fontName = 'DejaVuSans-Bold'
        title_style.alignment = TA_CENTER
        styles['Heading2'].fontName = 'DejaVuSans-Bold'
        
        # Başlık
        story.append(Paragraph("KLASİK TAHSİLAT RAPORU", title_style))
        if baslangic_tarihi == bitis_tarihi:
            tarih_str = datetime.strptime(baslangic_tarihi, '%Y-%m-%d').strftime('%d.%m.%Y')
            story.append(Paragraph(f"<b>Tarih:</b> {tarih_str}", styles['Normal']))
        else:
            baslangic_str = datetime.strptime(baslangic_tarihi, '%Y-%m-%d').strftime('%d.%m.%Y')
            bitis_str = datetime.strptime(bitis_tarihi, '%Y-%m-%d').strftime('%d.%m.%Y')
            story.append(Paragraph(f"<b>Tarih Aralığı:</b> {baslangic_str} - {bitis_str}", styles['Normal']))
        story.append(Spacer(1, 20))
        
        # Plasiyer Bazında Tahsilat Tablosu
        story.append(Paragraph("<b>Plasiyer Bazında Tahsilat</b>", styles['Heading2']))
        story.append(Spacer(1, 10))
        
        plasiyer_data = [['PLS/THSLT', 'NAKİT', 'HAVALE', 'KREDİ KARTI', 'ÇEK', 'SENET', 'TOPLAM']]
        for plasiyer in plasiyer_listesi:
            plasiyer_data.append([
                plasiyer,
                f"{float(plasiyer_verileri[plasiyer].get('Nakit Tahsilat', 0)):,.2f} ₺",
                f"{float(plasiyer_verileri[plasiyer].get('Gelen Havale', 0)):,.2f} ₺",
                f"{float(plasiyer_verileri[plasiyer].get('Kredi Kartı Fişi', 0)):,.2f} ₺",
                f"{float(plasiyer_verileri[plasiyer].get('Çek Girişi', 0)):,.2f} ₺",
                f"{float(plasiyer_verileri[plasiyer].get('Senet Girişi', 0)):,.2f} ₺",
                f"{float(plasiyer_verileri[plasiyer].get('TOPLAM', 0)):,.2f} ₺"
            ])
        
        # Toplam satırı
        plasiyer_data.append([
            'TOPLAM',
            f"{float(toplam_verileri.get('Nakit Tahsilat', 0)):,.2f} ₺",
            f"{float(toplam_verileri.get('Gelen Havale', 0)):,.2f} ₺",
            f"{float(toplam_verileri.get('Kredi Kartı Fişi', 0)):,.2f} ₺",
            f"{float(toplam_verileri.get('Çek Girişi', 0)):,.2f} ₺",
            f"{float(toplam_verileri.get('Senet Girişi', 0)):,.2f} ₺",
            f"{float(toplam_verileri.get('TOPLAM', 0)):,.2f} ₺"
        ])
        
        # Landscape A4 = 29.7cm genişlik, 2cm margin = 27.7cm kullanılabilir alan
        plasiyer_table = Table(plasiyer_data, colWidths=[3.5*cm, 3.5*cm, 3.5*cm, 3.5*cm, 3.5*cm, 3.5*cm, 3.5*cm])
        plasiyer_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#007bff')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#ffc107')),
            ('TEXTCOLOR', (0, -1), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),  # Plasiyer adı
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),  # Tutarlar
            ('FONTNAME', (0, 0), (-1, 0), 'DejaVuSans-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'DejaVuSans-Bold'),
            ('FONTNAME', (0, 1), (-1, -2), 'DejaVuSans'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
            ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#007bff')),
            ('LINEABOVE', (0, -1), (-1, -1), 1.5, colors.HexColor('#ffc107')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(plasiyer_table)
        story.append(Spacer(1, 20))
        
        # Nakit Teslim Edilmedi
        if nakit_teslim_edilmedi_toplam > 0:
            story.append(Paragraph(f"<b>Nakit Teslim Edilmedi Toplamı:</b> {float(nakit_teslim_edilmedi_toplam):,.2f} ₺", styles['Normal']))
            story.append(Spacer(1, 10))
            
            if nakit_teslim_edilmedi_by_user:
                nakit_data = [['Kullanıcı', 'Nakit Teslim Edilmedi']]
                for user_data in nakit_teslim_edilmedi_by_user:
                    nakit_data.append([
                        user_data.get('kullanici', '-'),
                        f"{float(user_data.get('toplam', 0)):,.2f} ₺"
                    ])
                
                nakit_table = Table(nakit_data, colWidths=[6*cm, 6*cm])
                nakit_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dc3545')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                    ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'DejaVuSans-Bold'),
                    ('FONTNAME', (0, 1), (-1, -1), 'DejaVuSans'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('FONTSIZE', (0, 1), (-1, -1), 8),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('LEFTPADDING', (0, 0), (-1, -1), 6),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
                    ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#dc3545')),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ]))
                story.append(nakit_table)
                story.append(Spacer(1, 20))
        
        # Banka Bazında Tahsilat Tablosu
        if banka_listesi:
            story.append(PageBreak())
            story.append(Paragraph("<b>Banka Bazında Tahsilat</b>", styles['Heading2']))
            story.append(Spacer(1, 10))
            
            banka_data = [['BANKA', 'KREDİ KARTI', 'HAVALE', 'TOPLAM']]
            for banka in banka_listesi:
                banka_data.append([
                    banka,
                    f"{float(banka_verileri[banka].get('Kredi Kartı Fişi', 0)):,.2f} ₺",
                    f"{float(banka_verileri[banka].get('Gelen Havale', 0)):,.2f} ₺",
                    f"{float(banka_verileri[banka].get('TOPLAM', 0)):,.2f} ₺"
                ])
            
            # Toplam satırı
            banka_data.append([
                'TOPLAM',
                f"{float(banka_toplam_verileri.get('Kredi Kartı Fişi', 0)):,.2f} ₺",
                f"{float(banka_toplam_verileri.get('Gelen Havale', 0)):,.2f} ₺",
                f"{float(banka_toplam_verileri.get('TOPLAM', 0)):,.2f} ₺"
            ])
            
            banka_table = Table(banka_data, colWidths=[6*cm, 6*cm, 6*cm, 6*cm])
            banka_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#28a745')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#ffc107')),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.black),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),  # Banka adı
                ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),  # Tutarlar
                ('FONTNAME', (0, 0), (-1, 0), 'DejaVuSans-Bold'),
                ('FONTNAME', (0, -1), (-1, -1), 'DejaVuSans-Bold'),
                ('FONTNAME', (0, 1), (-1, -2), 'DejaVuSans'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
                ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#28a745')),
                ('LINEABOVE', (0, -1), (-1, -1), 1.5, colors.HexColor('#ffc107')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            story.append(banka_table)
        
        # PDF oluştur
        doc.build(story)
        buffer.seek(0)
        
        # Response oluştur
        response = HttpResponse(buffer.read(), content_type='application/pdf')
        tarih_str = baslangic_tarihi.replace("-", "")
        if baslangic_tarihi != bitis_tarihi:
            tarih_str = f"{baslangic_tarihi.replace('-', '')}_{bitis_tarihi.replace('-', '')}"
        filename = f'klasik_tahsilat_raporu_{tarih_str}_{datetime.now().strftime("%H%M")}.pdf'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Exception as e:
        logger.error(f"Klasik tahsilat raporu PDF export hatası: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return HttpResponse(f"PDF oluşturulurken hata oluştu: {str(e)}", status=500)


def generate_klasik_tahsilat_excel(baslangic_tarihi, bitis_tarihi, plasiyer_listesi, plasiyer_verileri,
                                   toplam_verileri, banka_listesi, banka_verileri, banka_toplam_verileri,
                                   nakit_teslim_edilmedi_toplam, nakit_teslim_edilmedi_by_user):
    """Klasik Tahsilat Raporu Excel oluşturma fonksiyonu"""
    try:
        import pandas as pd
        from io import BytesIO
        
        output = BytesIO()
        
        # 1. Plasiyer Verileri
        plasiyer_rows = []
        for plasiyer in plasiyer_listesi:
            data = plasiyer_verileri[plasiyer]
            plasiyer_rows.append({
                'PLS/THSLT': plasiyer,
                'NAKİT': data.get('Nakit Tahsilat', 0),
                'HAVALE': data.get('Gelen Havale', 0),
                'KREDİ KARTI': data.get('Kredi Kartı Fişi', 0),
                'ÇEK': data.get('Çek Girişi', 0),
                'SENET': data.get('Senet Girişi', 0),
                'TOPLAM': data.get('TOPLAM', 0)
            })
        
        # Genel Toplam satırı
        if plasiyer_listesi:
            plasiyer_rows.append({
                'PLS/THSLT': 'GENEL TOPLAM',
                'NAKİT': toplam_verileri.get('Nakit Tahsilat', 0),
                'HAVALE': toplam_verileri.get('Gelen Havale', 0),
                'KREDİ KARTI': toplam_verileri.get('Kredi Kartı Fişi', 0),
                'ÇEK': toplam_verileri.get('Çek Girişi', 0),
                'SENET': toplam_verileri.get('Senet Girişi', 0),
                'TOPLAM': toplam_verileri.get('TOPLAM', 0)
            })
        
        df_plasiyer = pd.DataFrame(plasiyer_rows)
        
        # 2. Banka Verileri
        banka_rows = []
        for banka in banka_listesi:
            data = banka_verileri[banka]
            banka_rows.append({
                'BANKA': banka,
                'KREDİ KARTI': data.get('Kredi Kartı Fişi', 0),
                'HAVALE': data.get('Gelen Havale', 0),
                'TOPLAM': data.get('TOPLAM', 0)
            })
        
        # Banka Genel Toplam
        if banka_listesi:
            banka_rows.append({
                'BANKA': 'GENEL TOPLAM',
                'KREDİ KARTI': banka_toplam_verileri.get('Kredi Kartı Fişi', 0),
                'HAVALE': banka_toplam_verileri.get('Gelen Havale', 0),
                'TOPLAM': banka_toplam_verileri.get('TOPLAM', 0)
            })
        
        df_banka = pd.DataFrame(banka_rows)
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_plasiyer.to_excel(writer, sheet_name='Plasiyer Bazında', index=False)
            df_banka.to_excel(writer, sheet_name='Banka Bazında', index=False)
            
            # Sütun genişliklerini ayarla
            for sheet_name in writer.sheets:
                ws = writer.sheets[sheet_name]
                for col in ws.columns:
                    max_length = 0
                    column = col[0].column_letter
                    for cell in col:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    ws.column_dimensions[column].width = max_length + 2

        output.seek(0)
        
        # Response oluştur
        tarih_str = baslangic_tarihi.replace("-", "")
        if baslangic_tarihi != bitis_tarihi:
            tarih_str = f"{baslangic_tarihi.replace('-', '')}_{bitis_tarihi.replace('-', '')}"
            
        filename = f'klasik_tahsilat_raporu_{tarih_str}_{datetime.now().strftime("%H%M")}.xlsx'
        
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Exception as e:
        logger.error(f"Klasik tahsilat raporu Excel export hatası: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return HttpResponse(f"Excel oluşturulurken hata oluştu: {str(e)}", status=500)


def _cek_senetler_resolve_main_tab(request):
    """Çek/senet sayfası ana sekme: ?tab= veya ?sekme= (proxy / yazım farkları için ikisi de okunur)."""
    import unicodedata
    for key in ('tab', 'sekme'):
        raw = request.GET.get(key)
        if raw is None or str(raw).strip() == '':
            continue
        s = unicodedata.normalize('NFKC', str(raw).strip()).lower()
        s = s.replace('\u0131', 'i')  # Türkçe dotless ı → i (yıllık → yillik)
        if s == 'yillik':
            return 'yillik'
    return 'liste'


@login_required
def cek_senetler(request):
    """Çek ve Senetler Takip Sayfası"""
    from .models import CekSenet
    from django.core.paginator import Paginator
    from django.db.models import Q, Sum, Count
    from datetime import datetime, date
    
    # Filtreleme parametreleri
    odeme_turu = request.GET.get('odeme_turu', '')
    tip = request.GET.get('tip', '')
    cari_kod = request.GET.get('cari_kod', '')
    cari_unvan = request.GET.get('cari_unvan', '')
    vade_durumu = request.GET.get('vade_durumu', '')
    banka_adi = request.GET.get('banka_adi', '')
    
    # Ay filtreleri (year-month destekli multi-select)
    selected_months = request.GET.getlist('ay')

    # Yıllık sekme: tab/sekme=yillik iken boş veya geçersiz ozet_yil → düzgün URL'ye yönlendir
    if _cek_senetler_resolve_main_tab(request) == 'yillik':
        oz_raw = request.GET.get('ozet_yil', None)
        oz_s = (oz_raw if oz_raw is not None else '').strip()
        _y_canon = date.today().year
        _oz_ok = False
        if oz_s:
            try:
                _y_canon = int(oz_s)
                _oz_ok = True
            except (ValueError, TypeError):
                pass
        if not _oz_ok:
            from django.http import HttpResponseRedirect
            from django.urls import reverse
            from urllib.parse import urlencode
            return HttpResponseRedirect(
                f"{reverse('tahsilat:cek_senetler')}?{urlencode({'tab': 'yillik', 'sekme': 'yillik', 'ozet_yil': str(_y_canon)})}"
            )
    
    # Veri çekme
    queryset = CekSenet.objects.all()
    
    # Filtreleme
    if odeme_turu:
        queryset = queryset.filter(odeme_turu=odeme_turu)
    if tip:
        queryset = queryset.filter(tip=tip)

    durum_param = request.GET.get('durum', None)
    if durum_param is None:
        # Sayfa ilk açıldığında (parametre yoksa) sadece BEKLEMEDE olanları göster
        queryset = queryset.filter(durum='beklemede')
        durum = '' # Template'de "Tümü" seçili görünmesi için
    else:
        durum = durum_param
        if durum:
            queryset = queryset.filter(durum=durum)
            
    if cari_kod:
        queryset = queryset.filter(cari_kod__icontains=cari_kod)
    if cari_unvan:
        queryset = queryset.filter(cari_unvan__icontains=cari_unvan)
    if banka_adi:
        queryset = queryset.filter(banka_adi__icontains=banka_adi)
    
    # Ay filtreleri (vade_tarihi'nde yıl-ay eşlemesi)
    # Mevcut veriye göre hangi yıl-ay kombinasyonlarının gösterileceğini çıkar
    # NOT: available_months_map hesaplaması için filtrelerden ÖNCE tüm kayıtlara bakılmalı
    # Böylece kullanıcı yeni eklenen kayıtların aylarını da dropdown'da görebilir
    available_months_map = {}
    # Filtrelerden ÖNCE tüm kayıtlardan (sadece durum='beklemede' olanlar) ay bilgilerini al
    # Sayfa ilk açıldığında sadece beklemede olanlar gösterildiği için, dropdown'da da sadece onların ayları görünsün
    base_queryset = CekSenet.objects.all().filter(durum='beklemede')
    availability = base_queryset.values('vade_tarihi__year', 'vade_tarihi__month').annotate(count=Count('id'))
    for row in availability:
        y = row['vade_tarihi__year']
        m = row['vade_tarihi__month']
        if y and m:
            available_months_map.setdefault(y, set()).add(m)

    if selected_months:
        try:
            month_conditions = Q()
            for value in selected_months:
                if not value:
                    continue
                if '-' in value:
                    parts = value.split('-', 1)
                    if len(parts) == 2:
                        year_part, month_part = parts
                        try:
                            year_val = int(year_part)
                            month_val = int(month_part)
                            if 1 <= month_val <= 12:
                                month_conditions |= (Q(vade_tarihi__year=year_val) & Q(vade_tarihi__month=month_val))
                        except (ValueError, TypeError):
                            continue
                elif value.isdigit():
                    # Geriye dönük uyumluluk: yalnızca ay verilirse yıl filtrelemesi yapılmaz
                    month_val = int(value)
                    if 1 <= month_val <= 12:
                        month_conditions |= Q(vade_tarihi__month=month_val)

            if month_conditions:
                queryset = queryset.filter(month_conditions)
        except Exception:
            pass
    
    # Vade durumu filtreleme
    if vade_durumu:
        from datetime import timedelta
        today = date.today()
        if vade_durumu == 'vadesi_gecti':
            queryset = queryset.filter(vade_tarihi__lt=today, durum__in=['beklemede', 'onaylandi'])
        elif vade_durumu == 'vade_bugun':
            queryset = queryset.filter(vade_tarihi=today)
        elif vade_durumu == 'vade_yarin':
            tomorrow = today + timedelta(days=1)
            queryset = queryset.filter(vade_tarihi=tomorrow)
        elif vade_durumu == 'bu_hafta':
            end_of_week = today + timedelta(days=6)
            queryset = queryset.filter(vade_tarihi__gte=today, vade_tarihi__lte=end_of_week)
        elif vade_durumu == 'bu_ay':
            import calendar as _cal
            _last = _cal.monthrange(today.year, today.month)[1]
            end_of_month = date(today.year, today.month, _last)
            queryset = queryset.filter(vade_tarihi__gte=today, vade_tarihi__lte=end_of_month)
        elif vade_durumu == 'gelecek_ay':
            if today.month == 12:
                next_month_start = date(today.year + 1, 1, 1)
                next_month_end = date(today.year + 1, 2, 1) - timedelta(days=1)
            else:
                next_month_start = date(today.year, today.month + 1, 1)
                next_month_end = date(today.year, today.month + 2, 1) - timedelta(days=1)
            queryset = queryset.filter(vade_tarihi__gte=next_month_start, vade_tarihi__lte=next_month_end)
    
    # Sıralama: vade tarihi (en yakın vade önce), eşitlikte oluşturma
    queryset = queryset.order_by('vade_tarihi', '-olusturma_tarihi')
    
    # Tüm kayıtları göster (sayfalama kaldırıldı)
    page_obj = queryset
    
    # Banka listesini dinamik olarak al (tüm kayıtlardan)
    banka_listesi = sorted(set(CekSenet.objects.exclude(banka_adi__isnull=True).exclude(banka_adi='').values_list('banka_adi', flat=True).distinct()))
    
    # Toplam tutar hesaplama
    total_tutar = queryset.aggregate(total=Sum('tutar'))['total'] or 0
    
    # Gelen ve Giden ödemeler hesaplama
    gelen_odemeler = queryset.filter(tip='gelen').aggregate(total=Sum('tutar'))['total'] or 0
    giden_odemeler = queryset.filter(tip='giden').aggregate(total=Sum('tutar'))['total'] or 0
    gelen_adet = queryset.filter(tip='gelen').count()
    giden_adet = queryset.filter(tip='giden').count()

    # Ödenmesi gereken toplam tutar (Giden - Gelen)
    odenmesi_gereken_tutar = giden_odemeler - gelen_odemeler
    
    # İstatistikler
    stats = {
        'toplam_tutar': queryset.aggregate(total=Sum('tutar'))['total'] or 0,
        'beklemede_tutar': queryset.filter(durum='beklemede').aggregate(total=Sum('tutar'))['total'] or 0,
        'odendi_tutar': queryset.filter(durum='odendi').aggregate(total=Sum('tutar'))['total'] or 0,
        'vadesi_gecti_tutar': queryset.filter(vade_tarihi__lt=date.today(), durum__in=['beklemede']).aggregate(total=Sum('tutar'))['total'] or 0,
        'gelen_odemeler': gelen_odemeler,
        'giden_odemeler': giden_odemeler,
        'odenmesi_gereken_tutar': odenmesi_gereken_tutar,
    }
    
    # Ödeme Türü'ne göre istatistikler
    odeme_turu_stats = {}
    for odeme_choice in CekSenet.ODEME_TURU_CHOICES:
        odeme_turu_stats[odeme_choice[0]] = {
            'tutar': queryset.filter(odeme_turu=odeme_choice[0]).aggregate(total=Sum('tutar'))['total'] or 0,
            'adet': queryset.filter(odeme_turu=odeme_choice[0]).count(),
            'beklemede': queryset.filter(odeme_turu=odeme_choice[0], durum='beklemede').aggregate(total=Sum('tutar'))['total'] or 0,
            'odendi': queryset.filter(odeme_turu=odeme_choice[0], durum='odendi').aggregate(total=Sum('tutar'))['total'] or 0,
        }
    
    # Çek için Gelen ve Giden ayrı ayrı
    odeme_turu_stats['cek'] = {
        'tutar': queryset.filter(odeme_turu='cek').aggregate(total=Sum('tutar'))['total'] or 0,
        'adet': queryset.filter(odeme_turu='cek').count(),
        'gelen_tutar': queryset.filter(odeme_turu='cek', tip='gelen').aggregate(total=Sum('tutar'))['total'] or 0,
        'gelen_adet': queryset.filter(odeme_turu='cek', tip='gelen').count(),
        'giden_tutar': queryset.filter(odeme_turu='cek', tip='giden').aggregate(total=Sum('tutar'))['total'] or 0,
        'giden_adet': queryset.filter(odeme_turu='cek', tip='giden').count(),
    }
    
    # Senet için Gelen ve Giden ayrı ayrı
    odeme_turu_stats['senet'] = {
        'tutar': queryset.filter(odeme_turu='senet').aggregate(total=Sum('tutar'))['total'] or 0,
        'adet': queryset.filter(odeme_turu='senet').count(),
        'gelen_tutar': queryset.filter(odeme_turu='senet', tip='gelen').aggregate(total=Sum('tutar'))['total'] or 0,
        'gelen_adet': queryset.filter(odeme_turu='senet', tip='gelen').count(),
        'giden_tutar': queryset.filter(odeme_turu='senet', tip='giden').aggregate(total=Sum('tutar'))['total'] or 0,
        'giden_adet': queryset.filter(odeme_turu='senet', tip='giden').count(),
    }
    
    # Kredi için banka bazlı toplamlar
    kredi_banka_stats = {}
    kredi_queryset = queryset.filter(odeme_turu='kredi')
    for banka in banka_listesi:
        banka_kredi = kredi_queryset.filter(banka_adi=banka)
        kredi_banka_stats[banka] = {
            'tutar': banka_kredi.aggregate(total=Sum('tutar'))['total'] or 0,
            'adet': banka_kredi.count(),
        }
    
    # Durum sayıları
    durum_sayilari = {}
    for durum_choice in CekSenet.DURUM_CHOICES:
        durum_sayilari[durum_choice[0]] = queryset.filter(durum=durum_choice[0]).count()

    kayit_sayisi = queryset.count()
    durum_toplam_adet = sum(durum_sayilari.values())

    stats['beklemede_adet'] = durum_sayilari.get('beklemede', 0)
    stats['odendi_adet'] = durum_sayilari.get('odendi', 0)
    stats['ciro_adet'] = durum_sayilari.get('ciro', 0)
    stats['iptal_adet'] = durum_sayilari.get('iptal', 0)
    stats['vadesi_gecti_adet'] = queryset.filter(
        vade_tarihi__lt=date.today(), durum='beklemede'
    ).count()
    stats['ciro_tutar'] = queryset.filter(durum='ciro').aggregate(total=Sum('tutar'))['total'] or 0
    stats['iptal_tutar'] = queryset.filter(durum='iptal').aggregate(total=Sum('tutar'))['total'] or 0

    durum_breakdown = [
        {'code': c[0], 'label': c[1], 'adet': durum_sayilari.get(c[0], 0)}
        for c in CekSenet.DURUM_CHOICES
    ]

    from django.urls import reverse

    def _cek_senetler_url(updates=None, remove_keys=None):
        updates = updates or {}
        remove_keys = remove_keys or ()
        q = request.GET.copy()
        for k in remove_keys:
            q.pop(k, None)
        for k, v in updates.items():
            q[k] = v
        qs = q.urlencode()
        base = reverse('tahsilat:cek_senetler')
        return base + ('?' + qs if qs else '')

    cek_ui_urls = {
        'quick_bugun': _cek_senetler_url({'durum': '', 'vade_durumu': 'vade_bugun'}),
        'quick_yarin': _cek_senetler_url({'durum': '', 'vade_durumu': 'vade_yarin'}),
        'quick_gecmis': _cek_senetler_url({'durum': '', 'vade_durumu': 'vadesi_gecti'}),
        'quick_bu_hafta': _cek_senetler_url({'durum': '', 'vade_durumu': 'bu_hafta'}),
        'quick_bu_ay': _cek_senetler_url({'durum': '', 'vade_durumu': 'bu_ay'}),
        'quick_tum_durum': _cek_senetler_url({'durum': '', 'vade_durumu': ''}),
        'home_default': reverse('tahsilat:cek_senetler'),
    }
    
    # Ödeme türü sayıları
    odeme_turu_sayilari = {}
    for odeme_choice in CekSenet.ODEME_TURU_CHOICES:
        odeme_turu_sayilari[odeme_choice[0]] = queryset.filter(odeme_turu=odeme_choice[0]).count()

    odeme_breakdown = []
    for c in CekSenet.ODEME_TURU_CHOICES:
        key = c[0]
        st = odeme_turu_stats.get(key) or {}
        odeme_breakdown.append({
            'code': key,
            'label': c[1],
            'adet': odeme_turu_sayilari.get(key, 0),
            'tutar': st.get('tutar', 0) if isinstance(st, dict) else 0,
        })

    # Takvim verileri için aylık özet
    from datetime import datetime, timedelta
    import calendar

    def _safe_int_qs(qs, key, default):
        v = qs.get(key)
        if v is None or str(v).strip() == '':
            return default
        try:
            return int(str(v).strip())
        except (ValueError, TypeError):
            return default

    _now = datetime.now()
    year = _safe_int_qs(request.GET, 'year', _now.year)
    month = _safe_int_qs(request.GET, 'month', _now.month)
    if month < 1 or month > 12:
        month = _now.month
    if year < 1970 or year > 2100:
        year = _now.year
    current_year = year

    # Takvim verileri
    calendar_data = {}

    # Ayın tüm günleri için veri toplama
    try:
        days_in_month = calendar.monthrange(year, month)[1]
    except (ValueError, TypeError):
        year, month = _now.year, _now.month
        days_in_month = calendar.monthrange(year, month)[1]
    
    for day in range(1, days_in_month + 1):
        date_str = f"{year}-{month:02d}-{day:02d}"
        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        
        # Bu tarihteki vade tarihi olan kayıtlar
        day_records = queryset.filter(vade_tarihi=date_obj)
        
        # Günlük özet
        day_total = day_records.aggregate(total=Sum('tutar'))['total'] or 0
        day_count = day_records.count()
        
        # Ödeme türüne göre dağılım
        odeme_turu_breakdown = {}
        for choice in CekSenet.ODEME_TURU_CHOICES:
            count = day_records.filter(odeme_turu=choice[0]).count()
            if count > 0:
                odeme_turu_breakdown[choice[0]] = count
        
        calendar_data[day] = {
            'date': date_obj,
            'total': day_total,
            'count': day_count,
            'records': day_records,
            'odeme_turu_breakdown': odeme_turu_breakdown
        }
    
    # Takvim başlığı
    month_names = [
        'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
        'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'
    ]
    calendar_title = f"{month_names[month-1]} {year}"
    
    # Yıl-ay seçiminde kullanılacak mevcut yıllar ve aylar
    month_names_list = [
        'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
        'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'
    ]
    available_months = []
    for y in sorted(available_months_map.keys()):
        months_sorted = sorted([m for m in available_months_map[y] if 1 <= m <= 12])
        if months_sorted:
            available_months.append({
                'year': y,
                'months': [{'num': m, 'name': month_names_list[m-1]} for m in months_sorted]
            })

    filter_years = [current_year, current_year + 1]

    # Yıllık özet sekmesi: vade ayına göre gelen / giden / net (iptal hariç, liste filtrelerinden bağımsız)
    from decimal import Decimal as _Dec
    _oz_y = (request.GET.get('ozet_yil') or '').strip()
    try:
        yillik_ozet_yil = int(_oz_y) if _oz_y else date.today().year
    except (ValueError, TypeError):
        yillik_ozet_yil = date.today().year

    y_db = {y for y in CekSenet.objects.values_list('vade_tarihi__year', flat=True) if y}
    yillik_yil_secenekleri = sorted(y_db | {date.today().year, yillik_ozet_yil}, reverse=True)

    yillik_qs = CekSenet.objects.exclude(durum='iptal').filter(vade_tarihi__year=yillik_ozet_yil)
    yillik_ay_satirlari = []
    _yt_gelen = _Dec('0')
    _yt_giden = _Dec('0')
    for _m in range(1, 13):
        _mq = yillik_qs.filter(vade_tarihi__month=_m)
        _gelen = _mq.filter(tip='gelen').aggregate(s=Sum('tutar'))['s'] or _Dec('0')
        _giden = _mq.filter(tip='giden').aggregate(s=Sum('tutar'))['s'] or _Dec('0')
        _net = _giden - _gelen
        _yt_gelen += _gelen
        _yt_giden += _giden
        yillik_ay_satirlari.append({
            'month': _m,
            'month_name': month_names_list[_m - 1],
            'gelen': _gelen,
            'giden': _giden,
            'net': _net,
            'adet': _mq.count(),
        })
    yillik_toplam_gelen = _yt_gelen
    yillik_toplam_giden = _yt_giden
    yillik_toplam_net = _yt_giden - _yt_gelen

    active_main_tab = _cek_senetler_resolve_main_tab(request)

    context = {
        'sayfa_baslik': 'Çek ve Senetler',
        'sayfa_ikon': 'bi-receipt',
        'page_obj': page_obj,
        'kayit_sayisi': kayit_sayisi,
        'durum_toplam_adet': durum_toplam_adet,
        'durum_breakdown': durum_breakdown,
        'odeme_breakdown': odeme_breakdown,
        'cek_ui_urls': cek_ui_urls,
        'stats': stats,
        'odeme_turu_stats': odeme_turu_stats,
        'durum_sayilari': durum_sayilari,
        'odeme_turu_sayilari': odeme_turu_sayilari,
        'odeme_turu_choices': CekSenet.ODEME_TURU_CHOICES,
        'tip_choices': CekSenet.TIP_CHOICES,
        'durum_choices': CekSenet.DURUM_CHOICES,
        'filters': {
            'odeme_turu': odeme_turu,
            'tip': tip,
            'durum': durum,
            'cari_kod': cari_kod,
            'cari_unvan': cari_unvan,
            'vade_durumu': vade_durumu,
            'banka_adi': banka_adi,
            'selected_months': selected_months if selected_months else [],
        },
        'month_names': [
            'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
            'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'
        ],
        'filter_years': filter_years,
        'available_months': available_months,
        'banka_listesi': banka_listesi,
        'kredi_banka_stats': kredi_banka_stats,
        'total_tutar': total_tutar,
        'gelen_odemeler': gelen_odemeler,
        'giden_odemeler': giden_odemeler,
        'gelen_adet': gelen_adet,
        'giden_adet': giden_adet,
        'odenmesi_gereken_tutar': odenmesi_gereken_tutar,
        'today': date.today(),
        'calendar_data': calendar_data,
        'calendar_title': calendar_title,
        'current_year': year,
        'current_month': month,
        'yillik_ozet_yil': yillik_ozet_yil,
        'yillik_yil_secenekleri': yillik_yil_secenekleri,
        'yillik_ay_satirlari': yillik_ay_satirlari,
        'yillik_toplam_gelen': yillik_toplam_gelen,
        'yillik_toplam_giden': yillik_toplam_giden,
        'yillik_toplam_net': yillik_toplam_net,
        'active_main_tab': active_main_tab,
    }
    
    return render(request, 'tahsilat/cek_senetler.html', context)


@login_required
def cek_senet_ekle(request):
    """Yeni Çek/Senet Ekleme"""
    from .models import CekSenet
    from django.contrib import messages
    
    if request.method == 'POST':
        try:
            # Form verilerini al
            form_data = request.POST
            
            # Yeni kayıt oluştur
            cek_senet = CekSenet.objects.create(
                odeme_turu=form_data.get('odeme_turu'),
                tip=form_data.get('tip'),
                durum='beklemede',  # Varsayılan durum beklemede
                tutar=form_data.get('tutar'),
                para_birimi='TRY',  # Sabit TRY olarak ayarla
                islem_tarihi=form_data.get('islem_tarihi'),
                vade_tarihi=form_data.get('vade_tarihi'),
                odeme_tarihi=None,  # Yeni kayıtlarda ödeme tarihi yok
                cari_kod=form_data.get('cari_kod'),
                cari_unvan=form_data.get('cari_unvan'),
                banka_adi=form_data.get('banka_adi', ''),
                cek_senet_no=form_data.get('cek_senet_no', ''),
                banka_sube='',  # Kaldırıldı
                hesap_no='',  # Kaldırıldı
                kredi_turu=form_data.get('kredi_turu', ''),
                faiz_orani=form_data.get('faiz_orani') or None,
                taksit_sayisi=form_data.get('taksit_sayisi') or None,
                aciklama=form_data.get('aciklama', ''),
                notlar='',  # Kaldırıldı
                olusturan=request.user,
            )
            
            messages.success(request, f'{cek_senet.get_odeme_turu_display()} başarıyla eklendi.')
            return redirect('tahsilat:cek_senetler')
            
        except Exception as e:
            messages.error(request, f'Kayıt eklenirken hata oluştu: {str(e)}')
            return redirect('tahsilat:cek_senetler')
    
    return redirect('tahsilat:cek_senetler')


@login_required
def cek_senet_guncelle(request, pk):
    """Çek/Senet Güncelleme"""
    from .models import CekSenet
    from django.contrib import messages
    
    try:
        cek_senet = CekSenet.objects.get(pk=pk)
        
        if request.method == 'POST':
            # Form verilerini al ve güncelle
            form_data = request.POST
            
            cek_senet.odeme_turu = form_data.get('odeme_turu')
            cek_senet.tip = form_data.get('tip')
            # Durum alanı kaldırıldı - varsayılan olarak mevcut durum korunur
            cek_senet.tutar = form_data.get('tutar')
            cek_senet.para_birimi = 'TRY'  # Sabit TRY olarak ayarla
            cek_senet.islem_tarihi = form_data.get('islem_tarihi')
            cek_senet.vade_tarihi = form_data.get('vade_tarihi')
            # Ödeme tarihi sadece durum "ödendi" olduğunda otomatik ayarlanır
            cek_senet.cari_kod = form_data.get('cari_kod')
            cek_senet.cari_unvan = form_data.get('cari_unvan')
            cek_senet.banka_adi = form_data.get('banka_adi', '')
            cek_senet.cek_senet_no = form_data.get('cek_senet_no', '')
            cek_senet.banka_sube = ''  # Kaldırıldı
            cek_senet.hesap_no = ''  # Kaldırıldı
            cek_senet.kredi_turu = form_data.get('kredi_turu', '')
            cek_senet.faiz_orani = form_data.get('faiz_orani') or None
            cek_senet.taksit_sayisi = form_data.get('taksit_sayisi') or None
            cek_senet.aciklama = form_data.get('aciklama', '')
            cek_senet.notlar = ''  # Kaldırıldı
            
            cek_senet.save()
            
            messages.success(request, f'{cek_senet.get_odeme_turu_display()} başarıyla güncellendi.')
            return redirect('tahsilat:cek_senetler')
        
        else:
            # GET isteği - düzenleme formunu göster
            context = {
                'cek_senet': cek_senet,
                'choices': {
                    'odeme_turu': CekSenet.ODEME_TURU_CHOICES,
                    'tip': CekSenet.TIP_CHOICES,
                },
                'banka_listesi': [
                    'AKBANK', 'ALBARAKA', 'DENİZBANK', 'GARANTİ BANKASI', 'HALK BANKASI', 
                    'İŞBANKASI', 'KUVEYTTÜRK', 'QNB BANK', 'ŞEKERBANK', 
                    'TEB', 'VAKIFBANK', 'YAPIKREDİ', 'ZİRAAT BANKASI'
                ]
            }
            return render(request, 'tahsilat/cek_senet_edit_form.html', context)
        
    except CekSenet.DoesNotExist:
        messages.error(request, 'Kayıt bulunamadı.')
        return redirect('tahsilat:cek_senetler')
    except Exception as e:
        messages.error(request, f'Güncelleme sırasında hata oluştu: {str(e)}')
        return redirect('tahsilat:cek_senetler')


@login_required
def cek_senet_sil(request, pk):
    """Çek/Senet Silme"""
    from .models import CekSenet
    from django.contrib import messages
    
    try:
        cek_senet = CekSenet.objects.get(pk=pk)
        odeme_turu = cek_senet.get_odeme_turu_display()
        cek_senet.delete()
        messages.success(request, f'{odeme_turu} başarıyla silindi.')
    except CekSenet.DoesNotExist:
        messages.error(request, 'Kayıt bulunamadı.')
    except Exception as e:
        messages.error(request, f'Silme sırasında hata oluştu: {str(e)}')
    
    return redirect('tahsilat:cek_senetler')


@login_required
def get_cari_listesi_ajax(request):
    """Cari listesi AJAX endpoint"""
    from .mssql_service import MSSQLService
    from django.http import JsonResponse
    
    try:
        search_term = request.GET.get('search', '').strip()

        mssql_service = MSSQLService()
        cari_listesi = mssql_service.get_cari_listesi(search_term if search_term else None)

        # Boş değerleri filtrele
        filtered_list = []
        for cari in cari_listesi:
            if isinstance(cari, dict):
                # Hem eski (cari_kod/cari_unvan) hem yeni (code/definition) anahtarlarını destekle
                c_kod = (cari.get('cari_kod') or cari.get('code') or '').strip()
                c_unvan = (cari.get('cari_unvan') or cari.get('definition') or '').strip()
                title = (cari.get('title') or '')
                if c_kod and c_unvan:
                    filtered_list.append({
                        'code': c_kod,
                        'definition': c_unvan,
                        'title': title
                    })

        # Arama kısa ise listeden ilk 20 kaydı döndür (boş görünmesin)
        limit = 50 if search_term and len(search_term) >= 2 else 20

        return JsonResponse({
            'success': True,
            'data': filtered_list[:limit]
        })
    except Exception as e:
        logger.error(f"Cari listesi AJAX hatası: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': str(e),
            'data': []
        })


@login_required
def download_cek_senetler_template(request):
    """Çek/Senetler Excel şablonunu indirir"""
    import xlsxwriter
    from django.http import HttpResponse
    import io
    from datetime import datetime as dt
    
    # Excel dosyası oluştur
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output)
    worksheet = workbook.add_worksheet('Çek ve Senetler')
    
    # Formatlar
    header_format = workbook.add_format({
        'bold': True,
        'bg_color': '#2c3e50',
        'font_color': 'white',
        'border': 1,
        'align': 'center',
        'valign': 'vcenter',
        'font_size': 12
    })
    
    date_format = workbook.add_format({
        'num_format': 'dd.mm.yyyy',
        'border': 1,
        'align': 'center'
    })
    
    currency_format = workbook.add_format({
        'num_format': '#,##0.00',
        'border': 1,
        'align': 'right'
    })
    
    text_format = workbook.add_format({
        'border': 1,
        'align': 'left'
    })
    
    center_format = workbook.add_format({
        'border': 1,
        'align': 'center'
    })
    
    # Başlık satırı
    headers = [
        'Ödeme Türü*',
        'Tip*',
        'Durum',
        'Tutar*',
        'İşlem Tarihi*',
        'Vade Tarihi*',
        'Cari Kod*',
        'Cari Ünvan*',
        'Banka Adı',
        'Çek/Senet No',
        'Kredi Türü',
        'Faiz Oranı (%)',
        'Taksit Sayısı',
        'Açıklama'
    ]
    
    # Başlıkları yaz
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, header_format)
    
    # Örnek veri satırları (tarihler gerçek Excel tarihi olarak; Durum = CekSenet.DURUM_CHOICES)
    example_data = [
        ['Çek', 'Giden', 'Beklemede', 50000.00, dt(2025, 1, 15), dt(2025, 2, 15), '320.01.001', 'ÖRNEK TEDARİKÇİ A.Ş.', 'AKBANK', '1234567890', '', '', '', 'Örnek çek'],
        ['Senet', 'Gelen', 'Ciro', 75000.00, dt(2025, 1, 16), dt(2025, 3, 16), '120.01.001', 'ÖRNEK MÜŞTERİ LTD.', 'GARANTİ BANKASI', '0987654321', '', '', '', 'Örnek senet'],
        ['Kredi', 'Giden', 'Beklemede', 100000.00, dt(2025, 1, 17), dt(2025, 4, 17), '320.01.002', 'BAŞKA TEDARİKÇİ', '', '', 'Ticari Kredi', 15.50, 12, 'Örnek kredi'],
        ['Kredi Kartı', 'Gelen', 'Ödendi', 25000.00, dt(2025, 1, 18), dt(2025, 1, 18), '120.01.002', 'BAŞKA MÜŞTERİ', '', '', 'Taksitli', 12.00, 6, 'Örnek kredi kartı']
    ]
    
    # Örnek verileri yaz
    for row, data in enumerate(example_data, start=1):
        for col, value in enumerate(data):
            if col in [3]:  # Tutar sütunu
                worksheet.write(row, col, value, currency_format)
            elif col in [4, 5]:  # Tarih sütunları
                worksheet.write(row, col, value, date_format)
            elif col in [11]:  # Faiz oranı
                worksheet.write(row, col, value, center_format)
            elif col in [12]:  # Taksit sayısı
                worksheet.write(row, col, value, center_format)
            else:
                worksheet.write(row, col, value, text_format)
    
    # Sütun genişliklerini ayarla
    column_widths = [15, 10, 12, 15, 15, 15, 15, 30, 20, 15, 15, 12, 12, 25]
    for col, width in enumerate(column_widths):
        worksheet.set_column(col, col, width)
    
    # Notlar ve banka listesi ayrı sayfada: veri sayfasında yalnızca başlık + satırlar kalır (import yanlış satır okumasın)
    help_sheet = workbook.add_worksheet('Yardım')
    help_sheet.set_column(0, 0, 72)
    hr = 0
    help_sheet.write(hr, 0, 'NOTLAR:', header_format)
    hr += 1
    notes = [
        '1. * işaretli alanlar zorunludur.',
        '2. Ödeme Türü: Çek, Senet, Kredi, Kredi Kartı',
        '3. Tip: Gelen, Giden',
        '4. Durum: Beklemede, Ciro, Ödendi, İptal, Vadesi Geçti',
        '5. Tarihler hücrede tarih veya metin (GG.AA.YYYY) olabilir.',
        '6. Tutar pozitif sayı olmalıdır.',
        '7. Cari Kod: 320. ile başlayanlar TEDARİKÇİ, 120. ile başlayanlar MÜŞTERİ',
        '8. Veri yalnızca "Çek ve Senetler" sayfasına yazılmalıdır; bu sayfa bilgi amaçlıdır.',
    ]
    for line in notes:
        help_sheet.write(hr, 0, line, text_format)
        hr += 1
    hr += 1
    help_sheet.write(hr, 0, 'MEVCUT BANKALAR:', header_format)
    hr += 1
    banks = [
        'AKBANK', 'ALBARAKA', 'DENİZBANK', 'GARANTİ BANKASI', 'HALK BANKASI', 'İŞBANKASI',
        'KUVEYTTÜRK', 'QNB BANK', 'ŞEKERBANK', 'TEB', 'VAKIFBANK', 'YAPIKREDİ', 'ZİRAAT BANKASI'
    ]
    for bank in banks:
        help_sheet.write(hr, 0, bank, text_format)
        hr += 1
    
    workbook.close()
    
    # Response oluştur
    output.seek(0)
    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="cek_senetler_sablon.xlsx"'
    
    return response


@login_required
def import_cek_senetler_excel(request):
    """Excel dosyasından çek/senet verilerini import eder"""
    import logging
    logger = logging.getLogger(__name__)
    
    if request.method == 'POST':
        try:
            excel_file = request.FILES.get('excel_file')
            if not excel_file:
                messages.error(request, 'Excel dosyası seçilmedi.')
                return redirect('tahsilat:cek_senetler')
            
            logger.info(f'[EXCEL IMPORT] Dosya yükleniyor: {excel_file.name}, Boyut: {excel_file.size} bytes')
            
            # Excel dosyasını oku
            import pandas as pd
            from decimal import Decimal
            import unicodedata
            from .models import CekSenet
            
            try:
                # Yalnızca ilk sayfa (şablon: veri + ayrı Yardım sayfası; eski şablonda not satırları da atlanır)
                df = pd.read_excel(excel_file, sheet_name=0)
                logger.info(f'[EXCEL IMPORT] Excel okundu. Satır sayısı: {len(df)}, Sütunlar: {list(df.columns)}')
            except Exception as e:
                logger.error(f'[EXCEL IMPORT] Pandas okuma hatası: {str(e)}', exc_info=True)
                messages.error(request, f'Excel dosyası okunamadı. Dosya formatını kontrol ediniz.')
                return redirect('tahsilat:cek_senetler')
            
            def normalize_column_name(col):
                return str(col).strip() if pd.notna(col) else ''
            
            df.columns = [normalize_column_name(col) for col in df.columns]
            logger.info(f'[EXCEL IMPORT] Normalize edilmiş sütunlar: {list(df.columns)}')
            
            def nk(s):
                return unicodedata.normalize('NFKC', str(s).strip().lower())
            
            col_lookup = {nk(c): c for c in df.columns}
            
            def get_col(*labels):
                for lab in labels:
                    key = nk(lab)
                    if key in col_lookup:
                        return col_lookup[key]
                return None
            
            required_columns = [
                'Ödeme Türü*', 'Tip*', 'Tutar*', 'İşlem Tarihi*', 'Vade Tarihi*', 'Cari Kod*', 'Cari Ünvan*'
            ]
            missing_columns = [lab for lab in required_columns if get_col(lab) is None]
            
            if missing_columns:
                logger.warning(f'[EXCEL IMPORT] Eksik sütunlar: {missing_columns}, Mevcut sütunlar: {list(df.columns)}')
                messages.error(request, f'Eksik sütunlar: {", ".join(missing_columns)}. Excel şablonunu kontrol ediniz.')
                return redirect('tahsilat:cek_senetler')
            
            col_odeme = get_col('Ödeme Türü*')
            col_tip = get_col('Tip*')
            col_tutar = get_col('Tutar*')
            col_islem = get_col('İşlem Tarihi*')
            col_vade = get_col('Vade Tarihi*')
            col_cari_kod = get_col('Cari Kod*')
            col_cari_unvan = get_col('Cari Ünvan*')
            col_durum = get_col('Durum')
            col_banka = get_col('Banka Adı')
            col_no = get_col('Çek/Senet No')
            col_kredi_tur = get_col('Kredi Türü')
            col_faiz = get_col('Faiz Oranı (%)')
            col_taksit = get_col('Taksit Sayısı')
            col_aciklama = get_col('Açıklama')
            
            def parse_odeme_turu_cell(cell):
                """Geçerli ödeme türü hücresi -> model kodu; not/ boş satırlar için None."""
                if pd.isna(cell):
                    return None
                s = str(cell).strip()
                if not s or s.lower() == 'nan':
                    return None
                # Açıklama / not satırlarını ele
                if s.upper().startswith('NOTLAR') or s.lstrip().startswith('1.') or s.lstrip().startswith('MEVCUT'):
                    return None
                sl = nk(s).replace(' ', '')
                if sl in ('çek', 'cek'):
                    return 'cek'
                if sl == 'senet':
                    return 'senet'
                if 'kredi' in sl and 'kart' in sl:
                    return 'kredi_karti'
                if sl.startswith('kredi'):
                    return 'kredi'
                return None
            
            valid_durum_kodlari = {c[0] for c in CekSenet.DURUM_CHOICES}
            durum_label_to_code = {
                nk('Beklemede'): 'beklemede',
                nk('Ciro'): 'ciro',
                nk('Ödendi'): 'odendi',
                nk('İptal'): 'iptal',
                nk('Vadesi Geçti'): 'vadesi_gecti',
                nk('Onaylandı'): 'beklemede',
            }
            tip_label_to_code = {
                nk('Gelen'): 'gelen',
                nk('Giden'): 'giden',
            }
            
            success_count = 0
            error_count = 0
            skipped_count = 0
            errors = []
            
            logger.info(f'[EXCEL IMPORT] İşlem başlıyor. Toplam satır sayısı: {len(df)}')
            
            for index, row in df.iterrows():
                try:
                    row_num = index + 2
                    logger.debug(f'[EXCEL IMPORT] İşleniyor: Satır {row_num}')
                    
                    odeme_turu = parse_odeme_turu_cell(row[col_odeme])
                    if odeme_turu is None:
                        skipped_count += 1
                        continue
                    
                    tip_raw = str(row[col_tip]).strip() if pd.notna(row[col_tip]) else ''
                    tip = tip_label_to_code.get(nk(tip_raw))
                    if tip is None and tip_raw:
                        tl = tip_raw.strip().lower()
                        if tl in ('gelen', 'giden'):
                            tip = tl
                    
                    tutar_raw = row[col_tutar]
                    
                    if pd.isna(tutar_raw) or tutar_raw == '':
                        raise ValueError('Tutar boş geçilemez')
                    
                    try:
                        tutar = float(tutar_raw)
                    except (ValueError, TypeError):
                        raise ValueError(f'Geçersiz tutar formatı: {tutar_raw}')
                    
                    if tutar <= 0:
                        raise ValueError(f'Tutar pozitif olmalıdır: {tutar}')
                    
                    try:
                        islem_tarihi_raw = row[col_islem]
                        vade_tarihi_raw = row[col_vade]
                        
                        if pd.isna(islem_tarihi_raw):
                            raise ValueError('İşlem tarihi boş geçilemez')
                        if pd.isna(vade_tarihi_raw):
                            raise ValueError('Vade tarihi boş geçilemez')
                        
                        islem_tarihi = pd.to_datetime(islem_tarihi_raw).date()
                        vade_tarihi = pd.to_datetime(vade_tarihi_raw).date()
                    except ValueError:
                        raise
                    except Exception as e:
                        raise ValueError(f'Tarih formatı hatası: {str(e)}')
                    
                    cari_kod = str(row[col_cari_kod]).strip()
                    cari_unvan = str(row[col_cari_unvan]).strip()
                    
                    if not cari_kod:
                        raise ValueError('Cari kod boş geçilemez')
                    if not cari_unvan:
                        raise ValueError('Cari ünvan boş geçilemez')
                    
                    if col_durum and pd.notna(row[col_durum]):
                        durum_raw = str(row[col_durum]).strip()
                    else:
                        durum_raw = 'Beklemede'
                    durum = durum_label_to_code.get(nk(durum_raw), 'beklemede')
                    if durum not in valid_durum_kodlari:
                        durum = 'beklemede'
                    
                    banka_adi = ''
                    if col_banka and pd.notna(row[col_banka]):
                        banka_adi = str(row[col_banka]).strip()
                    
                    cek_senet_no = ''
                    if col_no and pd.notna(row[col_no]):
                        cek_senet_no = str(row[col_no]).strip()
                    
                    kredi_turu = ''
                    if col_kredi_tur and pd.notna(row[col_kredi_tur]):
                        kredi_turu = str(row[col_kredi_tur]).strip()
                    
                    faiz_orani = None
                    if col_faiz and pd.notna(row[col_faiz]):
                        try:
                            faiz_orani = float(row[col_faiz])
                        except (ValueError, TypeError):
                            raise ValueError(f'Geçersiz faiz oranı formatı: {row[col_faiz]}')
                    
                    taksit_sayisi = None
                    if col_taksit and pd.notna(row[col_taksit]):
                        try:
                            taksit_sayisi_int = int(row[col_taksit])
                            if taksit_sayisi_int < 1:
                                raise ValueError(f'Taksit sayısı en az 1 olmalıdır: {taksit_sayisi_int}')
                            taksit_sayisi = taksit_sayisi_int
                        except (ValueError, TypeError):
                            raise ValueError(f'Geçersiz taksit sayısı formatı: {row[col_taksit]}')
                    
                    aciklama = ''
                    if col_aciklama and pd.notna(row[col_aciklama]):
                        aciklama = str(row[col_aciklama]).strip()
                    
                    if tip not in ('gelen', 'giden'):
                        raise ValueError(
                            f'Geçersiz tip: "{tip_raw}" (beklenen: Gelen, Giden)'
                        )
                    
                    # CekSenet objesi oluştur (transaction içinde)
                    cek_senet = CekSenet.objects.create(
                        odeme_turu=odeme_turu,
                        tip=tip,
                        durum=durum,
                        tutar=Decimal(str(tutar)),
                        para_birimi='TRY',
                        islem_tarihi=islem_tarihi,
                        vade_tarihi=vade_tarihi,
                        odeme_tarihi=None,
                        cari_kod=cari_kod,
                        cari_unvan=cari_unvan,
                        banka_adi=banka_adi,
                        cek_senet_no=cek_senet_no,
                        banka_sube='',
                        hesap_no='',
                        kredi_turu=kredi_turu,
                        faiz_orani=Decimal(str(faiz_orani)) if faiz_orani is not None else None,
                        taksit_sayisi=taksit_sayisi,
                        aciklama=aciklama,
                        notlar='',
                        olusturan=request.user
                    )
                    
                    logger.debug(f'[EXCEL IMPORT] Satır {row_num} başarıyla eklendi: {odeme_turu} {tip} {tutar} {cari_kod}')
                    success_count += 1
                    
                except ValueError as e:
                    error_count += 1
                    error_msg = f'Satır {index + 2}: {str(e)}'
                    errors.append(error_msg)
                    logger.warning(f'[EXCEL IMPORT] {error_msg}')
                except Exception as e:
                    error_count += 1
                    error_msg = f'Satır {index + 2}: {str(e)}'
                    errors.append(error_msg)
                    logger.error(f'[EXCEL IMPORT] {error_msg}', exc_info=True)
            
            logger.info(
                f'[EXCEL IMPORT] İşlem tamamlandı. Başarılı: {success_count}, Hatalı: {error_count}, Atlanan (boş/not): {skipped_count}'
            )
            
            # Sonuç mesajı
            if success_count > 0:
                messages.success(request, f'{success_count} kayıt başarıyla eklendi.')
            
            if error_count > 0:
                # İlk 5 hatayı göster, gerisini logda tut
                display_errors = errors[:5]
                if len(errors) > 5:
                    display_errors.append(f'... ve {len(errors) - 5} hatası daha (loglarda)')
                messages.warning(request, f'{error_count} kayıt eklenemedi. Hatalar: {"; ".join(display_errors)}')
            
            return redirect('tahsilat:cek_senetler')
            
        except Exception as e:
            logger.error(f'[EXCEL IMPORT] Genel hata: {str(e)}', exc_info=True)
            messages.error(request, f'Excel dosyası işlenirken hata oluştu: {str(e)}')
            return redirect('tahsilat:cek_senetler')
    
    return redirect('tahsilat:cek_senetler')


@login_required
def change_cek_senet_status(request):
    """Çek/Senet durum değiştirme AJAX endpoint"""
    from .models import CekSenet
    from django.http import JsonResponse
    import json
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            kayit_id = data.get('id')
            new_status = data.get('status')
            aciklama = data.get('aciklama', '')
            
            if not kayit_id or not new_status:
                return JsonResponse({
                    'success': False,
                    'error': 'Kayıt ID ve durum gerekli'
                })
            
            # Kaydı bul ve durumu güncelle
            cek_senet = CekSenet.objects.get(pk=kayit_id)
            old_status = cek_senet.durum
            cek_senet.durum = new_status
            
            # Eğer açıklama varsa güncelle
            if aciklama:
                cek_senet.aciklama = aciklama
            
            # Ödendi durumuna geçirilirse ödeme tarihini bugün yap
            if new_status == 'odendi' and not cek_senet.odeme_tarihi:
                from datetime import date
                cek_senet.odeme_tarihi = date.today()
                logger.info(f"Ödeme tarihi otomatik ayarlandı: {cek_senet.odeme_tarihi}")
            
            cek_senet.save()
            
            logger.info(f"Çek/Senet durumu değiştirildi: ID={kayit_id}, {old_status} -> {new_status}")
            
            return JsonResponse({
                'success': True,
                'message': f'Durum başarıyla {cek_senet.get_durum_display()} olarak değiştirildi'
            })
            
        except CekSenet.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Kayıt bulunamadı'
            })
        except Exception as e:
            logger.error(f"Durum değiştirme hatası: {e}")
            return JsonResponse({
                'success': False,
                'error': str(e)
            })
    
    return JsonResponse({
        'success': False,
        'error': 'Geçersiz istek'
    })


@login_required
def bulk_update_cek_senet_durum_by_vade(request):
    """Vade tarihine göre toplu durum güncelleme"""
    from django.http import JsonResponse
    import json
    from datetime import date
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            vade_tarihi_str = data.get('vade_tarihi')
            yeni_durum = data.get('durum', 'odendi')
            
            if not vade_tarihi_str:
                return JsonResponse({
                    'success': False,
                    'error': 'Vade tarihi gerekli'
                })
            
            # Tarih formatını kontrol et ve dönüştür
            try:
                if '/' in vade_tarihi_str:
                    # DD/MM/YYYY format
                    gun, ay, yil = map(int, vade_tarihi_str.split('/'))
                    vade_tarihi_limit = date(yil, ay, gun)
                else:
                    # YYYY-MM-DD format
                    from datetime import datetime
                    vade_tarihi_limit = datetime.strptime(vade_tarihi_str, '%Y-%m-%d').date()
            except Exception as e:
                return JsonResponse({
                    'success': False,
                    'error': f'Geçersiz tarih formatı: {str(e)}'
                })
            
            # Geçerli durumları kontrol et
            gecerli_durumlar = ['beklemede', 'onaylandi', 'ciro', 'odendi', 'iptal']
            if yeni_durum not in gecerli_durumlar:
                return JsonResponse({
                    'success': False,
                    'error': f'Geçersiz durum. Geçerli durumlar: {", ".join(gecerli_durumlar)}'
                })
            
            # Vade tarihi belirtilen tarihten önceki kayıtları bul
            kayitlar = CekSenet.objects.filter(
                vade_tarihi__lt=vade_tarihi_limit
            )
            
            # Sadece mevcut durumları güncelle (isteğe göre)
            if yeni_durum == 'odendi':
                # Ödendi yaparken ödeme tarihini de güncelle
                updated_count = kayitlar.update(
                    durum='odendi',
                    odeme_tarihi=date.today()
                )
            else:
                updated_count = kayitlar.update(durum=yeni_durum)
            
            logger.info(f"[BULK UPDATE] Vade tarihi {vade_tarihi_str}'den önceki {kayitlar.count()} kayıt {yeni_durum} durumuna güncellendi")
            
            return JsonResponse({
                'success': True,
                'message': f'{updated_count} kayıt başarıyla {yeni_durum.upper()} durumuna güncellendi',
                'updated_count': updated_count
            })
            
        except Exception as e:
            logger.error(f"Toplu durum güncelleme hatası: {e}", exc_info=True)
            return JsonResponse({
                'success': False,
                'error': str(e)
            })
    
    return JsonResponse({
        'success': False,
        'error': 'Geçersiz istek'
    })


def export_stok_detayli_analiz_excel(request):
    """Stok Detaylı Analiz Excel Export"""
    try:
        import io
        import xlsxwriter
        from datetime import datetime
        
        # MSSQL servisinden veri al
        mssql_service = MSSQLService()
        
        # Veri çek
        stok_data = mssql_service.get_stok_detayli_analiz(
            malzeme_kodu='',
            aciklamasi='',
            marka='',
            malzeme_turu='',
            mevcut_stok='',
            page=1,
            page_size=100
        )
        
        # Excel dosyası oluştur
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output)
        worksheet = workbook.add_worksheet('Stok Detaylı Analiz')
        
        # Stil tanımlamaları
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#f8f9fa',
            'border': 1,
            'align': 'center',
            'valign': 'vcenter'
        })
        
        # Başlık satırları
        worksheet.merge_range('A1:H1', 'STOK DETAYLI ANALİZ RAPORU', header_format)
        
        # Tarih bilgisi
        tarih_str = datetime.now().strftime('%d.%m.%Y %H:%M')
        worksheet.merge_range('A2:H2', f'Rapor Tarihi: {tarih_str}', workbook.add_format({'align': 'center'}))
        
        # Sütun başlıkları
        headers = [
            'Malzeme Kodu',
            'Açıklaması',
            'Marka',
            'Malzeme Türü',
            'Mevcut Stok',
            'Birim Fiyat',
            'Toplam Değer',
            'Son Giriş Tarihi'
        ]
        
        # Başlık satırını yaz
        for col, header in enumerate(headers):
            worksheet.write(3, col, header, header_format)
        
        # Veri satırlarını yaz
        row = 4
        for item in stok_data:
            worksheet.write(row, 0, item.get('malzeme_kodu', ''))
            worksheet.write(row, 1, item.get('aciklamasi', ''))
            worksheet.write(row, 2, item.get('marka', ''))
            worksheet.write(row, 3, item.get('malzeme_turu', ''))
            worksheet.write(row, 4, item.get('mevcut_stok', 0))
            worksheet.write(row, 5, item.get('birim_fiyat', 0))
            worksheet.write(row, 6, item.get('toplam_deger', 0))
            worksheet.write(row, 7, item.get('son_giris_tarihi', ''))
            row += 1
        
        # Sütun genişliklerini ayarla
        worksheet.set_column('A:A', 15)  # Malzeme Kodu
        worksheet.set_column('B:B', 30)  # Açıklaması
        worksheet.set_column('C:C', 15)  # Marka
        worksheet.set_column('D:D', 20)  # Malzeme Türü
        worksheet.set_column('E:E', 12)  # Mevcut Stok
        worksheet.set_column('F:F', 15)  # Birim Fiyat
        worksheet.set_column('G:G', 15)  # Toplam Değer
        worksheet.set_column('H:H', 18)  # Son Giriş Tarihi
        
        workbook.close()
        output.seek(0)
        
        # Response oluştur
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="stok_detayli_analiz_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
        
        return response
        
    except Exception as e:
        logger.error(f"Excel export hatası: {e}")
        return HttpResponse(f"Excel oluşturulurken hata oluştu: {str(e)}", status=500)


@login_required
def ambar_deger_raporu(request):
    """Ambar Değer Raporu - FIYATANALIZ tablosundan ürün değerlerini hesaplar"""
    # Yetki kontrolü
    from .models import KullaniciYetki
    try:
        yetki = KullaniciYetki.objects.get(
            kullanici=request.user, menu_adi='stok_listesi')
        if not yetki.erisim_izni:
            messages.error(request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
            return redirect('tahsilat:dashboard')
    except KullaniciYetki.DoesNotExist:
        if request.user.username != 'FIRAT':
            messages.error(request, 'Bu sayfaya erişim yetkiniz bulunmamaktadır.')
            return redirect('tahsilat:dashboard')
    
    # Session'dan kullanıcı verilerini al
    user_data = request.session.get('mssql_user_data', {})
    if not user_data:
        user_data = {}
    
    # Filtreleme parametreleri
    malzeme_kodu_filter = request.GET.get('malzeme_kodu', '')
    marka_filter = request.GET.get('marka', '')
    malzeme_turu_filter = request.GET.get('malzeme_turu', '')
    maliyet_filter = request.GET.get('maliyet_filter', '')
    satis_filter = request.GET.get('satis_filter', '')
    
    # Verileri getir
    try:
        rapor_data = mssql_service.get_ambar_deger_raporu(
            malzeme_kodu=malzeme_kodu_filter if malzeme_kodu_filter else None,
            marka=marka_filter if marka_filter else None,
            malzeme_turu=malzeme_turu_filter if malzeme_turu_filter else None,
            maliyet_filter=maliyet_filter if maliyet_filter else None,
            satis_filter=satis_filter if satis_filter else None
        )
        
        context = {
            'user': request.user,
            'user_data': user_data,
            'sayfa_baslik': 'Ambar Değer Raporu',
            'sayfa_ikon': 'bi-calculator',
            'data': rapor_data['data'],
            'ozet': rapor_data['ozet'],
            'tur_gruplu': rapor_data['tur_gruplu'],
            'marka_gruplu': rapor_data['marka_gruplu'],
            'malzeme_kodu_filter': malzeme_kodu_filter,
            'marka_filter': marka_filter,
            'malzeme_turu_filter': malzeme_turu_filter,
            'maliyet_filter': maliyet_filter,
            'satis_filter': satis_filter,
            'malzeme_turleri': sorted(set([item['malzeme_turu'] for item in rapor_data['data'] if item['malzeme_turu']])),
            'markalar': sorted(set([item['marka'] for item in rapor_data['data'] if item['marka']])),
        }
        
        return render(request, 'tahsilat/ambar_deger_raporu.html', context)
        
    except Exception as e:
        logger.error(f"Ambar değer raporu hatası: {e}")
        messages.error(request, f'Rapor yüklenirken hata oluştu: {e}')
        
        # Hata durumunda boş context
        context = {
            'user': request.user,
            'user_data': {},
            'sayfa_baslik': 'Ambar Değer Raporu',
            'sayfa_ikon': 'bi-calculator',
            'data': [],
            'ozet': {
                'toplam_kayit': 0,
                'toplam_maliyet': 0,
                'toplam_satis_degeri': 0,
                'toplam_kar': 0,
                'kar_orani': 0,
            },
            'tur_gruplu': {'maliyet': {}, 'satis': {}},
            'marka_gruplu': {'maliyet': {}, 'satis': {}},
        }
        
        return render(request, 'tahsilat/ambar_deger_raporu.html', context)


@login_required
def kdv_raporu(request):
    from datetime import datetime
    yil = request.GET.get('yil')
    try:
        yil = int(yil) if yil else datetime.now().year
    except Exception:
        yil = datetime.now().year

    giden_query = """
        SELECT MONTH([TARİH]) AS ay, ISNULL(SUM(CAST([TUTAR] AS DECIMAL(18,2))), 0) AS tutar
        FROM [GO3].[dbo].[FATURA]
        WHERE YEAR([TARİH]) = ? AND (TRCODE IN (7,8)) AND (EINVOICE IN (1,2))
        GROUP BY MONTH([TARİH])
    """
    gelen_query = """
        SELECT MONTH([TARİH]) AS ay, ISNULL(SUM(CAST([TUTAR] AS DECIMAL(18,2))), 0) AS tutar
        FROM [GO3].[dbo].[FATURA]
        WHERE YEAR([TARİH]) = ? AND (TRCODE IN (1,3)) AND (EINVOICE IN (1,2))
        GROUP BY MONTH([TARİH])
    """

    try:
        giden_rows = mssql_service.execute_query_safe(giden_query, [yil])
    except Exception:
        giden_rows = []
    try:
        gelen_rows = mssql_service.execute_query_safe(gelen_query, [yil])
    except Exception:
        gelen_rows = []

    giden_map = {int(r.get('ay') or r.get('AY') or list(r.values())[0]): float(r.get('tutar') or r.get('TUTAR') or list(r.values())[1]) for r in giden_rows} if giden_rows else {}
    gelen_map = {int(r.get('ay') or r.get('AY') or list(r.values())[0]): float(r.get('tutar') or r.get('TUTAR') or list(r.values())[1]) for r in gelen_rows} if gelen_rows else {}

    monthly = []
    toplam_giden_kdv = 0.0
    toplam_gelen_kdv = 0.0
    for ay in range(1, 13):
        giden_brut = float(giden_map.get(ay, 0.0))
        gelen_brut = float(gelen_map.get(ay, 0.0))
        giden_kdv = giden_brut / 6.0
        gelen_kdv = gelen_brut / 6.0
        odenecek = giden_kdv - gelen_kdv
        toplam_giden_kdv += giden_kdv
        toplam_gelen_kdv += gelen_kdv
        monthly.append({
            'ay_num': ay,
            'yil': yil,
            'giden_brut': giden_brut,
            'giden_kdv': giden_kdv,
            'gelen_brut': gelen_brut,
            'gelen_kdv': gelen_kdv,
            'odenecek_kdv': odenecek,
        })

    context = {
        'yil': yil,
        'monthly': monthly,
        'toplam_giden_kdv': toplam_giden_kdv,
        'toplam_gelen_kdv': toplam_gelen_kdv,
        'genel_odenecek': (toplam_giden_kdv - toplam_gelen_kdv),
    }
    return render(request, 'tahsilat/kdv_raporu.html', context)


# Minimal stubs for views referenced in urls.py but missing after edits
@login_required
def stok_detayli_analiz(request):
    """Placeholder for stok_detayli_analiz to avoid import errors."""
    return render(request, 'tahsilat/stok_detayli_analiz.html', {})


@login_required
def yonetici(request):
    """Placeholder admin page (originally removed)."""
    return redirect('tahsilat:dashboard')
# Prim oranları (sabit) - plasiyer_prim sayfası için
PLASIYER_PRIM_ORANLAR = {
    'Nakit': 0.01,        # %1
    'Kredi Kartı': 0.01,  # %1
    'Havale': 0.01,       # %1
    'Çek': 0.0075,        # %0.75
    'Senet': 0.005        # %0.50
}

@login_required
def plasiyer_prim_list(request):
    """Plasiyer Prim Hesaplama Sayfası - tüm plasiyerler tek tabloda"""
    from datetime import datetime, date
    import calendar
    from .mssql_service import MSSQLService
    from .models import PlasiyerPrim

    plasiyer_listesi = [
        'ALİ', 'AZİZ', 'CAN', 'EYÜP', 'NECATİ', 'HASAN', 'YİĞİT', 'ATAKAN'
    ]

    current_year = 2026
    current_month = datetime.now().month
    years = [2026]
    months = range(1, 13)

    # Dönem: sadece 2026 yılı seçilebilir
    try:
        year = int(request.GET.get('yil') or current_year)
        month = int(request.GET.get('ay') or current_month)
    except (TypeError, ValueError):
        year, month = current_year, current_month
    year = 2026
    month = max(1, min(12, month))

    start_date = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end_date = date(year, month, last_day)

    mssql_service = MSSQLService()
    plasiyer_data = []

    for plasiyer in plasiyer_listesi:
        try:
            tahsilat_summary = mssql_service.get_plasiyer_prim_summary_from_gunluk(plasiyer, start_date, end_date)
        except Exception:
            tahsilat_summary = {'Nakit': 0.0, 'Kredi Kartı': 0.0, 'Havale': 0.0, 'Çek': 0.0, 'Senet': 0.0}
        hesaplama = []
        toplam_hakedis = 0.0
        tur_sira = ['Nakit', 'Kredi Kartı', 'Havale', 'Çek', 'Senet']
        for tur in tur_sira:
            tutar = tahsilat_summary.get(tur, 0.0) if isinstance(tahsilat_summary, dict) else 0.0
            oran = PLASIYER_PRIM_ORANLAR.get(tur, 0)
            hakedis = float(tutar) * oran
            toplam_hakedis += hakedis
            hesaplama.append({
                'tur': tur,
                'tutar': float(tutar),
                'oran_yuzde': oran * 100,
                'hakedis': round(hakedis, 2)
            })
        try:
            kayit = PlasiyerPrim.objects.get(plasiyer=plasiyer, donem_ay=month, donem_yil=year)
            gecikme_tutari = float(kayit.gecikme_tutari or 0)
        except PlasiyerPrim.DoesNotExist:
            gecikme_tutari = 0.0
        gecikme_kesintisi = round(gecikme_tutari * 0.01, 2)
        net_hakedis = round(toplam_hakedis - gecikme_kesintisi, 2)
        toplam_tutar = sum(h.get('tutar', 0) for h in hesaplama)
        # Örnek tasarım sırası: GECİKME, ÇEK, HAVALE, KREDİ KARTI, NAKİT, SENET, TOPLAM TAHSİLAT
        tur_etiketi = {'Nakit': 'NAKİT', 'Kredi Kartı': 'KREDİ KARTI', 'Havale': 'HAVALE', 'Çek': 'ÇEK', 'Senet': 'SENET'}
        display_order = ['Çek', 'Havale', 'Kredi Kartı', 'Nakit', 'Senet']
        hesaplama_by_tur = {h['tur']: h for h in hesaplama}
        display_rows = [
            {'label': 'GECİKME TUTARI', 'type': 'gecikme_tutar', 'gecikme_tutari': round(gecikme_tutari, 2)},
            {'label': 'GECİKME (Düzenle)', 'type': 'gecikme', 'gecikme_tutari': round(gecikme_tutari, 2), 'gecikme_kesintisi': round(gecikme_kesintisi, 2)},
        ]
        for tur in display_order:
            h = hesaplama_by_tur.get(tur)
            if h:
                display_rows.append({
                    'label': tur_etiketi.get(tur, tur),
                    'type': 'tur',
                    'tur': tur,
                    'tutar': float(h.get('tutar', 0)),
                    'hakedis': float(h.get('hakedis', 0))
                })
        display_rows.append({
            'label': 'TOPLAM',
            'type': 'toplam',
            'tutar': round(toplam_tutar, 2),
            'net_hakedis': round(net_hakedis, 2)
        })

        plasiyer_data.append({
            'plasiyer': plasiyer,
            'hesaplama': hesaplama,
            'display_rows': display_rows,
            'toplam_hakedis': round(toplam_hakedis, 2),
            'gecikme_tutari': round(gecikme_tutari, 2),
            'gecikme_kesintisi': round(gecikme_kesintisi, 2),
            'net_hakedis': round(net_hakedis, 2),
        })

    context = {
        'plasiyer_listesi': plasiyer_listesi,
        'years': years,
        'months': months,
        'current_year': current_year,
        'current_month': current_month,
        'selected_year': year,
        'selected_month': month,
        'plasiyer_data': plasiyer_data,
    }
    return render(request, 'tahsilat/plasiyer_prim.html', context)

@login_required
def plasiyer_prim_hesapla(request):
    """AJAX ile tüm plasiyerler için prim hesaplama"""
    from django.http import JsonResponse
    from .models import PlasiyerPrim
    from .mssql_service import MSSQLService
    from datetime import date
    import calendar
    import json
    import traceback

    plasiyer_listesi = [
        'ALİ', 'AZİZ', 'CAN', 'EYÜP', 'NECATİ', 'HASAN', 'YİĞİT', 'ATAKAN'
    ]

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Geçersiz istek metodu'})

    try:
        data = json.loads(request.body)
        month = int(data.get('month'))
        year = int(data.get('year'))
        if not month or not year:
            return JsonResponse({'success': False, 'error': 'Eksik parametreler (month, year)'})

        start_date = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        end_date = date(year, month, last_day)

        mssql_service = MSSQLService()
        plasiyer_data = []

        tur_sira = ['Nakit', 'Kredi Kartı', 'Havale', 'Çek', 'Senet']
        for plasiyer in plasiyer_listesi:
            tahsilat_summary = mssql_service.get_plasiyer_prim_summary_from_gunluk(plasiyer, start_date, end_date)
            hesaplama = []
            toplam_hakedis = 0.0
            for tur in tur_sira:
                tutar = tahsilat_summary.get(tur, 0.0)
                oran = PLASIYER_PRIM_ORANLAR.get(tur, 0)
                hakedis = float(tutar) * oran
                toplam_hakedis += hakedis
                hesaplama.append({
                    'tur': tur,
                    'tutar': float(tutar),
                    'oran_yuzde': oran * 100,
                    'hakedis': round(hakedis, 2)
                })
            try:
                kayit = PlasiyerPrim.objects.get(plasiyer=plasiyer, donem_ay=month, donem_yil=year)
                gecikme_tutari = float(kayit.gecikme_tutari or 0)
            except PlasiyerPrim.DoesNotExist:
                gecikme_tutari = 0.0
            gecikme_kesintisi = round(gecikme_tutari * 0.01, 2)
            net_hakedis = round(toplam_hakedis - gecikme_kesintisi, 2)
            plasiyer_data.append({
                'plasiyer': plasiyer,
                'hesaplama': hesaplama,
                'toplam_hakedis': round(toplam_hakedis, 2),
                'gecikme_tutari': round(gecikme_tutari, 2),
                'gecikme_kesintisi': gecikme_kesintisi,
                'net_hakedis': net_hakedis,
            })

        return JsonResponse({
            'success': True,
            'data': {
                'month': month,
                'year': year,
                'plasiyer_data': plasiyer_data
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e), 'trace': traceback.format_exc()})

@login_required
def plasiyer_prim_kaydet(request):
    """Hesaplanan prim verilerini toplu kaydet (tüm plasiyerler)."""
    from django.http import JsonResponse
    from .models import PlasiyerPrim
    import json

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Geçersiz istek metodu'})

    try:
        data = json.loads(request.body)
        month = int(data.get('month'))
        year = int(data.get('year'))
        kayitlar = data.get('kayitlar', [])
        if not kayitlar:
            return JsonResponse({'success': False, 'error': 'Kayıt listesi boş'})

        saved = 0
        for item in kayitlar:
            plasiyer = item.get('plasiyer')
            totals = item.get('totals', {})
            gecikme_tutari = float(item.get('gecikme_tutari', 0))
            net_hakedis = float(item.get('net_hakedis', 0))
            if not plasiyer:
                continue
            PlasiyerPrim.objects.update_or_create(
                plasiyer=plasiyer,
                donem_ay=month,
                donem_yil=year,
                defaults={
                    'nakit_toplam': totals.get('Nakit', 0),
                    'kredi_karti_toplam': totals.get('Kredi Kartı', 0),
                    'havale_toplam': totals.get('Havale', 0),
                    'cek_toplam': totals.get('Çek', 0),
                    'senet_toplam': totals.get('Senet', 0),
                    'gecikme_tutari': gecikme_tutari,
                    'toplam_prim': net_hakedis,
                    'olusturan': request.user
                }
            )
            saved += 1

        return JsonResponse({
            'success': True,
            'message': f'{saved} plasiyer prim kaydı başarıyla kaydedildi.'
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})
