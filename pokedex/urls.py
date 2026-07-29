from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("my_pokemon/", views.my_pokemon, name="my_pokemon"),
    path(
        "my_pokemon/<int:pk>/delete/",
        views.delete_saved_pokemon,
        name="delete_saved_pokemon",
    ),
    path("battle/", views.battle_view, name="battle"),
    path("battle/result/", views.battle_result, name="battle_result"),
    path("battle/history/", views.battle_history, name="battle_history"),
    path("api/pokemon-names/", views.pokemon_names, name="pokemon_names"),
    path("healthz/", views.healthz, name="healthz"),
]
