"""移動の確率計算。

* PvEの通常移動 = 10面ダイス + 移動力補正(0未満にはならない)。戦闘のダイスは6面なので混同しない
* 「早すぎるおんな」は10面ダイス2個。ルカのスキルは6面ダイス2個
* リモコンダイスは出目を1〜6で固定し、移動力補正を無視する
* 自分のスタートポイント/セーフティポイント、ショップ系のマスは「通過」でも効果があるので
  「N歩以上進める確率」、それ以外のマスは「ちょうどN歩の確率」が効いてくる
"""

from __future__ import annotations

from fractions import Fraction

from .dice import Dist, pct

MOVE_DIE_SIDES = 10


def move_dist(bonus: int = 0, dice: int = 1, sides: int = MOVE_DIE_SIDES) -> Dist:
    """移動歩数の分布。"""
    if dice < 1:
        raise ValueError("dice は1以上")
    total = Dist.const(0)
    for _ in range(dice):
        total = total + Dist.die(sides)
    return (total + bonus).map(lambda v: max(0, v))


def p_land_exact(distance: int, bonus: int = 0, dice: int = 1, sides: int = MOVE_DIE_SIDES) -> Fraction:
    """ちょうど distance 歩で止まれる確率。"""
    return move_dist(bonus, dice, sides).p_eq(distance)


def p_reach(distance: int, bonus: int = 0, dice: int = 1, sides: int = MOVE_DIE_SIDES) -> Fraction:
    """distance 歩以上進める(=そのマスを通過または停止できる)確率。"""
    return move_dist(bonus, dice, sides).p_ge(distance)


def reach_summary(distance: int, bonus: int = 0, dice: int = 1, sides: int = MOVE_DIE_SIDES) -> dict:
    dist = move_dist(bonus, dice, sides)
    return {
        "distance": distance,
        "land_exact_pct": pct(dist.p_eq(distance)),
        "reach_or_pass_pct": pct(dist.p_ge(distance)),
        "expected_move": round(float(dist.mean()), 2),
        "move_table_pct": dist.to_percent_table(),
    }
