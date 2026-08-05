from django.test import SimpleTestCase

from ..data.type_chart import (
    TYPE_CHART,
    TYPES,
    defensive_profile,
    describe,
    effectiveness,
)
from ..services import battle
from .factories import move, profile

EVEN_STATS = {
    "hp": 80, "attack": 80, "defense": 80,
    "special-attack": 80, "special-defense": 80, "speed": 80,
}


def even(number, name, types=("normal",), best_move=None, moves=None, **overrides):
    """A Pokemon with flat 80s, so only the thing under test varies."""
    stats = dict(EVEN_STATS, **overrides)
    return profile(
        number=number, name=name, types=types, stats=stats,
        best_move=best_move, moves=moves,
    )


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


class DefensiveProfileTests(SimpleTestCase):
    def test_single_type(self):
        result = defensive_profile(["electric"])

        self.assertEqual([e["type"] for e in result["weaknesses"]], ["ground"])
        self.assertEqual(
            [e["type"] for e in result["resistances"]], ["electric", "flying", "steel"]
        )
        self.assertEqual(result["immunities"], [])

    def test_dual_type_stacks_into_a_4x_weakness(self):
        # The classic: Rock hits both halves of Fire/Flying.
        result = defensive_profile(["fire", "flying"])

        worst = result["weaknesses"][0]
        self.assertEqual(worst["type"], "rock")
        self.assertEqual(worst["multiplier"], 4.0)

    def test_immunity_is_reported_separately_from_resistance(self):
        result = defensive_profile(["flying"])

        self.assertEqual([e["type"] for e in result["immunities"]], ["ground"])
        self.assertNotIn("ground", [e["type"] for e in result["resistances"]])

    def test_neutral_matchups_are_omitted(self):
        result = defensive_profile(["normal"])

        listed = {
            e["type"]
            for bucket in result.values()
            for e in bucket
        }
        self.assertNotIn("water", listed)
        self.assertEqual([e["type"] for e in result["immunities"]], ["ghost"])


class DamageFormulaTests(SimpleTestCase):
    """The formula, checked against values worked out by hand.

    At level 50 with flat 80 base stats: A = D = 111, so A/D cancels and
    ((2*50/5 + 2) * 90 * 1)/50 + 2 = 41.6 before modifiers.
    """

    def _attack(self, attacker, defender, the_move):
        return battle.damage_of(the_move, attacker, defender)

    def test_neutral_hit(self):
        # Fire attacker, Normal move: neutral into Normal, and no STAB.
        attack = self._attack(
            even(1, "A", types=("fire",)),
            even(2, "B", types=("normal",)),
            move("swift", power=90, type="normal"),
        )
        self.assertEqual(attack.damage, 41)
        self.assertEqual(attack.multiplier, 1.0)
        self.assertFalse(attack.stab)

    def test_stab_multiplies_by_one_and_a_half(self):
        attack = self._attack(
            even(1, "A", types=("normal",)),
            even(2, "B", types=("normal",)),
            move("body-slam", power=90, type="normal"),
        )
        self.assertTrue(attack.stab)
        self.assertEqual(attack.damage, 62)  # floor(41.6 * 1.5)

    def test_super_effective_doubles(self):
        attack = self._attack(
            even(1, "A", types=("normal",)),
            even(2, "B", types=("fire",)),
            move("surf", power=90, type="water"),
        )
        self.assertEqual(attack.multiplier, 2.0)
        self.assertEqual(attack.damage, 83)  # floor(41.6 * 2)

    def test_immunity_deals_nothing(self):
        attack = self._attack(
            even(1, "A", types=("electric",)),
            even(2, "B", types=("ground",)),
            move("thunderbolt", power=90, type="electric"),
        )
        self.assertEqual(attack.multiplier, 0.0)
        self.assertEqual(attack.damage, 0)

    def test_a_landed_hit_always_does_at_least_one(self):
        """Resisted chip damage must not round down to nothing."""
        attack = self._attack(
            even(1, "A", types=("normal",), **{"attack": 1}),
            even(2, "B", types=("rock", "steel"), **{"defense": 250}),
            move("tackle", power=5, type="normal"),
        )
        self.assertGreater(attack.multiplier, 0)
        self.assertEqual(attack.damage, 1)

    def test_physical_move_uses_attack_against_defense(self):
        attack = self._attack(
            even(1, "A", types=("normal",), **{"attack": 120, "special-attack": 10}),
            even(2, "B", types=("normal",), **{"defense": 60, "special-defense": 200}),
            move("strength", power=100, type="normal", damage_class="physical"),
        )
        self.assertEqual(attack.attack_stat, 151)   # 120 + 26 + 5
        self.assertEqual(attack.defense_stat, 91)   # 60 + 26 + 5
        self.assertEqual(attack.category, "physical")

    def test_special_move_uses_the_special_stats(self):
        attack = self._attack(
            even(1, "A", types=("normal",), **{"attack": 120, "special-attack": 40}),
            even(2, "B", types=("normal",), **{"defense": 60, "special-defense": 130}),
            move("psychic", power=100, type="psychic", damage_class="special"),
        )
        self.assertEqual(attack.attack_stat, 71)    # 40 + 26 + 5
        self.assertEqual(attack.defense_stat, 161)  # 130 + 26 + 5
        self.assertEqual(attack.damage, 21)

    def test_the_same_move_hurts_more_against_the_frailer_defence(self):
        """The core gap this rewrite closes: defence has to reduce damage."""
        physical = move("strength", power=100, type="normal", damage_class="physical")
        attacker = even(1, "A", types=("normal",))

        into_paper = self._attack(attacker, even(2, "B", **{"defense": 20}), physical)
        into_wall = self._attack(attacker, even(3, "C", **{"defense": 230}), physical)

        self.assertGreater(into_paper.damage, into_wall.damage)


