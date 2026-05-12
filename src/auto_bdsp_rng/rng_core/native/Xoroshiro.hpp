#pragma once
#include "types.hpp"

inline u64 rotl64(u64 x, int k) {
    return (x << k) | (x >> (64 - k));
}

// 与 PokeFinder 完全一致的 Xoroshiro 跳表（25 项）
inline constexpr u64 xoroshiro_jump_table[25][2] = {
    {0x8828E513B43D5, 0x95B8F76579AA001},
    {0x7A8FF5B1C465A931, 0x162AD6EC01B26EAE},
    {0xB18B0D36CD81A8F5, 0xB4FBAA5C54EE8B8F},
    {0x23AC5E0BA1CECB29, 0x1207A1706BEBB202},
    {0xBB18E9C8D463BB1B, 0x2C88EF71166BC53D},
    {0xE3FBE606EF4E8E09, 0xC3865BB154E9BE10},
    {0x28FAAAEBB31EE2DB, 0x1A9FC99FA7818274},
    {0x30A7C4EEF203C7EB, 0x588ABD4C2CE2BA80},
    {0xA425003F3220A91D, 0x9C90DEBC053E8CEF},
    {0x81E1DD96586CF985, 0xB82CA99A09A4E71E},
    {0x4F7FD3DFBB820BFB, 0x35D69E118698A31D},
    {0xFEE2760EF3A900B3, 0x49613606C466EFD3},
    {0xF0DF0531F434C57D, 0xBD031D011900A9E5},
    {0x442576715266740C, 0x235E761B3B378590},
    {0x1E8BAE8F680D2B35, 0x3710A7AE7945DF77},
    {0xFD7027FE6D2F6764, 0x75D8E7DBCEDA609C},
    {0x28EFF231AD438124, 0xDE2CBA60CD3332B5},
    {0x1808760D0A0909A1, 0x377E64C4E80A06FA},
    {0xB9A362FAFEDFE9D2, 0xCF0A2225DA7FB95},
    {0xF57881AB117349FD, 0x2BAB58A3CADFC0A3},
    {0x849272241425C996, 0x8D51ECDB9ED82455},
    {0xF1CCB8898CBC07CD, 0x521B29D0A57326C1},
    {0x61179E44214CAAFA, 0xFBE65017ABEC72DD},
    {0xD9AA6B1E93FBB6E4, 0x6C446B9BC95C267B},
    {0x86E3772194563F6D, 0x64F80248D23655C6},
};

// 与 PokeFinder 一致的 splitmix64
inline u64 splitmix64(u64 seed) {
    seed = 0xBF58476D1CE4E5B9ULL * (seed ^ (seed >> 30));
    seed = 0x94D049BB133111EBULL * (seed ^ (seed >> 27));
    return seed ^ (seed >> 31);
}

class Xoroshiro {
public:
    Xoroshiro(u64 seed0, u64 seed1) : s0(seed0), s1(seed1) {}

    u64 next() {
        u64 result = s0 + s1;
        s1 ^= s0;
        s0 = rotl64(s0, 24) ^ s1 ^ (s1 << 16);
        s1 = rotl64(s1, 37);
        return result;
    }

    void advance(u32 advances) {
        for (u32 i = 0; i < advances; i++) next();
    }

protected:
    u64 s0, s1;
};

// BDSP 游走精灵专用：seed 经 splitmix 初始化
class XoroshiroBDSP : public Xoroshiro {
public:
    explicit XoroshiroBDSP(u64 seed)
        : Xoroshiro(
              splitmix64(seed + 0x9E3779B97F4A7C15ULL),
              splitmix64(seed + 0x3C6EF372FE94F82AULL)) {}

    // (next() >> 32) % max —— 与 PokeFinder 一致
    u32 nextUInt(u32 max) {
        return static_cast<u32>(next() >> 32) % max;
    }
};
