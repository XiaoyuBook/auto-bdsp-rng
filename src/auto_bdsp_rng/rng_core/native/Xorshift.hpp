#pragma once
#include "types.hpp"

// 与 PokeFinder / ProjectXs 完全一致的 Xorshift 跳表（25 项）
inline constexpr u64 xorshift_jump_table[25][2] = {
    {0x10046D8B3, 0xF985D65FFD3C8001},
    {0x956C89FBFA6B67E9, 0xA42CA9AEB1E10DA6},
    {0xFF7AA97C47EC17C7, 0x1A0988E988F8A56E},
    {0x9DFF33679BD01948, 0xFB6668FF443B16F0},
    {0xBD36A1D3E3B212DA, 0x46A4759B1DC83CE2},
    {0x6D2F354B8B0E3C0B, 0x9640BC4CA0CBAA6C},
    {0xECF6383DCA4F108F, 0x947096C72B4D52FB},
    {0xE1054E817177890A, 0xDAF32F04DDCA12E},
    {0x2AE1912115107C6, 0xB9FA05AAB78641A5},
    {0x59981D3DF81649BE, 0x382FA5AA95F950E3},
    {0x6644B35F0F8CEE00, 0xDBA31D29FC044FDB},
    {0xECFF213C169FD455, 0x3CA16B953C338C19},
    {0xA9DFD9FB0A094939, 0x3FFDCB096A60ECBE},
    {0x79D7462B16C479F, 0xFD6AEF50F8C0B5FA},
    {0x3896736D707B6B6, 0x9148889B8269B55D},
    {0xDEA22E8899DBBEAA, 0x4C6AC659B91EF36A},
    {0xC1150DDD5AE7D320, 0x67CCF586CDDB0649},
    {0x5F0BE91AC7E9C381, 0x33C8177D6B2CC0F0},
    {0xCD15D2BA212E573, 0x4A5F78FC104E47B9},
    {0xAB586674147DEC3E, 0xD69063E6E8A0B936},
    {0x4BFD9D67ED372866, 0x7071114AF22D34F5},
    {0xDAF387CAB4EF5C18, 0x686287302B5CD38C},
    {0xFFAF82745790AF3E, 0xBB7D371F547CCA1E},
    {0x7B932849FE573AFA, 0xEB96ACD6C88829F9},
    {0x8CEDF8DFE2D6E821, 0xB4FD2C6573BF7047},
};

class Xorshift {
public:
    Xorshift(u64 seed0, u64 seed1) {
        // 高 32 位 → state[0], 低 32 位 → state[1], 同理 seed1 → state[2], state[3]
        state[0] = static_cast<u32>(seed0 >> 32);
        state[1] = static_cast<u32>(seed0);
        state[2] = static_cast<u32>(seed1 >> 32);
        state[3] = static_cast<u32>(seed1);
    }

    u32 next() {
        u32 t = state[0];
        u32 s = state[3];
        t ^= t << 11;
        t ^= t >> 8;
        t ^= s ^ (s >> 19);
        state[0] = state[1];
        state[1] = state[2];
        state[2] = state[3];
        state[3] = t;
        return t;
    }

    u32 next(u32 min, u32 max) {
        u32 diff = max - min;
        return (next() % diff) + min;
    }

    void advance(u32 advances) {
        for (u32 i = 0; i < advances; i++) {
            next();
        }
    }

    void jump(u32 advances) {
        advance(advances & 0x7f);
        advances >>= 7;
        for (int i = 0; advances; advances >>= 1, i++) {
            if (advances & 1) {
                u32 jump_state[4] = {0, 0, 0, 0};
                for (int j = 1; j >= 0; j--) {
                    u64 val = xorshift_jump_table[i][j];
                    for (int k = 0; k < 64; k++, val >>= 1) {
                        if (val & 1) {
                            jump_state[0] ^= state[0];
                            jump_state[1] ^= state[1];
                            jump_state[2] ^= state[2];
                            jump_state[3] ^= state[3];
                        }
                        next();
                    }
                }
                state[0] = jump_state[0];
                state[1] = jump_state[1];
                state[2] = jump_state[2];
                state[3] = jump_state[3];
            }
        }
    }

    const u32* data() const { return state; }

private:
    u32 state[4];
};
