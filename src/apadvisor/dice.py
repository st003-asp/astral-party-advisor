"""離散確率分布の厳密計算。

サイコロやバトルカードの乱数を `fractions.Fraction` で扱うので、
浮動小数の誤差なしに「撃破率 7/12」のような値を出せる。
"""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from typing import Callable, Iterable, Mapping


class Dist:
    """整数値をとる離散確率分布。"""

    __slots__ = ("pmf",)

    def __init__(self, pmf: Mapping[int, Fraction | int]):
        cleaned = {int(k): Fraction(v) for k, v in pmf.items() if v}
        total = sum(cleaned.values())
        if total != 1:
            raise ValueError(f"確率の合計が1ではありません: {total}")
        self.pmf: dict[int, Fraction] = dict(sorted(cleaned.items()))

    # ---- 生成 ----
    @staticmethod
    def const(value: int) -> "Dist":
        return Dist({value: 1})

    @staticmethod
    def uniform(lo: int, hi: int) -> "Dist":
        """lo〜hi(両端含む)の一様分布。「ATK +1~3」のカードは uniform(1, 3)。"""
        if hi < lo:
            raise ValueError("hi は lo 以上である必要があります")
        n = hi - lo + 1
        return Dist({v: Fraction(1, n) for v in range(lo, hi + 1)})

    @staticmethod
    def die(sides: int = 6) -> "Dist":
        return Dist.uniform(1, sides)

    @staticmethod
    def weighted(items: Iterable[tuple[int, Fraction | int]]) -> "Dist":
        acc: dict[int, Fraction] = defaultdict(Fraction)
        for value, p in items:
            acc[value] += Fraction(p)
        return Dist(acc)

    # ---- 演算 ----
    def combine(self, other: "Dist | int", fn: Callable[[int, int], int]) -> "Dist":
        """独立な2分布に2項演算を適用した分布。"""
        other = other if isinstance(other, Dist) else Dist.const(other)
        acc: dict[int, Fraction] = defaultdict(Fraction)
        for a, pa in self.pmf.items():
            for b, pb in other.pmf.items():
                acc[fn(a, b)] += pa * pb
        return Dist(acc)

    def map(self, fn: Callable[[int], int]) -> "Dist":
        acc: dict[int, Fraction] = defaultdict(Fraction)
        for a, pa in self.pmf.items():
            acc[fn(a)] += pa
        return Dist(acc)

    def __add__(self, other: "Dist | int") -> "Dist":
        return self.combine(other, lambda a, b: a + b)

    __radd__ = __add__

    def __sub__(self, other: "Dist | int") -> "Dist":
        return self.combine(other, lambda a, b: a - b)

    @staticmethod
    def mix(parts: Iterable[tuple[Fraction | int, "Dist"]]) -> "Dist":
        """重み付き混合分布(場合分けの合成)。"""
        acc: dict[int, Fraction] = defaultdict(Fraction)
        for weight, dist in parts:
            for v, p in dist.pmf.items():
                acc[v] += Fraction(weight) * p
        return Dist(acc)

    # ---- 集計 ----
    def prob(self, pred: Callable[[int], bool]) -> Fraction:
        return sum((p for v, p in self.pmf.items() if pred(v)), Fraction(0))

    def p_ge(self, k: int) -> Fraction:
        return self.prob(lambda v: v >= k)

    def p_le(self, k: int) -> Fraction:
        return self.prob(lambda v: v <= k)

    def p_eq(self, k: int) -> Fraction:
        return self.pmf.get(k, Fraction(0))

    def mean(self) -> Fraction:
        return sum((v * p for v, p in self.pmf.items()), Fraction(0))

    def min(self) -> int:
        return next(iter(self.pmf))

    def max(self) -> int:
        return next(reversed(self.pmf))

    def to_percent_table(self) -> dict[int, float]:
        """表示用。値→確率(%)。"""
        return {v: round(float(p) * 100, 2) for v, p in self.pmf.items()}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Dist) and self.pmf == other.pmf

    def __repr__(self) -> str:
        body = ", ".join(f"{v}: {p}" for v, p in self.pmf.items())
        return f"Dist({{{body}}})"


D6 = Dist.die(6)


def pct(p: Fraction | float) -> float:
    """確率を小数第1位までの%にする。"""
    return round(float(p) * 100, 1)
