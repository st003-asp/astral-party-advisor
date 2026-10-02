"""盤面(マップ)を使った経路の計算。

盤面データは knowledge/maps.json(日本語wikiのマップの表から scripts/build_maps.py で生成)。
"""

from __future__ import annotations

import json
import re
import unicodedata
from fractions import Fraction
from functools import lru_cache
from importlib import resources

from .dice import Dist, pct

SAFETY = "セーフティポイント"
SHOP = "ショップ"
CHIP_SHOP = "強化チップショップ"


class Board:
    """マップの盤面。マスは「C4」のような名前で、互いにつながっている。

    プレイヤーは来た道を引き返せない。分岐(行き先が2つ以上あるマス)では進む先を選ぶ。
    """

    def __init__(self, tiles: dict[str, dict]):
        self.tiles = tiles
        for tid, tile in tiles.items():
            for nxt in tile["next"]:
                if nxt not in tiles:
                    raise ValueError(f"{tid} の next に未定義のマス {nxt} があります")

    @staticmethod
    def from_dict(data: dict) -> "Board":
        return Board(
            {tid: {"kind": t.get("kind", ""), "next": list(t.get("next", []))} for tid, t in data["tiles"].items()}
        )

    def exits(self, tile: str, came_from: str | None) -> list[str]:
        """tile から進める先。来た道は除く(行き止まりなら引き返す)。"""
        options = [n for n in self.tiles[tile]["next"] if n != came_from]
        return options or list(self.tiles[tile]["next"])

    def paths(self, start: str, came_from: str | None, steps: int) -> list[tuple[str, ...]]:
        """start から steps 歩進むすべての経路(start 自身は含まない)。"""
        if steps <= 0:
            return [()]
        out: list[tuple[str, ...]] = []
        for nxt in self.exits(start, came_from):
            out.extend((nxt, *rest) for rest in self.paths(nxt, start, steps - 1))
        return out

    def _first_junction(self, start: str, came_from: str | None, max_steps: int) -> tuple[str, list[str]] | None:
        """進行方向で最初に出会う分岐のマスと、その選択肢。max_steps 以内になければ None。"""
        prev, cur = came_from, start
        for _ in range(max_steps):
            options = self.exits(cur, prev)
            if len(options) > 1:
                return cur, options
            prev, cur = cur, options[0]
        return None

    def first_choices(self, start: str, came_from: str | None, max_steps: int) -> list[str]:
        junction = self._first_junction(start, came_from, max_steps)
        return junction[1] if junction else []

    def _choice_of(self, start: str, came_from: str | None, path: tuple[str, ...]) -> str | None:
        """経路 path が最初の分岐でどちらへ進んだか。分岐に届かない短い経路なら None。"""
        prev, cur = came_from, start
        for tile in path:
            if len(self.exits(cur, prev)) > 1:
                return tile
            prev, cur = cur, tile
        return None

    def ahead(self, start: str, came_from: str | None, limit: int = 10) -> list[str]:
        """次の分岐まで一本道で続くマスを「C4 モンスター突撃ゲート」の形で返す。"""
        out = []
        prev, cur = came_from, start
        for _ in range(limit):
            options = self.exits(cur, prev)
            if len(options) != 1:
                break
            prev, cur = cur, options[0]
            out.append(self.label(cur))
        return out

    def kinds(self) -> list[str]:
        return sorted({t["kind"] for t in self.tiles.values()})

    def match_kind(self, kind: str) -> list[str]:
        """マスの種類の名前(一部でもよい)に当てはまるマス。"""
        target = _norm(kind)
        exact = [tid for tid, t in self.tiles.items() if _norm(t["kind"]) == target]
        return exact or [tid for tid, t in self.tiles.items() if target and target in _norm(t["kind"])]

    def candidates(self, kind: str, start: str | None, came_from: str | None, max_steps: int) -> list[dict]:
        """止まったマスの種類から、現在地の候補を出す。

        start が分かっていれば、そこから max_steps 歩以内に着ける kind のマスを歩数つきで返す。
        分からなければ、その種類のマスをすべて返す。どちらも隣のマスの種類を付ける(画面との照合用)。
        """
        targets = set(self.match_kind(kind))
        if not targets:
            raise KeyError(f"「{kind}」というマスはこのマップにない(あるのは {'、'.join(self.kinds())})")

        def describe(tile: str) -> dict:
            return {"マス": self.label(tile), "隣のマス": [self.label(n) for n in self.tiles[tile]["next"]]}

        if start is None:
            return [describe(t) for t in sorted(targets)]
        found: dict[tuple[str, str], dict] = {}
        for steps in range(1, max_steps + 1):
            for path in self.paths(start, came_from, steps):
                end, before = path[-1], (path[-2] if len(path) > 1 else start)
                if end in targets and (end, before) not in found:
                    found[(end, before)] = {**describe(end), "歩数": steps, "直前に通るマス": before, "経路": list(path)}
        return list(found.values())

    def label(self, tile: str) -> str:
        return f"{tile} {self.tiles[tile]['kind']}"

    def route_table(self, start: str, came_from: str | None, dist: Dist, *, own_start: str | None = None) -> dict:
        """最初の分岐の選び方ごとに、どこに止まるか・何を通るかの確率を出す。

        2つ目以降の分岐は、各選択肢を等確率で選ぶものとして平均する。
        own_start: 自分のスタートポイントのマス(通過時に止まれるので、セーフティポイントと同じ扱いにする)。
        """
        junction = self._first_junction(start, came_from, dist.max())
        choices: list[str | None] = list(junction[1]) if junction else [None]
        stoppable = {t for t, v in self.tiles.items() if v["kind"] == SAFETY} | ({own_start} if own_start else set())
        table = {}
        for choice in choices:
            land: dict[str, Fraction] = {}
            by_roll: dict[int, list[str]] = {}
            passes = {SHOP: Fraction(0), CHIP_SHOP: Fraction(0), "stop": Fraction(0)}
            for steps, p in dist.pmf.items():
                paths = [
                    pt
                    for pt in self.paths(start, came_from, steps)
                    if choice is None or self._choice_of(start, came_from, pt) in (choice, None)
                ]
                if not paths:
                    continue
                share = p / len(paths)
                ends = set()
                for pt in paths:
                    end = pt[-1] if pt else start
                    kind = self.tiles[end]["kind"]
                    land[kind] = land.get(kind, Fraction(0)) + share
                    ends.add(self.label(end))
                    kinds = {self.tiles[t]["kind"] for t in pt}
                    for key in (SHOP, CHIP_SHOP):
                        if key in kinds:
                            passes[key] += share
                    if stoppable & set(pt):
                        passes["stop"] += share
                by_roll[steps] = sorted(ends)
            if choice is None:
                name, upcoming = "(分岐なし)", self.ahead(start, came_from)
            else:
                name = f"{self.label(choice)} の方向"
                upcoming = [self.label(choice), *self.ahead(choice, junction[0])]
            table[name] = {
                "この先のマス": upcoming,
                "止まるマスの種類(%)": {k: pct(v) for k, v in sorted(land.items(), key=lambda kv: -kv[1])},
                "ショップを通る(%)": pct(passes[SHOP]),
                "強化チップショップを通る(%)": pct(passes[CHIP_SHOP]),
                "セーフティポイントか自分のスタートポイントで止まれる(%)": pct(passes["stop"]),
                "出目ごとの止まるマス": {str(k): v for k, v in sorted(by_roll.items())},
            }
        return table


