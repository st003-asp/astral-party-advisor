"""知識ベース(ルール・指針・キャラ要約・wikiデータ)の読み込みと検索。"""

from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from functools import lru_cache
from importlib import resources
from pathlib import Path

from . import events

# lookup の結果は以後のやり取りで毎回送り直されるので、長さがそのまま費用になる
MAP_ENTRY_MAX_CHARS = 16000  # マップのページ(盤面図+敵のステータス表)が収まる長さ
WIKI_ENTRY_MAX_CHARS = 5000  # それ以外の日本語wikiのページ
TRANSLATED_ENTRY_MAX_CHARS = 2500  # 対応表から添える英語・中国語ページ


def _read(name: str) -> str:
    return resources.files("apadvisor.knowledge").joinpath(name).read_text(encoding="utf-8")


# (表示名, data/ 以下のフォルダ)。上にあるものほど検索で優先する
WIKI_SOURCES = (
    ("日本語wiki", "wiki"),
    ("英語wiki", "wiki_en"),
    ("中国語wiki", "wiki_zh_bili"),
    ("中国語wiki(wiki.gg)", "wiki_zh"),
)


def data_dir() -> Path:
    """scripts/scrape_*.py の保存先。環境変数 APADVISOR_DATA_DIR で変更できる。"""
    env = os.environ.get("APADVISOR_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data"


def tile_icons() -> list[tuple[str, Path]]:
    """マスの絵柄の見本 [(マスの種類, 画像のパス)]。

    英語wikiのアイコンを縮小して knowledge/tile_icons に同梱している(scripts/fetch_tile_icons.py)。
    """
    folder = Path(__file__).resolve().parent / "knowledge" / "tile_icons"
    return sorted((p.stem, p) for p in folder.glob("*.png")) if folder.is_dir() else []


@lru_cache(maxsize=1)
def characters() -> list[dict]:
    return json.loads(_read("characters.json"))["characters"]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[\s\-‐－ー・:：!！?？()\[\]【】「」『』〜~]", "", text)


def character_from_label(label: str | None) -> str | None:
    """二つ名(「暗躍する忍者」など)やキャラ名からキャラ名を返す。分からなければ None。

    画面の表記はwikiと少し違うことがある(デスティニーガール/ディスティニーガール)ので、近い文字列も許す。
    """
    q = _norm(label or "")
    if not q:
        return None
    keys: dict[str, str] = {}
    for ch in characters():
        for text in (ch.get("title", ""), ch["name"], ch.get("en", "")):
            if text:
                keys.setdefault(_norm(text), ch["name"])
    if q in keys:
        return keys[q]
    close = difflib.get_close_matches(q, list(keys), n=1, cutoff=0.75)
    return keys[close[0]] if close else None


@lru_cache(maxsize=1)
def _wiki_keys() -> dict[str, list[tuple[str, str, Path]]]:
    """正規化したページ名 → [(wiki名, タイトル, ファイル)]。

    「チップ/バッファーシールド」は「バッファーシールド」で引ける。未取得のwikiは飛ばす。
    """
    keys: dict[str, list[tuple[str, str, Path]]] = {}
    for label, folder in WIKI_SOURCES:
        index_path = data_dir() / folder / "index.json"
        if not index_path.exists():
            continue
        for title, rel in json.loads(index_path.read_text(encoding="utf-8")).items():
            entry = (label, title, index_path.parent / rel)
            keys.setdefault(_norm(title.split("/")[-1]), []).append(entry)
    return keys


def find_pages(query: str, limit: int = 3) -> list[tuple[str, str, Path]]:
    keys = _wiki_keys()
    q = _norm(query)
    if not q:
        return []
    if q in keys:
        return keys[q][:limit]
    partial = [k for k in keys if q in k or k in q]
    if partial:
        partial.sort(key=lambda k: abs(len(k) - len(q)))
        return [e for k in partial for e in keys[k]][:limit]
    close = difflib.get_close_matches(q, list(keys), n=limit, cutoff=0.6)
    return [e for k in close for e in keys[k]][:limit]


@lru_cache(maxsize=1)
def name_map() -> dict[str, dict[str, str]]:
    """正規化した日本語名 → {"ja","en","zh"}(knowledge/name_map.json)。"""
    data = json.loads(_read("name_map.json"))
    return {_norm(row["ja"]): row for cat, rows in data.items() if not cat.startswith("_") for row in rows}


@lru_cache(maxsize=1)
def _overrides() -> dict:
    return json.loads(_read("stat_overrides.json"))


def override_note(ja_name: str) -> str:
    """wiki間で食い違う数値について、採用する値(高い方)を示す注記。該当しなければ空文字。"""
    data = _overrides()
    key = _norm(ja_name)
    lines = []
    for name, entry in data["monster"].items():
        if _norm(name) == key:
            stats = " / ".join(f"{d}: 攻撃{v[0]} 防御{v[1]} HP{v[2]}" for d, v in entry["stats"].items())
            lines.append(f"{name} のステータス(wiki間で食い違うため、各項目の高い方を採用): {stats}")
    for name, text in data["effect_notes"].items():
        if _norm(name) == key:
            lines.append(f"{name}: {text}")
    if not lines:
        return ""
    return "【数値の補正(以下のwiki本文よりこちらを優先。ゲーム画面と違えば画面が正)】\n" + "\n".join(lines)


def _translated_pages(ja_name: str) -> list[tuple[str, str, Path]]:
    """日本語名に対応する英語・中国語wikiのページ(取得済みのものだけ)。"""
    row = name_map().get(_norm(ja_name))
    if row is None:
        return []
    pages = []
    for lang, (label, folder) in (("en", WIKI_SOURCES[1]), ("zh", WIKI_SOURCES[2])):
        index_path = data_dir() / folder / "index.json"
        if lang in row and index_path.exists():
            rel = json.loads(index_path.read_text(encoding="utf-8")).get(row[lang])
            if rel:
                pages.append((label, row[lang], index_path.parent / rel))
    return pages


def _page_text(label: str, title: str, path: Path, limit: int) -> str:
    body = path.read_text(encoding="utf-8")
    if len(body) > limit:
        body = body[:limit] + "\n…(以下省略)"
    return f"【{label}: {title}】\n{body}"


CATALOG_KINDS = {"card": "カード", "chip": "チップ", "monster": "モンスター", "tile": "マス", "event": "イベント"}


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict[str, dict]]:
    """カード・チップ・モンスター・マス・イベントの一覧(knowledge/catalog.json)。"""
    data = json.loads(_read("catalog.json"))
    return {kind: entries for kind, entries in data.items() if not kind.startswith("_")}


