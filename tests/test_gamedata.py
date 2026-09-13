"""The tables a game is played by, read from tables written here and from the recorded games."""

import re
from pathlib import Path

import pytest
from s2clientprotocol import common_pb2, data_pb2, sc2api_pb2

from sc2nachos.gamedata import Attribute, GameData, Resources, TargetDomain, TargetType
from sc2nachos.ids import AbilityId, EffectId, UnitTypeId, UpgradeId
from sc2nachos.ids.raw import RawAbilityId, RawUnitTypeId
from sc2nachos.match import Race
from sc2nachos.protocol import Recording

CORPUS = sorted((Path(__file__).parent / "corpus").glob("*.sc2rec"))

# The upgrade table has dead ids too: it says these three are researched by the `ArmoryResearchSwarm` spelling,
# which an armory is never offered and which does nothing when ordered. The `ArmoryResearch` spelling is what an
# armory offers and runs, and is what `ARMORY_RESEARCH_VEHICLE_AND_SHIP_ARMOR_1` and its levels name.
_RESEARCHED_BY_A_DEAD_ID = {
    UpgradeId.TERRAN_VEHICLE_AND_SHIP_ARMOR_1,
    UpgradeId.TERRAN_VEHICLE_AND_SHIP_ARMOR_2,
    UpgradeId.TERRAN_VEHICLE_AND_SHIP_ARMOR_3,
}

# Rows whose maker the curated ids leave out, for one of two reasons. The first six name an ability the game no
# longer honors: tested in game, none is ever offered and ordering one does nothing, while HYDRALISK_MORPH_LURKER,
# plain SCV_BUILD_REFINERY aimed at a rich geyser, ZERGLING_MORPH_BANELING, RAVEN_SPAWN_AUTO_TURRET,
# SWARM_HOST_SPAWN_LOCUST and DISRUPTOR_PURIFICATION_NOVA all work. The rest name one nothing can order -- the game
# disguises a changeling, collapses a tower and takes a locust into the air by itself, and a bare tech lab or
# reactor is a tech requirement no unit is ever built as.
_NO_MAKER = {
    UnitTypeId.LURKER: RawAbilityId.LurkerAspectMPFromHydraliskBurrowed_LurkerMPFromHydraliskBurrowed,
    UnitTypeId.REFINERY_RICH: RawAbilityId.TerranBuild_Refinery_325,
    UnitTypeId.BANELING: RawAbilityId.MorphZerglingToBaneling_Baneling,
    UnitTypeId.AUTO_TURRET: RawAbilityId.RavenBuild_AutoTurret,
    UnitTypeId.LOCUST: RawAbilityId.SpawnInfestedTerran_LocustMP,
    UnitTypeId.PURIFICATION_NOVA: RawAbilityId.PurificationNovaMorph_PurificationNova,
    UnitTypeId.CHANGELING_MARINE: RawAbilityId.DisguiseAsMarineWithoutShield_Marine,
    UnitTypeId.CHANGELING_MARINE_SHIELD: RawAbilityId.DisguiseAsMarineWithShield_Marine,
    UnitTypeId.CHANGELING_ZEALOT: RawAbilityId.DisguiseAsZealot_Zealot,
    UnitTypeId.CHANGELING_ZERGLING: RawAbilityId.DisguiseAsZerglingWithoutWings_Zergling,
    UnitTypeId.CHANGELING_ZERGLING_WINGS: RawAbilityId.DisguiseAsZerglingWithWings_Zergling,
    UnitTypeId.COLLAPSIBLE_TOWER_DEBRIS_PURIFIER: RawAbilityId.MorphToCollapsiblePurifierTowerDebris,
    UnitTypeId.CREEP_TUMOR_BURROWED: RawAbilityId.BurrowCreepTumorDown_BurrowDown,
    UnitTypeId.COLLAPSIBLE_TOWER_DEBRIS_RAMP_LEFT_ROCK: RawAbilityId.MorphToCollapsibleRockTowerDebrisRampLeft,
    UnitTypeId.COLLAPSIBLE_TOWER_DEBRIS_RAMP_LEFT_ROCK_GREEN: (
        RawAbilityId.MorphToCollapsibleRockTowerDebrisRampLeftGreen
    ),
    UnitTypeId.COLLAPSIBLE_TOWER_DEBRIS_RAMP_RIGHT_ROCK: RawAbilityId.MorphToCollapsibleRockTowerDebrisRampRight,
    UnitTypeId.COLLAPSIBLE_TOWER_DEBRIS_RAMP_RIGHT_ROCK_GREEN: (
        RawAbilityId.MorphToCollapsibleRockTowerDebrisRampRightGreen
    ),
    UnitTypeId.COLLAPSIBLE_TOWER_DEBRIS_ROCK: RawAbilityId.MorphToCollapsibleRockTowerDebris,
    UnitTypeId.COLLAPSIBLE_TOWER_DEBRIS_TERRAN: RawAbilityId.MorphToCollapsibleTerranTowerDebris,
    UnitTypeId.LOCUST_FLYING: RawAbilityId.LocustMPMorphToAir_LocustMPFlyingSwoop,
    UnitTypeId.REACTOR: RawAbilityId.ReactorMorph,
    UnitTypeId.TECH_LAB: RawAbilityId.TechLabMorph,
}
_DEAD_LURKER_MORPH = _NO_MAKER[UnitTypeId.LURKER]
# The tech alias both forms of a viking carry: a row with no cost, speed, sight or weapon, that nothing requires
# and no unit is ever one of. Curated nowhere either.
_PHANTOM_VIKING = RawUnitTypeId.Viking

