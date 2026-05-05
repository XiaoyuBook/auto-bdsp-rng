from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from auto_bdsp_rng.rng_core.seed import SeedPair64, SeedState32, U32_MAX, U64_MAX


T = TypeVar("T")


def _rotl64(value: int, shift: int) -> int:
    value &= U64_MAX
    return ((value << shift) | (value >> (64 - shift))) & U64_MAX


def _splitmix(seed: int) -> int:
    seed = (0xBF58476D1CE4E5B9 * (seed ^ (seed >> 30))) & U64_MAX
    seed = (0x94D049BB133111EB * (seed ^ (seed >> 27))) & U64_MAX
    return (seed ^ (seed >> 31)) & U64_MAX


XORSHIFT_JUMP_TABLE: tuple[tuple[int, int], ...] = (
    (0x10046D8B3, 0xF985D65FFD3C8001),
    (0x956C89FBFA6B67E9, 0xA42CA9AEB1E10DA6),
    (0xFF7AA97C47EC17C7, 0x1A0988E988F8A56E),
    (0x9DFF33679BD01948, 0xFB6668FF443B16F0),
    (0xBD36A1D3E3B212DA, 0x46A4759B1DC83CE2),
    (0x6D2F354B8B0E3C0B, 0x9640BC4CA0CBAA6C),
    (0xECF6383DCA4F108F, 0x947096C72B4D52FB),
    (0xE1054E817177890A, 0xDAF32F04DDCA12E),
    (0x2AE1912115107C6, 0xB9FA05AAB78641A5),
    (0x59981D3DF81649BE, 0x382FA5AA95F950E3),
    (0x6644B35F0F8CEE00, 0xDBA31D29FC044FDB),
    (0xECFF213C169FD455, 0x3CA16B953C338C19),
    (0xA9DFD9FB0A094939, 0x3FFDCB096A60ECBE),
    (0x79D7462B16C479F, 0xFD6AEF50F8C0B5FA),
    (0x3896736D707B6B6, 0x9148889B8269B55D),
    (0xDEA22E8899DBBEAA, 0x4C6AC659B91EF36A),
    (0xC1150DDD5AE7D320, 0x67CCF586CDDB0649),
    (0x5F0BE91AC7E9C381, 0x33C8177D6B2CC0F0),
    (0xCD15D2BA212E573, 0x4A5F78FC104E47B9),
    (0xAB586674147DEC3E, 0xD69063E6E8A0B936),
    (0x4BFD9D67ED372866, 0x7071114AF22D34F5),
    (0xDAF387CAB4EF5C18, 0x686287302B5CD38C),
    (0xFFAF82745790AF3E, 0xBB7D371F547CCA1E),
    (0x7B932849FE573AFA, 0xEB96ACD6C88829F9),
    (0x8CEDF8DFE2D6E821, 0xB4FD2C6573BF7047),
)


XOROSHIRO_JUMP_TABLE: tuple[tuple[int, int], ...] = (
    (0x8828E513B43D5, 0x95B8F76579AA001),
    (0x7A8FF5B1C465A931, 0x162AD6EC01B26EAE),
    (0xB18B0D36CD81A8F5, 0xB4FBAA5C54EE8B8F),
    (0x23AC5E0BA1CECB29, 0x1207A1706BEBB202),
    (0xBB18E9C8D463BB1B, 0x2C88EF71166BC53D),
    (0xE3FBE606EF4E8E09, 0xC3865BB154E9BE10),
    (0x28FAAAEBB31EE2DB, 0x1A9FC99FA7818274),
    (0x30A7C4EEF203C7EB, 0x588ABD4C2CE2BA80),
    (0xA425003F3220A91D, 0x9C90DEBC053E8CEF),
    (0x81E1DD96586CF985, 0xB82CA99A09A4E71E),
    (0x4F7FD3DFBB820BFB, 0x35D69E118698A31D),
    (0xFEE2760EF3A900B3, 0x49613606C466EFD3),
    (0xF0DF0531F434C57D, 0xBD031D011900A9E5),
    (0x442576715266740C, 0x235E761B3B378590),
    (0x1E8BAE8F680D2B35, 0x3710A7AE7945DF77),
    (0xFD7027FE6D2F6764, 0x75D8E7DBCEDA609C),
    (0x28EFF231AD438124, 0xDE2CBA60CD3332B5),
    (0x1808760D0A0909A1, 0x377E64C4E80A06FA),
    (0xB9A362FAFEDFE9D2, 0xCF0A2225DA7FB95),
    (0xF57881AB117349FD, 0x2BAB58A3CADFC0A3),
    (0x849272241425C996, 0x8D51ECDB9ED82455),
    (0xF1CCB8898CBC07CD, 0x521B29D0A57326C1),
    (0x61179E44214CAAFA, 0xFBE65017ABEC72DD),
    (0xD9AA6B1E93FBB6E4, 0x6C446B9BC95C267B),
    (0x86E3772194563F6D, 0x64F80248D23655C6),
)