class MoveChoiceTests(SimpleTestCase):
    """Picking the move after seeing the opponent, not before."""

    MOVESET = [
        move("fire-blast", power=110, type="fire", damage_class="special"),
        move("earthquake", power=100, type="ground", damage_class="physical"),
    ]

    def test_the_chosen_move_changes_with_the_defender(self):
        attacker = even(1, "Charmander", types=("fire",), moves=self.MOVESET)

        # Grass burns; Fire Blast wins on raw power and STAB.
        into_grass = battle.choose_move(attacker, even(2, "Leaf", types=("grass",)))
        # Rock resists fire and is weak to ground, so Earthquake takes over.
        into_rock = battle.choose_move(attacker, even(3, "Boulder", types=("rock",)))

        self.assertEqual(into_grass.move["name"], "fire-blast")
        self.assertEqual(into_rock.move["name"], "earthquake")

    def test_a_move_the_defender_is_immune_to_is_never_chosen(self):
        attacker = even(
            1, "Zapmon", types=("electric",),
            moves=[
                move("thunder", power=110, type="electric", damage_class="special"),
                move("swift", power=60, type="normal", damage_class="special"),
            ],
        )

        attack = battle.choose_move(attacker, even(2, "Digmon", types=("ground",)))

        self.assertEqual(attack.move["name"], "swift")
        self.assertGreater(attack.damage, 0)

    def test_falls_back_to_the_stored_signature_move(self):
        """Profiles cached before candidate movesets existed still battle."""
        attacker = profile(number=1, name="Old", best_move=move("thunderbolt"))
        attacker["moves"] = []

        attack = battle.choose_move(attacker, profile(number=2, name="B"))

        self.assertEqual(attack.move["name"], "thunderbolt")

    def test_no_moves_at_all_deals_no_damage(self):
        attack = battle.choose_move(
            profile(number=1, name="A", best_move=None),
            profile(number=2, name="B"),
        )
        self.assertIsNone(attack.move)
        self.assertEqual(attack.damage, 0)


