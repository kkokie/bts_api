from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('predictor', '0005_prediction_pitcher_hand_prediction_score_breakdown'),
    ]

    operations = [
        migrations.AddField(
            model_name='prediction',
            name='got_hit',
            field=models.BooleanField(blank=True, default=None, null=True),
        ),
    ]
