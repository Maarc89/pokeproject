from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from ..models import Battle, Move, PokemonSpecies, SavedPokemon


class SpeciesTests(TestCase):
    def test_number_is_unique(self):
        PokemonSpecies.objects.create(number=25, slug="pikachu", name="Pikachu")
        with self.assertRaises(IntegrityError):
            PokemonSpecies.objects.create(number=25, slug="clone", name="Clone")

    def test_stat_total(self):
        species = PokemonSpecies.objects.create(
            number=25, slug="pikachu", name="Pikachu",
            stats={"hp": 35, "attack": 55, "speed": 90},
        )
        self.assertEqual(species.stat_total, 180)

    def test_stat_total_handles_missing_stats(self):
        species = PokemonSpecies.objects.create(number=1, slug="a", name="A")
        self.assertEqual(species.stat_total, 0)


class SavedPokemonTests(TestCase):
    def setUp(self):
        self.ash = User.objects.create_user("ash", password="pw-for-tests-123")
        self.misty = User.objects.create_user("misty", password="pw-for-tests-123")
        self.pikachu = PokemonSpecies.objects.create(
            number=25, slug="pikachu", name="Pikachu"
        )

    def test_a_user_cannot_save_the_same_species_twice(self):
        SavedPokemon.objects.create(user=self.ash, species=self.pikachu)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SavedPokemon.objects.create(user=self.ash, species=self.pikachu)

    def test_two_users_can_both_save_the_same_species(self):
        """The whole reason the schema was split.

        Under the original global-unique constraint only one account in the
        entire system could ever save Pikachu.
        """
        SavedPokemon.objects.create(user=self.ash, species=self.pikachu)
        SavedPokemon.objects.create(user=self.misty, species=self.pikachu)

        self.assertEqual(SavedPokemon.objects.count(), 2)
        self.assertEqual(PokemonSpecies.objects.count(), 1, "species is shared, not duplicated")

    def test_saved_pokemon_is_scoped_to_its_owner(self):
        SavedPokemon.objects.create(user=self.ash, species=self.pikachu)
        self.assertEqual(SavedPokemon.objects.filter(user=self.misty).count(), 0)


class MoveTests(TestCase):
    def test_display_name_humanises_the_slug(self):
        self.assertEqual(Move(name="double-edge").display_name, "Double Edge")

    def test_name_is_unique(self):
        Move.objects.create(name="tackle", power=40)
        with self.assertRaises(IntegrityError):
            Move.objects.create(name="tackle", power=40)

    def test_deleting_a_move_leaves_the_species(self):
        move = Move.objects.create(name="tackle", power=40)
        species = PokemonSpecies.objects.create(
            number=1, slug="a", name="A", best_move=move
        )
        move.delete()
        species.refresh_from_db()
        self.assertIsNone(species.best_move)


class BattleModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ash", password="pw-for-tests-123")
        self.a = PokemonSpecies.objects.create(number=6, slug="charizard", name="Charizard")
        self.b = PokemonSpecies.objects.create(number=9, slug="blastoise", name="Blastoise")

    def test_tie_has_no_winner(self):
        battle = Battle.objects.create(
            user=self.user, species_1=self.a, species_2=self.b, score_1=10, score_2=10
        )
        self.assertTrue(battle.is_tie)
        self.assertIn("tie", str(battle))

    def test_winner_is_recorded(self):
        battle = Battle.objects.create(
            user=self.user, species_1=self.a, species_2=self.b,
            winner=self.b, score_1=10, score_2=20,
        )
        self.assertFalse(battle.is_tie)
        self.assertIn("Blastoise", str(battle))

    def test_ordered_most_recent_first(self):
        first = Battle.objects.create(
            user=self.user, species_1=self.a, species_2=self.b, score_1=1, score_2=2
        )
        second = Battle.objects.create(
            user=self.user, species_1=self.a, species_2=self.b, score_1=3, score_2=4
        )
        self.assertEqual(list(Battle.objects.all()), [second, first])
