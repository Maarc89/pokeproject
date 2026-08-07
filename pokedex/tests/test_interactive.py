"""The played battle: rolls, replay safety, and the turn guard.

Seeds 0, 1 and 2 are used throughout and are not arbitrary. For turn 0, actor
"player", move "focus-blast" they produce:

    seed 0 -> accuracy roll 94, no crit, damage roll 99   (a miss at 70 accuracy)
    seed 1 -> accuracy roll 38, no crit, damage roll 95   (an ordinary hit)
    seed 2 -> accuracy roll 46, crit,    damage roll 87   (a critical hit)

If `resolve_hit` ever changes how many values it draws, or in what order, these
move -- which is the point. The draws are part of the contract, because they are
what makes a battle replayable.
"""

import math

from django.test import SimpleTestCase

from ..services import battle, interactive
from ..services.battle import Turn
from .factories import move, profile

EVEN_STATS = {
    "hp": 80, "attack": 80, "defense": 80,
    "special-attack": 80, "special-defense": 80, "speed": 80,
}

# 120-power Fighting, 70 accuracy: strong enough to matter, unreliable enough
# that a miss is reachable.
FOCUS_BLAST = move(
    name="focus-blast", power=120, type="fighting",
    damage_class="special", accuracy=70,
)
# Cannot miss, so it isolates the damage roll from the accuracy roll.
SWIFT = move(name="swift", power=60, type="normal", damage_class="special")
HYPER_BEAM = move(
    name="hyper-beam", power=150, type="normal",
    damage_class="special", turn_cost=2,
)

MISS_SEED = 0
HIT_SEED = 1
CRIT_SEED = 2


def even(number, name, types=("normal",), moves=None, **overrides):
    """A Pokemon with flat 80s, so only the thing under test varies."""
    return profile(
        number=number, name=name, types=types,
        stats=dict(EVEN_STATS, **overrides), moves=moves,
    )


def roll(seed, move_dict, attacker, defender, turn=0, actor=interactive.PLAYER):
    return interactive.resolve_hit(
        move_dict, attacker, defender,
        interactive.rng_for(seed, turn, actor, move_dict["name"]),
    )


class RngTests(SimpleTestCase):
    def test_the_same_decision_always_draws_the_same_numbers(self):
        """The property the whole anti-cheat design rests on: recomputing a turn
        cannot change it, so refreshing cannot turn a miss into a crit."""
        first = interactive.rng_for(99, 3, "player", "thunderbolt")
        second = interactive.rng_for(99, 3, "player", "thunderbolt")
        self.assertEqual(
            [first.random() for _ in range(5)],
            [second.random() for _ in range(5)],
        )

    def test_each_part_of_the_key_changes_the_stream(self):
        base = interactive.rng_for(99, 3, "player", "thunderbolt").random()
        for label, other in [
            ("seed", interactive.rng_for(98, 3, "player", "thunderbolt")),
            ("turn", interactive.rng_for(99, 4, "player", "thunderbolt")),
            ("actor", interactive.rng_for(99, 3, "opponent", "thunderbolt")),
            ("move", interactive.rng_for(99, 3, "player", "surf")),
        ]:
            with self.subTest(varying=label):
                self.assertNotEqual(base, other.random())

    def test_seeding_is_stable_across_processes(self):
        """`random.Random(str)` hashes with SHA-512. `hash()` is salted per
        process, and using it here would make every seeded test unreproducible.

        These figures were generated in a different interpreter run.
        """
        rng = interactive.rng_for(0, 0, "player", "focus-blast")
        self.assertEqual(rng.randint(1, 100), 94)
        self.assertFalse(rng.random() < interactive.CRIT_CHANCE)
        self.assertEqual(rng.randint(85, 100), 99)


