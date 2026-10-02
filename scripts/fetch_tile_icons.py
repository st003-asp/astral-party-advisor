"""マスのアイコン画像を英語wiki(astralparty.miraheze.org)から取得する。

    python scripts/fetch_tile_icons.py

保存先は data/tile_icons/<マスの種類>.png。講評のとき「マスの絵柄の見本」としてAIに渡し、
移動の点線が通るマスを読み取って盤面と照らし合わせるのに使う。
アイコンはゲームの素材なので、data/ 以下(.gitignore 済み)に置き、再配布しない。
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

API = "https://astralparty.miraheze.org/w/api.php"
HEADERS = {"User-Agent": "astral-party-advisor (tile icon fetch; personal use)"}
OUT = Path(__file__).resolve().parent.parent / "data" / "tile_icons"

# マスの種類(盤面データ maps.json での呼び名) → 英語wikiのファイル名
ICONS = {
    "イベント": "EventPlatform.png",
    "転送陣": "PortalPlatform.png",
    "災厄": "DamagePlatform.png",
    "リカバリー": "PlatformRecover.png",
    "モンスター突撃ゲート": "AssaultPlatform.png",
    "金儲け": "WindfallHitsPlatform.png",
    "カード報酬": "CardBouncePlatform.png",
    "モンスター": "PlatformMonster.png",
    "疾走": "HastePlatform.png",
    "ショップ": "ShopPlatform.png",
    "強化チップショップ": "PlatformChipShop.png",
    "セーフティポイント": "CheckPointPlatform.png",
    "怪奇飴のガチャガチャ": "PlatformQuirkyCandyMachine.png",
}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)
    params = {
        "action": "query",
        "format": "json",
        "prop": "imageinfo",
        "iiprop": "url|size",
        "titles": "|".join(f"File:{name}" for name in ICONS.values()),
    }
    pages = session.get(API, params=params, timeout=30).json()["query"]["pages"].values()
    urls = {p["title"].removeprefix("File:"): p["imageinfo"][0]["url"] for p in pages if "imageinfo" in p}
    failed = 0
    for kind, name in ICONS.items():
        target = OUT / f"{kind}.png"
        if target.exists():
            print(f"- {kind}: 取得済み")
            continue
        if name not in urls:
            print(f"- {kind}: wikiに {name} が見つかりません")
            failed += 1
            continue
        response = session.get(urls[name], timeout=30)
        response.raise_for_status()
        target.write_bytes(response.content)
        print(f"- {kind}: {name}({len(response.content):,} バイト)")
    print(f"→ {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
