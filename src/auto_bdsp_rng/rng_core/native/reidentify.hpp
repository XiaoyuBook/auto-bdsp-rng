#pragma once
#include "types.hpp"
#include <optional>
#include <vector>

struct ReidentifyResult {
    u64 seed0;
    u64 seed1;
    u64 advances;
};

std::optional<ReidentifyResult> reidentify_by_intervals_native(
    u64 seed0,
    u64 seed1,
    const std::vector<u32>& raw_intervals,
    int npc,
    u32 search_min,
    u32 search_max);

std::optional<ReidentifyResult> reidentify_by_intervals_noisy_native(
    u64 seed0,
    u64 seed1,
    const std::vector<u32>& raw_intervals,
    u32 search_min,
    u32 search_max);