class ResolveHitTests(SimpleTestCase):
    def setUp(self):
        self.attacker = even(1, "Attacker")
        self.defender = even(2, "Defender")
        # ((2x50/5 + 2) x 120 x 111/111)/50 + 2 = 54.8, doubled for Fighting
        # against Normal, floored -> 109.
        self.clean_hit = battle.damage_of(
            FOCUS_BLAST, self.attacker, self.defender
        ).hit_damage

    def test_the_clean_hit_is_what_the_deterministic_engine_says(self):
        self.assertEqual(self.clean_hit, 109)

    def test_an_inaccurate_move_can_miss(self):
        hit = roll(MISS_SEED, FOCUS_BLAST, self.attacker, self.defender)

        self.assertTrue(hit.missed)
        self.assertEqual(hit.damage, 0)
        self.assertFalse(hit.immune)

    def test_an_ordinary_hit(self):
        hit = roll(HIT_SEED, FOCUS_BLAST, self.attacker, self.defender)

        self.assertFalse(hit.missed)
        self.assertFalse(hit.crit)
        # 109 x 95% = 103.55 -> 103
        self.assertEqual(hit.damage, 103)

    def test_a_critical_hit_multiplies_the_damage(self):
        hit = roll(CRIT_SEED, FOCUS_BLAST, self.attacker, self.defender)

        self.assertTrue(hit.crit)
        # 109 x 87% x 1.5 = 142.245 -> 142
        self.assertEqual(hit.damage, 142)
        self.assertGreater(hit.damage, self.clean_hit)

    def test_a_non_critical_hit_stays_inside_the_roll_band(self):
        floor_damage = math.floor(self.clean_hit * interactive.MIN_ROLL / 100)

        for seed in range(200):
            hit = roll(seed, SWIFT, self.attacker, self.defender)
            if hit.crit or hit.missed:
                continue
            with self.subTest(seed=seed):
                clean = battle.damage_of(SWIFT, self.attacker, self.defender).hit_damage
                self.assertGreaterEqual(hit.damage, math.floor(clean * 0.85))
                self.assertLessEqual(hit.damage, clean)

        self.assertLess(floor_damage, self.clean_hit)

    def test_a_hundred_accuracy_move_never_misses(self):
        for seed in range(200):
            with self.subTest(seed=seed):
                self.assertFalse(roll(seed, SWIFT, self.attacker, self.defender).missed)

    def test_an_immunity_is_not_reported_as_a_miss(self):
        """"It had no effect" and "it missed" tell the player to do completely
        different things, so they must not collapse into one another."""
        ground = even(3, "Ground Type", types=("ground",))
        thunderbolt = move(name="thunderbolt", power=90, type="electric")

        # A seed whose accuracy roll would otherwise have missed.
        hit = roll(MISS_SEED, thunderbolt, self.attacker, ground)

        self.assertEqual(hit.damage, 0)
        self.assertTrue(hit.immune)
        self.assertFalse(hit.missed)

    def test_a_landed_hit_always_does_at_least_one_damage(self):
        feeble = move(name="feeble", power=1, type="normal")
        wall = even(4, "Wall", defense=250, **{"special-defense": 250})

        hit = roll(HIT_SEED, feeble, self.attacker, wall)

        self.assertGreaterEqual(hit.damage, 1)


class MovesetTests(SimpleTestCase):
    def test_it_takes_four_moves(self):
        candidates = [
            move(name=f"move-{index}", power=40 + index * 10) for index in range(10)
        ]
        self.assertEqual(len(interactive.player_moveset(candidates)), 4)

    def test_it_takes_the_strongest_by_effective_power(self):
        """Accuracy counts: a 150-power move landing 50% of the time is worth
        less than a 90-power move that always lands."""
        chosen = interactive.player_moveset([
            move(name="reliable", power=90),
            move(name="wild", power=150, accuracy=50),
        ], limit=1)

        self.assertEqual([entry["name"] for entry in chosen], ["reliable"])

    def test_it_drops_status_and_self_ko_moves(self):
        chosen = interactive.player_moveset([
            move(name="growl", power=0),
            move(name="explosion", power=250, self_ko=True),
            move(name="tackle", power=40),
        ])

        self.assertEqual([entry["name"] for entry in chosen], ["tackle"])

    def test_it_is_stable(self):
        candidates = [move(name=f"move-{index}", power=90) for index in range(8)]
        self.assertEqual(
            interactive.player_moveset(candidates),
            interactive.player_moveset(list(reversed(candidates))),
        )