@dataclass
class BDSPXorshift:
    """BDSP four-word Xorshift used by Project_Xs and PokeFinder."""

    state: SeedState32

    @classmethod
    def from_seed_pair64(cls, seed_pair: SeedPair64) -> "BDSPXorshift":
        return cls(seed_pair.to_state32())

    @classmethod
    def from_pokefinder_seed(cls, seed: int) -> "BDSPXorshift":
        seed &= U64_MAX
        return cls.from_seed_pair64(SeedPair64(seed, seed ^ (seed >> 32)))

    @property
    def words(self) -> tuple[int, int, int, int]:
        return self.state.words

    def next(self) -> int:
        s0, s1, s2, s3 = self.state.words
        value = (s0 ^ ((s0 << 11) & U32_MAX)) & U32_MAX
        value = (value ^ (value >> 8)) & U32_MAX
        value = (value ^ s3 ^ (s3 >> 19)) & U32_MAX
        self.state = SeedState32(s1, s2, s3, value)
        return value

    def advance(self, advances: int) -> None:
        if advances < 0:
            raise ValueError("advances must be non-negative")
        for _ in range(advances):
            self.next()

    def jump(self, advances: int) -> None:
        if advances < 0:
            raise ValueError("advances must be non-negative")
        self.advance(advances & 0x7F)
        advances >>= 7
        table_index = 0
        while advances:
            if table_index >= len(XORSHIFT_JUMP_TABLE):
                raise ValueError("advances exceeds supported jump table range")
            if advances & 1:
                jumped = [0, 0, 0, 0]
                for word64 in reversed(XORSHIFT_JUMP_TABLE[table_index]):
                    value = word64
                    for _ in range(64):
                        if value & 1:
                            jumped = [
                                current ^ state_word
                                for current, state_word in zip(jumped, self.state.words)
                            ]
                        self.next()
                        value >>= 1
                self.state = SeedState32.from_words(jumped)
            advances >>= 1
            table_index += 1

    def next_range(self, minimum: int, maximum: int) -> int:
        diff = maximum - minimum
        if diff <= 0:
            raise ValueError("maximum must be greater than minimum")
        return (self.next() % diff) + minimum


