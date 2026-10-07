"""カード・チップ・モンスター・マス・イベントの一覧(knowledge/catalog.json)を作る。

    python scripts/build_catalog.py

効果の説明は knowledge/catalog_text.json(自分の言葉で書いた要約)から取り、
コスト・レアリティ・属性・難易度別ステータスなどの数値は、取得済みのwikiデータ(data/)から取り込む。
wiki間で数値が食い違うものは、高い方(上方修正後とみなす)を採用する(stat_overrides.json)。
利用者が実行する必要はない(catalog.json は同梱済み)。wikiの更新を取り込むときにメンテナが実行する。
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "src" / "apadvisor" / "knowledge"
WIKI = ROOT / "data" / "wiki"
WIKI_EN = ROOT / "data" / "wiki_en"
DIFFICULTIES = ("極限", "狂気", "悪夢", "困難", "普通")
RARITY = {"blue": "青", "purple": "紫", "orange": "金", "gold": "金", "yellow": "金"}
# 英語wikiのページが別のチップ(統合・改名後)を指していて、レアリティを取り違えるもの
RARITY_FIX = {"アドレナリン注射液-一般": "紫"}
KEYWORD = {"None": "無", "Starlight": "スターライト", "Mark": "マーク", "Heal": "ヒール", "Charge": "チャージ"}


def norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace(" ", "").lower()


def sections(text: str) -> dict[str, str]:
    out, name, buf = {}, "_head", []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            out[name] = "\n".join(buf).strip()
            name, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    out[name] = "\n".join(buf).strip()
    return out


def wiki_page(folder: str, name: str) -> dict[str, str]:
    path = WIKI / folder / f"{name}.md"
    return sections(path.read_text(encoding="utf-8")) if path.exists() else {}


def card_fields(name: str) -> dict:
    """概要の1行目「バトルカード(攻撃) | コスト2 | 通常獲得(PvE)」から種類・コスト・入手を読む。"""
    head = wiki_page("カード", name).get("概要", "").splitlines()
    parts = [p.strip() for p in head[0].split("|")] if head else []
    out: dict = {}
    for part in parts:
        if part.startswith("コスト"):
            out["cost"] = int(re.sub(r"\D", "", part) or 0)
        elif "カード" in part:
            out["type"] = part
        elif "獲得" in part:
            out["availability"] = part
    return out


def english_chip(en_name: str | None) -> dict:
    """英語wikiのチップのページから、レアリティと属性を読む。"""
    if not en_name:
        return {}
    path = WIKI_EN / f"{en_name}.md"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    out = {}
    m = re.search(r"\|\s*rarity\s*=\s*(\w+)", text)
    if m and m.group(1).lower() in RARITY:
        out["rarity"] = RARITY[m.group(1).lower()]
    m = re.search(r"\|\s*keyword\s*=\s*(\w+)", text)
    if m:
        out["stack"] = KEYWORD.get(m.group(1), m.group(1))
    return out


def monster_fields(name: str, overrides: dict) -> dict:
    page = wiki_page("モンスター", name)
    summary = page.get("概要", "")
    out: dict = {}
    m = re.search(r"区分は(ノーマル|エリート|ボス)モンスター", summary)
    if m:
        out["class"] = {"ノーマル": "ザコ", "エリート": "エリート", "ボス": "ボス"}[m.group(1)]
    stats: dict[str, list[int]] = {}
    coin = None
    for line in page.get("能力", "").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0] in (*DIFFICULTIES, "全難易度") and len(cells) >= 4:
            nums = [int(c) for c in cells[1:4] if c.isdigit()]
            if len(nums) == 3:
                stats[cells[0]] = nums
            if coin is None and len(cells) >= 5 and cells[-1].isdigit():
                coin = int(cells[-1])
    override = overrides.get(name)
    if override:  # wiki間で食い違うものは各項目の高い方
        for diff, values in override["stats"].items():
            stats[diff] = [max(a, b) for a, b in zip(values, stats.get(diff, values))]
    if stats:
        out["stats"] = {d: {"攻撃": v[0], "防御": v[1], "HP": v[2]} for d, v in stats.items()}
    if coin is not None:
        out["coin"] = coin
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not WIKI.is_dir():
        print("data/wiki がありません。先に scripts/scrape_wiki.py を実行してください。")
        return 1
    texts = json.loads((KNOWLEDGE / "catalog_text.json").read_text(encoding="utf-8"))
    names = json.loads((KNOWLEDGE / "name_map.json").read_text(encoding="utf-8"))
    overrides = json.loads((KNOWLEDGE / "stat_overrides.json").read_text(encoding="utf-8"))
    effect_notes = overrides.get("effect_notes", {})
    translations = {norm(row["ja"]): row for cat, rows in names.items() if not cat.startswith("_") for row in rows}

    catalog: dict = {
        "_comment": (
            "カード・チップ・モンスター・マス・イベントの一覧。scripts/build_catalog.py で生成。"
            "summary は自分の言葉で書いた要約(catalog_text.json)、数値はwiki(高い方を採用)。ゲーム画面と違えば画面が正。"
        )
    }
    missing = []
    for kind, entries in texts.items():
        if kind.startswith("_"):
            continue
        out = {}
        for name, summary in entries.items():
            row = translations.get(norm(name.split("(")[0])) or translations.get(norm(name)) or {}
            entry: dict = {"summary": summary}
            if row.get("en"):
                entry["en"] = row["en"]
            if row.get("zh"):
                entry["zh"] = row["zh"]
            if kind == "card":
                entry.update(card_fields(name))
            elif kind == "chip":
                entry.update(english_chip(row.get("en")))
                if name in RARITY_FIX:
                    entry["rarity"] = RARITY_FIX[name]
                if "rarity" not in entry:
                    missing.append(f"チップ {name} のレアリティ")
            elif kind == "monster":
                entry.update(monster_fields(name, overrides.get("monster", {})))
            if name in effect_notes:
                entry["note"] = effect_notes[name]
            out[name] = entry
        catalog[kind] = out
        print(f"- {kind}: {len(out)}件")
    text = json.dumps(catalog, ensure_ascii=False, indent=1)
    (KNOWLEDGE / "catalog.json").write_text(text + "\n", encoding="utf-8")
    print(f"→ {KNOWLEDGE / 'catalog.json'}({len(text):,}文字)")
    if missing:
        print("取り込めなかった項目: " + "、".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
