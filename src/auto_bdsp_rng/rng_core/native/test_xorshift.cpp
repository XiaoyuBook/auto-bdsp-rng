#include "Xorshift.hpp"
#include <cstdio>

int main() {
    Xorshift rng(0x1234567890ABCDEFULL, 0xFEDCBA0987654321ULL);
    printf("C++ Xorshift first 5 outputs:\n");
    for (int i = 0; i < 5; i++)
        printf("  %08X\n", rng.next());

    Xorshift rng2(0x1234567890ABCDEFULL, 0xFEDCBA0987654321ULL);
    rng2.advance(10);
    printf("After advance(10):\n  %08X\n", rng2.next());

    Xorshift rng3(0x1234567890ABCDEFULL, 0xFEDCBA0987654321ULL);
    rng3.jump(100);
    printf("After jump(100):\n");
    for (int i = 0; i < 3; i++)
        printf("  %08X\n", rng3.next());

    return 0;
}
