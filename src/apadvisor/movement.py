"""移動の確率計算。

* PvEの通常移動 = 10面ダイス + 移動力補正(0未満にはならない)。戦闘のダイスは6面なので混同しない
* 「早すぎるおんな」は10面ダイス2個。ルカのスキルは6面ダイス2個
* リモコンダイスは出目を1〜6で固定し、移動力補正を無視する
* 自分のスタートポイント/セーフティポイント、ショップ系のマスは「通過」でも効果があるので
  「N歩以上進める確率」、それ以外のマスは「ちょうどN歩の確率」が効いてくる
"""

from __future__ import annotations

from dataclasses import dataclass
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


# ---------------------------------------------------------------------------
# 分岐のあるボード
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    id: str
    kind: str
    next: tuple[str, ...]


class Board:
    """マスの有向グラフ。`data/maps/*.json` から読み込む想定。

    JSON形式: {"tiles": {"a1": {"kind": "shop", "next": ["a2", "b1"]}, ...}}
    """

    def __init__(self, tiles: dict[str, Tile]):
        self.tiles = tiles

    @staticmethod
    def from_dict(data: dict) -> "Board":
        tiles = {
            tid: Tile(tid, t.get("kind", "normal"), tuple(t.get("next", ())))
            for tid, t in data["tiles"].items()
        }
        for tile in tiles.values():
            for nxt in tile.next:
                if nxt not in tiles:
                    raise ValueError(f"{tile.id} の next に未定義のマス {nxt} があります")
        return Board(tiles)

    def paths(self, start: str, steps: int) -> list[tuple[str, ...]]:
        """start から steps 歩進むすべての経路(start 自身は含まない)。"""
        if steps == 0:
            return [()]
        out: list[tuple[str, ...]] = []
        for nxt in self.tiles[start].next:
            out.extend((nxt, *rest) for rest in self.paths(nxt, steps - 1))
        return out

    def branch_table(self, start: str, dist: Dist) -> dict[str, dict]:
        """最初の分岐の選び方ごとに「止まるマスの種類」と「通過するマスの種類」の確率を出す。

        最初の分岐より後の分岐は、各選択肢を等確率で選ぶものとして平均する
        (2つ目以降の分岐まで含めて最適化したい場合は paths() を直接使う)。
        """
        first_choices = self._first_branch(start)
        table: dict[str, dict] = {}
        for choice in first_choices:
            land: dict[str, Fraction] = {}
            passed: dict[str, Fraction] = {}
            for steps, p in dist.pmf.items():
                paths = [pt for pt in self.paths(start, steps) if self._goes_through(start, pt, choice)]
                if not paths:
                    continue
                share = p / len(paths)
                for pt in paths:
                    if pt:
                        kind = self.tiles[pt[-1]].kind
                        land[kind] = land.get(kind, Fraction(0)) + share
                    for kind in {self.tiles[t].kind for t in pt[:-1]}:
                        passed[kind] = passed.get(kind, Fraction(0)) + share
            table[choice or "(分岐なし)"] = {
                "land_pct": {k: pct(v) for k, v in sorted(land.items())},
                "pass_pct": {k: pct(v) for k, v in sorted(passed.items())},
            }
        return table

    def _first_branch(self, start: str) -> list[str | None]:
        """進行方向で最初に出会う分岐の選択肢(分岐先マスのID)。分岐がなければ [None]。"""
        seen = set()
        cur = start
        while cur not in seen:
            seen.add(cur)
            nxt = self.tiles[cur].next
            if len(nxt) > 1:
                return list(nxt)
            if not nxt:
                break
            cur = nxt[0]
        return [None]

    def _goes_through(self, start: str, path: tuple[str, ...], choice: str | None) -> bool:
        if choice is None:
            return True
        cur = start
        for tile in path:
            if len(self.tiles[cur].next) > 1:
                return tile == choice
            cur = tile
        return True  # 分岐に届かない短い移動はどの選択肢にも共通
