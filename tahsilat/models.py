from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import os


def evrak_upload_path(instance, filename):
    """Evrak dosyalarının yükleneceği yolu belirler"""
    return os.path.join('evraklar', filename)


class UploadedImage(models.Model):
    """Genel resim upload modeli (legacy support)"""
    title = models.CharField(max_length=200)
    image = models.ImageField(upload_to='uploads/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class TahsilatEvrak(models.Model):
    """Tahsilat evrakları modeli"""
    tahsilat_id = models.IntegerField(
        'Tahsilat ID', help_text='TAHSILATTB tablosundaki ID')
    cari_kod = models.CharField('Cari Kod', max_length=50, blank=True)
    evrak_no = models.CharField('Evrak No', max_length=100, blank=True)
    evrak_dosyasi = models.FileField(
        'Evrak Dosyası', upload_to=evrak_upload_path)
    aciklama = models.TextField('Açıklama', blank=True)
    yukleyen = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name='Yükleyen')
    yuklenen_tarih = models.DateTimeField('Yükleme Tarihi', auto_now_add=True)
    dosya_boyutu = models.IntegerField(
        'Dosya Boyutu (byte)', null=True, blank=True)

    class Meta:
        verbose_name = 'Tahsilat Evrak'
        verbose_name_plural = 'Tahsilat Evrakları'
        ordering = ['-yuklenen_tarih']

    def __str__(self):
        return f'Tahsilat {self.tahsilat_id} - {self.evrak_no}'

    def get_file_extension(self):
        """Dosya uzantısını döndürür"""
        if self.evrak_dosyasi.name:
            return os.path.splitext(self.evrak_dosyasi.name)[1].lower()
        return ''

    def is_image(self):
        """Dosyanın resim olup olmadığını kontrol eder"""
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        return self.get_file_extension() in image_extensions

    def get_file_size_mb(self):
        """Dosya boyutunu MB cinsinden döndürür"""
        if self.dosya_boyutu:
            return round(self.dosya_boyutu / 1024 / 1024, 2)
        return 0


class GiderMasraf(models.Model):
    """Gider masraf modeli"""

    GIDER_TURU_CHOICES = [
        ('dolmus', 'Dolmuş'),
        ('nakliye', 'Nakliye'),
        ('kahve', 'Kahve'),
        ('temizlik_malzemesi', 'Temizlik Malzemesi'),
        ('diger', 'Diğer'),
    ]

    gider_adi = models.CharField(
        'Gider Adı', max_length=50, choices=GIDER_TURU_CHOICES)
    aciklama = models.TextField('Açıklama', blank=True)
    tutar = models.DecimalField('Tutar', max_digits=15, decimal_places=2)
    tarih = models.DateField('Tarih')
    ekleyen = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name='Ekleyen')
    eklenen_tarih = models.DateTimeField('Eklenen Tarih', auto_now_add=True)

    class Meta:
        verbose_name = 'Gider Masraf'
        verbose_name_plural = 'Gider Masrafları'
        ordering = ['-tarih', '-eklenen_tarih']

    def __str__(self):
        return f'{self.get_gider_adi_display()} - {self.tutar} TL'

    def get_gider_adi_display(self):
        """Gider adının görüntülenen halini döndürür"""
        return dict(self.GIDER_TURU_CHOICES).get(self.gider_adi, self.gider_adi)