class SimulationTests(SimpleTestCase):
    def test_type_advantage_decides_an_otherwise_even_match(self):
        """Identical stats and identical move power; only typing differs."""
        fire = even(1, "Firemon", types=("fire",),
                    best_move=move("flamethrower", power=90, type="fire"))
        water = even(2, "Watermon", types=("water",),
                     best_move=move("surf", power=90, type="water"))

        outcome = battle.resolve(fire, water)

        self.assertFalse(outcome.is_tie)
        self.assertEqual(outcome.winner.name, "Watermon")
        self.assertTrue(outcome.second_won)
        self.assertFalse(outcome.first_won)
        self.assertEqual(outcome.first.attack.multiplier, 0.5)   # fire into water
        self.assertEqual(outcome.second.attack.multiplier, 2.0)  # water into fire

    def test_the_faster_pokemon_wins_a_dead_heat(self):
        """Same damage, same HP -- the one that swings first lands the KO."""
        slow = even(1, "Slow", best_move=move("swift", power=90, type="normal"),
                    **{"speed": 10})
        fast = even(2, "Fast", best_move=move("swift", power=90, type="normal"),
                    **{"speed": 200})

        outcome = battle.resolve(slow, fast)

        self.assertEqual(outcome.first.turns_to_ko, outcome.second.turns_to_ko)
        self.assertEqual(outcome.winner.name, "Fast")
        self.assertTrue(outcome.turns[0].attacker_is_first is False)

    def test_hp_is_a_pool_so_bulk_buys_turns(self):
        glass = even(1, "Glass", best_move=move("swift", power=90, type="normal"),
                     **{"hp": 10})
        tank = even(2, "Tank", best_move=move("swift", power=90, type="normal"),
                    **{"hp": 250})

        outcome = battle.resolve(glass, tank)

        self.assertEqual(outcome.winner.name, "Tank")
        self.assertGreater(outcome.first.turns_to_ko, outcome.second.turns_to_ko)

    def test_neither_side_able_to_damage_is_a_stalemate(self):
        one = even(1, "A", best_move=None)
        two = even(2, "B", best_move=None)

        outcome = battle.resolve(one, two)

        self.assertTrue(outcome.stalemate)
        self.assertTrue(outcome.is_tie)
        self.assertIsNone(outcome.winner)
        self.assertIsNone(outcome.loser)
        self.assertFalse(outcome.first_won)
        self.assertFalse(outcome.second_won)
        self.assertEqual(outcome.turns, [])

    def test_a_pokemon_immune_to_everything_still_loses_if_it_cannot_hit_back(self):
        attacker = even(1, "Zapmon", types=("electric",),
                        best_move=move("thunderbolt", power=90, type="electric"))
        # Ground is immune to Electric, and this one has no move of its own.
        wall = even(2, "Digmon", types=("ground",), best_move=None)

        outcome = battle.resolve(attacker, wall)

        self.assertTrue(outcome.stalemate)
        self.assertIsNone(outcome.first.turns_to_ko)

    def test_the_log_records_every_blow_and_ends_on_the_knockout(self):
        strong = even(1, "Strong", best_move=move("swift", power=150, type="normal"),
                      **{"speed": 200})
        weak = even(2, "Weak", best_move=move("swift", power=10, type="normal"),
                    **{"speed": 5})

        outcome = battle.resolve(strong, weak)

        self.assertTrue(outcome.turns)
        self.assertTrue(outcome.turns[-1].fainted)
        self.assertEqual(outcome.turns[-1].defender_name, "Weak")
        self.assertEqual(outcome.turns[-1].remaining_hp, 0)
        # Only the final blow faints anyone.
        self.assertEqual(sum(1 for turn in outcome.turns if turn.fainted), 1)
        self.assertEqual(outcome.total_turns, len(outcome.turns))

    def test_turn_numbers_are_sequential(self):
        one = even(1, "A", best_move=move("swift", power=40, type="normal"))
        two = even(2, "B", best_move=move("swift", power=40, type="normal"))

        outcome = battle.resolve(one, two)

        self.assertEqual(
            [turn.number for turn in outcome.turns],
            list(range(1, len(outcome.turns) + 1)),
        )

    def test_hp_falls_monotonically_for_each_side(self):
        one = even(1, "A", best_move=move("swift", power=60, type="normal"))
        two = even(2, "B", best_move=move("swift", power=50, type="normal"))

        outcome = battle.resolve(one, two)

        for side in (True, False):
            taken = [t.remaining_hp for t in outcome.turns if t.attacker_is_first is side]
            self.assertEqual(taken, sorted(taken, reverse=True))

    def test_winner_and_loser_are_consistent(self):
        strong = even(1, "Strong", best_move=move("swift", power=120, type="normal"),
                      **{"attack": 150, "special-attack": 150, "speed": 150})
        weak = even(2, "Weak", best_move=move("swift", power=10, type="normal"),
                    **{"speed": 5})

        outcome = battle.resolve(strong, weak)

        self.assertTrue(outcome.first_won)
        self.assertFalse(outcome.second_won)
        self.assertEqual(outcome.winner.name, "Strong")
        self.assertEqual(outcome.loser.name, "Weak")

    def test_two_pokemon_sharing_a_name_still_resolve_correctly(self):
        """The winner is found by position, never by comparing names."""
        weak = even(1, "Ditto", best_move=move("swift", power=10, type="normal"),
                    **{"speed": 5})
        strong = even(2, "Ditto", best_move=move("swift", power=150, type="normal"),
                      **{"speed": 200})

        outcome = battle.resolve(weak, strong)

        self.assertTrue(outcome.second_won)
        self.assertFalse(outcome.first_won)
        self.assertIs(outcome.winner, outcome.second)
        self.assertIs(outcome.loser, outcome.first)


