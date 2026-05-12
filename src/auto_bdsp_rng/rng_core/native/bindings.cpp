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
    int tid, int sid,
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
            lead, static_cast<u16>(tid), static_cast<u16>(sid), 0, filter);
    } else {
        results = generate_non_roamer(
            seed0, seed1, initial_advances, max_advances, offset,
            lead, static_cast<ShinyTemplate>(shiny_template), fateful,
            static_cast<u8>(iv_count), static_cast<u8>(ability_template),
            static_cast<u8>(gender_ratio), static_cast<u8>(ability_count),
            static_cast<u16>(tid), static_cast<u16>(sid), filter);
    }

    py::list out;
    for (const auto& r : results) {
        out.append(state_to_tuple(r));
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
          py::arg("tid"), py::arg("sid"),
          py::arg("filter_skip"), py::arg("filter_ability"),
          py::arg("filter_gender"), py::arg("filter_shiny"),
          py::arg("height_min"), py::arg("height_max"),
          py::arg("weight_min"), py::arg("weight_max"),
          py::arg("iv_min"), py::arg("iv_max"),
          py::arg("natures"), py::arg("hidden_powers"),
          "Generate BDSP static encounter states (C++ native).");
}