@lru_cache(maxsize=1)
def _maps() -> dict[str, dict]:
    text = resources.files("apadvisor.knowledge").joinpath("maps.json").read_text(encoding="utf-8")
    return json.loads(text)["maps"]


def map_names() -> list[str]:
    return list(_maps())


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace(" ", "")


def find_map(name: str | None) -> str | None:
    """マップ名のゆれ(「異変図書館(協力チャレンジ)」など)を吸収して、盤面データのキーを返す。"""
    if not name:
        return None
    target = _norm(name)
    keys = map_names()
    exact = [k for k in keys if _norm(k) == target]
    if exact:
        return exact[0]
    partial = [k for k in keys if _norm(k.split("/")[0]) in target or target in _norm(k)]
    return partial[0] if len(partial) == 1 else None


def get_board(name: str) -> Board:
    key = find_map(name)
    if key is None:
        raise KeyError(f"盤面データのないマップ: {name}(あるのは {'、'.join(map_names())})")
    return Board.from_dict(_maps()[key])


def start_tile(name: str | None, order: int | None) -> tuple[str, str | None] | None:
    """order 番手のスタートポイントのマスと、最初に向いている先のマス。"""
    key = find_map(name)
    if key is None or order is None:
        return None
    for tid, tile in _maps()[key]["tiles"].items():
        if tile["kind"] == f"スタートポイント({order}番手)":
            return tid, tile.get("start_facing")
    return None


