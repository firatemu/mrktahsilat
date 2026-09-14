from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tahsilat', '0024_plasiyer_necati_to_bakir'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hakedishedef',
            name='marka',
            field=models.CharField(max_length=500, verbose_name='Marka'),
        ),
    ]
