#include "Xoroshiro.hpp"
#include <cstdio>

int main() {
    XoroshiroBDSP rng(0xA1B2C3D4ULL);
    printf("C++ XoroshiroBDSP first 5 next():\n");
    for (int i = 0; i < 5; i++)
        printf("  %016lX\n", rng.next());

    XoroshiroBDSP rng2(0xA1B2C3D4ULL);
    printf("nextUInt(U32_MAX):\n");
    for (int i = 0; i < 5; i++)
        printf("  %08X\n", rng2.nextUInt(0xFFFFFFFF));

    XoroshiroBDSP rng3(0xA1B2C3D4ULL);
    printf("nextUInt(25):\n");
    for (int i = 0; i < 5; i++)
        printf("  %u\n", rng3.nextUInt(25));

    return 0;
}
