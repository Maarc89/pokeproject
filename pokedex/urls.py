from django.urls import path
from . import views
from django.contrib.auth import views as auth_views
from django.contrib.auth.views import LogoutView

urlpatterns = [
    path('', views.index, name='index'),
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('my_pokemon/', views.my_pokemon, name='my_pokemon'),
    path('logout/', LogoutView.as_view(next_page='index'), name='logout'),
]