# ゲーム画面(Steam版)とwikiで呼び名が違うもの。画面の呼び名 → wikiの呼び名
SCREEN_NAMES = {"ガード": "防御"}


def find_catalog(query: str, limit: int = 3) -> list[tuple[str, str, dict]]:
    """名前(日本語・英語・中国語。一部でもよい)で一覧を引く。[(種類, 名前, 項目)]"""
    for screen, wiki in SCREEN_NAMES.items():
        query = query.replace(screen, wiki)
    q = _norm(query)
    if not q:
        return []
    exact, partial = [], []
    for kind, entries in catalog().items():
        for name, entry in entries.items():
            names = [_norm(name), _norm(name.split("(")[0])] + [_norm(entry.get(k, "")) for k in ("en", "zh")]
            if q in names:
                exact.append((kind, name, entry))
            elif any(n and (q in n or n in q) for n in names):
                partial.append((kind, name, entry))
    partial.sort(key=lambda hit: abs(len(hit[1]) - len(query)))
    return (exact + partial)[:limit]


def catalog_text(kind: str, name: str, entry: dict) -> str:
    lines = [f"【{CATALOG_KINDS[kind]}: {name}】" + (f"(英: {entry['en']})" if entry.get("en") else "")]
    lines.append(entry["summary"])
    facts = []
    for key, label in (("type", "種類"), ("cost", "コスト"), ("availability", "入手"), ("rarity", "レアリティ"),
                       ("stack", "属性"), ("class", "区分"), ("coin", "撃破コイン")):
        if key in entry:
            facts.append(f"{label}: {entry[key]}")
    if facts:
        lines.append("、".join(facts))
    if entry.get("stats"):
        lines.append(
            "ステータス(進捗イベントで増える): "
            + " / ".join(f"{d} 攻{v['攻撃']} 防{v['防御']} HP{v['HP']}" for d, v in entry["stats"].items())
        )
    if entry.get("note"):
        lines.append(entry["note"])
    return "\n".join(lines)


