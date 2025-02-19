from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', views.index, name='index'),
path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('my_pokemon/', views.my_pokemon, name='my_pokemon'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
]
