#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "generator.hpp"

namespace py = pybind11;

// Python tuple/list → FilterParams
static FilterParams build_filter(
    bool skip, int ability, int gender, int shiny_Mask,
    int height_min, int height_max, int weight_min, int weight_max,
    py::tuple iv_min_py, py::tuple iv_max_py,
    py::tuple natures_py, py::tuple hidden_powers_py)
{
    FilterParams f;
    f.skip = skip;
    f.ability = static_cast<u8>(ability);
    f.gender = static_cast<u8>(gender);
    f.shiny_mask = static_cast<u8>(shiny_Mask);
    f.height_min = static_cast<u8>(height_min);
    f.height_max = static_cast<u8>(height_max);
    f.weight_min = static_cast<u8>(weight_min);
    f.weight_max = static_cast<u8>(weight_max);

    for (int i = 0; i < 6; i++) {
        f.iv_min[i] = static_cast<u8>(py::int_(iv_min_py[i]));
        f.iv_max[i] = static_cast<u8>(py::int_(iv_max_py[i]));
    }
    for (int i = 0; i < 25; i++) {
        f.natures[i] = py::bool_(natures_py[i]);
    }
    for (int i = 0; i < 16; i++) {
        f.hidden_powers[i] = py::bool_(hidden_powers_py[i]);
    }
    return f;
}

// StateResult → Python tuple
static py::tuple state_to_tuple(const StateResult& r) {
    py::tuple ivs(6);
    for (int i = 0; i < 6; i++) ivs[i] = r.ivs[i];
    return py::make_tuple(
        r.advances, r.ec, r.sidtid, r.pid, ivs,
        r.ability, r.gender, r.level, r.nature,
        r.shiny, r.height, r.weight);
}

// 统一入口
static py::list generate_static_py(
    u64 seed0, u64 seed1,
    u32 initial_advances, u32 max_advances, u32 offset,
    int lead, bool roamer,
    int shiny_template, bool fateful,
    int iv_count, int ability_template,
    int gender_ratio, int ability_count,
    int tid, int sid, int level,
    // Filter params
    bool filter_skip, int filter_ability, int filter_gender, int filter_shiny,
    int height_min, int height_max, int weight_min, int weight_max,
    py::tuple iv_min_py, py::tuple iv_max_py,
    py::tuple natures_py, py::tuple hidden_powers_py)
{
    FilterParams filter = build_filter(
        filter_skip, filter_ability, filter_gender, filter_shiny,
        height_min, height_max, weight_min, weight_max,
        iv_min_py, iv_max_py, natures_py, hidden_powers_py);

    std::vector<StateResult> results;
    if (roamer) {
        results = generate_roamer(
            seed0, seed1, initial_advances, max_advances, offset,
            lead, static_cast<u16>(tid), static_cast<u16>(sid), 0,
            static_cast<u8>(level), filter);
    } else {
        results = generate_non_roamer(
            seed0, seed1, initial_advances, max_advances, offset,
            lead, static_cast<ShinyTemplate>(shiny_template), fateful,
            static_cast<u8>(iv_count), static_cast<u8>(ability_template),
            static_cast<u8>(gender_ratio), static_cast<u8>(ability_count),
            static_cast<u16>(tid), static_cast<u16>(sid),
            static_cast<u8>(level), filter);
    }

    py::list out;
    for (const auto& r : results) {
        out.append(state_to_tuple(r));
    }
    return out;
}

// ── IVChecker：PokeFinder Nature::computeStat 公式 ──

static const float nature_modifiers[25][5] = {
    {1.0f, 1.0f, 1.0f, 1.0f, 1.0f}, // 0: Hardy
    {1.1f, 0.9f, 1.0f, 1.0f, 1.0f}, // 1: Lonely
    {1.1f, 1.0f, 1.0f, 1.0f, 0.9f}, // 2: Brave
    {1.1f, 1.0f, 0.9f, 1.0f, 1.0f}, // 3: Adamant
    {1.1f, 1.0f, 1.0f, 0.9f, 1.0f}, // 4: Naughty
    {0.9f, 1.1f, 1.0f, 1.0f, 1.0f}, // 5: Bold
    {1.0f, 1.0f, 1.0f, 1.0f, 1.0f}, // 6: Docile
    {1.0f, 1.1f, 1.0f, 1.0f, 0.9f}, // 7: Relaxed
    {1.0f, 1.1f, 0.9f, 1.0f, 1.0f}, // 8: Impish
    {1.0f, 1.1f, 1.0f, 0.9f, 1.0f}, // 9: Lax
    {0.9f, 1.0f, 1.0f, 1.0f, 1.1f}, // 10: Timid
    {1.0f, 0.9f, 1.0f, 1.0f, 1.1f}, // 11: Hasty
    {1.0f, 1.0f, 1.0f, 1.0f, 1.0f}, // 12: Serious
    {1.0f, 1.0f, 0.9f, 1.0f, 1.1f}, // 13: Jolly
    {1.0f, 1.0f, 1.0f, 0.9f, 1.1f}, // 14: Naive
    {0.9f, 1.0f, 1.1f, 1.0f, 1.0f}, // 15: Modest
    {1.0f, 0.9f, 1.1f, 1.0f, 1.0f}, // 16: Mild
    {1.0f, 1.0f, 1.1f, 1.0f, 0.9f}, // 17: Quiet
    {1.0f, 1.0f, 1.0f, 1.0f, 1.0f}, // 18: Bashful
    {1.0f, 1.0f, 1.1f, 0.9f, 1.0f}, // 19: Rash
    {0.9f, 1.0f, 1.0f, 1.1f, 1.0f}, // 20: Calm
    {1.0f, 0.9f, 1.0f, 1.1f, 1.0f}, // 21: Gentle
    {1.0f, 1.0f, 1.0f, 1.1f, 0.9f}, // 22: Sassy
    {1.0f, 1.0f, 0.9f, 1.1f, 1.0f}, // 23: Careful
    {1.0f, 1.0f, 1.0f, 1.0f, 1.0f}, // 24: Quirky
};