@dataclass
class Xoroshiro:
    """PokeFinder-compatible xoroshiro128+ implementation."""

    seed0: int
    seed1: int = 0x82A2B175229D6A5B

    def __post_init__(self) -> None:
        if not 0 <= self.seed0 <= U64_MAX:
            raise ValueError("seed0 must be a 64-bit unsigned integer")
        if not 0 <= self.seed1 <= U64_MAX:
            raise ValueError("seed1 must be a 64-bit unsigned integer")

    @property
    def state(self) -> tuple[int, int]:
        return (self.seed0, self.seed1)

    def next(self) -> int:
        s0, s1 = self.seed0, self.seed1
        result = (s0 + s1) & U64_MAX
        s1 ^= s0
        self.seed0 = (_rotl64(s0, 24) ^ s1 ^ ((s1 << 16) & U64_MAX)) & U64_MAX
        self.seed1 = _rotl64(s1, 37)
        return result

    def advance(self, advances: int) -> None:
        if advances < 0:
            raise ValueError("advances must be non-negative")
        for _ in range(advances):
            self.next()

    def jump(self, advances: int) -> None:
        if advances < 0:
            raise ValueError("advances must be non-negative")
        self.advance(advances & 0x7F)
        advances >>= 7
        table_index = 0
        while advances:
            if table_index >= len(XOROSHIRO_JUMP_TABLE):
                raise ValueError("advances exceeds supported jump table range")
            if advances & 1:
                jumped0 = 0
                jumped1 = 0
                for word64 in reversed(XOROSHIRO_JUMP_TABLE[table_index]):
                    value = word64
                    for _ in range(64):
                        if value & 1:
                            jumped0 ^= self.seed0
                            jumped1 ^= self.seed1
                        self.next()
                        value >>= 1
                self.seed0 = jumped0 & U64_MAX
                self.seed1 = jumped1 & U64_MAX
            advances >>= 1
            table_index += 1

    def next_uint(self, maximum: int) -> int:
        if maximum <= 0:
            raise ValueError("maximum must be positive")
        mask = (maximum - 1).bit_length()
        mask = (1 << mask) - 1
        while True:
            result = self.next() & mask
            if result < maximum:
                return result


class XoroshiroBDSP(Xoroshiro):
    """BDSP xoroshiro variant seeded through PokeFinder's splitmix sequence."""

    def __init__(self, seed: int):
        if not 0 <= seed <= U64_MAX:
            raise ValueError("seed must be a 64-bit unsigned integer")
        super().__init__(
            _splitmix((seed + 0x9E3779B97F4A7C15) & U64_MAX),
            _splitmix((seed + 0x3C6EF372FE94F82A) & U64_MAX),
        )

    def next_uint(self, maximum: int) -> int:
        if maximum <= 0:
            raise ValueError("maximum must be positive")
        return ((self.next() >> 32) & U32_MAX) % maximum


class RNGList(Generic[T]):
    """Reusable ring buffer for RNG outputs, matching PokeFinder's RNGList behavior."""

    def __init__(
        self,
        rng: object,
        *,
        size: int,
        generate: Callable[[object], T] | None = None,
    ) -> None:
        if size <= 0 or size & (size - 1):
            raise ValueError("size must be a positive power of two")
        self._rng = rng
        self._size = size
        self._generate = generate
        self._list = [self._generate_next() for _ in range(size)]
        self._head = 0
        self._pointer = 0

    @property
    def buffer(self) -> tuple[T, ...]:
        return tuple(self._list)

    @property
    def pointer(self) -> int:
        return self._pointer

    @property
    def head(self) -> int:
        return self._head

    def _wrap(self, value: int) -> int:
        return value % self._size

    def _generate_next(self) -> T:
        if self._generate is not None:
            return self._generate(self._rng)
        return self._rng.next()  # type: ignore[no-any-return, attr-defined]

    def advance_state(self) -> None:
        self._list[self._head] = self._generate_next()
        self._head = self._wrap(self._head + 1)
        self._pointer = self._head

    def advance_states(self, advances: int) -> None:
        if advances < 0:
            raise ValueError("advances must be non-negative")
        for _ in range(advances):
            self.advance_state()

    def advance(self, advances: int) -> None:
        if advances < 0:
            raise ValueError("advances must be non-negative")
        self._pointer = self._wrap(self._pointer + advances)

    def next(self) -> T:
        result = self._list[self._pointer]
        self._pointer = self._wrap(self._pointer + 1)
        return result

    def next_mod(self, maximum: int) -> int:
        if maximum <= 0:
            raise ValueError("maximum must be positive")
        return int(self.next()) % maximum

    def reset_state(self) -> None:
        self._pointer = self._head