def behind_start(name: str, tile: str) -> str | None:
    """tile がスタートポイントなら、最初の向きの反対側のマス(試合開始時はそちらへ進めない)。"""
    data = _maps()[find_map(name)]["tiles"].get(tile, {})
    facing = data.get("start_facing")
    others = [n for n in data.get("next", []) if n != facing]
    return others[0] if facing and len(others) == 1 else None


def without_tile_ids(text: str, name: str | None) -> str:
    """文章に残ったマスの名前(C4 など)を、マスの種類に置き換える。

    マスの名前はwikiの図を知らない人には通じないので、レポートには出さない。
    「H3(ショップ)」「H3のショップ」は「ショップ」に、単独の「H3」も「ショップ」にする。
    """
    key = find_map(name)
    if key is None or not text:
        return text
    tiles = _maps()[key]["tiles"]
    ids = "|".join(re.escape(t) for t in sorted(tiles, key=len, reverse=True))
    out = re.sub(rf"(?<![A-Za-z0-9.])({ids})(?![A-Za-z0-9]|\.\d)", lambda m: tiles[m.group(1)]["kind"], text)
    if out == text:
        return text
    # 「H3(ショップ)」「ショップのH3」のように種類が添えてあった箇所は、同じ言葉が重なるのでまとめる
    for kind in sorted({t["kind"] for t in tiles.values()}, key=len, reverse=True):
        k, base = re.escape(kind), re.escape(kind.split("(")[0])
        out = re.sub(rf"{k}\s*[(（]\s*{base}\s*[)）]", kind, out)
        out = re.sub(rf"{base}(?:の|\s+)?{k}", kind, out)
        out = re.sub(rf"{k}(?:の|\s+)?{base}(?![(（])", kind, out)
    return out


def board_text(name: str | None) -> str:
    """AIに渡す盤面の説明(マスの一覧とつながり、スタート位置、敵の優先方向)。盤面データがなければ空文字。"""
    key = find_map(name)
    if key is None:
        return ""
    data = _maps()[key]
    lines = [
        f"マップ「{key}」の盤面。マスは「行の文字+列の番号」で呼ぶ(wikiの図で、行はAから下へ、列は0から右へ)。",
        "ゲーム画面は斜め上からの視点なので、図の上下左右とは一致しない。隣り合うマスの種類の並びで位置を照合する。",
        "書式は「マス: 種類 (つながっているマス)」。つながりが3つ以上あるマスが分岐。",
    ]
    for tid, tile in data["tiles"].items():
        extra = f"、最初の向きは {tile['start_facing']}" if "start_facing" in tile else ""
        lines.append(f"{tid}: {tile['kind']} ({', '.join(tile['next'])}{extra})")
    priorities = data.get("monster_priority") or []
    if priorities:
        first = "、".join(f"{p['from']}→{p['to']}" for p in priorities if p["priority"] == 1)
        second = "、".join(f"{p['from']}→{p['to']}" for p in priorities if p["priority"] == 2)
        lines.append(f"分岐で敵が進む向き: 最優先 {first}" + (f" / 次点 {second}" if second else ""))
    spawns = data.get("monster_spawns") or []
    if spawns:
        lines.append("敵の出現位置(進捗ごと): " + " / ".join(spawns))
    return "\n".join(lines)