class KullaniciYetki(models.Model):
    """Kullanıcı yetkilendirme modeli - Ana menü ve alt menü desteği"""

    MENU_CHOICES = [
        # Ana Menüler
        ('dashboard', 'Dashboard'),
        ('genel_gorunum', 'Genel Görünüm'),
        ('satislar', 'Satışlar'),
        ('tahsilatlar', 'Tahsilatlar'),
        ('alimlar', 'Alımlar'),
        ('muhasebe', 'Muhasebe'),
        ('gider_masraf', 'Gider Masraf'),
        ('perakende', 'Perakende'),
        ('klasik_tahsilat_raporu', 'Klasik Tahsilat Raporu'),
        ('kdv_raporu', 'KDV Raporu'),
        ('yetkilendirme', 'Yetkilendirme'),
        ('cari_ekstre', 'Cari Ekstre'),
        ('yeni_tahsilat', 'Yeni Tahsilat'),
        ('stok_yonetimi', 'Stok Yönetimi'),
        ('cari_yonetimi', 'Cari Yönetimi'),
        ('cari_gecikmeleri', 'Cari Geçiklemeleri'),

        # Muhasebe Alt Menüleri
        ('muhasebe_yeni_tahsilat', 'Muhasebe > Yeni Tahsilat'),
        ('muhasebe_tahsilat_listesi', 'Muhasebe > Tahsilat Listesi'),
        ('muhasebe_klasik_rapor', 'Muhasebe > Klasik Rapor'),
        ('muhasebe_gunluk_rapor', 'Muhasebe > Günlük Rapor'),

        # Genel Görünüm Alt Menüleri
        ('genel_dashboard', 'Genel Görünüm > Dashboard'),
        ('genel_tahsilatlar', 'Genel Görünüm > Tahsilatlar'),
        ('genel_satislar', 'Genel Görünüm > Satışlar'),
        ('genel_alimlar', 'Genel Görünüm > Alımlar'),
        ('genel_ekstre', 'Genel Görünüm > Ekstre'),
        ('genel_cari_analiz', 'Genel Görünüm > Cari Genel Analiz'),
        ('genel_cari_aylik_ozet', 'Genel Görünüm > Cari Aylık Özet'),

        # Stok Yönetimi Alt Menüleri
        ('stok_listesi', 'Stok Yönetimi > Stok Listesi'),
        ('fiyat_analizi', 'Stok Yönetimi > Fiyat Analizi'),
        ('stok_detayli_analiz', 'Stok Yönetimi > Detaylı Analiz'),
        ('ortalama_maliyet', 'Stok Yönetimi > Ortalama Maliyet'),

        # Cari Yönetimi Alt Menüleri
        ('cari_bakiyeler', 'Cari Yönetimi > Cari Bakiyeler'),
        ('cari_analiz', 'Cari Yönetimi > Cari Analiz'),
        ('cari_vade_analizi', 'Cari Yönetimi > Vade Analizi'),
        ('cari_gecikmeleri_detay', 'Cari Yönetimi > Geçikmeleri'),

        # Mesajlaşma
        ('chat', 'Mesajlaşma'),

        # LOGO Aktarım
        ('logo_transfer', 'LOGOYA AKTAR'),

        # Diğer (sidebar'da kullanılan ek menüler)
        ('cek_senetler', 'Çek ve Senetler'),
        ('tahsilatlarim', 'Tahsilatlarım'),
        ('satislarim', 'Satışlarım'),
    ]

    kullanici = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name='Kullanıcı')
    menu_adi = models.CharField(
        'Menü Adı', max_length=50, choices=MENU_CHOICES)
    erisim_izni = models.BooleanField('Erişim İzni', default=False)
    olusturan = models.ForeignKey(User, on_delete=models.CASCADE,
                                  related_name='olusturulan_yetkiler', verbose_name='Oluşturan')
    olusturma_tarihi = models.DateTimeField(
        'Oluşturma Tarihi', auto_now_add=True)
    guncelleme_tarihi = models.DateTimeField(
        'Güncelleme Tarihi', auto_now=True)

    class Meta:
        verbose_name = 'Kullanıcı Yetki'
        verbose_name_plural = 'Kullanıcı Yetkileri'
        unique_together = ['kullanici', 'menu_adi']
        ordering = ['kullanici__username', 'menu_adi']

    def __str__(self):
        return f'{self.kullanici.username} - {self.get_menu_adi_display()}'

    def get_menu_adi_display(self):
        """Menü adının görüntülenen halini döndürür"""
        return dict(self.MENU_CHOICES).get(self.menu_adi, self.menu_adi)

    @classmethod
    def get_menu_hierarchy(cls):
        """Menü hiyerarşisini döndürür"""
        return {
            'Ana Menüler': [
                ('dashboard', 'Dashboard'),
                ('genel_gorunum', 'Genel Görünüm'),
                ('satislar', 'Satışlar'),
                ('tahsilatlar', 'Tahsilatlar'),
                ('alimlar', 'Alımlar'),
                ('muhasebe', 'Muhasebe'),
                ('gider_masraf', 'Gider Masraf'),
                ('perakende', 'Perakende'),
                ('klasik_tahsilat_raporu', 'Klasik Tahsilat Raporu'),
                ('kdv_raporu', 'KDV Raporu'),
                ('yetkilendirme', 'Yetkilendirme'),
                ('cari_ekstre', 'Cari Ekstre'),
                ('yeni_tahsilat', 'Yeni Tahsilat'),
                ('stok_yonetimi', 'Stok Yönetimi'),
                ('cari_yonetimi', 'Cari Yönetimi'),
                ('cari_gecikmeleri', 'Cari Geçiklemeleri'),
            ],
            'Muhasebe Alt Menüleri': [
                ('muhasebe_yeni_tahsilat', 'Yeni Tahsilat'),
                ('muhasebe_tahsilat_listesi', 'Tahsilat Listesi'),
                ('muhasebe_klasik_rapor', 'Klasik Rapor'),
                ('kdv_raporu', 'KDV Raporu'),
                ('muhasebe_gunluk_rapor', 'Günlük Rapor'),
                ('cek_senetler', 'Çek ve Senetler'),
            ],
            'Genel Görünüm Alt Menüleri': [
                ('genel_dashboard', 'Dashboard'),
                ('genel_tahsilatlar', 'Tahsilatlar'),
                ('genel_satislar', 'Satışlar'),
                ('genel_alimlar', 'Alımlar'),
                ('genel_ekstre', 'Ekstre'),
                ('genel_cari_analiz', 'Cari Genel Analiz'),
                ('genel_cari_aylik_ozet', 'Cari Aylık Özet'),
            ],
            'Stok Yönetimi Alt Menüleri': [
                ('stok_listesi', 'Stok Listesi'),
                ('fiyat_analizi', 'Fiyat Analizi'),
                ('stok_detayli_analiz', 'Detaylı Analiz'),
                ('ortalama_maliyet', 'Ortalama Maliyet'),
            ],
            'Cari Yönetimi Alt Menüleri': [
                ('cari_bakiyeler', 'Cari Bakiyeler'),
                ('cari_analiz', 'Cari Analiz'),
                ('cari_vade_analizi', 'Vade Analizi'),
                ('cari_gecikmeleri_detay', 'Geçikmeleri'),
            ],
            'Mesajlaşma': [
                ('chat', 'Mesajlaşma'),
            ],
            'LOGO Aktarım': [
                ('logo_transfer', 'LOGOYA AKTAR'),
            ],
            'Tahsilat İşlemleri': [
                ('tahsilatlarim', 'Tahsilatlarım'),
            ],
            'Satış İşlemleri': [
                ('satislarim', 'Satışlarım'),
            ],
        }