static u16 compute_stat_pf(u16 base, u8 iv, u8 nature, u8 level, u8 index) {
    u16 s = (2 * base + iv) * level / 100;
    if (index == 0) return s + level + 10; // HP
    if (nature >= 25) return s + 5;
    return static_cast<u16>((s + 5) * nature_modifiers[nature][index - 1]);
}

// 返回 (minIV, maxIV) — 与 PokeFinder IVChecker 完全一致
static py::tuple compute_iv_range(u16 base, u16 stat_val, u8 nature, u8 level, u8 index) {
    u8 min_iv = 31, max_iv = 0;
    if (nature >= 25) {
        // 未知性格：尝试全部三种修正（1.0, 0.9, 1.1）
        const float mods[3] = {1.0f, 0.9f, 1.1f};
        for (u8 iv = 0; iv < 32; iv++) {
            u16 s = (2 * base + iv) * level / 100;
            if (index == 0) {
                u16 hp = s + level + 10;
                if (hp == stat_val) { if (iv < min_iv) min_iv = iv; if (iv > max_iv) max_iv = iv; }
            } else {
                for (float m : mods) {
                    if (static_cast<u16>((s + 5) * m) == stat_val) {
                        if (iv < min_iv) min_iv = iv; if (iv > max_iv) max_iv = iv;
                        break;
                    }
                }
            }
        }
    } else {
        for (u8 iv = 0; iv < 32; iv++) {
            if (compute_stat_pf(base, iv, nature, level, index) == stat_val) {
                if (iv < min_iv) min_iv = iv;
                if (iv > max_iv) max_iv = iv;
            }
        }
    }
    return py::make_tuple(min_iv, max_iv);
}

// 批量计算 6 项能力 → 返回 [(min0,max0), ..., (min5,max5)]
static py::list compute_iv_ranges_py(py::list bases_py, py::list stats_py, int nature, int level) {
    py::list out;
    for (int i = 0; i < 6; i++) {
        u16 base = bases_py[i].cast<u16>();
        u16 sv = stats_py[i].cast<u16>();
        out.append(compute_iv_range(base, sv, static_cast<u8>(nature), static_cast<u8>(level), static_cast<u8>(i)));
    }
    return out;
}

PYBIND11_MODULE(_native, m) {
    m.doc() = "BDSP RNG static generator (C++ native extension)";
    m.def("generate_static", &generate_static_py,
          py::arg("seed0"), py::arg("seed1"),
          py::arg("initial_advances"), py::arg("max_advances"), py::arg("offset"),
          py::arg("lead"), py::arg("roamer"),
          py::arg("shiny_template"), py::arg("fateful"),
          py::arg("iv_count"), py::arg("ability_template"),
          py::arg("gender_ratio"), py::arg("ability_count"),
          py::arg("tid"), py::arg("sid"), py::arg("level"),
          py::arg("filter_skip"), py::arg("filter_ability"),
          py::arg("filter_gender"), py::arg("filter_shiny"),
          py::arg("height_min"), py::arg("height_max"),
          py::arg("weight_min"), py::arg("weight_max"),
          py::arg("iv_min"), py::arg("iv_max"),
          py::arg("natures"), py::arg("hidden_powers"),
          "Generate BDSP static encounter states (C++ native).");
    m.def("compute_iv_ranges", &compute_iv_ranges_py,
          py::arg("bases"), py::arg("stats"), py::arg("nature"), py::arg("level"),
          "Compute IV ranges from stats using PokeFinder's Nature::computeStat formula.");
}
