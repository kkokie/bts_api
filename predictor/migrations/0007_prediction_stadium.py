from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('predictor', '0006_prediction_got_hit'),
    ]

    operations = [
        migrations.AddField(
            model_name='prediction',
            name='stadium',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
    ]
