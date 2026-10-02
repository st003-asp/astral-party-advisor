"""日本語wikiのマップページにある盤面の表を、盤面データ(knowledge/maps.json)に変換する。

    python scripts/build_maps.py

表は「マスの行」と「つなぎの行」が交互に並び、列も「マスの列」と「つなぎの列」が交互に並ぶ。
    A |  | ？ | ─ | CS | ...      ← マスの行(？ と CS が ─ でつながっている)
      |  | △ |  |  | ...          ← つなぎの行(△ は上下のマスをつなぐ。矢印は敵の優先方向)
マスは「行の文字+列の番号」(例: C4)で呼ぶ。

変換後、英語wikiに書かれているマスの種類ごとの数と照合する(data/wiki_en が必要)。
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "data" / "wiki" / "PvE"
WIKI_EN = ROOT / "data" / "wiki_en"
OUT = ROOT / "src" / "apadvisor" / "knowledge" / "maps.json"
NAME_MAP = ROOT / "src" / "apadvisor" / "knowledge" / "name_map.json"

# 表の記号 → マスの種類(日本語名)と、英語wikiでの呼び名
TILE_KINDS = {
    "？": ("イベント", "Event"),
    "転": ("転送陣", "Portal"),
    "災": ("災厄", "Sudden Calamity"),
    "薬": ("リカバリー", "Recover"),
    "突": ("モンスター突撃ゲート", "Assault"),
    "金": ("金儲け", "Windfall Hits"),
    "札": ("カード報酬", "Card Bounce"),
    "敵": ("モンスター", "Monster"),
    "疾": ("疾走", "Haste"),
    "店": ("ショップ", "Shop"),
    "CS": ("強化チップショップ", "Chip Shop"),
    "SP": ("セーフティポイント", "Check Point"),
    "飴": ("怪奇飴のガチャガチャ", "Quirky Candy Machine"),
}
# つなぎの記号。矢印は「その向きに敵が優先して進む」または「プレイヤーの初期の向き」
PRIORITY = {"◀": 1, "▶": 1, "▲": 1, "▼": 1, "◁": 2, "▷": 2, "△": 2, "▽": 2}
POINTS = {
    "◀": (0, -1), "◁": (0, -1), "←": (0, -1),
    "▶": (0, 1), "▷": (0, 1), "→": (0, 1),
    "▲": (-1, 0), "△": (-1, 0), "↑": (-1, 0),
    "▼": (1, 0), "▽": (1, 0), "↓": (1, 0),
    "↘": (1, 1), "↙": (1, -1), "↗": (-1, 1), "↖": (-1, -1),
}
DIAG_DOWN_RIGHT = {"＼", "↘", "↖"}  # 左上と右下をつなぐ
DIAG_DOWN_LEFT = {"／", "↙", "↗"}  # 右上と左下をつなぐ


H_GLYPHS = set("─◀◁▶▷←→")
V_GLYPHS = set("│▲△▼▽↑↓")
FACING_GLYPHS = set("←→↑↓↘↙↗↖")
ROW_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def read_grids(text: str) -> list[tuple[str, list[list[str]]]]:
    """マップページの本文から、盤面の表をすべて取り出す。[(直前の見出し, セルの2次元配列)]"""
    lines = text.splitlines()
    grids = []
    heading = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            heading = line[3:].strip()
        if re.match(r"^\s*\|\s*0\s*\|", line):
            width = len(line.split("|")) - 2  # 先頭と末尾は行ラベルの列
            rows = []
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^\s*\|\s*0\s*\|", lines[i]):
                cells = [c.strip() for c in lines[i].split("|")][1:]
                rows.append((cells + [""] * width)[:width])
                i += 1
            if rows:
                grids.append((heading, rows))
        i += 1
    return grids


def is_tile(symbol: str) -> bool:
    return symbol in TILE_KINDS or bool(re.fullmatch(r"P[1-4]", symbol))


def tile_id(row: int, col: int) -> str:
    """表の位置(マスはふつう偶数番目) → 「C4」のような名前。半端な位置のマスは「G1.5」になる。"""
    number = col / 2
    return f"{ROW_LETTERS[row // 2]}{int(number) if number == int(number) else number}"


def parse_board(grid: list[list[str]]) -> tuple[dict, list[str]]:
    problems: list[str] = []
    tiles: dict[str, dict] = {}
    cell = {(r, c): s for r, row in enumerate(grid) for c, s in enumerate(row) if s}
    for (r, c), symbol in cell.items():
        if is_tile(symbol):
            kind = TILE_KINDS[symbol][0] if symbol in TILE_KINDS else f"スタートポイント({symbol[1]}番手)"
            tiles[tile_id(r, c)] = {"kind": kind, "symbol": symbol, "next": []}
        elif r % 2 == 0 and c % 2 == 0 and symbol not in H_GLYPHS | V_GLYPHS:
            problems.append(f"未知のマス記号 {symbol!r} ({tile_id(r, c)})")

    def walk(r: int, c: int, dr: int, dc: int, same: set[str]) -> tuple[int, int] | None:
        """つなぎの記号をたどって、その先にあるマスの位置を返す。"""
        for _ in range(4):
            r, c = r + dr, c + dc
            symbol = cell.get((r, c), "")
            if is_tile(symbol):
                return (r, c)
            if symbol not in same:
                return None
        return None

    def ends_of(r: int, c: int, symbol: str):
        if symbol in DIAG_DOWN_RIGHT:
            return walk(r, c, -1, -1, DIAG_DOWN_RIGHT), walk(r, c, 1, 1, DIAG_DOWN_RIGHT)
        if symbol in DIAG_DOWN_LEFT:
            return walk(r, c, -1, 1, DIAG_DOWN_LEFT), walk(r, c, 1, -1, DIAG_DOWN_LEFT)
        candidates = []
        if symbol in H_GLYPHS or r % 2 == 0:
            candidates.append((walk(r, c, 0, -1, H_GLYPHS), walk(r, c, 0, 1, H_GLYPHS)))
        if symbol in V_GLYPHS or c % 2 == 0:
            candidates.append((walk(r, c, -1, 0, V_GLYPHS), walk(r, c, 1, 0, V_GLYPHS)))
        if r % 2 == 1 and c % 2 == 1:  # 斜めの位置にある矢印: 両端にマスがあるほうの斜め
            candidates.append((walk(r, c, -1, -1, set()), walk(r, c, 1, 1, set())))
            candidates.append((walk(r, c, -1, 1, set()), walk(r, c, 1, -1, set())))
        valid = [e for e in candidates if e[0] and e[1]]
        return valid[0] if len(valid) == 1 else (None, None)

    priorities: list[dict] = []
    for (r, c), symbol in cell.items():
        if is_tile(symbol):
            continue
        a_pos, b_pos = ends_of(r, c, symbol)
        if not (a_pos and b_pos):
            if symbol in FACING_GLYPHS:  # マスの横に書かれた「初期の向き」の注記
                dr, dc = POINTS[symbol]
                starts = [
                    (r + rr, c + cc)
                    for rr in (-1, 0, 1)
                    for cc in (-1, 0, 1)
                    if re.fullmatch(r"P[1-4]", cell.get((r + rr, c + cc), ""))
                ]
                target = walk(starts[0][0], starts[0][1], dr, dc, H_GLYPHS | V_GLYPHS) if len(starts) == 1 else None
                if target:
                    tiles[tile_id(*starts[0])]["start_facing"] = tile_id(*target)
                    continue
            problems.append(f"つなぎ {symbol!r} の両端のマスを決められない(表の {r} 行 {c} 列)")
            continue
        a, b = tile_id(*a_pos), tile_id(*b_pos)
        tiles[a]["next"].append(b)
        tiles[b]["next"].append(a)
        if symbol in POINTS:
            dr, dc = POINTS[symbol]
            # a は左または上側、b は右または下側。矢印の指す側が行き先
            forward = (dr > 0) or (dr == 0 and dc > 0)
            tail, head = (a, b) if forward else (b, a)
            if symbol in PRIORITY:
                priorities.append({"from": tail, "to": head, "priority": PRIORITY[symbol]})
            elif tiles[tail]["symbol"].startswith("P"):
                tiles[tail]["start_facing"] = head
    for tid, tile in tiles.items():
        tile["next"] = sorted(set(tile["next"]))
        if not tile["next"]:
            problems.append(f"どこともつながっていないマス {tid}")
        if tile["symbol"].startswith("P") and "start_facing" not in tile:
            problems.append(f"初期の向きが分からないスタートポイント {tid}")
    board = {"tiles": tiles, "monster_priority": priorities}
    return board, problems


def read_spawns(text: str) -> list[str]:
    """「モンスター出現位置」の節にある「進捗1：ゴクチョー(C5)、…」の行。なければ空。"""
    out = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == "モンスター出現位置"
        elif inside and re.match(r"^\S+[：:].*[(（][A-Z]\d", line.strip()):
            out.append(unicodedata.normalize("NFKC", line.strip()).replace(",", "、"))
    return out


def english_counts(en_title: str) -> dict[str, int]:
    path = WIKI_EN / f"{re.sub(r'[\\\\/:*?\"<>|]', '_', en_title)}.md"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    counts = {}
    for _, en in TILE_KINDS.values():
        m = re.search(rf"\|\s*{re.escape(en)}\s*=\s*(\d+)", text)
        if m:
            counts[en] = int(m.group(1))
    m = re.search(r"\|\s*Start Point\s*=\s*(\d+)", text)
    if m:
        counts["Start Point"] = int(m.group(1))
    return counts


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    en_names = {row["ja"]: row.get("en") for row in json.loads(NAME_MAP.read_text(encoding="utf-8"))["map"]}
    maps = {}
    for path in sorted(WIKI.glob("マップ__*.md")):
        name = path.stem.split("__", 1)[1]
        page = path.read_text(encoding="utf-8")
        grids = read_grids(page)
        spawns = read_spawns(page)
        if not grids:
            print(f"- {name}: 盤面の表なし")
            continue
        for heading, grid in grids:
            key = name if len(grids) == 1 else f"{name}/{heading}"
            board, problems = parse_board(grid)
            symbols = Counter(t["symbol"] for t in board["tiles"].values())
            ours = {en: symbols.get(sym, 0) for sym, (_, en) in TILE_KINDS.items() if symbols.get(sym)}
            ours["Start Point"] = sum(n for s, n in symbols.items() if s.startswith("P"))
            theirs = english_counts(en_names.get(name) or "")
            diff = {k: (ours.get(k, 0), v) for k, v in theirs.items() if ours.get(k, 0) != v}
            status = (
                "英語wikiのマス数と一致"
                if theirs and not diff
                else ("照合先なし" if not theirs else f"不一致(こちら, 英語wiki): {diff}")
            )
            junctions = sum(1 for t in board["tiles"].values() if len(t["next"]) >= 3)
            print(f"- {key}: {len(board['tiles'])}マス、分岐{junctions}か所、{status}")
            for p in problems:
                print(f"    要確認: {p}")
            for tile in board["tiles"].values():
                del tile["symbol"]
            if spawns:
                board["monster_spawns"] = spawns
            maps[key] = board
    comment = (
        "PvEマップの盤面。日本語wiki(wikiwiki.jp/tenoujigaoh)のマップページの表から scripts/build_maps.py で生成。"
        "tiles のキーは「行の文字+列の番号」。next はつながっているマス。"
        "start_facing はそのスタートポイントのプレイヤーが最初に向いている先のマス。"
        "monster_priority は分岐で敵が優先して進む向き(1が最優先、2が次点)。"
        "monster_spawns は進捗ごとの敵の出現位置。"
    )
    text = json.dumps({"_comment": comment, "maps": maps}, ensure_ascii=False, indent=1)
    text = re.sub(r"\[\s+((?:\"[A-Z][\d.]+\",?\s*)+)\]", lambda m: "[" + re.sub(r"\s+", " ", m.group(1)).strip() + "]", text)
    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"→ {OUT}({len(maps)}マップ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
