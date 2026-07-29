"""Carry rows from the old per-user Pokemon table into the new split schema.

The old table stored one denormalized copy of the PokeAPI data per user. Each
distinct Pokemon number becomes a single shared PokemonSpecies, and each original
row becomes a SavedPokemon pointing at it.
"""

from django.db import migrations


def split_list(value):
    """The old columns held ', '-joined strings; the new ones hold JSON lists."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def forwards(apps, schema_editor):
    Pokemon = apps.get_model("pokedex", "Pokemon")
    PokemonSpecies = apps.get_model("pokedex", "PokemonSpecies")
    SavedPokemon = apps.get_model("pokedex", "SavedPokemon")

    species_by_number = {}

    for legacy in Pokemon.objects.all().order_by("number", "pk"):
        species = species_by_number.get(legacy.number)
        if species is None:
            species, _ = PokemonSpecies.objects.get_or_create(
                number=legacy.number,
                defaults={
                    # The old schema had no slug; derive one from the name.
                    "slug": legacy.name.strip().lower().replace(" ", "-"),
                    "name": legacy.name,
                    # Types were stored capitalized; PokeAPI slugs are lowercase
                    # and the new type-badge CSS keys off the lowercase form.
                    "types": [t.lower() for t in split_list(legacy.types)],
                    "abilities": split_list(legacy.abilities),
                    "sprite": legacy.sprite or "",
                    "stats": {},
                },
            )
            species_by_number[legacy.number] = species

        SavedPokemon.objects.get_or_create(user_id=legacy.user_id, species=species)


def backwards(apps, schema_editor):
    Pokemon = apps.get_model("pokedex", "Pokemon")
    SavedPokemon = apps.get_model("pokedex", "SavedPokemon")

    for saved in SavedPokemon.objects.select_related("species").all():
        species = saved.species
        Pokemon.objects.get_or_create(
            user_id=saved.user_id,
            number=species.number,
            defaults={
                "name": species.name,
                "types": ", ".join(t.capitalize() for t in species.types),
                "abilities": ", ".join(species.abilities),
                "sprite": species.sprite,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("pokedex", "0002_add_species_move_saved_battle"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
