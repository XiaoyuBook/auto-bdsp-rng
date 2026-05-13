#pragma once
#include "types.hpp"
#include <type_traits>

// 与 PokeFinder 完全一致的 RNGList 环形缓冲区
// Integer: 存储的数值类型 (u32)
// RNG:     RNG 类 (Xorshift 等)
// Size:    缓冲区大小（必须为 2 的幂）
// Generate:可选的转换函数，用于修改生成的值（如 BDSP 的 EC 转换）
template <typename Integer, class RNG, u16 Size, Integer (*Generate)(RNG&) = nullptr>
class RNGList {
    static_assert(Size && ((Size & (Size - 1)) == 0), "Size must be power of two");

public:
    // 构造：预填充整个缓冲区
    explicit RNGList(RNG& rng) : rng(rng), head(0), pointer(0) {
        for (u16 i = 0; i < Size; i++) {
            if constexpr (Generate != nullptr) {
                list[i] = Generate(rng);
            } else {
                list[i] = rng.next();
            }
        }
    }

    // 生成下一个值写入缓冲区头部，重置指针
    void advanceState() {
        if constexpr (Generate != nullptr) {
            list[head] = Generate(rng);
        } else {
            list[head] = rng.next();
        }
        head++;
        if constexpr (Size != 256) {
            head %= Size;
        }
        pointer = head;
    }

    // 推进读取指针（不生成新值）
    void advance(u32 advances) {
        pointer += advances;
        if constexpr (Size != 256) {
            pointer %= Size;
        }
    }

    // 读取当前指针位置的值并推进
    Integer next() {
        Integer result = list[pointer++];
        if constexpr (Size != 256) {
            pointer %= Size;
        }
        return result;
    }

    // 读取当前值并对 max 取模
    Integer next(u32 max) {
        return next() % max;
    }

    using SizeType = std::conditional_t<Size <= 256, u8, u16>;
    RNG& rng;
    Integer list[Size];
    SizeType head, pointer;
};