class CariGeckme(models.Model):
    """Cari geçikme verilerini saklayan model"""
    cari_kod = models.CharField('Cari Kod', max_length=50, db_index=True)
    cari_unvan = models.CharField('Cari Ünvan', max_length=200)
    plasiyer = models.CharField('Plasiyer', max_length=150, blank=True, null=True)
    bolge = models.CharField('Bölge', max_length=150, blank=True, null=True)
    gecikme_tutari = models.DecimalField(
        'Geçikme Tutarı', max_digits=15, decimal_places=2)
    gecikme_fatura_sayisi = models.IntegerField('Geçikme Fatura Sayısı')
    en_eski_vade = models.DateField('En Eski Vade')
    en_yeni_vade = models.DateField('En Yeni Vade')
    gecikme_gun_sayisi = models.IntegerField('Geçikme Gün Sayısı')
    hesaplama_tarihi = models.DateTimeField(
        'Hesaplama Tarihi', auto_now_add=True)

    class Meta:
        verbose_name = 'Cari Geçikme'
        verbose_name_plural = 'Cari Geçikmeleri'
        ordering = ['-gecikme_tutari']
        indexes = [
            models.Index(fields=['cari_kod']),
            models.Index(fields=['-gecikme_tutari']),
            models.Index(fields=['hesaplama_tarihi']),
        ]

    def __str__(self):
        return f'{self.cari_kod} - {self.cari_unvan} - ₺ {self.gecikme_tutari}'

    @classmethod
    def clear_old_data(cls):
        """Eski verileri temizle"""
        cls.objects.all().delete()

    @classmethod
    def get_latest_data(cls):
        """En son hesaplanan verileri getir"""
        return cls.objects.all()

    @classmethod
    def get_summary_stats(cls):
        """Özet istatistikleri getir"""
        from django.db.models import Sum, Count
        stats = cls.objects.aggregate(
            toplam_gecikme=Sum('gecikme_tutari'),
            toplam_cari_sayisi=Count('id'),
            toplam_fatura_sayisi=Sum('gecikme_fatura_sayisi')
        )
        return {
            'toplam_gecikme': stats['toplam_gecikme'] or 0,
            'toplam_cari_sayisi': stats['toplam_cari_sayisi'] or 0,
            'toplam_fatura_sayisi': stats['toplam_fatura_sayisi'] or 0
        }


class Mesaj(models.Model):
    """Kullanıcılar arası mesajlaşma modeli"""
    gonderen = models.ForeignKey(User, on_delete=models.CASCADE,
                                 related_name='gonderilen_mesajlar', verbose_name="Gönderen")
    alici = models.ForeignKey(User, on_delete=models.CASCADE,
                              related_name='alinan_mesajlar', verbose_name="Alıcı")
    mesaj = models.TextField(verbose_name="Mesaj")
    okundu = models.BooleanField(default=False, verbose_name="Okundu")
    okunma_tarihi = models.DateTimeField(
        null=True, blank=True, verbose_name="Okunma Tarihi")
    olusturma_tarihi = models.DateTimeField(
        auto_now_add=True, verbose_name="Gönderim Tarihi")

    class Meta:
        verbose_name = "Mesaj"
        verbose_name_plural = "Mesajlar"
        ordering = ['-olusturma_tarihi']

    def __str__(self):
        return f"{self.gonderen.username} -> {self.alici.username}: {self.mesaj[:50]}..."

    def mark_as_read(self):
        """Mesajı okundu olarak işaretle"""
        if not self.okundu:
            self.okundu = True
            self.okunma_tarihi = timezone.now()
            self.save()


class KullaniciDurumu(models.Model):
    """Kullanıcı çevrimiçi/çevrimdışı durumu"""
    kullanici = models.OneToOneField(
        User, on_delete=models.CASCADE, verbose_name="Kullanıcı")
    son_aktivite = models.DateTimeField(
        auto_now=True, verbose_name="Son Aktivite")
    cevrimici = models.BooleanField(default=True, verbose_name="Çevrimiçi")

    class Meta:
        verbose_name = "Kullanıcı Durumu"
        verbose_name_plural = "Kullanıcı Durumları"

    def __str__(self):
        return f"{self.kullanici.username} - {'Çevrimiçi' if self.cevrimici else 'Çevrimdışı'}"

    def update_activity(self):
        """Kullanıcı aktivitesini güncelle"""
        self.son_aktivite = timezone.now()
        self.cevrimici = True
        self.save()

    @classmethod
    def mark_user_online(cls, user):
        """Kullanıcıyı çevrimiçi olarak işaretle"""
        durum, created = cls.objects.get_or_create(
            kullanici=user,
            defaults={'cevrimici': True}
        )
        if not created:
            durum.update_activity()
        return durum

    @classmethod
    def mark_user_offline(cls, user):
        """Kullanıcıyı çevrimdışı olarak işaretle"""
        try:
            durum = cls.objects.get(kullanici=user)
            durum.cevrimici = False
            durum.save()
        except cls.DoesNotExist:
            pass

    @classmethod
    def get_online_users(cls):
        """Çevrimiçi kullanıcıları getir"""
        return cls.objects.filter(cevrimici=True)

    @classmethod
    def cleanup_offline_users(cls):
        """5 dakikadan fazla aktif olmayan kullanıcıları çevrimdışı yap"""
        from datetime import timedelta
        offline_threshold = timezone.now() - timedelta(minutes=5)

        cls.objects.filter(
            son_aktivite__lt=offline_threshold,
            cevrimici=True
        ).update(cevrimici=False)


