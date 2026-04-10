import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mrktahsilat.settings')
django.setup()

from tahsilat.models import CekSenet

total = CekSenet.objects.count()
cek_count = CekSenet.objects.filter(odeme_turu='cek').count()
senet_count = CekSenet.objects.filter(odeme_turu='senet').count()
other_count = CekSenet.objects.exclude(odeme_turu__in=['cek', 'senet']).count()

print(f"✓ Total CekSenet records: {total}")
print(f"✓ Cek records (odeme_turu='cek'): {cek_count}")
print(f"✓ Senet records: {senet_count}")
print(f"✓ Other records: {other_count}")

if cek_count == 0:
    print("\n✓ All ÇEK records have been successfully deleted!")
else:
    print(f"\n⚠ Warning: {cek_count} ÇEK records still exist")



