#pragma once
#include "types.hpp"
#include <array>
#include <vector>

struct StateResult {
    u32 advances;
    u32 ec;
    u32 sidtid;
    u32 pid;
    u8 ivs[6];
    u8 ability;
    u8 gender;
    u8 level;
    u8 nature;
    u8 shiny;
    u8 height;
    u8 weight;
};

enum class ShinyTemplate : u8 { Random = 0, Never = 1, Star = 3, Square = 4, Static = 5 };

struct FilterParams {
    bool skip = false;
    u8 ability = 255;
    u8 gender = 255;
    u8 shiny_mask = 255;  // bitmask: 1=star, 2=square
    u8 height_min = 0, height_max = 255;
    u8 weight_min = 0, weight_max = 255;
    std::array<u8, 6> iv_min = {0, 0, 0, 0, 0, 0};
    std::array<u8, 6> iv_max = {31, 31, 31, 31, 31, 31};
    std::array<bool, 25> natures{};
    std::array<bool, 16> hidden_powers{};

    FilterParams() {
        natures.fill(true);
        hidden_powers.fill(true);
    }
};

std::vector<StateResult> generate_non_roamer(
    u64 seed0, u64 seed1,
    u32 initial_advances, u32 max_advances, u32 offset,
    int lead, ShinyTemplate shiny_template, bool fateful,
    u8 iv_count, u8 ability_template, u8 gender_ratio, u8 ability_count,
    u16 tid, u16 sid, u8 level,
    const FilterParams& filter);

std::vector<StateResult> generate_non_roamer_multi(
    u64 seed0, u64 seed1,
    u32 initial_advances, u32 max_advances, u32 offset,
    int lead, ShinyTemplate shiny_template, bool fateful,
    u8 iv_count, u8 ability_template, u8 gender_ratio, u8 ability_count,
    u16 tid, u16 sid, u8 level,
    const std::vector<FilterParams>& filters);

std::vector<StateResult> generate_roamer(
    u64 seed0, u64 seed1,
    u32 initial_advances, u32 max_advances, u32 offset,
    int lead, u16 tid, u16 sid, u16 species, u8 level,
    const FilterParams& filter);

std::vector<StateResult> generate_roamer_multi(
    u64 seed0, u64 seed1,
    u32 initial_advances, u32 max_advances, u32 offset,
    int lead, u16 tid, u16 sid, u16 species, u8 level,
    const std::vector<FilterParams>& filters);
