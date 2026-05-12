#include "generator.hpp"
#include <cstdio>

int main() {
    FilterParams filter; // all pass
    filter.shiny_mask = 255; // no shiny filter

    printf("=== Non-Roamer (species=483, no filter, max_advances=3) ===\n");
    auto states = generate_non_roamer(
        0x1234567890ABCDEFULL, 0xFEDCBA0987654321ULL,
        0, 3, 0,
        255, ShinyTemplate::Random, false,
        0, 255, 255, 2,
        12345, 54321,
        filter);

    for (auto& s : states) {
        printf("adv=%u ec=%08X sidtid=%08X pid=%08X shiny=%u ability=%u gender=%u nature=%u "
               "ivs=[%u,%u,%u,%u,%u,%u] height=%u weight=%u\n",
               s.advances, s.ec, s.sidtid, s.pid, s.shiny, s.ability, s.gender, s.nature,
               s.ivs[0], s.ivs[1], s.ivs[2], s.ivs[3], s.ivs[4], s.ivs[5],
               s.height, s.weight);
    }

    printf("\n=== Roamer (species=488, no filter, max_advances=3) ===\n");
    FilterParams filter2;
    auto roamers = generate_roamer(
        0x1234567890ABCDEFULL, 0xFEDCBA0987654321ULL,
        0, 3, 0,
        255, 12345, 54321, 488,
        filter2);

    for (auto& s : roamers) {
        printf("adv=%u ec=%08X sidtid=%08X pid=%08X shiny=%u ability=%u gender=%u nature=%u "
               "ivs=[%u,%u,%u,%u,%u,%u] height=%u weight=%u\n",
               s.advances, s.ec, s.sidtid, s.pid, s.shiny, s.ability, s.gender, s.nature,
               s.ivs[0], s.ivs[1], s.ivs[2], s.ivs[3], s.ivs[4], s.ivs[5],
               s.height, s.weight);
    }

    return 0;
}