class AdvanceTests(SimpleTestCase):
    def state(self, seed=HIT_SEED, player_moves=(SWIFT,), opponent_moves=(SWIFT,),
              player_speed=80, opponent_speed=80):
        return interactive.GameState.start(
            seed=seed,
            player=even(1, "Player", moves=list(player_moves), speed=player_speed),
            opponent=even(2, "Opponent", moves=list(opponent_moves), speed=opponent_speed),
        )

    def test_both_sides_act_in_a_round(self):
        state = self.state()
        turns = interactive.advance(state, "swift")

        self.assertEqual(len(turns), 2)
        self.assertEqual(state.turn_number, 1)
        self.assertLess(state.opponent_hp, state.opponent_max_hp)
        self.assertLess(state.player_hp, state.player_max_hp)

    def test_the_faster_pokemon_strikes_first(self):
        fast = self.state(player_speed=200, opponent_speed=10)
        self.assertTrue(interactive.advance(fast, "swift")[0].attacker_is_first)

        slow = self.state(player_speed=10, opponent_speed=200)
        self.assertFalse(interactive.advance(slow, "swift")[0].attacker_is_first)

    def test_turn_order_matches_the_simulation(self):
        """A played battle and a simulated one must agree on who moves first,
        or the same matchup tells two different stories."""
        state = self.state(player_speed=120, opponent_speed=100)
        outcome = battle.resolve(state.player, state.opponent)

        self.assertEqual(
            interactive.advance(state, "swift")[0].attacker_is_first,
            outcome.turns[0].attacker_is_first,
        )

    def test_replaying_a_round_gives_the_identical_result(self):
        first = self.state()
        second = self.state()

        interactive.advance(first, "swift")
        interactive.advance(second, "swift")

        self.assertEqual(first.player_hp, second.player_hp)
        self.assertEqual(first.opponent_hp, second.opponent_hp)

    def test_a_charge_move_costs_the_following_turn(self):
        state = self.state(player_moves=(HYPER_BEAM,), player_speed=200, opponent_speed=10)

        first_round = interactive.advance(state, "hyper-beam")
        self.assertFalse(first_round[0].stalled)
        self.assertTrue(state.player_recharging)

        second_round = interactive.advance(state, "hyper-beam")
        self.assertTrue(second_round[0].stalled)
        self.assertEqual(second_round[0].damage, 0)
        # And the flag clears, so it is one lost turn rather than a lock-out.
        self.assertFalse(state.player_recharging)

    def test_a_knockout_ends_the_round_immediately(self):
        state = self.state(player_speed=200, opponent_speed=10)
        state.opponent_hp = 1

        turns = interactive.advance(state, "swift")

        self.assertEqual(len(turns), 1)
        self.assertTrue(turns[0].fainted)
        self.assertEqual(state.result, "won")
        # The opponent never got to answer, so the player is untouched.
        self.assertEqual(state.player_hp, state.player_max_hp)

    def test_losing_is_reported_as_a_loss(self):
        state = self.state(player_speed=10, opponent_speed=200)
        state.player_hp = 1

        interactive.advance(state, "swift")

        self.assertEqual(state.result, "lost")

    def test_a_finished_game_does_not_advance(self):
        state = self.state()
        state.result = "won"

        self.assertEqual(interactive.advance(state, "swift"), [])
        self.assertEqual(state.turn_number, 0)

    def test_a_pokemon_with_no_moves_just_stands_there(self):
        state = self.state(player_moves=(), opponent_speed=10, player_speed=200)

        turns = interactive.advance(state, "swift")

        self.assertIsNone(turns[0].move)
        self.assertEqual(turns[0].damage, 0)
        self.assertEqual(state.opponent_hp, state.opponent_max_hp)

    def test_turn_numbers_run_on_across_rounds(self):
        state = self.state()
        interactive.advance(state, "swift")
        second = interactive.advance(state, "swift", log_length=2)

        self.assertEqual([turn.number for turn in second], [3, 4])


class TurnSerialisationTests(SimpleTestCase):
    def test_a_turn_survives_the_round_trip(self):
        state = interactive.GameState.start(
            seed=HIT_SEED,
            player=even(1, "Player", moves=[SWIFT]),
            opponent=even(2, "Opponent", moves=[SWIFT]),
        )
        original = interactive.advance(state, "swift")[0]

        restored = interactive.turn_from_json(interactive.turn_to_json(original))

        self.assertEqual(restored, original)

    def test_a_log_written_before_a_field_existed_still_loads(self):
        """Turns are stored as JSON, so an older entry will be missing whatever
        was added since. It should fall back to the default, not blow up."""
        payload = interactive.turn_to_json(
            Turn(
                number=1, attacker_is_first=True, attacker_name="A",
                defender_name="B", move=None, damage=0, multiplier=1.0,
                stab=False, remaining_hp=10, max_hp=10, fainted=False,
            )
        )
        del payload["crit"]
        del payload["stalled"]

        restored = interactive.turn_from_json(payload)

        self.assertFalse(restored.crit)
        self.assertFalse(restored.stalled)


class SimulationIsUnchangedTests(SimpleTestCase):
    """The deterministic engine must not have picked up the game's behaviour."""

    def test_a_simulated_turn_never_misses_or_crits(self):
        outcome = battle.resolve(
            even(1, "First", moves=[FOCUS_BLAST]),
            even(2, "Second", moves=[SWIFT]),
        )

        self.assertTrue(outcome.turns)
        for turn in outcome.turns:
            self.assertFalse(turn.missed)
            self.assertFalse(turn.crit)
            self.assertFalse(turn.stalled)

    def test_the_simulation_still_discounts_by_reliability(self):
        """`reliability` governs move choice and the simulated result. Only the
        played battle rolls -- swapping one for the other here would silently
        change every stored battle score."""
        attacker = even(1, "Attacker")
        defender = even(2, "Defender")

        attack = battle.damage_of(FOCUS_BLAST, attacker, defender)

        # 70 accuracy, so per-turn damage is 70% of a clean hit.
        self.assertEqual(attack.hit_damage, 109)
        self.assertEqual(attack.damage, math.floor(109 * 0.7))
