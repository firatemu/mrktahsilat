import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mrktahsilat.settings')
django.setup()

from tahsilat.models import CekSenet

total = CekSenet.objects.count()
cek_count = CekSenet.objects.filter(odeme_turu='cek').count()
senet_count = CekSenet.objects.filter(odeme_turu='senet').count()
kredi_count = CekSenet.objects.filter(odeme_turu='kredi').count()
kredi_karti_count = CekSenet.objects.filter(odeme_turu='kredi_karti').count()
other_count = CekSenet.objects.exclude(odeme_turu__in=['cek', 'senet', 'kredi', 'kredi_karti']).count()

print(f"✓ Total CekSenet records: {total}")
print(f"✓ Cek records: {cek_count}")
print(f"✓ Senet records: {senet_count}")
print(f"✓ Kredi records: {kredi_count}")
print(f"✓ Kredi Kartı records: {kredi_karti_count}")
print(f"✓ Other records: {other_count}")

if senet_count == 0:
    print("\n✓ All SENET records have been successfully deleted!")
else:
    print(f"\n⚠ Warning: {senet_count} SENET records still exist")

# Show distribution of remaining records
print("\nDistribution of remaining records:")
for record in CekSenet.objects.all()[:10]:
    print(f"  - {record.odeme_turu}: {record.cari_unvan} - {record.tutar} {record.para_birimi}")



