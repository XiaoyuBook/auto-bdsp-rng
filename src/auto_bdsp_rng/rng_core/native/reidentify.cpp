#include "reidentify.hpp"
#include "Xorshift.hpp"
#include <algorithm>
#include <cstddef>
#include <limits>

namespace {

bool is_player_blink(u32 value) {
    return (value & 0b1110) == 0;
}

ReidentifyResult result_after_advances(u64 seed0, u64 seed1, u64 advances) {
    Xorshift rng(seed0, seed1);
    rng.jump(static_cast<u32>(advances));
    const u32* state = rng.data();
    return ReidentifyResult{
        (static_cast<u64>(state[0]) << 32) | state[1],
        (static_cast<u64>(state[2]) << 32) | state[3],
        advances,
    };
}

std::optional<std::vector<u8>> interval_pattern(const std::vector<u32>& raw_intervals, u32 search_max) {
    if (raw_intervals.empty()) {
        return std::nullopt;
    }
    u64 observed_len = 1;
    for (std::size_t index = 1; index < raw_intervals.size(); ++index) {
        observed_len += raw_intervals[index];
        if (observed_len > search_max) {
            return std::nullopt;
        }
    }
    if (observed_len == 0 || observed_len > static_cast<u64>(std::numeric_limits<std::size_t>::max())) {
        return std::nullopt;
    }
    std::vector<u8> pattern(static_cast<std::size_t>(observed_len), 0);
    pattern[0] = 1;
    u64 position = 0;
    for (std::size_t index = 1; index < raw_intervals.size(); ++index) {
        position += raw_intervals[index];
        if (position >= observed_len) {
            return std::nullopt;
        }
        pattern[static_cast<std::size_t>(position)] = 1;
    }
    return pattern;
}

std::vector<u8> noisy_blink_bools(const std::vector<u32>& raw_intervals) {
    std::vector<u8> blink_bools;
    blink_bools.push_back(1);
    for (std::size_t index = 1; index < raw_intervals.size(); ++index) {
        const u32 interval = raw_intervals[index];
        for (u32 skipped = 1; skipped < interval; ++skipped) {
            blink_bools.push_back(0);
        }
        blink_bools.push_back(1);
    }
    return blink_bools;
}

} // namespace

std::optional<ReidentifyResult> reidentify_by_intervals_native(
    u64 seed0,
    u64 seed1,
    const std::vector<u32>& raw_intervals,
    int npc,
    u32 search_min,
    u32 search_max)
{
    if (npc < 0) {
        return std::nullopt;
    }
    if (search_max < search_min) {
        std::swap(search_min, search_max);
    }
    const auto pattern = interval_pattern(raw_intervals, search_max);
    if (!pattern.has_value()) {
        return std::nullopt;
    }
    const std::size_t observed_len = pattern->size();
    const u32 step = static_cast<u32>(npc) + 1;

    for (u32 distance = 0; distance < step; ++distance) {
        Xorshift rng(seed0, seed1);
        std::vector<u8> sampled;
        std::vector<u32> sampled_indices;
        sampled.reserve((search_max + step - 1) / step);
        sampled_indices.reserve((search_max + step - 1) / step);

        for (u32 index = 0; index < search_max; ++index) {
            const u8 blink = is_player_blink(rng.next()) ? 1 : 0;
            if (index >= distance && ((index - distance) % step) == 0) {
                sampled.push_back(blink);
                sampled_indices.push_back(index);
            }
        }
        if (sampled.size() < observed_len) {
            continue;
        }

        const std::size_t last_start = sampled.size() - observed_len;
        for (std::size_t start = 0; start <= last_start; ++start) {
            const u32 last_index = sampled_indices[start + observed_len - 1];
            if (last_index < search_min) {
                continue;
            }
            bool matched = true;
            for (std::size_t offset = 0; offset < observed_len; ++offset) {
                if (sampled[start + offset] != (*pattern)[offset]) {
                    matched = false;
                    break;
                }
            }
            if (matched) {
                return result_after_advances(seed0, seed1, last_index);
            }
        }
    }
    return std::nullopt;
}

std::optional<ReidentifyResult> reidentify_by_intervals_noisy_native(
    u64 seed0,
    u64 seed1,
    const std::vector<u32>& raw_intervals,
    u32 search_min,
    u32 search_max)
{
    const std::vector<u8> blink_bools = noisy_blink_bools(raw_intervals);
    const u64 reident_time = blink_bools.size();
    const u64 possible_length = reident_time * 4 / 3;
    if (possible_length == 0 || search_max <= possible_length) {
        return std::nullopt;
    }

    Xorshift rng(seed0, seed1);
    rng.jump(search_min);
    std::vector<u8> blink_rands;
    blink_rands.reserve(search_max);
    for (u32 index = 0; index < search_max; ++index) {
        blink_rands.push_back(is_player_blink(rng.next()) ? 1 : 0);
    }

    bool found = false;
    u64 best_pokemon_blinks = 0;
    u64 best_advance = 0;
    const u64 max_advance = search_max - possible_length;

    for (u64 advance = 0; advance < max_advance; ++advance) {
        u64 observed_index = 0;
        u64 rand_index = 0;
        u64 pokemon_blinks = 0;
        bool valid = true;

        while (observed_index < reident_time) {
            u64 difference = 0;
            while (true) {
                if (rand_index >= possible_length) {
                    valid = false;
                    break;
                }
                if (blink_bools[static_cast<std::size_t>(observed_index)]
                    == blink_rands[static_cast<std::size_t>(advance + rand_index)]) {
                    break;
                }
                ++difference;
                ++rand_index;
            }
            if (!valid) {
                break;
            }
            pokemon_blinks += difference;
            ++rand_index;
            ++observed_index;
        }

        if (!valid) {
            continue;
        }
        if (!found || pokemon_blinks < best_pokemon_blinks
            || (pokemon_blinks == best_pokemon_blinks && advance < best_advance)) {
            found = true;
            best_pokemon_blinks = pokemon_blinks;
            best_advance = advance;
        }
    }

    if (!found) {
        return std::nullopt;
    }
    const u64 advances = static_cast<u64>(search_min) + best_pokemon_blinks + best_advance + reident_time;
    return result_after_advances(seed0, seed1, advances);
}