# What the game says a marine is, cut down to the fields a test reads.
_MARINE = data_pb2.UnitTypeData(
    unit_id=UnitTypeId.MARINE,
    race=common_pb2.Race.Terran,
    mineral_cost=50,
    food_required=1.0,
    build_time=400.0,
    ability_id=RawAbilityId.BarracksTrain_Marine,
    attributes=[data_pb2.Attribute.Light, data_pb2.Attribute.Biological],
    weapons=[data_pb2.Weapon(type=data_pb2.Weapon.TargetType.Any, damage=6.0, attacks=1, range=5.0, speed=0.86)],
)


class TestResources:
    def test_amounts_add_and_subtract(self) -> None:
        assert Resources(50, 25) + Resources(100, 0) == Resources(150, 25)
        assert Resources(150, 25) - Resources(50, 25) == Resources(100, 0)

    def test_subtracting_more_than_there_is_says_how_short_it_is(self) -> None:
        assert Resources(50, 0) - Resources(75, 25) == Resources(-25, -25)

    def test_an_amount_totals_both_of_its_halves(self) -> None:
        assert Resources(150, 125).total == 275
        assert (Resources(50, 0) * 4).total == 200

    def test_an_amount_negates(self) -> None:
        assert -Resources(50, 25) == Resources(-50, -25)
        assert Resources(150, 50) + -Resources(50, 25) == Resources(100, 25)

    def test_an_amount_scales_by_a_count_from_either_side(self) -> None:
        assert Resources(50, 25) * 4 == 4 * Resources(50, 25) == Resources(200, 100)

    def test_an_amount_takes_a_fraction_of_itself(self) -> None:
        assert Resources(400, 300) / 2 == Resources(400, 300) * 0.5 == Resources(200, 150)
        assert Resources(50, 25) * 0.5 == Resources(25, 12.5)

    def test_what_the_game_gave_stays_whole_until_it_is_divided(self) -> None:
        """The tables are in whole minerals, and adding or scaling by a count keeps them that way."""
        marine = Resources(50, 0)
        assert isinstance((marine * 4 + marine).minerals, int)
        assert not isinstance((marine / 3).minerals, int)

    def test_a_bank_covers_what_it_has_enough_of(self) -> None:
        bank = Resources(150, 50)
        assert bank.covers(Resources(150, 50))
        assert bank.covers(Resources(100, 0))
        assert not bank.covers(Resources(100, 75))
        assert not bank.covers(Resources(200, 0))

    def test_covering_is_not_an_ordering(self) -> None:
        """Neither of these covers the other, which is why there is no `>=` to reach for."""
        assert not Resources(100, 0).covers(Resources(0, 100))
        assert not Resources(0, 100).covers(Resources(100, 0))

    def test_amounts_can_be_summed_from_nothing(self) -> None:
        costs = [Resources(50, 0), Resources(100, 25), Resources(0, 75)]
        assert sum(costs, start=Resources(0, 0)) == Resources(150, 100)


