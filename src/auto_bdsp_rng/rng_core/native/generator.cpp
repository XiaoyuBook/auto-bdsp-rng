#include "generator.hpp"
#include "Xorshift.hpp"
#include "Xoroshiro.hpp"
#include "RNGList.hpp"

namespace {

constexpr u32 U32_MAX = 0xFFFFFFFF;
constexpr u8 GENDER_MALE = 0;
constexpr u8 GENDER_FEMALE = 1;
constexpr u8 GENDER_GENDERLESS = 2;

// PokeFinder 的 EC 生成函数: rng.next(0x80000000, 0x7FFFFFFF)
u32 gen_ec(Xorshift& rng) {
    return rng.next(0x80000000, 0x7FFFFFFF);
}

u8 get_shiny(u32 pid, u16 tsv) {
    u16 psv = ((pid >> 16) ^ (pid & 0xFFFF)) & 0xFFFF;
    if (tsv == psv) return 2;       // Square
    if ((tsv ^ psv) < 16) return 1; // Star
    return 0;
}

bool is_shiny(u32 pid, u16 tsv) {
    u16 psv = ((pid >> 16) ^ (pid & 0xFFFF)) & 0xFFFF;
    return (tsv == psv) || ((tsv ^ psv) < 16);
}

u32 force_shiny(u32 pid, u16 tsv, u8 target_shiny) {
    if (get_shiny(pid, tsv) == target_shiny) return pid & U32_MAX;
    u16 high = (pid >> 16) & 0xFFFF;
    u16 low = pid & 0xFFFF;
    high = (high ^ tsv ^ (2 - target_shiny)) & 0xFFFF;
    return ((high << 16) | low) & U32_MAX;
}

u32 force_non_shiny(u32 pid, u16 tsv) {
    if (is_shiny(pid, tsv)) {
        return (pid ^ 0x10000000) & U32_MAX;
    }
    return pid & U32_MAX;
}

bool is_synchronize(int lead) { return 0 <= lead && lead <= 24; }
bool is_cute_charm(int lead) { return lead == 25 || lead == 26; }

u8 hidden_power(const u8 ivs[6]) {
    u8 bits = 0;
    for (int i = 0; i < 6; i++) bits |= (ivs[i] & 1) << i;
    return (bits * 15) / 63;
}

bool pass_filter(const StateResult& r, const FilterParams& f) {
    if (f.skip) return true;
    if (f.ability != 255 && f.ability != r.ability) return false;
    if (f.gender != 255 && f.gender != r.gender) return false;
    if (f.shiny_mask != 255 && !(f.shiny_mask & r.shiny)) return false;
    if (!f.natures[r.nature]) return false;
    if (!f.hidden_powers[hidden_power(r.ivs)]) return false;
    if (r.height < f.height_min || r.height > f.height_max) return false;
    if (r.weight < f.weight_min || r.weight > f.weight_max) return false;
    for (int i = 0; i < 6; i++) {
        if (r.ivs[i] < f.iv_min[i] || r.ivs[i] > f.iv_max[i]) return false;
    }
    return true;
}

// 第2关快速拒绝（不含 IV/hidden power，在计算 IV 前调用）
bool quick_reject_shiny_a_g_n_h_w(u8 shiny, u8 ability, u8 gender, u8 nature,
                                   u8 height, u8 weight, const FilterParams& f) {
    if (f.skip) return false;
    if (f.ability != 255 && f.ability != ability) return true;
    if (f.gender != 255 && f.gender != gender) return true;
    if (f.shiny_mask != 255 && !(f.shiny_mask & shiny)) return true;
    if (!f.natures[nature]) return true;
    if (height < f.height_min || height > f.height_max) return true;
    if (weight < f.weight_min || weight > f.weight_max) return true;
    return false;
}

} // namespace

