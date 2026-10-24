"""What a replay played back with the fog off showed its player, as the fog on would have, and every unit in full."""

from s2clientprotocol import common_pb2, raw_pb2, sc2api_pb2

from sc2nachos._fog_off_playback import _FogOffPlayback, every_unit_in_full
from sc2nachos.ids import UnitTypeId
from sc2nachos.units import Alliance, Visibility
from support import make_bytes, make_enemy, make_observation, make_tables, make_unit

# A map of 16 by 16 tiles whose left half the player sees, and one it sees all of.
_LEFT_HALF_IN_SIGHT = make_bytes(*([2] * 8 + [0] * 8 for _ in range(16)))
_ALL_IN_SIGHT = make_bytes(*([2] * 16 for _ in range(16)))
_IN_SIGHT = (4.5, 8.5)
_OUT_OF_SIGHT = (12.5, 8.5)
_TOWNHALL = 7


def _playback() -> _FogOffPlayback:
    """A playback with the fog off, from its first observation."""
    return _FogOffPlayback(make_tables(), make_enemy())


def _observed(
    *units: raw_pb2.Unit, sight: common_pb2.ImageData = _LEFT_HALF_IN_SIGHT
) -> sc2api_pb2.ResponseObservation:
    """An observation of `units` on the map, of which the player sees what `sight` says."""
    return make_observation(0, units=units, visibility=sight)


def _listed(observation: sc2api_pb2.ResponseObservation) -> list[raw_pb2.Unit]:
    """The units `observation` lists."""
    return list(observation.observation.raw_data.units)


def _unseen_enemy(*, at: tuple[float, float]) -> raw_pb2.Unit:
    """An enemy marine the player does not see, as the fog off lists one: hidden, with its owner and health."""
    return make_unit(
        2, at=at, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE, owner=2, health=40, health_max=45
    )


def _enemy_townhall(visibility: Visibility, **fields: object) -> raw_pb2.Unit:
    """The enemy's command center, out of sight unless it is visible."""
    at = _IN_SIGHT if visibility is Visibility.VISIBLE else _OUT_OF_SIGHT
    return make_unit(
        _TOWNHALL, UnitTypeId.COMMAND_CENTER, at=at, alliance=Alliance.ENEMY, visibility=visibility, **fields
    )


