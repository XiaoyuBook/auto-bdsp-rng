#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <chrono>
#include <thread>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <memory>
#endif
#include "generator.hpp"
#include "reidentify.hpp"

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

static std::vector<FilterParams> build_filters(py::list filters_py)
{
    std::vector<FilterParams> filters;
    filters.reserve(py::len(filters_py));
    for (const auto& item : filters_py) {
        py::tuple f = item.cast<py::tuple>();
        filters.push_back(build_filter(
            f[0].cast<bool>(), f[1].cast<int>(), f[2].cast<int>(), f[3].cast<int>(),
            f[4].cast<int>(), f[5].cast<int>(), f[6].cast<int>(), f[7].cast<int>(),
            f[8].cast<py::tuple>(), f[9].cast<py::tuple>(),
            f[10].cast<py::tuple>(), f[11].cast<py::tuple>()));
    }
    return filters;
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

static py::list states_to_list(const std::vector<StateResult>& results, const SearchCancellation* cancellation) {
    py::list out;
#ifdef _WIN32
    // Sleep(1) can round up to a 15.6ms scheduler tick. A reusable high-resolution
    // timer keeps the real 1ms handoff without paying that delay for every batch.
    std::unique_ptr<void, decltype(&CloseHandle)> scheduling_timer(
        results.size() > 4096
            ? CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
                                    TIMER_MODIFY_STATE | SYNCHRONIZE)
            : nullptr,
        &CloseHandle);
#endif
    for (std::size_t index = 0; index < results.size(); ++index) {
        if ((index & 1023) == 0) {
            check_cancelled(cancellation);
            // A scheduler yield may immediately reacquire the GIL on Windows.
            // Give its waiter a real scheduling window between bounded batches.
            if ((index & 4095) == 0 && index != 0) {
                py::gil_scoped_release release;
                bool paused = false;
#ifdef _WIN32
                if (scheduling_timer) {
                    LARGE_INTEGER due;
                    due.QuadPart = -10'000;  // Relative 1ms, in 100ns units.
                    paused = SetWaitableTimer(scheduling_timer.get(), &due, 0, nullptr, nullptr, FALSE)
                        && WaitForSingleObject(scheduling_timer.get(), INFINITE) == WAIT_OBJECT_0;
                }
#endif
                if (!paused) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(1));
                }
            }
        }
        out.append(state_to_tuple(results[index]));
    }
    check_cancelled(cancellation);
    return out;
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
    py::tuple natures_py, py::tuple hidden_powers_py,
    const SearchCancellation* cancellation)
{
    FilterParams filter = build_filter(
        filter_skip, filter_ability, filter_gender, filter_shiny,
        height_min, height_max, weight_min, weight_max,
        iv_min_py, iv_max_py, natures_py, hidden_powers_py);

    std::vector<StateResult> results;
    {
    py::gil_scoped_release release;
    if (roamer) {
        results = generate_roamer(
            seed0, seed1, initial_advances, max_advances, offset,
            lead, static_cast<u16>(tid), static_cast<u16>(sid), 0,
            static_cast<u8>(level), filter, cancellation);
    } else {
        results = generate_non_roamer(
            seed0, seed1, initial_advances, max_advances, offset,
            lead, static_cast<ShinyTemplate>(shiny_template), fateful,
            static_cast<u8>(iv_count), static_cast<u8>(ability_template),
            static_cast<u8>(gender_ratio), static_cast<u8>(ability_count),
            static_cast<u16>(tid), static_cast<u16>(sid),
            static_cast<u8>(level), filter, cancellation);
    }
    }
    return states_to_list(results, cancellation);
}