@lru_cache(maxsize=1)
def map_guides() -> dict[str, str]:
    """マップ名 → マップ別の攻略データ(knowledge/map_guide.md の節)。"""
    text = _read("map_guide.md")
    guides = {}
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        guides[title.strip()] = f"## {title.strip()}\n{body.strip()}"
    return guides


def map_guide(name: str | None) -> str:
    """マップ名(「異変図書館(協力チャレンジ)」「魔法学院(スイーツ場)/前半」なども可)に合う攻略データ。なければ空文字。"""
    if not name:
        return ""
    target = _norm(name.split("/")[0])
    for title, body in map_guides().items():
        keys = [_norm(k.split("(")[0]) for k in re.split(r"[・]", title)]
        if any(k and (k in target or target in k) for k in keys):
            return body
    return ""


def lookup(query: str) -> str:
    """名前から、キャラ要約・カード/チップ/敵などの一覧・マップ別の攻略データを返す。

    同梱のデータで見つからず、wikiデータ(data/)を取得してあれば、wikiのページ本文も探す。
    """
    parts: list[str] = []
    q = _norm(query)
    for ch in characters():
        if q and (q == _norm(ch["name"]) or q == _norm(ch.get("en", "")) or q in _norm(ch["name"])):
            parts.append("【キャラ要約】\n" + json.dumps(ch, ensure_ascii=False))
            break
    parts.extend(catalog_text(kind, name, entry) for kind, name, entry in find_catalog(query))
    guide = map_guide(query)
    if guide:
        parts.append("【マップ別の攻略データ】\n" + guide)
    if parts:
        return "\n\n".join(parts)
    seen: set[Path] = set()
    hits = find_pages(query)
    ja_names = [title.split("/")[-1] for label, title, _ in hits if label == WIKI_SOURCES[0][0]] or [query]
    for ja_name in ja_names:
        note = override_note(ja_name)
        if note:
            parts.append(note)
    for label, title, path in hits:
        if path.exists() and path not in seen:
            seen.add(path)
            limit = MAP_ENTRY_MAX_CHARS if title.startswith("PvE/") else WIKI_ENTRY_MAX_CHARS
            parts.append(_page_text(label, title, path, limit))
    for ja_name in ja_names:
        for label, title, path in _translated_pages(ja_name):
            if path.exists() and path not in seen:
                seen.add(path)
                parts.append(_page_text(label, title, path, TRANSLATED_ENTRY_MAX_CHARS))
    if not parts:
        hint = "" if _wiki_keys() else "(wikiデータ未取得。scripts/scrape_wiki.py を実行すると検索できる)"
        return f"「{query}」に該当する項目は見つからなかった。{hint}"
    return "\n\n".join(parts)


def character_table() -> str:
    """システムプロンプト用のキャラ要約。"""
    blocks = []
    for ch in characters():
        stats = " / ".join(f"Lv{i}:HP{s[0]} 攻{s[1]} 防{s[2]} 移動+{s[3]}" for i, s in enumerate(ch["stats"]))
        lines = [
            f"### {ch['name']}({ch['role']}、推奨手番: {ch['order']}、初期コイン{ch['coins']}"
            + (f"、Lv1到達時+{ch['lv1_refund']}コイン" if ch.get("lv1_refund") else "")
            + ")",
            f"ステータス: {stats}",
            f"スキル: {ch['skill']}",
        ]
        if ch.get("passive"):
            lines.append(f"パッシブ: {ch['passive']}")
        if ch.get("potential"):
            lines.append(f"潜在解放: {ch['potential']}")
        if ch.get("play"):
            lines.append("立ち回り: " + " / ".join(ch["play"]))
        if ch.get("chips"):
            lines.append("相性の良いチップ: " + "、".join(ch["chips"]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


@lru_cache(maxsize=16)
def reference_text(map_name: str | None = None) -> str:
    """毎回同じ内容になる参照資料(プロンプトキャッシュの対象)。マップが分かれば、そのマップの攻略データも入れる。"""
    guide = map_guide(map_name)
    return "\n\n".join(
        part
        for part in [
            _read("rules.md"),
            _read("strategy.md"),
            _read("advanced.md"),
            "# このマップの攻略データ\n\n" + guide if guide else "",
            "# キャラクター要約\n\n" + character_table(),
            "# イベントマスの発生傾向(有志の実測。進捗=画面上部の進捗バーの値)\n\n" + events.event_reference(),
        ]
        if part
    )
