"""Data loading for BDSP static templates and species metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from struct import unpack_from
from typing import Iterable

from auto_bdsp_rng.gen8_static.models import PersonalInfo8, Shiny, StaticTemplate8
from auto_bdsp_rng.resources import resource_path


class GameVersion(StrEnum):
    BD = "BD"
    SP = "SP"
    BDSP = "BDSP"


class StaticEncounterCategory(StrEnum):
    STARTERS = "starters"
    GIFTS = "gifts"
    FOSSILS = "fossils"
    STATIONARY = "stationary"
    ROAMERS = "roamers"
    LEGENDS = "legends"
    RAMANAS_PARK_PURE_SPACE = "ramanasParkPureSpace"
    RAMANAS_PARK_STRANGE_SPACE = "ramanasParkStrangeSpace"
    MYTHICS = "mythics"


@dataclass(frozen=True)
class SpeciesInfo8:
    species: int
    form: int
    stats: tuple[int, int, int, int, int, int]
    types: tuple[int, int]
    gender_ratio: int
    abilities: tuple[int, int, int]
    form_count: int
    form_stat_index: int
    hatch_species: int
    present: bool

    @property
    def normal_ability_count(self) -> int:
        _first, second, _hidden = self.abilities
        return 1 if second == 0 else 2

    @property
    def has_hidden_ability(self) -> bool:
        first, _second, hidden = self.abilities
        return hidden != 0 and hidden != first

    def as_generator_info(self) -> PersonalInfo8:
        ability_slots = 2 if self.abilities[0] != 0 else 1
        return PersonalInfo8(
            gender_ratio=self.gender_ratio,
            ability_count=ability_slots,
        )


@dataclass(frozen=True)
class StaticEncounterRecord:
    category: StaticEncounterCategory
    description: str
    version: GameVersion
    template: StaticTemplate8
    species_info: SpeciesInfo8


_PERSONAL_BDSP_RECORD_SIZE = 0x44

_STATIC_TEMPLATE_ROWS: tuple[tuple[str, str, str, int, int, Shiny, int, int, int, bool, bool], ...] = (
    ("starters", "Turtwig", "BDSP", 387, 0, Shiny.RANDOM, 255, 255, 0, False, False),
    ("starters", "Chimchar", "BDSP", 390, 0, Shiny.RANDOM, 255, 255, 0, False, False),
    ("starters", "Piplup", "BDSP", 393, 0, Shiny.RANDOM, 255, 255, 0, False, False),
    ("gifts", "Eevee", "BDSP", 133, 0, Shiny.RANDOM, 255, 255, 0, False, False),
    ("gifts", "Happiny egg", "BDSP", 440, 0, Shiny.RANDOM, 255, 255, 0, False, False),
    ("gifts", "Riolu egg", "BDSP", 447, 0, Shiny.RANDOM, 255, 255, 0, False, False),
    ("fossils", "Omanyte", "BDSP", 138, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("fossils", "Kabuto", "BDSP", 140, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("fossils", "Aerodactyl", "BDSP", 142, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("fossils", "Lileep", "BDSP", 345, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("fossils", "Anorith", "BDSP", 347, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("fossils", "Cranidos", "BDSP", 408, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("fossils", "Shieldon", "BDSP", 410, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("stationary", "Drifloon", "BDSP", 425, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("stationary", "Spiritomb", "BDSP", 442, 0, Shiny.RANDOM, 255, 255, 0, False, False),
    ("stationary", "Rotom", "BDSP", 479, 0, Shiny.RANDOM, 255, 255, 0, False, False),
    ("roamers", "Mespirit", "BDSP", 481, 0, Shiny.RANDOM, 255, 255, 3, False, True),
    ("roamers", "Cresselia", "BDSP", 488, 0, Shiny.RANDOM, 255, 255, 3, False, True),
    ("legends", "Uxie", "BDSP", 480, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("legends", "Azelf", "BDSP", 482, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("legends", "Dialga", "BD", 483, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("legends", "Palkia", "SP", 484, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("legends", "Heatran", "BDSP", 485, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("legends", "Regigigas", "BDSP", 486, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("legends", "Giratina", "BDSP", 487, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("ramanasParkPureSpace", "Articuno", "SP", 144, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkPureSpace", "Zapdos", "SP", 145, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkPureSpace", "Moltres", "SP", 146, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkPureSpace", "Raikou", "BD", 243, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkPureSpace", "Entei", "BD", 244, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkPureSpace", "Suicune", "BD", 245, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkPureSpace", "Regirock", "BDSP", 377, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkPureSpace", "Regice", "BDSP", 378, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkPureSpace", "Registeel", "BDSP", 379, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkPureSpace", "Latias", "BDSP", 380, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("ramanasParkPureSpace", "Latios", "BDSP", 381, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("ramanasParkStrangeSpace", "Mewtwo", "BDSP", 150, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkStrangeSpace", "Lugia", "SP", 249, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkStrangeSpace", "Ho-Oh", "BD", 250, 0, Shiny.RANDOM, 2, 255, 3, False, False),
    ("ramanasParkStrangeSpace", "Kyogre", "BDSP", 382, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("ramanasParkStrangeSpace", "Groudon", "BDSP", 383, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("ramanasParkStrangeSpace", "Rayquaza", "BDSP", 384, 0, Shiny.RANDOM, 255, 255, 3, False, False),
    ("mythics", "Mew", "BDSP", 151, 0, Shiny.NEVER, 1, 255, 3, True, False),
    ("mythics", "Jirachi", "BDSP", 385, 0, Shiny.NEVER, 1, 255, 3, True, False),
    ("mythics", "Darkrai", "BDSP", 491, 0, Shiny.RANDOM, 255, 255, 3, True, False),
    ("mythics", "Shaymin", "BDSP", 492, 0, Shiny.RANDOM, 255, 255, 3, True, False),
    ("mythics", "Arceus", "BDSP", 493, 0, Shiny.RANDOM, 255, 255, 3, True, False),
)


def _personal_bdsp_path() -> Path:
    return resource_path("third_party", "PokeFinder", "Core", "Resources", "Personal", "Gen8", "personal_bdsp.bin")


def _normalize_version(version: GameVersion | str) -> GameVersion:
    try:
        return GameVersion(version)
    except ValueError as exc:
        raise ValueError("version must be BD, SP, or BDSP") from exc


def _version_matches(template_version: GameVersion, requested_version: GameVersion) -> bool:
    return requested_version == GameVersion.BDSP or template_version in (requested_version, GameVersion.BDSP)


def _parse_species_info(data: bytes, index: int) -> SpeciesInfo8:
    offset = index * _PERSONAL_BDSP_RECORD_SIZE
    hp, atk, defense, speed, sp_atk, sp_def = data[offset : offset + 6]
    type1, type2 = data[offset + 0x6], data[offset + 0x7]
    gender = data[offset + 0x12]
    ability1 = unpack_from("<H", data, offset + 0x18)[0]
    ability2 = unpack_from("<H", data, offset + 0x1A)[0]
    hidden_ability = unpack_from("<H", data, offset + 0x1C)[0]
    form_stat_index = unpack_from("<H", data, offset + 0x1E)[0]
    form_count = data[offset + 0x20]
    present = bool((data[offset + 0x21] >> 6) & 1)
    hatch_species = unpack_from("<H", data, offset + 0x3E)[0]
    return SpeciesInfo8(
        species=index,
        form=0,
        stats=(hp, atk, defense, sp_atk, sp_def, speed),
        types=(type1, type2),
        gender_ratio=gender,
        abilities=(ability1, ability2, hidden_ability),
        form_count=form_count,
        form_stat_index=form_stat_index,
        hatch_species=hatch_species,
        present=present,
    )


@cache
def load_species_info(path: str | Path | None = None) -> tuple[SpeciesInfo8, ...]:
    data_path = Path(path) if path is not None else _personal_bdsp_path()
    data = data_path.read_bytes()
    if len(data) % _PERSONAL_BDSP_RECORD_SIZE != 0:
        raise ValueError("personal_bdsp.bin length is not a multiple of the BDSP record size")
    return tuple(_parse_species_info(data, index) for index in range(len(data) // _PERSONAL_BDSP_RECORD_SIZE))


def get_species_info(species: int, form: int = 0, *, table: tuple[SpeciesInfo8, ...] | None = None) -> SpeciesInfo8:
    species_table = load_species_info() if table is None else table
    if not 0 <= species < len(species_table):
        raise ValueError(f"unknown BDSP species id: {species}")
    base = species_table[species]
    if form == 0 or base.form_stat_index == 0:
        return base
    index = base.form_stat_index + form - 1
    if not 0 <= index < len(species_table):
        raise ValueError(f"unknown BDSP species/form id: {species}/{form}")
    form_info = species_table[index]
    return SpeciesInfo8(
        species=species,
        form=form,
        stats=form_info.stats,
        types=form_info.types,
        gender_ratio=form_info.gender_ratio,
        abilities=form_info.abilities,
        form_count=form_info.form_count,
        form_stat_index=form_info.form_stat_index,
        hatch_species=form_info.hatch_species,
        present=form_info.present,
    )


@cache
def load_static_encounters() -> tuple[StaticEncounterRecord, ...]:
    records: list[StaticEncounterRecord] = []
    species_table = load_species_info()
    for category, description, version, species, form, shiny, ability, gender, iv_count, fateful, roamer in _STATIC_TEMPLATE_ROWS:
        species_info = get_species_info(species, form, table=species_table)
        template = StaticTemplate8(
            species=species,
            form=form,
            shiny=shiny,
            ability=ability,
            gender=gender,
            iv_count=iv_count,
            level=_level_for(description),
            fateful=fateful,
            roamer=roamer,
            info=species_info.as_generator_info(),
            version=version,
        )
        records.append(
            StaticEncounterRecord(
                category=StaticEncounterCategory(category),
                description=description,
                version=GameVersion(version),
                template=template,
                species_info=species_info,
            )
        )
    return tuple(records)


def _level_for(description: str) -> int:
    levels = {
        "Turtwig": 5,
        "Chimchar": 5,
        "Piplup": 5,
        "Eevee": 5,
        "Happiny egg": 1,
        "Riolu egg": 1,
        "Omanyte": 1,
        "Kabuto": 1,
        "Aerodactyl": 1,
        "Lileep": 1,
        "Anorith": 1,
        "Cranidos": 1,
        "Shieldon": 1,
        "Drifloon": 22,
        "Spiritomb": 25,
        "Rotom": 15,
        "Mespirit": 50,
        "Cresselia": 50,
        "Uxie": 50,
        "Azelf": 50,
        "Dialga": 47,
        "Palkia": 47,
        "Heatran": 70,
        "Regigigas": 70,
        "Giratina": 70,
        "Darkrai": 50,
        "Shaymin": 30,
        "Arceus": 80,
    }
    return levels.get(description, 70)


def get_static_encounters(
    category: StaticEncounterCategory | str | None = None,
    version: GameVersion | str = GameVersion.BDSP,
) -> tuple[StaticEncounterRecord, ...]:
    requested_version = _normalize_version(version)
    requested_category = StaticEncounterCategory(category) if category is not None else None
    return tuple(
        record
        for record in load_static_encounters()
        if (requested_category is None or record.category == requested_category)
        and _version_matches(record.version, requested_version)
    )


def get_static_templates(
    category: StaticEncounterCategory | str | None = None,
    version: GameVersion | str = GameVersion.BDSP,
) -> tuple[StaticTemplate8, ...]:
    return tuple(record.template for record in get_static_encounters(category, version))


def validate_data(records: Iterable[StaticEncounterRecord] | None = None) -> None:
    encounter_records = tuple(load_static_encounters() if records is None else records)
    if not encounter_records:
        raise ValueError("BDSP static encounter data is empty")

    categories = {record.category for record in encounter_records}
    missing = set(StaticEncounterCategory) - categories
    if missing:
        names = ", ".join(sorted(category.value for category in missing))
        raise ValueError(f"missing static encounter categories: {names}")

    for record in encounter_records:
        template = record.template
        info = record.species_info
        if template.species != info.species:
            raise ValueError(f"{record.description} species info does not match its template")
        if template.ability == 2 and not info.has_hidden_ability:
            raise ValueError(f"{record.description} requires hidden ability data")
        if template.iv_count == 3 and template.level < 1:
            raise ValueError(f"{record.description} has an invalid fixed-IV level")


__all__ = [
    "GameVersion",
    "SpeciesInfo8",
    "StaticEncounterCategory",
    "StaticEncounterRecord",
    "get_species_info",
    "get_static_encounters",
    "get_static_templates",
    "load_species_info",
    "load_static_encounters",
    "validate_data",
]
