from __future__ import annotations

from auto_bdsp_rng.data import (
    GameVersion,
    StaticEncounterCategory,
    get_species_info,
    get_static_encounters,
    get_static_templates,
    load_species_info,
    load_static_encounters,
    validate_data,
)
from auto_bdsp_rng.gen8_static import Shiny


def test_load_species_info_parses_bdsp_personal_table():
    species = load_species_info()

    assert len(species) == 560
    turtwig = get_species_info(387)
    assert turtwig.stats == (55, 68, 64, 45, 55, 31)
    assert turtwig.gender_ratio == 31
    assert turtwig.abilities == (65, 65, 75)
    assert turtwig.normal_ability_count == 2
    assert turtwig.has_hidden_ability is True
    assert turtwig.as_generator_info().ability_count == 2


def test_static_encounters_include_template_metadata_and_species_info():
    records = load_static_encounters()

    assert len(records) == 47
    starters = get_static_encounters(StaticEncounterCategory.STARTERS)
    assert [record.description for record in starters] == ["Turtwig", "Chimchar", "Piplup"]
    assert starters[0].template.species == 387
    assert starters[0].template.level == 5
    assert starters[0].template.info.gender_ratio == 31


def test_static_encounters_filter_by_game_version():
    bd_legends = get_static_encounters(StaticEncounterCategory.LEGENDS, GameVersion.BD)
    sp_legends = get_static_encounters(StaticEncounterCategory.LEGENDS, GameVersion.SP)
    all_legends = get_static_encounters(StaticEncounterCategory.LEGENDS, GameVersion.BDSP)

    assert {record.description for record in bd_legends} == {"Uxie", "Azelf", "Dialga", "Heatran", "Regigigas", "Giratina"}
    assert {record.description for record in sp_legends} == {"Uxie", "Azelf", "Palkia", "Heatran", "Regigigas", "Giratina"}
    assert {record.description for record in all_legends} == {
        "Uxie",
        "Azelf",
        "Dialga",
        "Palkia",
        "Heatran",
        "Regigigas",
        "Giratina",
    }


def test_static_templates_preserve_fixed_ivs_shiny_lock_and_roamer_flags():
    fossils = get_static_templates(StaticEncounterCategory.FOSSILS)
    mythics = get_static_encounters(StaticEncounterCategory.MYTHICS)
    roamers = get_static_templates(StaticEncounterCategory.ROAMERS)

    assert all(template.iv_count == 3 for template in fossils)
    assert {record.description for record in mythics if record.template.shiny == Shiny.NEVER} == {"Mew", "Jirachi"}
    assert all(template.roamer for template in roamers)


def test_validate_data_accepts_embedded_bdsp_tables():
    validate_data()
