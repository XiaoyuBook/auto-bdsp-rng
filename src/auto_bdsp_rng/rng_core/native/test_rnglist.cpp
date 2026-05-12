#include "RNGList.hpp"
#include "Xorshift.hpp"
#include <cstdio>

// 模拟 EC 生成转换
u32 gen_ec(Xorshift& rng) {
    return rng.next(0x80000000, 0x7FFFFFFF) + 0x80000000;
}

int main() {
    Xorshift rng(0x1234567890ABCDEFULL, 0xFEDCBA0987654321ULL);
    RNGList<u32, Xorshift, 32, nullptr> list(rng);

    printf("First 5 values from RNGList:\n");
    for (int i = 0; i < 5; i++)
        printf("  %08X\n", list.next());

    // 验证 advanceState
    list.advanceState();
    printf("After advanceState, next:\n  %08X\n", list.next());

    // 验证 EC 生成
    Xorshift rng2(0x1234567890ABCDEFULL, 0xFEDCBA0987654321ULL);
    RNGList<u32, Xorshift, 32, gen_ec> ec_list(rng2);
    printf("First 3 EC values:\n");
    for (int i = 0; i < 3; i++)
        printf("  %08X\n", ec_list.next());

    return 0;
}
