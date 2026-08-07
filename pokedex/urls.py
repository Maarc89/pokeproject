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
    # Interactive battle. The <int:pk> routes come after the literal ones so
    # "play" and "games" can never be read as a primary key.
    path("battle/play/", views.play_setup, name="play_setup"),
    path("battle/play/draft/", views.play_draft, name="play_draft"),
    path("battle/play/start/", views.play_start, name="play_start"),
    path("battle/games/", views.play_history, name="play_history"),
    path("battle/play/<int:pk>/", views.play, name="play"),
    path("battle/play/<int:pk>/move/", views.play_move, name="play_move"),
    path("battle/play/<int:pk>/forfeit/", views.play_forfeit, name="play_forfeit"),
    path("api/pokemon-names/", views.pokemon_names, name="pokemon_names"),
    path("healthz/", views.healthz, name="healthz"),
]