class LogoTransfer(models.Model):
    """LOGO veritabanına aktarım işlemleri için model"""

    DURUM_CHOICES = [
        ('beklemede', 'Beklemede'),
        ('isleniyor', 'İşleniyor'),
        ('basarili', 'Başarılı'),
        ('hata', 'Hata'),
    ]

    # Birinci tablo verileri (LG_002_05_CLFICHE)
    ficheno = models.CharField('Fiş Numarası', max_length=17)
    trcode = models.SmallIntegerField('İşlem Kodu')
    credit = models.FloatField('Kredi Tutarı')
    repcredit = models.FloatField('Rapor Kredi Tutarı')
    tarih = models.DateTimeField('Tarih')

    # İkinci tablo verileri (LG_002_05_CLFLINE) - otomatik doldurulacak
    clientref = models.IntegerField('Müşteri Referansı', default=421)
    sourcefref = models.IntegerField(
        'Kaynak Fiş Referansı', null=True, blank=True)
    tranno = models.CharField('İşlem Numarası', max_length=17, blank=True)
    amount = models.FloatField('Tutar', null=True, blank=True)
    trnet = models.FloatField('İşlem Net Tutarı', null=True, blank=True)
    reportnet = models.FloatField('Rapor Net Tutarı', null=True, blank=True)
    bnlnnet = models.FloatField('Banka Net Tutarı', null=True, blank=True)

    # Sistem alanları
    durum = models.CharField('Durum', max_length=20,
                             choices=DURUM_CHOICES, default='beklemede')
    hata_mesaji = models.TextField('Hata Mesajı', blank=True)
    olusturan = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name='Oluşturan')
    olusturma_tarihi = models.DateTimeField(
        'Oluşturma Tarihi', auto_now_add=True)
    guncelleme_tarihi = models.DateTimeField(
        'Güncelleme Tarihi', auto_now=True)

    class Meta:
        verbose_name = 'LOGO Aktarım'
        verbose_name_plural = 'LOGO Aktarımları'
        ordering = ['-olusturma_tarihi']

    def __str__(self):
        return f'{self.ficheno} - {self.get_durum_display()}'

    def get_durum_display(self):
        """Durumun görüntülenen halini döndürür"""
        return dict(self.DURUM_CHOICES).get(self.durum, self.durum)

    def save(self, *args, **kwargs):
        """Kaydetme sırasında otomatik alanları doldur"""
        # İkinci tablo verilerini otomatik doldur
        if not self.tranno:
            self.tranno = self.ficheno
        if not self.amount:
            self.amount = self.credit
        if not self.trnet:
            self.trnet = self.credit
        if not self.reportnet:
            self.reportnet = self.repcredit
        if not self.bnlnnet:
            self.bnlnnet = self.credit

        super().save(*args, **kwargs)

    def logo_aktar(self):
        """LOGO veritabanına aktarım işlemini gerçekleştir"""
        try:
            self.durum = 'isleniyor'
            self.save()

            # LOGO veritabanı bağlantısı için MSSQLService kullan
            from .mssql_service import mssql_service

            # Birinci tablo INSERT - LG_002_05_CLFICHE (25950 kaydının yapısına göre)
            insert_clfiche_sql = """
            INSERT INTO [GO3].[dbo].[LG_002_05_CLFICHE] (
                FICHENO, DATE_, DOCODE, TRCODE, SPECCODE, CYPHCODE, BRANCH, DEPARTMENT,
                GENEXP1, GENEXP2, GENEXP3, GENEXP4, GENEXP5, GENEXP6, DEBIT, CREDIT,
                REPDEBIT, REPCREDIT, CAPIBLOCK_CREATEDBY, CAPIBLOCK_CREADEDDATE,
                CAPIBLOCK_CREATEDHOUR, CAPIBLOCK_CREATEDMIN, CAPIBLOCK_CREATEDSEC,
                CAPIBLOCK_MODIFIEDBY, CAPIBLOCK_MODIFIEDDATE, CAPIBLOCK_MODIFIEDHOUR,
                CAPIBLOCK_MODIFIEDMIN, CAPIBLOCK_MODIFIEDSEC, ACCOUNTED, INVOREF,
                CASHACCREF, CASHCENREF, PRINTCNT, CANCELLED, CANCELLEDACC, ACCFICHEREF,
                GENEXCTYP, LINEEXCTYP, TEXTINC, SITEID, RECSTATUS, ORGLOGICREF, WFSTATUS,
                TIME, CLCARDREF, BANKACCREF, BNACCREF, BNCENTERREF, TRADINGGRP,
                POSCOMMACCREF, POSCOMMCENREF, POINTCOMMACCREF, POINTCOMMCENREF,
                PROJECTREF, STATUS, WFLOWCRDREF, ORGLOGOID, AFFECTCOLLATRL,
                GRPFIRMTRANS, AFFECTRISK, POSTERMINALNR, POSTERMINALNUM, APPROVE,
                APPROVEDATE, SALESMANREF, CSTRANSREF, DOCDATE, GUID, DEVIR, PRINTDATE,
                FOREXIM, TYPECODE, EINVOICE, HOUR_, MINUTE_, DEDUCTCODE, ELECTDOC,
                NOTIFYCRDREF, GIBACCFICHEREF, PARTIALCSPAYREF, GIBINCMTAXREF, EXIMVAT
            ) 
            OUTPUT INSERTED.LOGICALREF
            VALUES (
                ?, ?, '', ?, '', '', 0, 0,
                '', '', '', '', '', '', 0, ?,
                0, ?, 6, ?,
                ?, ?, ?,
                0, NULL, 0,
                0, 0, 0, 0,
                0, 0, 0, 0, 0, 0,
                3, 0, 0, 0, 1, 0, 0,
                ?, 0, 0, 0, 0, '',
                0, 0, 0, 0,
                0, 0, 0, '', 0,
                0, 1, 0, '',
                0, NULL, 0, 0, NULL, '', 0, NULL,
                0, '', 0, 0, 0, '', 0,
                0, 0, 0, 0, 0
            )
            """

            # Tarih formatını hazırla
            tarih_str = self.tarih.strftime('%Y-%m-%d %H:%M:%S')
            capi_date = self.tarih.strftime(
                '%Y-%m-%d %H:%M:%S')  # SQL Server formatı
            capi_hour = self.tarih.hour
            capi_min = self.tarih.minute
            capi_sec = self.tarih.second

            # LOGO TIME değerini dinamik olarak hesapla
            logo_time = int(self.tarih.timestamp() * 1000) % 1000000000

            # Birinci tablo INSERT'i çalıştır ve LOGICALREF'i al
            try:
                new_logicalref = mssql_service.execute_insert_with_identity(insert_clfiche_sql, [
                    self.ficheno, tarih_str, self.trcode, self.credit,
                    self.repcredit, capi_date, capi_hour, capi_min, capi_sec,
                    logo_time
                ])
                print(f"Birinci tablo INSERT sonucu: {new_logicalref}")
                if not new_logicalref:
                    raise Exception(
                        "Birinci tablo INSERT işlemi başarısız veya LOGICALREF alınamadı")
            except Exception as e:
                raise Exception(f"Birinci tablo INSERT hatası: {str(e)}")

            # LOGICALREF'i kaydet
            self.sourcefref = new_logicalref

            # İkinci tablo INSERT - LG_002_05_CLFLINE (Tüm değer verilen kolonlar)
            insert_clfline_sql = """
            INSERT INTO [GO3].[dbo].[LG_002_05_CLFLINE] (
                CLIENTREF, CLACCREF, CLCENTERREF, CASHCENTERREF, CASHACCOUNTREF,
                VIRMANREF, SOURCEFREF, DATE_, DEPARTMENT, BRANCH, MODULENR, TRCODE,
                LINENR, SPECODE, CYPHCODE, TRANNO, DOCODE, LINEEXP, ACCOUNTED, SIGN, AMOUNT, TRCURR, TRRATE, TRNET,
                REPORTRATE, REPORTNET, EXTENREF, PAYDEFREF, ACCFICHEREF, PRINTCNT,
                CAPIBLOCK_CREATEDBY, CAPIBLOCK_CREADEDDATE, CAPIBLOCK_CREATEDHOUR,
                CAPIBLOCK_CREATEDMIN, CAPIBLOCK_CREATEDSEC, CAPIBLOCK_MODIFIEDBY, CAPIBLOCK_MODIFIEDDATE, CAPIBLOCK_MODIFIEDHOUR, CAPIBLOCK_MODIFIEDMIN, CAPIBLOCK_MODIFIEDSEC,
                CANCELLED, TRGFLAG, LINEEXCTYP, ONLYONEPAYLINE, DISCFLAG, DISCRATE,
                VATRATE, CASHAMOUNT, DISCACCREF, DISCCENREF, VATRACCREF, VATRCENREF,
                PAYMENTREF, VATAMOUNT, SITEID, RECSTATUS, ORGLOGICREF, INFIDX,
                POSCOMMACCREF, POSCOMMCENREF, POINTCOMMACCREF, POINTCOMMCENREF, TRADINGGRP, CHEQINFO, CREDITCNO,
                CLPRJREF, STATUS, EXIMFILEREF, EXIMPROCNR, MONTH_, YEAR_,
                FUNDSHARERAT, AFFECTCOLLATRL, GRPFIRMTRANS, REFLVATACCREF, REFLVATOTHACCREF,
                AFFECTRISK, BATCHNUM, APPROVENUM, EUVATSTATUS, EXIMTYPE, EIDISTFLNNR,
                EISRVDSTTYP, EXIMDISTTYP,                 SALESMANREF, BANKACCREF, BNACCREF, BNCENTERREF, ORGLOGOID, GUID, DOCDATE,
                INSTALREF, DEVIR, DEVIRMODULENR, FTIME, OFFERREF, RETCCFCREF,
                EMFLINEREF, FROMEXCHDIFF, CANDEDUCT, DEDUCTIONPART1, DEDUCTIONPART2,
                UNDERDEDUCTLIMIT, VATDEDUCTRATE, VATDEDUCTACCREF, VATDEDUCTOTHACCREF,
                VATDEDUCTCENREF, VATDEDUCTOTHCENREF, CANTCREDEDUCT, PAIDINCASH,
                BRUTAMOUNT, NETAMOUNT, BRUTAMOUNTTR, NETAMOUNTTR, BRUTAMOUNTREP,
                NETAMOUNTREP, BNLNTRCURR, BNLNTRRATE, BNLNTRNET, INCDEDUCTAMNT,
                AFFECTCOST, FOREXIM, EXIMFILECODECLF, SPECODE2, SERVREASONDEF
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
            """

            # İkinci tablo INSERT'i çalıştır
            try:
                result2 = mssql_service.execute_insert(insert_clfline_sql, [
                    self.clientref,      # CLIENTREF = 421
                    0,                   # CLACCREF = 0
                    0,                   # CLCENTERREF = 0
                    0,                   # CASHCENTERREF = 0
                    0,                   # CASHACCOUNTREF = 0
                    0,                   # VIRMANREF = 0
                    new_logicalref,      # SOURCEFREF = birinci tablodan gelen LOGICALREF
                    tarih_str,           # DATE_ = bizim girdiğimiz tarih
                    0,                   # DEPARTMENT = 0 (sabit)
                    0,                   # BRANCH = 0 (sabit)
                    self.tarih.second,   # MODULENR = girelien tarihin saniyesi
                    self.trcode,         # TRCODE = 70
                    7,                   # LINENR = 7
                    '',                  # SPECODE = ''
                    '',                  # CYPHCODE = ''
                    self.ficheno,        # TRANNO = FICHENO
                    '',                  # DOCODE = ''
                    '',                  # LINEEXP = ''
                    0,                   # ACCOUNTED = 0
                    1,                   # SIGN = 1
                    self.credit,         # AMOUNT = CREDIT
                    0,                   # TRCURR = 0
                    0,                   # TRRATE = 0
                    self.credit,         # TRNET = CREDIT
                    1,                   # REPORTRATE = 1
                    self.repcredit,      # REPORTNET = REPCREDIT
                    0,                   # EXTENREF = 0
                    1,                   # PAYDEFREF = 1
                    0,                   # ACCFICHEREF = 0
                    0,                   # PRINTCNT = 0
                    6,                   # CAPIBLOCK_CREATEDBY = 6
                    capi_date,           # CAPIBLOCK_CREADEDDATE
                    capi_hour,           # CAPIBLOCK_CREATEDHOUR
                    capi_min,            # CAPIBLOCK_CREATEDMIN
                    capi_sec,            # CAPIBLOCK_CREATEDSEC
                    0,                   # CAPIBLOCK_MODIFIEDBY = 0
                    '',                  # CAPIBLOCK_MODIFIEDDATE = ''
                    0,                   # CAPIBLOCK_MODIFIEDHOUR = 0
                    0,                   # CAPIBLOCK_MODIFIEDMIN = 0
                    0,                   # CAPIBLOCK_MODIFIEDSEC = 0
                    0,                   # CANCELLED = 0
                    0,                   # TRGFLAG = 0
                    0,                   # LINEEXCTYP = 0
                    0,                   # ONLYONEPAYLINE = 0
                    0,                   # DISCFLAG = 0
                    0,                   # DISCRATE = 0
                    0,                   # VATRATE = 0
                    0,                   # CASHAMOUNT = 0
                    0,                   # DISCACCREF = 0
                    0,                   # DISCCENREF = 0
                    0,                   # VATRACCREF = 0
                    0,                   # VATRCENREF = 0
                    0,                   # PAYMENTREF = 0
                    0,                   # VATAMOUNT = 0
                    0,                   # SITEID = 0
                    2,                   # RECSTATUS = 2
                    0,                   # ORGLOGICREF = 0
                    0,                   # INFIDX = 0
                    0,                   # POSCOMMACCREF = 0
                    0,                   # POSCOMMCENREF = 0
                    0,                   # POINTCOMMACCREF = 0
                    0,                   # POINTCOMMCENREF = 0
                    '',                  # TRADINGGRP = ''
                    '',                  # CHEQINFO = ''
                    '',                  # CREDITCNO = ''
                    0,                   # CLPRJREF = 0
                    0,                   # STATUS = 0
                    0,                   # EXIMFILEREF = 0
                    0,                   # EXIMPROCNR = 0
                    self.tarih.month,    # MONTH_ = 10
                    self.tarih.year,     # YEAR_ = 2025
                    0,                   # FUNDSHARERAT = 0
                    0,                   # AFFECTCOLLATRL = 0
                    0,                   # GRPFIRMTRANS = 0
                    0,                   # REFLVATACCREF = 0
                    0,                   # REFLVATOTHACCREF = 0
                    1,                   # AFFECTRISK = 1
                    '',                  # BATCHNUM = '' (boş)
                    '',                  # APPROVENUM = '' (boş)
                    0,                   # EUVATSTATUS = 0
                    0,                   # EXIMTYPE = 0
                    0,                   # EIDISTFLNNR = 0
                    0,                   # EISRVDSTTYP = 0
                    0,                   # EXIMDISTTYP = 0
                    0,                   # SALESMANREF = 0
                    30,                  # BANKACCREF = 30
                    0,                   # BNACCREF = 0
                    0,                   # BNCENTERREF = 0
                    '',                  # ORGLOGOID = ''
                    '',                  # GUID = ''
                    # DOCDATE = DATE_ ile aynı (eklediğimiz tarih)
                    tarih_str,
                    0,                   # INSTALREF = 0
                    0,                   # DEVIR = 0
                    0,                   # DEVIRMODULENR = 0
                    0,                   # FTIME = 0
                    0,                   # OFFERREF = 0
                    0,                   # RETCCFCREF = 0
                    0,                   # EMFLINEREF = 0
                    0,                   # FROMEXCHDIFF = 0
                    0,                   # CANDEDUCT = 0
                    0,                   # DEDUCTIONPART1 = 0
                    0,                   # DEDUCTIONPART2 = 0
                    0,                   # UNDERDEDUCTLIMIT = 0
                    0,                   # VATDEDUCTRATE = 0
                    0,                   # VATDEDUCTACCREF = 0
                    0,                   # VATDEDUCTOTHACCREF = 0
                    0,                   # VATDEDUCTCENREF = 0
                    0,                   # VATDEDUCTOTHCENREF = 0
                    0,                   # CANTCREDEDUCT = 0
                    0,                   # PAIDINCASH = 0
                    0,                   # BRUTAMOUNT = 0
                    0,                   # NETAMOUNT = 0
                    0,                   # BRUTAMOUNTTR = 0
                    0,                   # NETAMOUNTTR = 0
                    0,                   # BRUTAMOUNTREP = 0
                    0,                   # NETAMOUNTREP = 0
                    0,                   # BNLNTRCURR = 0
                    0,                   # BNLNTRRATE = 0
                    self.credit,         # BNLNTRNET = CREDIT
                    0,                   # INCDEDUCTAMNT = 0
                    0,                   # AFFECTCOST = 0
                    0,                   # FOREXIM = 0
                    '',                  # EXIMFILECODECLF = ''
                    '',                  # SPECODE2 = ''
                    ''                   # SERVREASONDEF = ''
                ])
                print(f"İkinci tablo INSERT sonucu: {result2}")
                if not result2:
                    raise Exception("İkinci tablo INSERT işlemi başarısız")
            except Exception as e:
                raise Exception(f"İkinci tablo INSERT hatası: {str(e)}")

            # Başarılı durumunu güncelle
            self.sourcefref = new_logicalref
            self.durum = 'basarili'
            self.save()
            return True

        except Exception as e:
            self.durum = 'hata'
            self.hata_mesaji = str(e)
            self.save()
            return False


