from django.db import models


class Prediction(models.Model):
    LIST_A = 'A'
    LIST_B = 'B'
    LIST_CHOICES = [(LIST_A, 'A-List'), (LIST_B, 'B-List')]

    date = models.DateField(db_index=True)
    list_type = models.CharField(max_length=1, choices=LIST_CHOICES)
    name = models.CharField(max_length=100)
    team = models.CharField(max_length=50)
    position = models.CharField(max_length=20, default='Unknown')
    bats = models.CharField(max_length=1, default='R')
    score = models.FloatField(default=0.0)
    opponent = models.CharField(max_length=20, default='Unknown')
    probable_pitcher = models.CharField(max_length=100, default='TBD')
    pitcher_era = models.FloatField(null=True, blank=True)
    pitcher_whip = models.FloatField(null=True, blank=True)
    avg_batting_order = models.FloatField(null=True, blank=True)
    notes = models.CharField(max_length=200, blank=True, default='')
    pitcher_hand = models.CharField(max_length=1, blank=True, default='')
    score_breakdown = models.CharField(max_length=800, blank=True, default='')
    got_hit = models.BooleanField(null=True, blank=True, default=None)
    stadium = models.CharField(max_length=100, blank=True, default='')
    game_time = models.CharField(max_length=20, blank=True, default='')
    park_factor = models.IntegerField(null=True, blank=True)
    is_home = models.BooleanField(default=True)
    team_rank = models.IntegerField(null=True, blank=True)
    hit_streak = models.IntegerField(default=0)
    lineup_confirmed = models.BooleanField(null=True, blank=True, default=None)

    class Meta:
        unique_together = ('date', 'name')
        ordering = ['-score']

    @property
    def fire_emojis(self):
        return '🔥' * (self.hit_streak // 3)

    def __str__(self):
        return f"{self.date} | {self.list_type} | {self.name} ({self.score})"

    @classmethod
    def save_from_lists(cls, date, a_list, b_list):
        """Wipes existing predictions for this date and saves new ones via bulk insert."""
        cls.objects.filter(date=date).delete()

        objects = [
            cls(
                date=date, list_type=cls.LIST_A,
                name=p['Name'], team=p['Team'], position=p['Pos'],
                bats=p['Bats'], score=p['Score'], opponent=p['Opponent'],
                probable_pitcher=p['Probable_Pitcher'],
                pitcher_era=p.get('Pitcher_ERA'), pitcher_whip=p.get('Pitcher_WHIP'),
                avg_batting_order=p.get('Avg_Order'),
                notes=p['Notes'],
                pitcher_hand=p.get('Pitcher_Hand', ''),
                score_breakdown=p.get('Score_Breakdown', ''),
                stadium=p.get('Stadium', ''),
                game_time=p.get('Game_Time', ''),
                park_factor=p.get('Park_Factor'),
                is_home=p.get('Is_Home', True),
                team_rank=p.get('Team_Rank'),
                hit_streak=p.get('Hit_Streak', 0),
            )
            for p in a_list
        ] + [
            cls(
                date=date, list_type=cls.LIST_B,
                name=p['Name'], team=p['Team'], position=p['Pos'],
                bats=p['Bats'], score=p['Score'], opponent=p['Opponent'],
                probable_pitcher=p['Probable_Pitcher'],
                pitcher_era=p.get('Pitcher_ERA'), pitcher_whip=p.get('Pitcher_WHIP'),
                avg_batting_order=p.get('Avg_Order'),
                notes=p['Notes'],
                pitcher_hand=p.get('Pitcher_Hand', ''),
                score_breakdown=p.get('Score_Breakdown', ''),
                stadium=p.get('Stadium', ''),
                game_time=p.get('Game_Time', ''),
                park_factor=p.get('Park_Factor'),
                is_home=p.get('Is_Home', True),
                team_rank=p.get('Team_Rank'),
                hit_streak=p.get('Hit_Streak', 0),
            )
            for p in b_list
        ]

        cls.objects.bulk_create(objects)
        return len(a_list), len(b_list)
