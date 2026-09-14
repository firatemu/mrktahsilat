from django.db import migrations, models


def rename_plasiyer_necati_to_bakir(apps, schema_editor):
    PlasiyerPrim = apps.get_model('tahsilat', 'PlasiyerPrim')
    HakedisHedef = apps.get_model('tahsilat', 'HakedisHedef')
    PlasiyerPrim.objects.filter(plasiyer='NECATİ').update(plasiyer='BAKIR')
    HakedisHedef.objects.filter(plasiyer='NECATİ').update(plasiyer='BAKIR')


def rename_plasiyer_bakir_to_necati(apps, schema_editor):
    PlasiyerPrim = apps.get_model('tahsilat', 'PlasiyerPrim')
    HakedisHedef = apps.get_model('tahsilat', 'HakedisHedef')
    PlasiyerPrim.objects.filter(plasiyer='BAKIR').update(plasiyer='NECATİ')
    HakedisHedef.objects.filter(plasiyer='BAKIR').update(plasiyer='NECATİ')


PLASIYER_CHOICES = [
    ('ALİ', 'ALİ'),
    ('AZİZ', 'AZİZ'),
    ('CAN', 'CAN'),
    ('EYÜP', 'EYÜP'),
    ('BAKIR', 'BAKIR'),
    ('HASAN', 'HASAN'),
    ('YİĞİT', 'YİĞİT'),
    ('ATAKAN', 'ATAKAN'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('tahsilat', '0023_rename_tahsilat_h_donem_y_273547_idx_tahsilat_ha_donem_y_e49abb_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(
            rename_plasiyer_necati_to_bakir,
            rename_plasiyer_bakir_to_necati,
        ),
        migrations.AlterField(
            model_name='plasiyerprim',
            name='plasiyer',
            field=models.CharField(
                choices=PLASIYER_CHOICES,
                max_length=50,
                verbose_name='Plasiyer',
            ),
        ),
    ]
