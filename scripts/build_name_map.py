"""日本語名 ↔ 英語名 ↔ 中国語名の対応表を作り、数値で照合する。

    python scripts/build_name_map.py candidates   # 数値の一致から対応候補を出す(対応表を作るときの下調べ)
    python scripts/build_name_map.py verify       # knowledge/name_map.json を各wikiの数値と照合する
    python scripts/build_name_map.py reconcile    # 食い違う数値を高い方に寄せた補正表(stat_overrides.json)を作る

対応表そのもの(src/apadvisor/knowledge/name_map.json)は、候補を人が確認して確定させたもの。
verify は、対応づけたページ同士で「効果文に出てくる数値」や「敵のステータス」が食い違うものを一覧にする。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MAP_PATH = ROOT / "src" / "apadvisor" / "knowledge" / "name_map.json"
DIFFS_JA = ["普通", "困難", "悪夢", "狂気", "極限"]


def numbers(text: str) -> Counter:
    """効果文に出てくる数値の多重集合(色指定やファイル名の数字は除く)。"""
    text = re.sub(r"#[0-9a-fA-F]{6}", " ", text)
    text = re.sub(r"\[\[(?:File|file|文件)[^\]]*\]\]", " ", text)
    text = re.sub(r"\{\{状态/图标\|([^|}]*)\|\d+\}\}", r"\1", text)
    text = re.sub(r"(蓝|绿|红)\d", r"\1", text)
    text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return Counter(re.findall(r"\d+", text))


def significant(nums: Counter) -> Counter:
    return Counter({k: v for k, v in nums.items() if k not in ("0", "1")})


def similarity(a: Counter, b: Counter) -> float:
    if not a and not b:
        return 0.0
    inter = sum((a & b).values())
    union = sum((a | b).values())
    return inter / union if union else 0.0


def template_fields(text: str, template: str) -> dict[str, str]:
    """{{template |key=value ...}} の最初の1つを辞書にする(入れ子の {{ }} は値に含める)。"""
    start = text.find("{{" + template)
    if start < 0:
        return {}
    depth, i = 0, start
    while i < len(text):
        if text.startswith("{{", i):
            depth += 1
            i += 2
        elif text.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                break
        else:
            i += 1
    body = text[start + 2 + len(template) : i - 2]
    fields: dict[str, str] = {}
    depth, key, buf = 0, None, []
    parts: list[str] = []
    for ch_i, ch in enumerate(body):
        if body.startswith("{{", ch_i) or body.startswith("[[", ch_i):
            depth += 1
        elif body.startswith("}}", ch_i) or body.startswith("]]", ch_i):
            depth -= 1
        if ch == "|" and depth <= 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    for part in parts:
        if "=" in part:
            key, _, value = part.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def load(folder: str) -> dict[str, str]:
    index_path = DATA / folder / "index.json"
    if not index_path.exists():
        return {}
    index = json.loads(index_path.read_text(encoding="utf-8"))
    return {title: (DATA / folder / rel).read_text(encoding="utf-8") for title, rel in index.items()}


def ja_section(text: str, heading: str = "概要") -> str:
    m = re.search(rf"## {heading}\n(.*?)(?=\n## |\Z)", text, re.S)
    return m.group(1) if m else ""


def ja_monster_stats(text: str) -> dict[str, tuple[int, int, int]]:
    stats = {}
    for diff in DIFFS_JA:
        m = re.search(rf"^{diff} \| (\d+) \| (\d+) \| (\d+)", text, re.M)
        if m:
            stats[diff] = tuple(int(x) for x in m.groups())
    return stats


def en_monster_stats(fields: dict[str, str]) -> dict[str, tuple[int, int, int]]:
    out = {}
    for diff, prefix in zip(DIFFS_JA, ["", "hard", "nm", "ins", "ex"]):
        try:
            out[diff] = tuple(int(fields[f"{prefix}{k}"]) for k in ("atk", "def", "hp"))
        except (KeyError, ValueError):
            pass
    return out


def zh_monster_stats(fields: dict[str, str]) -> dict[str, tuple[int, int, int]]:
    out = {}
    for diff, key in zip(DIFFS_JA, ["普通", "困难", "噩梦", "疯狂", "极限"]):
        nums = re.findall(r"\d+", fields.get(key, ""))
        if len(nums) >= 3:
            out[diff] = tuple(int(x) for x in nums[:3])
    return out


def catalogs() -> dict[str, dict[str, dict[str, dict]]]:
    """カテゴリ → 言語 → 名前 → {"nums": 数値, "stats": 敵ステータス, "text": 効果文}。"""
    ja, en, zh = load("wiki"), load("wiki_en"), load("wiki_zh_bili")
    cat: dict[str, dict[str, dict[str, dict]]] = {
        c: {"ja": {}, "en": {}, "zh": {}} for c in ("chip", "card", "monster", "character", "event")
    }
    prefix = {"チップ": "chip", "カード": "card", "モンスター": "monster", "キャラクター": "character", "イベント": "event"}
    for title, text in ja.items():
        head, _, name = title.partition("/")
        if head not in prefix or not name or name in ("一覧", "育成") or "/" in name:
            continue
        body = ja_section(text)
        cat[prefix[head]]["ja"][name] = {"nums": numbers(body), "stats": ja_monster_stats(text), "text": body.strip()}
    for title, text in en.items():
        for template, c, key in (
            ("ChipInfobox", "chip", "desc"),
            ("CardInfobox", "card", "desc"),
            ("MonsterInfobox", "monster", None),
            ("CharaInfobox", "character", None),
        ):
            f = template_fields(text, template)
            if not f:
                continue
            body = f.get(key, "") if key else " ".join(v for k, v in f.items() if k.endswith("desc"))
            entry = {"nums": numbers(body), "stats": en_monster_stats(f), "text": body, "fields": f}
            if c == "character":
                entry["ja_name"] = template_fields(text, "CharPlayable").get("nameJapanese", "")
            cat[c]["en"][title] = entry
    for title, text in zh.items():
        if "/" in title or title.startswith(("沙盒", "测试")):
            continue
        for template, c in (("星趴筹码", "chip"), ("星趴手牌", "card"), ("星趴怪物", "monster"), ("星趴角色", "character"), ("Card/Event", "event")):
            f = template_fields(text, template)
            if not f:
                continue
            if c == "card":
                body = " ".join(f.get(k, "") for k in ("内容(通用)", "内容(PVE)", "内容(PVP)", "费用"))
            elif c == "chip":
                body = f.get("描述", "")
            elif c == "event":
                body = f.get("内容", "")
            else:
                body = " ".join(v for k, v in f.items() if "描述" in k)
            cat[c]["zh"][title] = {
                "nums": numbers(body),
                "stats": zh_monster_stats(f),
                "text": body,
                "fields": f,
                "en_name": f.get("英文", "") or f.get("英文名", ""),
                "ja_name": f.get("日文名", ""),
            }
    return cat


def candidates() -> None:
    cat = catalogs()
    for c, langs in cat.items():
        print(f"\n######## {c}: ja={len(langs['ja'])} en={len(langs['en'])} zh={len(langs['zh'])}")
        for other in ("en", "zh"):
            print(f"--- ja → {other}")
            for name, entry in langs["ja"].items():
                scored = []
                for oname, oentry in langs[other].items():
                    if c == "monster":
                        common = set(entry["stats"]) & set(oentry["stats"])
                        score = sum(entry["stats"][d] == oentry["stats"][d] for d in common) / max(len(common), 1)
                    else:
                        score = similarity(entry["nums"], oentry["nums"])
                    if score > 0:
                        scored.append((score, oname))
                scored.sort(reverse=True)
                top = ", ".join(f"{n}({s:.2f})" for s, n in scored[:4])
                print(f"{name} | {''.join(sorted(entry['nums'].elements()))[:20]} => {top}")
        for other in ("en", "zh"):
            print(f"--- {other} 一覧")
            print(" / ".join(langs[other]))


def verify() -> int:
    """対応表を各wikiと照合する。

    1. 対応先のページが実在するか(全カテゴリ)
    2. 敵: 難易度別の攻撃/防御/HPが日本語wikiと一致するか(英語・中国語)
    3. チップ・カード・イベント: 効果文の数値が日本語wikiと一致するか(中国語のみ。
       英語wikiは等級違いを1ページにまとめているので数値の比較はしない)
    4. キャラ: 英語・中国語wikiに書かれている日本語名と矛盾しないか
    """
    cat = catalogs()
    mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    indexes = {
        "ja": json.loads((DATA / "wiki" / "index.json").read_text(encoding="utf-8")),
        "en": json.loads((DATA / "wiki_en" / "index.json").read_text(encoding="utf-8")),
        "zh": json.loads((DATA / "wiki_zh_bili" / "index.json").read_text(encoding="utf-8")),
    }
    ja_prefix = {"chip": "チップ", "card": "カード", "monster": "モンスター", "character": "キャラクター", "event": "イベント", "map": "PvE/マップ"}
    counts = Counter()

    def report(kind: str, message: str) -> None:
        counts[kind] += 1
        print(f"[{kind}] {message}")

    for c, rows in mapping.items():
        if c.startswith("_"):
            continue
        for row in rows:
            if f"{ja_prefix[c]}/{row['ja']}" not in indexes["ja"]:
                report("ページなし", f"{c}: 日本語wikiに {row['ja']} がない")
            for other in ("en", "zh"):
                if row.get(other) and row[other] not in indexes[other]:
                    report("ページなし", f"{c}: {row['ja']} → {other} の {row[other]} がない")

        langs = cat.get(c)
        if langs is None:
            continue
        mapped = {r["ja"] for r in rows}
        unmapped = [n for n in langs["ja"] if n not in mapped]
        if unmapped:
            report("対応表にない", f"{c}: {'、'.join(unmapped)}")
        for row in rows:
            ja = langs["ja"].get(row["ja"])
            if ja is None:
                continue
            for other in ("en", "zh"):
                entry = langs[other].get(row.get(other, ""))
                if entry is None:
                    continue
                if c == "monster":
                    diff = {
                        d: f"日本語{ja['stats'][d]} / {other}{entry['stats'][d]}"
                        for d in DIFFS_JA
                        if d in ja["stats"] and d in entry["stats"] and ja["stats"][d] != entry["stats"][d]
                    }
                    if diff:
                        report("ステータス不一致", f"{row['ja']} / {row[other]}: {diff}")
                    elif not (set(ja["stats"]) & set(entry["stats"])):
                        report("照合できず", f"monster: {row['ja']} / {row[other]}(比較できるステータス表がない)")
                elif c == "character":
                    written = entry.get("ja_name", "")
                    if written and written.replace(" ", "") not in (row["ja"], *CHARACTER_JA_ALIASES.get(row["ja"], ())):
                        report("名前不一致", f"{row['ja']} / {row[other]}: {other} wikiの日本語名は {written}")
                elif other == "zh":
                    # 「1人」「1枚」のような数え方は言語で書き方が違うので、0と1は比べない
                    ja_nums, zh_nums = significant(ja["nums"]), significant(entry["nums"])
                    if ja_nums != zh_nums:
                        only_ja = " ".join(f"{k}×{v}" for k, v in sorted((ja_nums - zh_nums).items()))
                        only_zh = " ".join(f"{k}×{v}" for k, v in sorted((zh_nums - ja_nums).items()))
                        report("数値不一致", f"{c}: {row['ja']} / {row['zh']}: 日本語だけ[{only_ja}] 中国語だけ[{only_zh}]")
    print("\n集計:", dict(counts) or "問題なし")
    return 0


OVERRIDES_PATH = ROOT / "src" / "apadvisor" / "knowledge" / "stat_overrides.json"

# 効果文の数値がwiki間で実際に食い違っていたもの(書き方の違いは除く)。高い方を採用する
EFFECT_NOTES = {
    "龍の咆哮": "攻撃+4、敵の防御-4(日本語wiki)。中国語wikiは+3/-3。高い方の+4/-4を採用",
    "エナジーバー": "攻撃+3、移動+2(日本語wiki)。中国語wikiは攻撃+2。高い方の+3を採用",
    "ショップ大特価": "品揃え+2が3ターン続く(中国語wiki)。日本語wikiは2ターン。長い方の3ターンを採用",
}


def reconcile() -> int:
    """wiki間で食い違う数値を「高い方」に寄せた補正表を作る。

    アップデートで上方修正された値が、更新の遅いwikiに残っていないとみなす方針(ユーザーの判断)。
    敵のステータスは難易度ごと・項目ごとに、日本語/英語/中国語wikiの最大値を取る。
    """
    cat = catalogs()
    mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    monsters: dict[str, dict] = {}
    for row in mapping["monster"]:
        ja = cat["monster"]["ja"].get(row["ja"])
        if ja is None:
            continue
        sources = {"日本語": ja["stats"]}
        for other, label in (("en", "英語"), ("zh", "中国語")):
            entry = cat["monster"][other].get(row.get(other, ""))
            if entry and entry["stats"]:
                sources[label] = entry["stats"]
        merged, differs = {}, False
        for diff in DIFFS_JA:
            values = [s[diff] for s in sources.values() if diff in s]
            if not values:
                continue
            merged[diff] = [max(v[i] for v in values) for i in range(3)]
            differs |= len({tuple(v) for v in values}) > 1
        if differs:
            monsters[row["ja"]] = {
                "stats": merged,
                "sources": {label: {d: list(v) for d, v in s.items()} for label, s in sources.items()},
            }
    result = {
        "_comment": (
            "wiki間で数値が食い違うものを高い方に寄せた補正表。scripts/build_name_map.py reconcile で生成。"
            "stats は難易度ごとの [攻撃, 防御, HP] で、各wikiの最大値。sources は各wikiの記載。"
            "上方修正後の値が正しいとみなす方針によるもので、実際のゲーム画面と違えば画面が正。"
        ),
        "monster": monsters,
        "effect_notes": EFFECT_NOTES,
    }
    text = json.dumps(result, ensure_ascii=False, indent=1)
    text = re.sub(r"\[\s+(\d+),\s+(\d+),\s+(\d+)\s+\]", r"[\1, \2, \3]", text)
    OVERRIDES_PATH.write_text(text + "\n", encoding="utf-8")
    print(f"敵 {len(monsters)} 体、効果文 {len(EFFECT_NOTES)} 件 → {OVERRIDES_PATH}")
    return 0


# 日本語wikiのページ名と、英語・中国語wikiに書かれている日本語表記が違うキャラ
CHARACTER_JA_ALIASES = {
    "Z3000": ("ゼット3000",),
    "マムシ": ("真夢梓", "天川真夢梓"),
    "ナンシー": ("ナンシーロー", "ナンシー・ロー"),
    "ヒメ": ("姫夢楓", "ヒメ・ムフウ"),
    "ユメ": ("姫夢朝",),
    "カイセイ": ("藍海晴",),
    "レン": ("恋",),
    "リン": ("凛",),
    "テル": ("照",),
    "ミサキ": ("美咲", "龍ケ崎美咲"),
    "コマチ": ("小町",),
    "ルカ": ("星魅琉華",),
    "スミカゲ": ("墨影",),
    "シェリー": ("橘シェリー",),
    "ハンナ": ("遠野ハンナ",),
    "ジル": ("ジル・スティングレイ", "ジル·スティソグレイ"),
    "ドロシー": ("ドロシー・ヘイズ", "ドロシー·ヘイズ"),
    "あめちゃん": ("ニーディーガール",),
    "超てんちゃん": ("超絶最かわてんしちゃん",),
}


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    command = sys.argv[1] if len(sys.argv) > 1 else "verify"
    commands = {"candidates": candidates, "verify": verify, "reconcile": reconcile}
    if command not in commands:
        sys.exit(f"使い方: build_name_map.py [{' | '.join(commands)}]")
    sys.exit(commands[command]() or 0)
