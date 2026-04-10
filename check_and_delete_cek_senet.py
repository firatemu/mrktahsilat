import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mrktahsilat.settings')
django.setup()

from tahsilat.models import CekSenet
import json
from datetime import datetime

# Check current records
total = CekSenet.objects.count()
cek_count = CekSenet.objects.filter(odeme_turu='cek').count()
senet_count = CekSenet.objects.filter(odeme_turu='senet').count()

print(f"Current status:")
print(f"  Total CekSenet records: {total}")
print(f"  Cek records (odeme_turu='cek'): {cek_count}")
print(f"  Senet records (odeme_turu='senet'): {senet_count}")

if cek_count > 0 or senet_count > 0:
    # Get all records to be deleted
    records_to_delete = CekSenet.objects.filter(odeme_turu__in=['cek', 'senet'])
    
    # Create backup data
    backup_data = []
    for record in records_to_delete:
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
    backup_filename = f'/var/www/mrktahsilat/backup_cek_senet_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(backup_filename, 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n✓ Backup created: {backup_filename}")
    print(f"✓ Total records backed up: {len(backup_data)}")
    
    # Delete the records
    deleted_count, _ = CekSenet.objects.filter(odeme_turu__in=['cek', 'senet']).delete()
    print(f"\n✓ Deleted {deleted_count} records with odeme_turu='cek' or 'senet'")
    
else:
    print("\n✓ No Çek or Senet records found to delete")

# Show final status
remaining_total = CekSenet.objects.count()
remaining_cek = CekSenet.objects.filter(odeme_turu='cek').count()
remaining_senet = CekSenet.objects.filter(odeme_turu='senet').count()

print(f"\nFinal status:")
print(f"  Total records: {remaining_total}")
print(f"  Cek records: {remaining_cek}")
print(f"  Senet records: {remaining_senet}")

if remaining_cek == 0 and remaining_senet == 0:
    print("\n✓ All Çek and Senet records have been successfully deleted!")
else:
    print(f"\n⚠ Warning: {remaining_cek + remaining_senet} records still exist")



