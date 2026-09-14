from django.urls import path
from . import views

app_name = 'tahsilat'

urlpatterns = [
    # Authentication
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('check-session/', views.check_session_view, name='check_session'),

    # Ana sayfa
    path('', views.dashboard, name='dashboard'),

    # Genel Görünüm (sadece admin) - dropdown menü
    path('genel-gorunum/', views.genel_gorunum,
         name='genel_gorunum'),  # Ana sayfa (redirect)
    path('genel-gorunum/dashboard/', views.genel_dashboard, name='genel_dashboard'),
    path('genel-gorunum/tahsilatlar/',
         views.genel_tahsilatlar, name='genel_tahsilatlar'),
    path('genel-gorunum/satislar/', views.genel_satislar, name='genel_satislar'),
    path('genel-gorunum/alimlar/', views.genel_alimlar, name='genel_alimlar'),
    path('genel-gorunum/ekstre/', views.genel_ekstre, name='genel_ekstre'),
    path('genel-gorunum/cari-genel-analiz/', views.genel_cari_analiz, name='genel_cari_analiz'),
    path('genel-gorunum/cari-aylik-ozet/', views.genel_cari_aylik_ozet, name='genel_cari_aylik_ozet'),

    # AJAX endpoints
    path('ajax/get-plasiyer-regions/', views.get_plasiyer_regions, name='get_plasiyer_regions'),
    path('ajax/get-all-regions/', views.get_all_regions, name='get_all_regions'),
    path(
        'ajax/search-cari-yeni-tahsilat/',
        views.ajax_search_cari_yeni_tahsilat,
        name='ajax_search_cari_yeni_tahsilat',
    ),

    # Tahsilat işlemleri
    path('yeni-tahsilat/', views.yeni_tahsilat, name='yeni_tahsilat'),
    path('muhasebe/yeni-tahsilat/', views.muhasebe_yeni_tahsilat,
         name='muhasebe_yeni_tahsilat'),
    path('muhasebe/tahsilat-listesi/', views.muhasebe_tahsilat_listesi,
         name='muhasebe_tahsilat_listesi'),
    path('muhasebe/tahsilat-duzenle/<int:tahsilat_id>/', views.muhasebe_tahsilat_duzenle,
         name='muhasebe_tahsilat_duzenle'),
    path('muhasebe/kdv-raporu/', views.kdv_raporu, name='kdv_raporu'),
    path('muhasebe/gunluk-rapor/', views.muhasebe_gunluk_rapor, name='muhasebe_gunluk_rapor'),
    path('update-teslim-durumu/', views.update_teslim_durumu,
         name='update_teslim_durumu'),
    path('update-logo-durumu/', views.update_logo_durumu,
         name='update_logo_durumu'),
    path('csrf-debug/', views.csrf_debug, name='csrf_debug'),

    # Chat URLs
    path('chat/', views.chat, name='chat'),
    path('chat/send/', views.send_message, name='send_message'),
    path('chat/get-messages/', views.get_messages, name='get_messages'),
    path('chat/mark-read/', views.mark_messages_read, name='mark_messages_read'),
    path('chat/unread-counts/', views.get_unread_counts, name='get_unread_counts'),
    path('chat/clear/', views.clear_chat, name='clear_chat'),
    path('chat/update-status/', views.update_user_status,
         name='update_user_status'),
    path('chat/get-statuses/', views.get_user_statuses, name='get_user_statuses'),
    path('chat/get-users/', views.get_chat_users, name='get_chat_users'),
    path('chat/notifications/', views.get_chat_notifications,
         name='get_chat_notifications'),
    path('chat/mark-notification-read/',
         views.mark_notification_read, name='mark_notification_read'),
    path('chat/search/', views.search_messages, name='search_messages'),
    path('chat/history/', views.get_message_history, name='get_message_history'),

    # Tahsilat Raporları menüsü
    path('tahsilat-raporlari/', views.raporlar, name='raporlar'),
    path('tahsilat-raporlari/tahsilatlarim/',
         views.tahsilatlarim, name='tahsilatlarim'),

    # Tahsilat düzenleme özellikleri kaldırılmıştır

    # Tahsilat silme (sadece İŞLENMEDİ durumdakiler)
    path('sil/', views.tahsilat_sil_ajax, name='tahsilat_sil_ajax'),

    # Cari Ekstre
    path('cari-ekstre/', views.cari_ekstre, name='cari_ekstre'),
    # Cari Hareketler
    path('cari-hareketler/<str:cari_kod>/', views.cari_hareketler, name='cari_hareketler'),

    # Satışlarım
    path('satislarim/', views.satislarim, name='satislarim'),
    path('fatura-detay/<int:fatura_id>/',
         views.fatura_detay_ajax, name='fatura_detay_ajax'),

    # Stok Yönetimi
    # MALZEME_STOK tablosu - ana sayfa
    path('stok-yonetimi/', views.stok_yonetimi, name='stok_yonetimi'),
    # MALZEME_STOK tablosu - liste görünümü
    path('stok-listesi/', views.stok_listesi, name='stok_listesi'),
    path('fiyat-analizi/', views.fiyat_analizi,
         name='fiyat_analizi'),  # FIYATANALIZ tablosu
    path('fiyat-analizi/pdf/', views.export_fiyat_analizi_pdf,
         name='fiyat_analizi_pdf_export'),
    # MALZEME_STOK_PERFORMANS tablosu - detaylı analiz
    path('stok-detayli-analiz/', views.stok_detayli_analiz,
         name='stok_detayli_analiz'),
    path('stok-yonetimi/ortalama-maliyet/', views.ortalama_maliyet,
         name='ortalama_maliyet'),
    # Ambar Değer Raporu
    path('ambar-deger-raporu/', views.ambar_deger_raporu,
         name='ambar_deger_raporu'),

    # Cari Yönetimi
    path('cari-bakiyeler/', views.cari_bakiyeler,
         name='cari_bakiyeler'),  # Cari Bakiyeler
    path('cari-analiz/', views.cari_analiz,
         name='cari_analiz'),  # TUMCARIHARETLER tablosu
    path('cari-vade-analizi/', views.cari_vade_analizi,
         name='cari_vade_analizi'),  # Ödeme vade analizi
    path('cari-gecikmeleri/', views.cari_gecikmeleri,
         name='cari_gecikmeleri'),  # Cari geçikmeleri
    path('cari-gecikmeleri/excel/', views.cari_gecikmeleri_excel,
         name='cari_gecikmeleri_excel'),

    # Gider Masraf Yönetimi
    path('gider-masraf/', views.gider_masraf_listesi,
         name='gider_masraf_listesi'),
    path('gider-masraf/ekle/', views.gider_masraf_ekle, name='gider_masraf_ekle'),
    path('gider-masraf/guncelle/<int:gider_id>/',
         views.gider_masraf_guncelle, name='gider_masraf_guncelle'),
    path('gider-masraf/sil/<int:gider_id>/',
         views.gider_masraf_sil, name='gider_masraf_sil'),

    # Perakende Cari Takibi
    path('perakende/', views.perakende, name='perakende'),

    # Klasik Tahsilat Raporu
    path('klasik-tahsilat-raporu/', views.klasik_tahsilat_raporu,
         name='klasik_tahsilat_raporu'),

    # Yetkilendirme
    path('yetkilendirme/', views.yetkilendirme, name='yetkilendirme'),

    # Yönetici Paneli
    # path('yonetici/', views.yonetici, name='yonetici'),  # Silindi - gereksiz sayfa

    # LOGO Aktarım
    path('logo-transfer/', views.logo_transfer, name='logo_transfer'),
    path('logo-transfer/ajax/', views.logo_transfer_ajax,
         name='logo_transfer_ajax'),
    path('toggle-logo-integration/', views.toggle_logo_integration_ajax, name='toggle_logo_integration_ajax'),

    # Banka Listesi AJAX - Temporarily commented out due to missing view function
    path('get-banka-listesi/', views.get_banka_listesi, name='get_banka_listesi'),
    path('get-next-fis-no/', views.get_next_fis_no_ajax, name='get_next_fis_no_ajax'),

    # Plasiyer Performans Export
    path('export/plasiyer-performans-excel/', views.export_plasiyer_performans_excel, 
         name='export_plasiyer_performans_excel'),
    path('export/plasiyer-performans-pdf/', views.export_plasiyer_performans_pdf, 
         name='export_plasiyer_performans_pdf'),

    # Kullanıcı Senkronizasyonu
    path('sync-users/', views.sync_users, name='sync_users'),

    # Çek ve Senetler
    path('cek-senetler/', views.cek_senetler, name='cek_senetler'),
    path('cek-senetler/ekle/', views.cek_senet_ekle, name='cek_senet_ekle'),
    path('cek-senetler/edit/<int:pk>/', views.cek_senet_guncelle, name='cek_senet_guncelle'),
    path('cek-senetler/delete/<int:pk>/', views.cek_senet_sil, name='cek_senet_sil'),
    path('ajax/get-cari-listesi/', views.get_cari_listesi_ajax, name='get_cari_listesi_ajax'),
    path('cek-senetler/download-template/', views.download_cek_senetler_template, name='download_cek_senetler_template'),
    path('cek-senetler/import-excel/', views.import_cek_senetler_excel, name='import_cek_senetler_excel'),
    path('ajax/change-cek-senet-status/', views.change_cek_senet_status, name='change_cek_senet_status'),
    path('ajax/bulk-update-cek-senet-durum/', views.bulk_update_cek_senet_durum_by_vade, name='bulk_update_cek_senet_durum_by_vade'),

    # Plasiyer Prim
    path('plasiyer-prim/', views.plasiyer_prim_list, name='plasiyer_prim_list'),
    path('plasiyer-prim/hesapla/', views.plasiyer_prim_hesapla, name='plasiyer_prim_hesapla'),
    path('plasiyer-prim/kaydet/', views.plasiyer_prim_kaydet, name='plasiyer_prim_kaydet'),

    # Hakediş Hedef Modülü
    path('hakedis-yonetimi/hedef-belirleme/', views.hedef_belirleme, name='hedef_belirleme'),
    path('hakedis-yonetimi/plasiyer-hedef-durumu/', views.plasiyer_hedef_durumu, name='plasiyer_hedef_durumu'),
    path('hakedis-yonetimi/hedeflerim/', views.hedeflerim, name='hedeflerim'),

]
