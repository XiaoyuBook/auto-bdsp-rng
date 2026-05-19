from __future__ import annotations

import json
from pathlib import Path

from auto_bdsp_rng.gen8_id import IDFilter, IDGenerator8


def _state_dicts(states):
    return [
        {
            "advances": state.advances,
            "displayTID": state.display_tid,
            "sid": state.sid,
            "tid": state.tid,
            "tsv": state.tsv,
        }
        for state in states
    ]


def test_gen8_id_generator_matches_pokefinder_samples():
    data = json.loads(Path("third_party/PokeFinder/Test/Gen8/id8.json").read_text(encoding="utf-8"))

    for sample in data["generate"]:
        generator = IDGenerator8(initial_advances=0, max_advances=len(sample["results"]))

        assert _state_dicts(generator.generate(sample["seed0"], sample["seed1"])) == sample["results"]


def test_gen8_id_filter_matches_individual_fields():
    generator = IDGenerator8(
        initial_advances=0,
        max_advances=9,
        state_filter=IDFilter(tid=[2056], sid=[49216], tsv=[3204], display_tid=[421832]),
    )

    states = generator.generate(4611686018427387904, 4611686018427387904)

    assert _state_dicts(states) == [
        {"advances": 1, "displayTID": 421832, "sid": 49216, "tid": 2056, "tsv": 3204}
    ]