std::vector<StateResult> generate_non_roamer(
    u64 seed0, u64 seed1,
    u32 initial_advances, u32 max_advances, u32 offset,
    int lead, ShinyTemplate shiny_template, bool fateful,
    u8 iv_count, u8 ability_template, u8 gender_ratio, u8 ability_count,
    u16 tid, u16 sid,
    const FilterParams& filter)
{
    u16 tsv = tid ^ sid;
    bool need_shiny = filter.shiny_mask != 255;

    Xorshift rng(seed0, seed1);
    rng.jump(initial_advances + offset);
    RNGList<u32, Xorshift, 32, gen_ec> rng_list(rng);

    std::vector<StateResult> results;
    for (u32 cnt = 0; cnt <= max_advances; cnt++) {
        u32 ec = rng_list.next();
        u32 sidtid = rng_list.next();
        u32 pid = rng_list.next();

        u8 shiny;
        if (shiny_template == ShinyTemplate::Never) {
            shiny = 0;
            pid = force_non_shiny(pid, tsv);
        } else {
            u16 fake_tsv = ((sidtid >> 16) ^ (sidtid & 0xFFFF)) & 0xFFFF;
            shiny = get_shiny(pid, fake_tsv);
            if (shiny) {
                if (fateful) shiny = 2;
                pid = force_shiny(pid, tsv, shiny);
            } else {
                pid = force_non_shiny(pid, tsv);
            }
        }

        // 第1关：shiny 提前拒绝
        if (need_shiny && !(filter.shiny_mask & shiny)) {
            rng_list.advanceState();
            continue;
        }

        // IV 生成
        u8 ivs[6] = {255, 255, 255, 255, 255, 255};
        for (u8 i = 0; i < iv_count;) {
            u8 idx = rng_list.next(6);
            if (ivs[idx] == 255) { ivs[idx] = 31; i++; }
        }
        for (u8& iv : ivs) {
            if (iv == 255) iv = rng_list.next(32);
        }

        // Ability
        u8 ability;
        switch (ability_template) {
            case 0: case 1: ability = ability_template; break;
            case 2: rng_list.next(); ability = 2; break;
            default: ability = rng_list.next(ability_count > 2 ? 2 : ability_count); break;
        }

        // Gender
        u8 gender;
        switch (gender_ratio) {
            case 255: gender = GENDER_GENDERLESS; break;
            case 254: gender = GENDER_FEMALE; break;
            case 0:   gender = GENDER_MALE; break;
            default:
                if (is_cute_charm(lead) && rng_list.next(3) > 0) {
                    gender = (lead == 25) ? GENDER_MALE : GENDER_FEMALE;
                } else {
                    gender = (rng_list.next(253) + 1) < gender_ratio ? GENDER_FEMALE : GENDER_MALE;
                }
                break;
        }

        // Nature
        u8 nature;
        if (is_synchronize(lead)) {
            nature = static_cast<u8>(lead);
        } else {
            nature = rng_list.next(25);
        }

        // Height / Weight
        u8 height = rng_list.next(129) + rng_list.next(128);
        u8 weight = rng_list.next(129) + rng_list.next(128);

        // 第2关：ability/gender/nature/height/weight 提前拒绝
        if (quick_reject_shiny_a_g_n_h_w(shiny, ability, gender, nature, height, weight, filter)) {
            rng_list.advanceState();
            continue;
        }

        // 第3关：IV + hidden power（需要先获得完整 ivs）
        if (!filter.skip) {
            bool iv_pass = true;
            for (int i = 0; i < 6; i++) {
                if (ivs[i] < filter.iv_min[i] || ivs[i] > filter.iv_max[i]) {
                    iv_pass = false; break;
                }
            }
            if (!iv_pass || !filter.hidden_powers[hidden_power(ivs)]) {
                rng_list.advanceState();
                continue;
            }
        }

        StateResult r{};
        r.advances = initial_advances + cnt;
        r.ec = ec; r.sidtid = sidtid; r.pid = pid;
        for (int i = 0; i < 6; i++) r.ivs[i] = ivs[i];
        r.ability = ability; r.gender = gender; r.level = 1;
        r.nature = nature; r.shiny = shiny;
        r.height = height; r.weight = weight;
        results.push_back(r);

        rng_list.advanceState();
    }
    return results;
}

std::vector<StateResult> generate_roamer(
    u64 seed0, u64 seed1,
    u32 initial_advances, u32 max_advances, u32 offset,
    int lead, u16 tid, u16 sid, u16 species,
    const FilterParams& filter)
{
    u16 tsv = tid ^ sid;
    bool need_shiny = filter.shiny_mask != 255;
    u8 gender = (species == 488) ? GENDER_FEMALE : GENDER_GENDERLESS;

    Xorshift roamer(seed0, seed1);
    roamer.jump(initial_advances + offset);

    std::vector<StateResult> results;
    for (u32 cnt = 0; cnt <= max_advances; cnt++) {
        u32 ec = gen_ec(roamer);
        XoroshiroBDSP rng(ec);

        u32 sidtid = rng.nextUInt(U32_MAX);
        u32 pid = rng.nextUInt(U32_MAX);

        u16 fake_tsv = ((sidtid >> 16) ^ (sidtid & 0xFFFF)) & 0xFFFF;
        u8 shiny = get_shiny(pid, fake_tsv);
        if (shiny) {
            pid = force_shiny(pid, tsv, shiny);
        } else {
            pid = force_non_shiny(pid, tsv);
        }

        if (need_shiny && !(filter.shiny_mask & shiny)) continue;

        // 3 fixed 31 IVs
        u8 ivs[6] = {255, 255, 255, 255, 255, 255};
        for (int i = 0; i < 3;) {
            u8 idx = rng.nextUInt(6);
            if (ivs[idx] == 255) { ivs[idx] = 31; i++; }
        }
        for (u8& iv : ivs) {
            if (iv == 255) iv = rng.nextUInt(32);
        }

        u8 ability = rng.nextUInt(2);

        u8 nature;
        if (is_synchronize(lead)) {
            nature = static_cast<u8>(lead);
        } else {
            nature = rng.nextUInt(25);
        }

        u8 height = rng.nextUInt(129) + rng.nextUInt(128);
        u8 weight = rng.nextUInt(129) + rng.nextUInt(128);

        if (quick_reject_shiny_a_g_n_h_w(shiny, ability, gender, nature, height, weight, filter)) continue;
        if (!filter.skip) {
            bool iv_pass = true;
            for (int i = 0; i < 6; i++) {
                if (ivs[i] < filter.iv_min[i] || ivs[i] > filter.iv_max[i]) {
                    iv_pass = false; break;
                }
            }
            if (!iv_pass || !filter.hidden_powers[hidden_power(ivs)]) continue;
        }

        StateResult r{};
        r.advances = initial_advances + cnt;
        r.ec = ec; r.sidtid = sidtid; r.pid = pid;
        for (int i = 0; i < 6; i++) r.ivs[i] = ivs[i];
        r.ability = ability; r.gender = gender; r.level = 1;
        r.nature = nature; r.shiny = shiny;
        r.height = height; r.weight = weight;
        results.push_back(r);
    }
    return results;
}
