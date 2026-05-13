"""C++ 原生扩展正确性对比 + 性能基准测试。

在已安装 C++ 扩展的环境中运行：
  pip install -e .
  python -m pytest tests/rng_core/test_native_generator.py -v
"""

from __future__ import annotations

import time

import pytest

from auto_bdsp_rng.gen8_static import Lead, Profile8, Shiny, State8, StateFilter, StaticGenerator8
from auto_bdsp_rng.gen8_static.generator import _HAS_NATIVE
from auto_bdsp_rng.gen8_static.models import StaticTemplate8


@pytest.fixture
def template() -> StaticTemplate8:
    return StaticTemplate8(species=483, shiny=Shiny.RANDOM, iv_count=0, ability=255)


@pytest.fixture
def profile() -> Profile8:
    return Profile8(tid=12345, sid=54321)


@pytest.fixture
def state_filter() -> StateFilter:
    return StateFilter()


def _run_generator(initial: int, max_adv: int, lead: object, template: object,
                   profile: object, sf: object,
                   seed0: int = 0x1234567890ABCDEF,
                   seed1: int = 0xFEDCBA0987654321) -> list[State8]:
    gen = StaticGenerator8(initial, max_adv, 0, lead, template, profile, sf)
    return gen.generate(seed0, seed1)


def _state_key(s: State8) -> tuple:
    return (s.advances, s.ec, s.pid, s.sidtid, s.shiny, tuple(s.ivs),
            s.ability, s.gender, s.nature, s.height, s.weight)


@pytest.mark.skipif(not _HAS_NATIVE, reason="C++ native extension not installed")
def test_native_matches_python_non_roamer(template, profile, state_filter):
    """C++ 原生扩展输出与 Python 实现完全一致（非游走精灵，1000 帧）。"""
    import auto_bdsp_rng.gen8_static.generator as mod

    # Python 路径
    mod._HAS_NATIVE = False
    py_states = set(_state_key(s) for s in
                    _run_generator(0, 1000, Lead.NONE, template, profile, state_filter))

    # C++ 路径
    mod._HAS_NATIVE = True
    cpp_states = set(_state_key(s) for s in
                     _run_generator(0, 1000, Lead.NONE, template, profile, state_filter))

    # 恢复
    mod._HAS_NATIVE = True  # should already be set

    assert py_states == cpp_states, \
        f"Python={len(py_states)} results, C++={len(cpp_states)} results"


@pytest.mark.skipif(not _HAS_NATIVE, reason="C++ native extension not installed")
def test_native_performance(template, profile, state_filter):
    """C++ 原生扩展在 100 万帧内完成（< 1 秒）。"""
    import auto_bdsp_rng.gen8_static.generator as mod

    assert _HAS_NATIVE
    t0 = time.perf_counter()
    states = _run_generator(0, 1_000_000, Lead.NONE, template, profile, state_filter)
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0, f"C++ 100万帧耗时 {elapsed:.2f}s 超过 1s 上限"
    assert len(states) == 1_000_001


def test_python_fallback_works(template, profile, state_filter):
    """Python fallback 路径在无 C++ 扩展时正常运行。"""
    states = _run_generator(0, 100, Lead.NONE, template, profile, state_filter)
    assert len(states) == 101
    assert states[0].ec == 0xB7524223  # 已知参考值
