from django.contrib import admin

from .models import Battle, Move, PokemonSpecies, SavedPokemon


@admin.register(Move)
class MoveAdmin(admin.ModelAdmin):
    list_display = ("name", "power", "type", "damage_class")
    list_filter = ("type", "damage_class")
    search_fields = ("name",)


@admin.register(PokemonSpecies)
class PokemonSpeciesAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "stat_total", "best_move", "fetched_at")
    search_fields = ("name", "slug", "number")
    ordering = ("number",)
    autocomplete_fields = ("best_move",)
    readonly_fields = ("fetched_at",)


@admin.register(SavedPokemon)
class SavedPokemonAdmin(admin.ModelAdmin):
    list_display = ("species", "user", "saved_at")
    list_filter = ("saved_at",)
    search_fields = ("user__username", "species__name")
    autocomplete_fields = ("species",)
    list_select_related = ("species", "user")


@admin.register(Battle)
class BattleAdmin(admin.ModelAdmin):
    list_display = ("__str__", "user", "score_1", "score_2", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__username", "species_1__name", "species_2__name")
    list_select_related = ("species_1", "species_2", "winner", "user")
    readonly_fields = ("created_at",)
