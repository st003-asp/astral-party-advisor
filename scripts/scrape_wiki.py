"""日本語wiki(wikiwiki.jp/tenoujigaoh)の本文をローカルに保存する。

    python scripts/scrape_wiki.py            # 未取得のページだけ取得
    python scripts/scrape_wiki.py --refresh  # 全ページ取り直し

保存先は data/wiki/<カテゴリ>/<ページ名>.md と data/wiki/index.json。
wikiの文章は各執筆者の著作物なので、このフォルダは .gitignore 済み(再配布しない)。
サーバーに負荷をかけないよう1ページごとに数秒待つ。全部で30分ほどかかる。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://wikiwiki.jp/tenoujigaoh/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}
CATEGORIES = ("キャラクター", "カード", "チップ", "モンスター", "マス", "イベント", "PvE", "基礎知識", "用語集", "よくある質問")
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "wiki"
DELAY_SEC = 4.0


def fetch(url: str, session: requests.Session) -> str:
    for attempt in range(6):
        resp = session.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 0)) or 60 * (attempt + 1)
            print(f"  429 → {wait}秒待機", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.text
    raise RuntimeError(f"取得できませんでした: {url}")


def list_pages(session: requests.Session) -> list[str]:
    soup = BeautifulSoup(fetch(BASE + "?cmd=list", session), "html.parser")
    body = soup.select_one("#content") or soup
    titles = []
    for a in body.select("a"):
        if not a.get("href", "").startswith("/tenoujigaoh/"):
            continue
        title = a.get_text(strip=True)
        if title.split("/")[0] in CATEGORIES:
            titles.append(title)
    return sorted(set(titles))


def table_to_text(table) -> str:
    lines = []
    for tr in table.find_all("tr"):
        cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def page_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("#content") or soup.select_one("#body") or soup
    for junk in body.select("script, style, .ad, ins, iframe, form"):
        junk.decompose()
    for table in body.find_all("table"):
        if table.find_parent("table") is None:
            table.replace_with(soup.new_string("\n" + table_to_text(table) + "\n"))
    for img in body.find_all("img"):
        alt = img.get("alt") or img.get("title") or ""
        img.replace_with(soup.new_string(f"[{alt}]" if alt and not alt.endswith((".png", ".jpg", ".webp")) else ""))
    for tag in body.find_all(re.compile(r"^h[1-6]$")):
        tag.insert_before(soup.new_string("\n## "))
    for tag in body.find_all(["li", "p", "br", "div", "dt", "dd"]):
        tag.insert_before(soup.new_string("\n"))
    text = body.get_text("")
    text = re.sub(r"[ \t　]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def safe_name(title: str) -> str:
    return re.sub(r'[\\:*?"<>|]', "_", title.split("/", 1)[1] if "/" in title else title).replace("/", "__")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="取得済みのページも取り直す")
    parser.add_argument("--only", help="このカテゴリだけ取得する(例: チップ)")
    args = parser.parse_args()

    session = requests.Session()
    titles = list_pages(session)
    if args.only:
        titles = [t for t in titles if t.split("/")[0] == args.only]
    print(f"{len(titles)} ページ", flush=True)

    index_path = OUT_DIR / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    for i, title in enumerate(titles, 1):
        category = title.split("/")[0]
        path = OUT_DIR / category / f"{safe_name(title)}.md"
        if path.exists() and not args.refresh:
            index.setdefault(title, str(path.relative_to(OUT_DIR)).replace("\\", "/"))
            continue
        time.sleep(DELAY_SEC)
        try:
            text = page_to_text(fetch(BASE + urllib.parse.quote(title), session))
        except Exception as exc:  # 1ページの失敗で全体を止めない
            print(f"[{i}/{len(titles)}] 失敗 {title!r}: {exc}", flush=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n\n{text}\n", encoding="utf-8")
        index[title] = str(path.relative_to(OUT_DIR)).replace("\\", "/")
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{i}/{len(titles)}] ok ({len(text)}字)", flush=True)

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完了: {len(index)} ページ → {OUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
