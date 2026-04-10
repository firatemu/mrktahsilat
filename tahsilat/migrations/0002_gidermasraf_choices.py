# Generated manually for GiderMasraf choices

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tahsilat', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='GiderMasraf',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('gider_adi', models.CharField(choices=[('dolmus', 'Dolmuş'), ('nakliye', 'Nakliye'), ('kahve', 'Kahve'), ('temizlik_malzemesi', 'Temizlik Malzemesi'), ('diger', 'Diğer')], max_length=50, verbose_name='Gider Adı')),
                ('aciklama', models.TextField(blank=True, verbose_name='Açıklama')),
                ('tutar', models.DecimalField(decimal_places=2, max_digits=15, verbose_name='Tutar')),
                ('tarih', models.DateField(verbose_name='Tarih')),
                ('eklenen_tarih', models.DateTimeField(auto_now_add=True, verbose_name='Eklenen Tarih')),
                ('ekleyen', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='auth.user', verbose_name='Ekleyen')),
            ],
            options={
                'verbose_name': 'Gider Masraf',
                'verbose_name_plural': 'Gider Masrafları',
                'ordering': ['-tarih', '-eklenen_tarih'],
            },
        ),
    ]