class UserPermission(models.Model):
    """Kullanıcı menü yetkileri"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='permissions')
    menu_name = models.CharField(max_length=100, verbose_name='Menü Adı')
    has_access = models.BooleanField(default=False, verbose_name='Erişim İzni')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['user', 'menu_name']
        verbose_name = 'Kullanıcı Yetkisi'
        verbose_name_plural = 'Kullanıcı Yetkileri'
    
    def __str__(self):
        return f"{self.user.username} - {self.menu_name}: {'Evet' if self.has_access else 'Hayır'}"


class CekSenet(models.Model):
    """Çek ve Senet Takip Modeli"""
    
    ODEME_TURU_CHOICES = [
        ('cek', 'Çek'),
        ('senet', 'Senet'),
        ('kredi', 'Kredi'),
        ('kredi_karti', 'Kredi Kartı'),
    ]
    
    DURUM_CHOICES = [
        ('beklemede', 'Beklemede'),
        ('ciro', 'Ciro'),
        ('odendi', 'Ödendi'),
        ('iptal', 'İptal'),
        ('vadesi_gecti', 'Vadesi Geçti'),
    ]
    
    TIP_CHOICES = [
        ('gelen', 'Gelen'),
        ('giden', 'Giden'),
    ]
    
    # Temel Bilgiler
    odeme_turu = models.CharField(max_length=20, choices=ODEME_TURU_CHOICES, verbose_name='Ödeme Türü')
    tip = models.CharField(max_length=10, choices=TIP_CHOICES, verbose_name='Tip')
    durum = models.CharField(max_length=20, choices=DURUM_CHOICES, default='beklemede', verbose_name='Durum')
    
    # Finansal Bilgiler
    tutar = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='Tutar')
    para_birimi = models.CharField(max_length=3, default='TRY', verbose_name='Para Birimi')
    
    # Tarih Bilgileri
    islem_tarihi = models.DateField(verbose_name='İşlem Tarihi')
    vade_tarihi = models.DateField(verbose_name='Vade Tarihi')
    odeme_tarihi = models.DateField(null=True, blank=True, verbose_name='Ödeme Tarihi')
    
    # Taraflar
    cari_kod = models.CharField(max_length=50, verbose_name='Cari Kod')
    cari_unvan = models.CharField(max_length=200, verbose_name='Cari Ünvan')
    banka_adi = models.CharField(max_length=100, blank=True, verbose_name='Banka Adı')
    
    # Çek/Senet Özel Bilgiler
    cek_senet_no = models.CharField(max_length=50, blank=True, verbose_name='Çek/Senet No')
    banka_sube = models.CharField(max_length=100, blank=True, verbose_name='Banka Şubesi')
    hesap_no = models.CharField(max_length=50, blank=True, verbose_name='Hesap No')
    
    # Kredi/Kredi Kartı Özel Bilgiler
    kredi_turu = models.CharField(max_length=50, blank=True, verbose_name='Kredi Türü')
    faiz_orani = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name='Faiz Oranı (%)')
    taksit_sayisi = models.IntegerField(null=True, blank=True, verbose_name='Taksit Sayısı')
    
    # Açıklama ve Notlar
    aciklama = models.TextField(blank=True, verbose_name='Açıklama')
    notlar = models.TextField(blank=True, verbose_name='Notlar')
    
    # Sistem Bilgileri
    olusturan = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='Oluşturan')
    olusturma_tarihi = models.DateTimeField(auto_now_add=True, verbose_name='Oluşturma Tarihi')
    guncelleme_tarihi = models.DateTimeField(auto_now=True, verbose_name='Güncelleme Tarihi')
    
    class Meta:
        verbose_name = 'Çek ve Senet'
        verbose_name_plural = 'Çek ve Senetler'
        ordering = ['-olusturma_tarihi']

    @property
    def vade_durumu(self):
        """Şablon / Excel ile uyumlu vade etiketi kodu."""
        from datetime import date, timedelta

        today = date.today()
        if self.vade_tarihi < today and self.durum not in ('odendi', 'iptal'):
            return 'vadesi_gecti'
        if self.vade_tarihi == today:
            return 'vade_bugun'
        if self.vade_tarihi == today + timedelta(days=1):
            return 'vade_yarin'
        return 'normal'

    @property
    def kalan_gun(self):
        from datetime import date

        return (self.vade_tarihi - date.today()).days

    def __str__(self):
        return f"{self.get_odeme_turu_display()} - {self.cari_unvan} - {self.tutar} {self.para_birimi}"


class SystemSettings(models.Model):
    """Sistem genel ayarlarının tutulduğu model"""
    key = models.CharField(max_length=50, unique=True, verbose_name="Ayar Anahtarı")
    value = models.BooleanField(default=True, verbose_name="Ayar Kapalı/Açık Değeri")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Güncelleme Tarihi")
    
    class Meta:
        verbose_name = 'Sistem Ayarı'
        verbose_name_plural = 'Sistem Ayarları'
        
    def __str__(self):
        return f"{self.key}: {'Açık' if self.value else 'Kapalı'}"


class PlasiyerPrim(models.Model):
    """Plasiyer prim takibi modeli"""
    PLASIYER_CHOICES = [
        ('ALİ', 'ALİ'),
        ('AZİZ', 'AZİZ'),
        ('CAN', 'CAN'),
        ('EYÜP', 'EYÜP'),
        ('NECATİ', 'NECATİ'),
        ('HASAN', 'HASAN'),
        ('YİĞİT', 'YİĞİT'),
        ('ATAKAN', 'ATAKAN'),
    ]

    plasiyer = models.CharField('Plasiyer', max_length=50, choices=PLASIYER_CHOICES)
    donem_ay = models.IntegerField('Dönem Ay')
    donem_yil = models.IntegerField('Dönem Yıl')
    
    # Toplam Tahsilatlar
    nakit_toplam = models.DecimalField('Nakit Toplam', max_digits=15, decimal_places=2, default=0)
    kredi_karti_toplam = models.DecimalField('Kredi Kartı Toplam', max_digits=15, decimal_places=2, default=0)
    havale_toplam = models.DecimalField('Havale Toplam', max_digits=15, decimal_places=2, default=0)
    cek_toplam = models.DecimalField('Çek Toplam', max_digits=15, decimal_places=2, default=0)
    senet_toplam = models.DecimalField('Senet Toplam', max_digits=15, decimal_places=2, default=0)
    
    # Hesaplama Detayları
    gecikme_tutari = models.DecimalField('Gecikme Tutarı', max_digits=15, decimal_places=2, default=0)
    toplam_prim = models.DecimalField('Toplam Prim', max_digits=15, decimal_places=2, default=0)
    
    olusturan = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='Oluşturan')
    olusturma_tarihi = models.DateTimeField('Oluşturma Tarihi', auto_now_add=True)
    guncelleme_tarihi = models.DateTimeField('Güncelleme Tarihi', auto_now=True)

    class Meta:
        verbose_name = 'Plasiyer Prim'
        verbose_name_plural = 'Plasiyer Primleri'
        ordering = ['-donem_yil', '-donem_ay', 'plasiyer']
        unique_together = ['plasiyer', 'donem_ay', 'donem_yil']

    def __str__(self):
        return f"{self.plasiyer} - {self.donem_ay}/{self.donem_yil}"
