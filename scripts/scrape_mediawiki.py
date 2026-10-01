"""英語wiki・中国語wiki(いずれも MediaWiki)の本文を API でまとめて取得する。

    python scripts/scrape_mediawiki.py          # 全部
    python scripts/scrape_mediawiki.py en       # 英語wiki (astralparty.miraheze.org)
    python scripts/scrape_mediawiki.py zh       # 中国語wiki (astralparty.wiki.gg/zh)
    python scripts/scrape_mediawiki.py zh_bili  # 中国語wiki (wiki.biligame.com/starengine)

保存先は data/wiki_<名前>/(wikiテキストのまま)。data/ は .gitignore 済みで再配布しない。
API は50ページずつ返すので、数十リクエストで終わる。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

SITES = {
    "en": "https://astralparty.miraheze.org/w/api.php",
    "zh": "https://astralparty.wiki.gg/zh/api.php",
    "zh_bili": "https://wiki.biligame.com/starengine/api.php",
}
# biligame はブラウザ風のヘッダーでないと接続を切られる
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
}
# 判断に関係しないページ(音声、ギャラリー、ガチャ、イベント告知など)
SKIP = re.compile(
    r"/(Audio|Gallery|Unlocks|Story|画廊|语音)$"
    r"|^(Clear Pass|Gacha|Customization|Modian|Redemption|Birthdays|BGM|Music|官方公告|MediaWiki|Widget|Lua)"
    r"|公告|游戏日志|日志$",
    re.IGNORECASE,
)
BATCH = {"zh_bili": 15}  # 既定は50。biligame は大きいリクエストを 567 で弾く
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DELAY_SEC = 2.0


def api(session: requests.Session, url: str, **params) -> dict:
    params.update(format="json", formatversion="2")
    for attempt in range(5):
        resp = session.get(url, params=params, headers=HEADERS, timeout=60)
        if resp.status_code in (429, 503, 567):  # 567 は biligame のアクセス制限
            wait = 60 * (attempt + 1)
            print(f"  {resp.status_code} → {wait}秒待機", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"取得できませんでした: {url}")


def list_titles(session: requests.Session, url: str) -> list[str]:
    titles: list[str] = []
    cont: dict = {}
    while True:
        data = api(session, url, action="query", list="allpages", aplimit="500", apfilterredir="nonredirects", **cont)
        titles += [p["title"] for p in data["query"]["allpages"]]
        if "continue" not in data:
            return titles
        cont = data["continue"]
        time.sleep(DELAY_SEC)


def safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", title)


def scrape(lang: str) -> None:
    url = SITES[lang]
    out_dir = DATA_DIR / f"wiki_{lang}"
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    titles = [t for t in list_titles(session, url) if not SKIP.search(t)]
    print(f"[{lang}] {len(titles)} ページ", flush=True)

    index_path = out_dir / "index.json"
    index: dict[str, str] = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    for title in titles:  # 以前の実行で保存済みのページは取り直さない
        if title not in index and (out_dir / f"{safe_name(title)}.md").exists():
            index[title] = f"{safe_name(title)}.md"
    todo = [t for t in titles if t not in index]
    size = BATCH.get(lang, 50)
    for i in range(0, len(todo), size):
        batch = todo[i : i + size]
        data = api(
            session, url, action="query", prop="revisions", rvprop="content", rvslots="main", titles="|".join(batch)
        )
        for page in data["query"]["pages"]:
            revisions = page.get("revisions")
            if not revisions:
                continue
            text = revisions[0]["slots"]["main"]["content"]
            name = f"{safe_name(page['title'])}.md"
            (out_dir / name).write_text(f"# {page['title']}\n\n{text}\n", encoding="utf-8")
            index[page["title"]] = name
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{lang}] {len(index)}/{len(titles)}", flush=True)
        time.sleep(DELAY_SEC)

    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{lang}] 完了: {len(index)} ページ → {out_dir}", flush=True)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    langs = sys.argv[1:] or list(SITES)
    for lang in langs:
        if lang not in SITES:
            print(f"未知の指定: {lang}(指定できるのは {', '.join(SITES)})", file=sys.stderr)
            return 2
        scrape(lang)
    return 0


if __name__ == "__main__":
    sys.exit(main())
