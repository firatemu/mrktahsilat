import json
from django.db import migrations, models


def normalize_hakedis_target_types(apps, schema_editor):
    HakedisHedef = apps.get_model('tahsilat', 'HakedisHedef')

    for hedef in HakedisHedef.objects.all().iterator():
        raw_value = ' '.join(str(hedef.malzeme_turu or '').strip().split())
        if not raw_value:
            normalized_value = ''
        else:
            try:
                parsed = json.loads(raw_value)
                if isinstance(parsed, list):
                    values = parsed
                else:
                    values = [raw_value]
            except (TypeError, ValueError, json.JSONDecodeError):
                values = [raw_value]

            normalized = []
            seen = set()
            for item in values:
                value = ' '.join(str(item or '').strip().split())
                if not value:
                    continue
                upper_value = value.upper()
                if upper_value in seen:
                    continue
                normalized.append(value)
                seen.add(upper_value)

            if not normalized:
                normalized_value = ''
            else:
                normalized.sort(key=lambda item: item.upper())
                normalized_value = json.dumps(normalized, ensure_ascii=False)

        if hedef.malzeme_turu != normalized_value:
            hedef.malzeme_turu = normalized_value
            hedef.save(update_fields=['malzeme_turu'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('tahsilat', '0021_hakedishedef'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hakedishedef',
            name='malzeme_turu',
            field=models.CharField(blank=True, default='', max_length=500, verbose_name='Malzeme Türü'),
        ),
        migrations.RunPython(normalize_hakedis_target_types, noop_reverse),
    ]
