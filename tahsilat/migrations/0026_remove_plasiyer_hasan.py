from django.db import migrations, models


def deactivate_hasan_hedefleri(apps, schema_editor):
    HakedisHedef = apps.get_model('tahsilat', 'HakedisHedef')
    HakedisHedef.objects.filter(plasiyer='HASAN').update(aktif=False)


def reactivate_hasan_hedefleri(apps, schema_editor):
    HakedisHedef = apps.get_model('tahsilat', 'HakedisHedef')
    HakedisHedef.objects.filter(plasiyer='HASAN').update(aktif=True)


PLASIYER_CHOICES = [
    ('ALİ', 'ALİ'),
    ('AZİZ', 'AZİZ'),
    ('CAN', 'CAN'),
    ('EYÜP', 'EYÜP'),
    ('BAKIR', 'BAKIR'),
    ('YİĞİT', 'YİĞİT'),
    ('ATAKAN', 'ATAKAN'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('tahsilat', '0025_alter_hakedishedef_marka'),
    ]

    operations = [
        migrations.RunPython(
            deactivate_hasan_hedefleri,
            reactivate_hasan_hedefleri,
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