class TestReadingTheTables:
    def test_a_unit_type_is_read_under_the_curated_id_that_names_it(self) -> None:
        """A row carries no name of its own: the id is the name, and the catalog spelling comes off that."""
        data = GameData(sc2api_pb2.ResponseData(units=[_MARINE]))
        marine = data.units[UnitTypeId.MARINE]
        assert marine.id is UnitTypeId.MARINE
        assert marine.race is Race.TERRAN
        assert marine.cost == Resources(minerals=50, vespene=0)
        assert marine.supply_cost == 1.0
        assert marine.attributes == {Attribute.LIGHT, Attribute.BIOLOGICAL}
        assert marine.creation_ability is AbilityId.BARRACKS_TRAIN_MARINE

    def test_a_row_the_curated_ids_do_not_name_is_left_out(self) -> None:
        """The game describes its whole catalog; a table holds only what a bot has a name for."""
        ursadon = data_pb2.UnitTypeData(unit_id=RawUnitTypeId.Ursadon)
        unheard_of = data_pb2.UnitTypeData(unit_id=99999)
        data = GameData(sc2api_pb2.ResponseData(units=[_MARINE, ursadon, unheard_of]))
        assert set(data.units) == {UnitTypeId.MARINE}

    def test_a_field_naming_something_uncurated_reads_as_nothing(self) -> None:
        """As the lurker's creation ability does, the game naming an ability that no longer works."""
        hydralisk = data_pb2.UnitTypeData(unit_id=UnitTypeId.HYDRALISK, ability_id=_DEAD_LURKER_MORPH)
        data = GameData(sc2api_pb2.ResponseData(units=[hydralisk]))
        assert data.units[UnitTypeId.HYDRALISK].creation_ability is None

    def test_an_id_of_zero_names_nothing(self) -> None:
        data = GameData(sc2api_pb2.ResponseData(units=[_MARINE]))
        marine = data.units[UnitTypeId.MARINE]
        assert marine.tech_requirement is None
        assert marine.base_type is None
        assert marine.tech_aliases == ()

    def test_a_build_time_is_seconds_where_the_game_counts_steps(self) -> None:
        data = GameData(sc2api_pb2.ResponseData(units=[_MARINE]))
        assert data.units[UnitTypeId.MARINE].build_time == pytest.approx(17.857, abs=1e-3)

    def test_a_weapon_says_what_it_hits_and_how_long_it_waits(self) -> None:
        data = GameData(sc2api_pb2.ResponseData(units=[_MARINE]))
        (weapon,) = data.units[UnitTypeId.MARINE].weapons
        assert weapon.target_domain is TargetDomain.ANY
        assert (weapon.damage, weapon.attacks, weapon.range) == (6.0, 1, 5.0)
        assert weapon.cooldown == pytest.approx(0.86)
        assert weapon.damage_bonuses == {}

    def test_a_weapons_bonus_damage_is_read_by_the_attribute_it_is_earned_by(self) -> None:
        bonus = data_pb2.DamageBonus(attribute=data_pb2.Attribute.Armored, bonus=10.0)
        marauder = data_pb2.UnitTypeData(
            unit_id=UnitTypeId.MARAUDER, weapons=[data_pb2.Weapon(damage=10.0, damage_bonus=[bonus])]
        )
        data = GameData(sc2api_pb2.ResponseData(units=[marauder]))
        (weapon,) = data.units[UnitTypeId.MARAUDER].weapons
        assert weapon.damage_bonuses == {Attribute.ARMORED: 10.0}

    def test_an_ability_that_places_a_structure_says_how_much_room_it_takes(self) -> None:
        depot = data_pb2.AbilityData(
            ability_id=AbilityId.SCV_BUILD_SUPPLY_DEPOT,
            target=data_pb2.AbilityData.Target.Point,
            is_building=True,
            footprint_radius=1.0,
        )
        data = GameData(sc2api_pb2.ResponseData(abilities=[depot]))
        row = data.abilities[AbilityId.SCV_BUILD_SUPPLY_DEPOT]
        assert row.needs_placement
        assert row.footprint_radius == 1.0
        assert row.target_type is TargetType.POINT

    def test_an_ability_that_is_aimed_at_nothing_has_no_footprint(self) -> None:
        stim = data_pb2.AbilityData(ability_id=AbilityId.GENERAL_STIM, target=data_pb2.AbilityData.Target.Value("None"))
        data = GameData(sc2api_pb2.ResponseData(abilities=[stim]))
        row = data.abilities[AbilityId.GENERAL_STIM]
        assert row.target_type is TargetType.NOTHING
        assert row.footprint_radius is None

    def test_an_upgrade_is_read_with_the_ability_that_researches_it(self) -> None:
        stimpack = data_pb2.UpgradeData(
            upgrade_id=UpgradeId.STIMPACK,
            mineral_cost=100,
            vespene_cost=100,
            research_time=2240.0,
            ability_id=AbilityId.BARRACKS_TECH_LAB_RESEARCH_STIMPACK,
        )
        data = GameData(sc2api_pb2.ResponseData(upgrades=[stimpack]))
        row = data.upgrades[UpgradeId.STIMPACK]
        assert row.cost == Resources(minerals=100, vespene=100)
        assert row.research_time == pytest.approx(100.0)
        assert row.research_ability is AbilityId.BARRACKS_TECH_LAB_RESEARCH_STIMPACK

    def test_an_effect_is_read_with_the_ground_it_covers(self) -> None:
        storm = data_pb2.EffectData(effect_id=EffectId.HIGH_TEMPLAR_STORM, radius=1.5)
        data = GameData(sc2api_pb2.ResponseData(effects=[storm]))
        assert data.effects[EffectId.HIGH_TEMPLAR_STORM].radius == 1.5

    def test_the_tables_refuse_writes(self) -> None:
        data = GameData(sc2api_pb2.ResponseData(units=[_MARINE]))
        with pytest.raises(TypeError):
            data.units[UnitTypeId.MARINE] = data.units[UnitTypeId.MARINE]  # type: ignore[index]


