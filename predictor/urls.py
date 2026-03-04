from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('scoring/', views.scoring_guide, name='scoring_guide'),
]