from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('predictor', '0012_prediction_lineup_confirmed'),
    ]

    operations = [
        migrations.AddField(
            model_name='prediction',
            name='xba',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='prediction',
            name='score_breakdown',
            field=models.CharField(blank=True, default='', max_length=1200),
        ),
    ]