class LevelFiftyStatTests(SimpleTestCase):
    def test_hp_and_other_stats_use_their_own_formulas(self):
        # Shared +26 from 31 IVs and 85 EVs; HP alone adds the level and +10.
        self.assertEqual(battle.hp_pool(80), 166)
        self.assertEqual(battle.battle_stat(80), 111)

    def test_bulk_scales_with_base_hp(self):
        self.assertEqual(battle.hp_pool(160) - battle.hp_pool(80), 80)


class ReliabilityTests(SimpleTestCase):
    """Accuracy and charge turns discount a move's per-turn output."""

    def _attack(self, **move_fields):
        return battle.damage_of(
            move("test-move", power=90, type="normal", **move_fields),
            even(1, "A", types=("fire",)),
            even(2, "B", types=("normal",)),
        )

    def test_a_perfect_move_averages_its_full_hit(self):
        attack = self._attack()
        self.assertEqual(attack.damage, attack.hit_damage)
        self.assertFalse(attack.is_unreliable)

    def test_accuracy_discounts_the_average(self):
        attack = self._attack(accuracy=50)
        self.assertEqual(attack.hit_damage, 41)
        self.assertEqual(attack.damage, 20)  # floor(41.6 * 0.5)
        self.assertTrue(attack.is_unreliable)

    def test_a_charge_turn_halves_the_average(self):
        attack = self._attack(turn_cost=2)
        self.assertEqual(attack.hit_damage, 41)
        self.assertEqual(attack.damage, 20)

    def test_a_reliable_move_beats_a_stronger_erratic_one(self):
        """The flaw this closes: raw power handed everyone a one-shot move."""
        attacker = even(
            1, "A", types=("normal",),
            moves=[
                move("hyper-beam", power=150, type="normal",
                     damage_class="physical", turn_cost=2),
                move("body-slam", power=85, type="normal", damage_class="physical"),
            ],
        )

        attack = battle.choose_move(attacker, even(2, "B", types=("normal",)))

        self.assertEqual(attack.move["name"], "body-slam")

    def test_a_self_knockout_move_is_never_chosen(self):
        attacker = even(
            1, "A", types=("normal",),
            moves=[
                move("explosion", power=250, type="normal",
                     damage_class="physical", self_ko=True),
                move("tackle", power=40, type="normal", damage_class="physical"),
            ],
        )

        attack = battle.choose_move(attacker, even(2, "B", types=("normal",)))

        self.assertEqual(attack.move["name"], "tackle")


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