static py::list generate_static_multi_py(
    u64 seed0, u64 seed1,
    u32 initial_advances, u32 max_advances, u32 offset,
    int lead, bool roamer,
    int shiny_template, bool fateful,
    int iv_count, int ability_template,
    int gender_ratio, int ability_count,
    int tid, int sid, int level,
    py::list filters_py, const SearchCancellation* cancellation)
{
    std::vector<FilterParams> filters = build_filters(filters_py);
    std::vector<StateResult> results;
    {
    py::gil_scoped_release release;
    if (roamer) {
        results = generate_roamer_multi(
            seed0, seed1, initial_advances, max_advances, offset,
            lead, static_cast<u16>(tid), static_cast<u16>(sid), 0,
            static_cast<u8>(level), filters, cancellation);
    } else {
        results = generate_non_roamer_multi(
            seed0, seed1, initial_advances, max_advances, offset,
            lead, static_cast<ShinyTemplate>(shiny_template), fateful,
            static_cast<u8>(iv_count), static_cast<u8>(ability_template),
            static_cast<u8>(gender_ratio), static_cast<u8>(ability_count),
            static_cast<u16>(tid), static_cast<u16>(sid),
            static_cast<u8>(level), filters, cancellation);
    }
    }
    return states_to_list(results, cancellation);
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

static py::object reidentify_by_intervals_py(
    u64 seed0, u64 seed1, std::vector<u32> intervals, int npc, u32 search_min, u32 search_max,
    const SearchCancellation* cancellation)
{
    std::optional<ReidentifyResult> result;
    {
        py::gil_scoped_release release;
        result = reidentify_by_intervals_native(seed0, seed1, intervals, npc, search_min, search_max, cancellation);
    }
    if (!result.has_value()) {
        return py::none();
    }
    return py::make_tuple(result->seed0, result->seed1, result->advances);
}

static py::object reidentify_by_intervals_noisy_py(
    u64 seed0, u64 seed1, std::vector<u32> intervals, u32 search_min, u32 search_max,
    const SearchCancellation* cancellation)
{
    std::optional<ReidentifyResult> result;
    {
        py::gil_scoped_release release;
        result = reidentify_by_intervals_noisy_native(seed0, seed1, intervals, search_min, search_max, cancellation);
    }
    if (!result.has_value()) {
        return py::none();
    }
    return py::make_tuple(result->seed0, result->seed1, result->advances);
}

PYBIND11_MODULE(_native, m) {
    m.doc() = "BDSP RNG static generator (C++ native extension)";
    py::register_exception<SearchCancelled>(m, "SearchCancelled");
    py::class_<SearchCancellation>(m, "SearchCancellation")
        .def(py::init<>())
        .def("cancel", &SearchCancellation::cancel)
        .def("is_cancelled", &SearchCancellation::is_cancelled);
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
          py::arg("cancel_token") = nullptr,
          "Generate BDSP static encounter states (C++ native).");
    m.def("compute_iv_ranges", &compute_iv_ranges_py,
          py::arg("bases"), py::arg("stats"), py::arg("nature"), py::arg("level"),
          "Compute IV ranges from stats using PokeFinder's Nature::computeStat formula.");
    m.def("generate_static_multi", &generate_static_multi_py,
          py::arg("seed0"), py::arg("seed1"),
          py::arg("initial_advances"), py::arg("max_advances"), py::arg("offset"),
          py::arg("lead"), py::arg("roamer"),
          py::arg("shiny_template"), py::arg("fateful"),
          py::arg("iv_count"), py::arg("ability_template"),
          py::arg("gender_ratio"), py::arg("ability_count"),
          py::arg("tid"), py::arg("sid"), py::arg("level"),
          py::arg("filters"),
          py::arg("cancel_token") = nullptr,
          "Generate BDSP static encounter states matching any filter (C++ native).");
    m.def("reidentify_by_intervals", &reidentify_by_intervals_py,
          py::arg("seed0"), py::arg("seed1"), py::arg("intervals"),
          py::arg("npc"), py::arg("search_min"), py::arg("search_max"),
          py::arg("cancel_token") = nullptr,
          "Reidentify Project_Xs Xorshift state from blink intervals (C++ native).");
    m.def("reidentify_by_intervals_noisy", &reidentify_by_intervals_noisy_py,
          py::arg("seed0"), py::arg("seed1"), py::arg("intervals"),
          py::arg("search_min"), py::arg("search_max"),
          py::arg("cancel_token") = nullptr,
          "Reidentify Project_Xs Xorshift state from noisy blink intervals (C++ native).");
}