def _answer(path: Path) -> sc2api_pb2.ResponseData:
    """What a recorded game said its tables were."""
    for exchange in Recording(path):
        if exchange.response.HasField("data"):
            return exchange.response.data
    raise AssertionError(f"{path.name} holds no tables")


def _tables(path: Path) -> GameData:
    """The tables a recorded game was played by."""
    return GameData(_answer(path))


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem)
class TestARecordedGamesTables:
    def test_every_curated_id_has_a_row(self, path: Path) -> None:
        data = _tables(path)
        assert not set(UnitTypeId) - set(data.units)
        assert not set(AbilityId) - set(data.abilities)
        assert not set(UpgradeId) - set(data.upgrades)
        assert not set(EffectId) - set(data.effects)

    def test_only_the_rows_a_bot_can_name_are_read(self, path: Path) -> None:
        """The game describes 2005 unit types and 4134 abilities; a bot has a name for a seventh of them."""
        data = _tables(path)
        assert (len(data.units), len(data.abilities)) == (len(UnitTypeId), len(AbilityId))
        assert (len(data.upgrades), len(data.effects)) == (len(UpgradeId), len(EffectId))

    def test_only_the_known_rows_lose_the_ability_that_makes_them(self, path: Path) -> None:
        """Everything else a curated row names is curated too, so nothing else comes back empty."""
        answer = _answer(path)
        data = GameData(answer)
        named = {row.unit_id: row.ability_id for row in answer.units if row.ability_id}
        lost = {unit: named[unit] for unit in UnitTypeId if unit in named and data.units[unit].creation_ability is None}
        assert lost == _NO_MAKER

    def test_a_viking_is_the_one_row_that_loses_a_tech_alias(self, path: Path) -> None:
        """Its alias is an empty row no unit is ever one of; every other alias names a unit you can own."""
        answer = _answer(path)
        data = GameData(answer)
        aliased = {row.unit_id: list(row.tech_alias) for row in answer.units if row.tech_alias}
        lost = {
            unit: aliased[unit]
            for unit in UnitTypeId
            if unit in aliased and len(data.units[unit].tech_aliases) != len(aliased[unit])
        }
        assert set(lost) == {UnitTypeId.VIKING, UnitTypeId.VIKING_LANDED}
        assert all(aliases == [_PHANTOM_VIKING] for aliases in lost.values())

    def test_a_general_ability_is_named_after_the_levels_that_remap_to_it(self, path: Path) -> None:
        """Renaming an upgrade has to carry its general ability along, or the two drift apart."""
        data = _tables(path)
        levels: dict[AbilityId, set[str]] = {}
        for ability in AbilityId:
            general = data.abilities[ability].remaps_to
            if general is not None and re.fullmatch(r".+_[123]", ability.name):
                levels.setdefault(general, set()).add(ability.name.rsplit("_", 1)[0])
        for general, stems in levels.items():
            assert stems == {general.name}, f"{general.name} is not what its levels are called: {sorted(stems)}"

    def test_a_row_is_keyed_by_its_own_id(self, path: Path) -> None:
        data = _tables(path)
        assert all(key == row.id for key, row in data.units.items())
        assert all(key == row.id for key, row in data.abilities.items())

    def test_a_morph_costs_what_it_came_from_as_well(self, path: Path) -> None:
        """The game charges only the difference, but its table names the whole of what has been spent."""
        data = _tables(path)
        for before, after in (
            (UnitTypeId.COMMAND_CENTER, UnitTypeId.ORBITAL_COMMAND),
            (UnitTypeId.HATCHERY, UnitTypeId.LAIR),
            (UnitTypeId.LAIR, UnitTypeId.HIVE),
            (UnitTypeId.ZERGLING, UnitTypeId.BANELING),
        ):
            grew, came_from = data.units[after].cost, data.units[before].cost
            assert grew.minerals >= came_from.minerals and grew.vespene >= came_from.vespene
            assert grew != came_from
        # The build time is the morph alone, so it is shorter than building what it morphed from took.
        assert data.units[UnitTypeId.ORBITAL_COMMAND].build_time < data.units[UnitTypeId.COMMAND_CENTER].build_time

    def test_every_curated_upgrade_names_the_ability_that_researches_it(self, path: Path) -> None:
        data = _tables(path)
        for upgrade in UpgradeId:
            row = data.upgrades[upgrade]
            assert row.research_time > 0
            if upgrade in _RESEARCHED_BY_A_DEAD_ID:
                assert row.research_ability is None, f"{upgrade} names a maker again"
            else:
                assert row.research_ability is not None, f"{upgrade} cannot be researched"

    def test_a_transient_form_of_a_unit_names_the_one_it_is_a_form_of(self, path: Path) -> None:
        data = _tables(path)
        for unit, base in (
            (UnitTypeId.SIEGE_TANK_SIEGED, UnitTypeId.SIEGE_TANK),
            (UnitTypeId.SUPPLY_DEPOT_LOWERED, UnitTypeId.SUPPLY_DEPOT),
            (UnitTypeId.BARRACKS_FLYING, UnitTypeId.BARRACKS),
        ):
            assert data.units[unit].base_type is base