class TestThePlayersView:
    def test_its_own_units_and_the_enemys_in_sight_are_shown_as_reported(self) -> None:
        own = make_unit(1, at=_OUT_OF_SIGHT)
        enemy = make_unit(2, at=_IN_SIGHT, alliance=Alliance.ENEMY)
        assert _listed(_playback().player_view(_observed(own, enemy))) == [own, enemy]

    def test_an_enemy_it_does_not_see_is_left_out_though_the_tile_under_it_reads_in_sight(self) -> None:
        """As an enemy drone at the edge of sight was, reporting its owner and health (in game)."""
        for at in (_OUT_OF_SIGHT, _IN_SIGHT):
            unseen = _unseen_enemy(at=at)
            assert _listed(_playback().player_view(_observed(unseen))) == []

    def test_an_enemy_undetected_in_its_sight_reporting_no_owner_is_shown(self) -> None:
        undetected = make_unit(2, at=_IN_SIGHT, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        assert _listed(_playback().player_view(_observed(undetected))) == [undetected]

    def test_an_enemy_structure_it_never_saw_is_left_out(self) -> None:
        never_seen = _enemy_townhall(Visibility.IN_FOG)
        assert _listed(_playback().player_view(_observed(never_seen))) == []

    def test_an_enemys_placeholder_is_left_out(self) -> None:
        placeholder = make_unit(2, UnitTypeId.SUPPLY_DEPOT, at=_IN_SIGHT, alliance=Alliance.ENEMY)
        placeholder.display_type = raw_pb2.DisplayType.Placeholder
        assert _listed(_playback().player_view(_observed(placeholder))) == []

    def test_a_structure_seen_and_gone_into_the_fog_is_a_copy_under_a_tag_of_its_own_as_it_went(self) -> None:
        playback = _playback()
        playback.player_view(_observed(_enemy_townhall(Visibility.VISIBLE, health=1500)))
        gone = _enemy_townhall(Visibility.IN_FOG, health=1400)
        (copy,) = _listed(playback.player_view(_observed(gone)))
        assert copy.tag != _TOWNHALL
        assert playback.real_tag(copy.tag) == _TOWNHALL
        assert (copy.display_type, copy.pos, copy.health) == (raw_pb2.DisplayType.Snapshot, gone.pos, 1400)
        # Remembered as it went, though the game goes on reporting it.
        assert _listed(playback.player_view(_observed(_enemy_townhall(Visibility.IN_FOG, health=900)))) == [copy]

    def test_a_copy_stays_while_its_spot_is_out_of_sight_and_goes_once_it_is_in_sight(self) -> None:
        playback = _playback()
        playback.player_view(_observed(_enemy_townhall(Visibility.VISIBLE)))
        (copy,) = _listed(playback.player_view(_observed(_enemy_townhall(Visibility.IN_FOG))))
        # Died out of sight: the game lists it no more, and reports no death the player cannot see (in game).
        assert _listed(playback.player_view(_observed())) == [copy]
        assert _listed(playback.player_view(_observed(sight=_ALL_IN_SIGHT))) == []
        assert playback.real_tag(copy.tag) == copy.tag

    def test_a_copy_stays_while_its_unit_is_listed_in_the_fog_though_its_spot_reads_in_sight(self) -> None:
        """As a mineral field at the edge of sight was (in game)."""
        playback = _playback()
        playback.player_view(_observed(_enemy_townhall(Visibility.VISIBLE)))
        (copy,) = _listed(playback.player_view(_observed(_enemy_townhall(Visibility.IN_FOG))))
        still_in_the_fog = _enemy_townhall(Visibility.IN_FOG)
        assert _listed(playback.player_view(_observed(still_in_the_fog, sight=_ALL_IN_SIGHT))) == [copy]

    def test_a_copy_goes_once_its_spot_is_in_sight_if_its_unit_moved_off_it(self) -> None:
        """As a structure that lifts off out of sight and lands elsewhere."""
        playback = _playback()
        playback.player_view(_observed(_enemy_townhall(Visibility.VISIBLE)))
        (copy,) = _listed(playback.player_view(_observed(_enemy_townhall(Visibility.IN_FOG))))
        landed_elsewhere = _enemy_townhall(Visibility.IN_FOG)
        landed_elsewhere.pos.y += 3.0
        assert _listed(playback.player_view(_observed(landed_elsewhere))) == [copy]
        assert _listed(playback.player_view(_observed(landed_elsewhere, sight=_ALL_IN_SIGHT))) == []

    def test_a_structure_seen_again_is_shown_under_its_own_tag_and_copied_afresh_into_the_fog(self) -> None:
        playback = _playback()
        playback.player_view(_observed(_enemy_townhall(Visibility.VISIBLE)))
        (first_copy,) = _listed(playback.player_view(_observed(_enemy_townhall(Visibility.IN_FOG))))
        seen_again = _enemy_townhall(Visibility.VISIBLE)
        assert _listed(playback.player_view(_observed(seen_again))) == [seen_again]
        (second_copy,) = _listed(playback.player_view(_observed(_enemy_townhall(Visibility.IN_FOG))))
        assert second_copy.tag not in (_TOWNHALL, first_copy.tag)

    def test_the_maps_units_in_the_fog_are_copies_from_the_start(self) -> None:
        mineral_field = make_unit(
            9, UnitTypeId.MINERAL_FIELD, at=_OUT_OF_SIGHT, alliance=Alliance.NEUTRAL, visibility=Visibility.IN_FOG
        )
        playback = _playback()
        (copy,) = _listed(playback.player_view(_observed(mineral_field)))
        assert copy.tag != 9
        assert playback.real_tag(copy.tag) == 9

    def test_the_rest_of_the_observation_is_left_as_reported(self) -> None:
        observation = make_observation(48, dead=[5], visibility=_LEFT_HALF_IN_SIGHT, chat=[(2, "gg")])
        view = _playback().player_view(observation)
        assert view.observation.game_loop == 48
        assert list(view.observation.raw_data.event.dead_units) == [5]
        assert view.chat == observation.chat
        assert view.observation.raw_data.map_state == observation.observation.raw_data.map_state


class TestEveryUnitInFull:
    def test_a_unit_out_of_the_players_sight_is_shown_visible(self) -> None:
        hidden = _unseen_enemy(at=_IN_SIGHT)
        in_fog = _enemy_townhall(Visibility.IN_FOG, health=1400)
        shown = list(every_unit_in_full(_observed(hidden, in_fog)).units)
        assert [(unit.tag, unit.display_type, unit.health) for unit in shown] == [
            (2, raw_pb2.DisplayType.Visible, 40),
            (_TOWNHALL, raw_pb2.DisplayType.Visible, 1400),
        ]

    def test_a_unit_undetected_in_the_players_sight_is_shown_as_reported(self) -> None:
        undetected = make_unit(2, at=_IN_SIGHT, alliance=Alliance.ENEMY, visibility=Visibility.INVISIBLE)
        assert list(every_unit_in_full(_observed(undetected)).units) == [undetected]

    def test_the_deaths_and_this_players_upgrades_come_along(self) -> None:
        observation = make_observation(16, dead=[5], upgrades=[1], visibility=_LEFT_HALF_IN_SIGHT)
        every_unit = every_unit_in_full(observation)
        assert list(every_unit.event.dead_units) == [5]
        assert list(every_unit.player.upgrade_ids) == [1]
