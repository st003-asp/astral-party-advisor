"""チップ抽選・スターレベル・コインまわりの数値。

確率表の出典: 日本語wiki「チップ/一覧」、英語wiki "Chips"。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Literal

from .dice import pct

Rarity = Literal["blue", "purple", "gold"]
Difficulty = Literal["普通", "困難", "悪夢", "狂気", "極限"]

# スターレベル → (青, 紫, 金) の出現率(%)
_RARITY_NORMAL = {0: (60, 30, 10), 1: (60, 30, 10), 2: (40, 40, 20), 3: (20, 50, 30)}
_RARITY_HARD = {0: (60, 37, 3), 1: (60, 37, 3), 2: (40, 45, 15), 3: (25, 50, 25)}

# レベルアップ/チップショップの「一番左の枠」はレベルでレアリティがほぼ決まる
_LEFT_SLOT_RARITY: dict[int, Rarity] = {0: "blue", 1: "blue", 2: "purple", 3: "gold"}

LEVEL_UP_COST = (15, 20, 25)  # Lv0→1, 1→2, 2→3
CHIP_SHOP_BASE_PRICE = 10
CHIP_SHOP_PRICE_STEP = 5
TRANSFER_AMOUNT = 5
HAND_LIMIT = 8


def rarity_rates(star_level: int, difficulty: Difficulty = "悪夢") -> dict[Rarity, Fraction]:
    table = _RARITY_NORMAL if difficulty == "普通" else _RARITY_HARD
    blue, purple, gold = table[max(0, min(star_level, 3))]
    return {"blue": Fraction(blue, 100), "purple": Fraction(purple, 100), "gold": Fraction(gold, 100)}


def p_rarity_in_offer(
    rarity: Rarity,
    star_level: int,
    difficulty: Difficulty = "悪夢",
    *,
    slots: int = 3,
    left_slot_fixed: bool = True,
) -> Fraction:
    """3択の中に指定レアリティが1枚以上出る確率。

    left_slot_fixed=True はレベルアップ/チップショップ(左枠がクラス抽選)、
    False はミッション報酬など全枠が通常抽選の場合。
    """
    p = rarity_rates(star_level, difficulty)[rarity]
    random_slots = slots - 1 if left_slot_fixed else slots
    p_none = (1 - p) ** random_slots
    if left_slot_fixed and _LEFT_SLOT_RARITY[max(0, min(star_level, 3))] == rarity:
        return Fraction(1)
    return 1 - p_none


def p_specific_chip(
    rarity: Rarity,
    pool_size: int,
    star_level: int,
    difficulty: Difficulty = "悪夢",
    *,
    random_slots: int = 2,
) -> Fraction:
    """特定の1枚が通常抽選枠のどこかに出る確率の近似。

    pool_size: 同じレアリティで現在出現しうるチップの枚数(属性固定や取得済みで減る)。
    通常抽選枠は「レアリティを引く → その中から等確率」と仮定している。
    同種スタックを集めたときの偏りは反映していないので、実際はこれより高くなりうる。
    """
    if pool_size < 1:
        raise ValueError("pool_size は1以上")
    p_slot = rarity_rates(star_level, difficulty)[rarity] / pool_size
    return 1 - (1 - p_slot) ** random_slots


def chip_shop_price(purchases_so_far: int) -> int:
    return CHIP_SHOP_BASE_PRICE + CHIP_SHOP_PRICE_STEP * purchases_so_far


def level_up_cost(star_level: int) -> int | None:
    """次のレベルアップに必要なコイン。最大レベルなら None。"""
    return LEVEL_UP_COST[star_level] if 0 <= star_level < 3 else None


def round_bonus(difficulty: Difficulty = "悪夢", starlight: int = 0) -> int:
    """3の倍数ラウンドでもらえるコイン。"""
    base = 6 if difficulty in ("普通", "困難") else 5
    return base + starlight


def rounds_until_bonus(current_round: int) -> int:
    """次のラウンドボーナスまでのラウンド数(今が3の倍数なら0)。"""
    return (-current_round) % 3


def chip_offer_summary(star_level: int, difficulty: Difficulty = "悪夢", purchases_so_far: int = 0) -> dict:
    rates = rarity_rates(star_level, difficulty)
    return {
        "star_level": star_level,
        "difficulty": difficulty,
        "per_slot_pct": {k: pct(v) for k, v in rates.items()},
        "left_slot_rarity": _LEFT_SLOT_RARITY[max(0, min(star_level, 3))],
        "at_least_one_pct_levelup_or_shop": {
            r: pct(p_rarity_in_offer(r, star_level, difficulty)) for r in ("blue", "purple", "gold")
        },
        "at_least_one_pct_mission": {
            r: pct(p_rarity_in_offer(r, star_level, difficulty, left_slot_fixed=False))
            for r in ("blue", "purple", "gold")
        },
        "next_chip_shop_price": chip_shop_price(purchases_so_far),
        "next_level_up_cost": level_up_cost(star_level),
    }
