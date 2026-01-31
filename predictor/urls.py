from django.urls import path
from . import views

urlpatterns = [
    # When the user goes to the homepage (''), call the dashboard view
    path('', views.dashboard, name='dashboard'),
]