import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mrktahsilat.settings')
django.setup()

from tahsilat.models import CekSenet
import json
from datetime import datetime

# Get all records to be deleted
cek_records = CekSenet.objects.filter(odeme_turu='cek')

# Create backup data
backup_data = []
for record in cek_records:
    backup_data.append({
        'id': record.id,
        'odeme_turu': record.odeme_turu,
        'tip': record.tip,
        'durum': record.durum,
        'tutar': str(record.tutar),
        'para_birimi': record.para_birimi,
        'islem_tarihi': record.islem_tarihi.isoformat() if record.islem_tarihi else None,
        'vade_tarihi': record.vade_tarihi.isoformat() if record.vade_tarihi else None,
        'odeme_tarihi': record.odeme_tarihi.isoformat() if record.odeme_tarihi else None,
        'cari_kod': record.cari_kod,
        'cari_unvan': record.cari_unvan,
        'banka_adi': record.banka_adi,
        'cek_senet_no': record.cek_senet_no,
        'banka_sube': record.banka_sube,
        'hesap_no': record.hesap_no,
        'kredi_turu': record.kredi_turu,
        'faiz_orani': str(record.faiz_orani) if record.faiz_orani else None,
        'taksit_sayisi': record.taksit_sayisi,
        'aciklama': record.aciklama,
        'notlar': record.notlar,
        'olusturan': record.olusturan.username if record.olusturan else None,
        'olusturma_tarihi': record.olusturma_tarihi.isoformat(),
        'guncelleme_tarihi': record.guncelleme_tarihi.isoformat(),
    })

# Save backup to file
backup_filename = f'/var/www/mrktahsilat/backup_cek_records_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
with open(backup_filename, 'w', encoding='utf-8') as f:
    json.dump(backup_data, f, ensure_ascii=False, indent=2)

print(f"Backup created: {backup_filename}")
print(f"Total records backed up: {len(backup_data)}")

# Now delete the records
if len(backup_data) > 0:
    deleted_count, _ = CekSenet.objects.filter(odeme_turu='cek').delete()
    print(f"\nDeleted {deleted_count} records with odeme_turu='cek'")
else:
    print("\nNo records to delete")

# Show remaining records
remaining_total = CekSenet.objects.count()
remaining_cek = CekSenet.objects.filter(odeme_turu='cek').count()
print(f"\nRemaining records:")
print(f"  Total: {remaining_total}")
print(f"  Cek: {remaining_cek}")



