from django.test import SimpleTestCase

from ..data.type_chart import TYPE_CHART, TYPES, describe, effectiveness
from ..services import battle
from .factories import move, profile


class TypeChartTests(SimpleTestCase):
    def test_every_type_has_a_row(self):
        self.assertEqual(set(TYPE_CHART), set(TYPES))

    def test_every_referenced_type_is_known(self):
        for attacking, row in TYPE_CHART.items():
            for defending in row:
                self.assertIn(defending, TYPES, f"{attacking} -> {defending}")

    def test_known_matchups(self):
        cases = [
            ("water", ["fire"], 2.0),
            ("fire", ["water"], 0.5),
            ("electric", ["ground"], 0.0),
            ("normal", ["ghost"], 0.0),
            ("ghost", ["normal"], 0.0),
            ("dragon", ["fairy"], 0.0),
            ("fighting", ["normal"], 2.0),
            ("grass", ["water"], 2.0),
        ]
        for attacking, defending, expected in cases:
            with self.subTest(attacking=attacking, defending=defending):
                self.assertEqual(effectiveness(attacking, defending), expected)

    def test_dual_types_stack(self):
        # Rock is 2x on both halves of fire/flying, so Charizard takes 4x.
        self.assertEqual(effectiveness("rock", ["fire", "flying"]), 4.0)
        # Water is 2x on fire but 0.5x on... nothing here; ground/rock is 4x.
        self.assertEqual(effectiveness("water", ["ground", "rock"]), 4.0)

    def test_immunity_beats_weakness_in_a_dual_type(self):
        # Ground is 2x on steel but 0x on flying -> 0x overall.
        self.assertEqual(effectiveness("ground", ["steel", "flying"]), 0.0)

    def test_unknown_type_is_neutral(self):
        self.assertEqual(effectiveness("stardust", ["fire"]), 1.0)
        self.assertEqual(effectiveness("", ["fire"]), 1.0)

    def test_describe_labels(self):
        self.assertEqual(describe(0), "No effect")
        self.assertEqual(describe(1), "Neutral")
        self.assertEqual(describe(2), "Super effective")
        self.assertEqual(describe(4), "Devastating")


class BattleScoringTests(SimpleTestCase):
    def test_type_advantage_decides_an_otherwise_even_match(self):
        """The point of the rewrite: typing has to matter.

        Two Pokemon with identical stats and identical move power, where only
        the type matchup differs, must not tie.
        """
        stats = {
            "hp": 80, "attack": 80, "defense": 80,
            "special-attack": 80, "special-defense": 80, "speed": 80,
        }
        fire = profile(
            number=1, name="Firemon", types=("fire",), stats=dict(stats),
            best_move=move("flamethrower", power=90, type="fire"),
        )
        water = profile(
            number=2, name="Watermon", types=("water",), stats=dict(stats),
            best_move=move("surf", power=90, type="water"),
        )

        outcome = battle.resolve(fire, water)

        self.assertFalse(outcome.is_tie)
        self.assertEqual(outcome.winner.name, "Watermon")
        self.assertEqual(outcome.first.multiplier, 0.5)   # fire into water
        self.assertEqual(outcome.second.multiplier, 2.0)  # water into fire

    def test_identical_pokemon_tie(self):
        one = profile(number=1, name="A", best_move=move())
        two = profile(number=2, name="B", best_move=move())

        outcome = battle.resolve(one, two)

        self.assertTrue(outcome.is_tie)
        self.assertIsNone(outcome.winner)
        self.assertIsNone(outcome.loser)
        self.assertFalse(outcome.first_won)
        self.assertFalse(outcome.second_won)

    def test_pokemon_without_a_damaging_move_scores_on_stats_alone(self):
        attacker = profile(number=1, name="A", best_move=None)
        defender = profile(number=2, name="B", best_move=move())

        outcome = battle.resolve(attacker, defender)

        self.assertEqual(outcome.first.move_score, 0)
        self.assertEqual(outcome.first.total, outcome.first.stat_score)

    def test_immune_defender_nullifies_the_move(self):
        attacker = profile(
            number=1, name="Zapmon", types=("electric",),
            best_move=move("thunderbolt", power=90, type="electric"),
        )
        defender = profile(number=2, name="Digmon", types=("ground",))

        outcome = battle.resolve(attacker, defender)

        self.assertEqual(outcome.first.multiplier, 0.0)
        self.assertEqual(outcome.first.move_score, 0)

    def test_stab_increases_the_move_contribution(self):
        defender = profile(number=2, name="B", types=("normal",))
        without = battle.score(
            profile(number=1, name="A", types=("fire",),
                    best_move=move("hyper-beam", power=100, type="normal", stab=False)),
            defender,
        )
        with_stab = battle.score(
            profile(number=1, name="A", types=("fire",),
                    best_move=move("fire-blast", power=100, type="fire", stab=True)),
            defender,
        )

        self.assertEqual(with_stab.move_score, round(without.move_score * 1.5))
        self.assertTrue(with_stab.stab)
        self.assertFalse(without.stab)

    def test_winner_and_loser_are_consistent(self):
        strong = profile(
            number=1, name="Strong",
            stats={"hp": 150, "attack": 150, "defense": 150,
                   "special-attack": 150, "special-defense": 150, "speed": 150},
        )
        weak = profile(number=2, name="Weak")

        outcome = battle.resolve(strong, weak)

        self.assertTrue(outcome.first_won)
        self.assertFalse(outcome.second_won)
        self.assertEqual(outcome.winner.name, "Strong")
        self.assertEqual(outcome.loser.name, "Weak")


class StatComparisonTests(SimpleTestCase):
    def test_rows_cover_all_six_stats(self):
        rows = battle.stat_comparison(profile(), profile())
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["label"], "Hp")

    def test_percentages_sum_to_100_and_mark_the_leader(self):
        left = profile(stats={"hp": 75, "attack": 0, "defense": 0,
                              "special-attack": 0, "special-defense": 0, "speed": 0})
        right = profile(stats={"hp": 25, "attack": 0, "defense": 0,
                               "special-attack": 0, "special-defense": 0, "speed": 0})

        hp_row = battle.stat_comparison(left, right)[0]

        self.assertEqual(hp_row["left_pct"], 75)
        self.assertEqual(hp_row["right_pct"], 25)
        self.assertEqual(hp_row["leader"], "left")

    def test_zero_zero_stat_does_not_divide_by_zero(self):
        zeros = dict.fromkeys(
            ("hp", "attack", "defense", "special-attack", "special-defense", "speed"), 0
        )
        rows = battle.stat_comparison(profile(stats=dict(zeros)), profile(stats=dict(zeros)))
        self.assertEqual(rows[0]["left_pct"], 50)
        self.assertEqual(rows[0]["leader"], "")
