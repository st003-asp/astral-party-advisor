"""解析結果を、人に見せられる形(プレイヤー名を伏せたHTML)に書き出す。

    apadvisor export reports/<動画名> --dest docs/sample-report

- 文章中のプレイヤー名を「プレイヤーA」〜「プレイヤーD」に置き換える
- レポートで使う画像だけをコピーし、名前が出る場所(左のプレイヤー一覧、上部の帯)を塗りつぶす
- 画像は縮小して容量を抑える
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import cv2
import numpy as np

from .analyze import Decision
from .report import write_html
from .schemas import Advice, TurnReview
from .video import load_frames

# 画面サイズに対する割合 (x0, y0, x1, y1)。リプレイ再生画面でプレイヤー名が出る場所
NAME_BOXES = (
    (0.085, 0.100, 0.215, 0.140),  # 左のプレイヤー一覧 1人目の名前
    (0.085, 0.232, 0.215, 0.268),  # 2人目
    (0.085, 0.365, 0.215, 0.400),  # 3人目
    (0.085, 0.495, 0.215, 0.530),  # 4人目
    (0.22, 0.075, 0.78, 0.190),  # 上部の帯(「〇〇ターン開始」「〇〇 防御を選びました！」など)
)
ALIASES = "ABCD"


def load_results(out_dir: Path) -> tuple[list[Decision], dict[str, Advice]]:
    """reviews.json から、レポート用の判断一覧を組み立てる(APIは呼ばない)。"""
    frames = load_frames(out_dir) or []
    by_index = {f.index: f for f in frames}
    cache = json.loads((out_dir / "reviews.json").read_text(encoding="utf-8"))
    decisions: list[Decision] = []
    advice: dict[str, Advice] = {}
    for segment_id in sorted(cache):
        entry = cache[segment_id]
        shown = [by_index[i] for i in entry["frames"]]
        review = TurnReview(**entry["review"])
        for n, item in enumerate(review.decisions):
            pos = max(1, min(item.image_number, len(shown))) - 1
            decision = Decision(f"{segment_id}-{n}", item.decision_type, [shown[pos]], after=shown[pos + 1 : pos + 2])
            decisions.append(decision)
            advice[decision.id] = item
    return decisions, advice


def player_names(out_dir: Path) -> list[str]:
    """roster.json / owners.json に記録されたプレイヤー名(手番順)。"""
    names: list[str] = []
    roster = out_dir / "roster.json"
    if roster.exists():
        names += [p["player_name"] for p in json.loads(roster.read_text(encoding="utf-8"))["players"]]
    owners = out_dir / "owners.json"
    if owners.exists():
        entries = sorted(
            (o for o in json.loads(owners.read_text(encoding="utf-8")).values() if o.get("player_name")),
            key=lambda o: o.get("turn_order") or 9,
        )
        names += [o["player_name"] for o in entries if o["player_name"] not in names]
    return names


def anonymizer(names: list[str]):
    """プレイヤー名を「プレイヤーA」などに置き換える関数を返す。長い名前から先に置き換える。"""
    table = {name: f"プレイヤー{ALIASES[i % len(ALIASES)]}" for i, name in enumerate(dict.fromkeys(names))}
    patterns = []
    for name in sorted(table, key=len, reverse=True):
        escaped = re.escape(name)
        # 英数字だけの短い名前は、別の単語の一部に当たらないよう前後が英数字でない場合だけ置き換える
        if re.fullmatch(r"[A-Za-z0-9_]+", name):
            escaped = rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"
        patterns.append((re.compile(escaped, re.IGNORECASE), table[name]))

    def replace(text: str | None) -> str | None:
        if text is None:
            return None
        for pattern, alias in patterns:
            text = pattern.sub(alias, text)
        return text

    return replace


def anonymize_advice(item: Advice, replace) -> Advice:
    data = item.model_dump()
    for key in ("situation", "recommended", "reasoning", "actual_action"):
        data[key] = replace(data[key])
    data["unreadable"] = [replace(x) for x in data["unreadable"]]
    data["options"] = [{k: replace(v) for k, v in o.items()} for o in data["options"]]
    return type(item)(**data)


def mask_names(image: np.ndarray) -> np.ndarray:
    """名前が出る場所を、元に戻せない粗いモザイクにする。"""
    h, w = image.shape[:2]
    out = image.copy()
    for x0, y0, x1, y1 in NAME_BOXES:
        xa, ya, xb, yb = int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)
        patch = out[ya:yb, xa:xb]
        tiny = cv2.resize(patch, (max(2, (xb - xa) // 48), 2), interpolation=cv2.INTER_AREA)
        out[ya:yb, xa:xb] = cv2.resize(tiny, (xb - xa, yb - ya), interpolation=cv2.INTER_LINEAR)
    return out


def export_report(out_dir: Path, dest: Path, *, title: str, extra_names: list[str] | None = None, width: int = 960) -> Path:
    decisions, advice = load_results(out_dir)
    replace = anonymizer(player_names(out_dir) + list(extra_names or []))
    advice = {key: anonymize_advice(item, replace) for key, item in advice.items()}

    (dest / "frames").mkdir(parents=True, exist_ok=True)
    used = {f.file for d in decisions for f in [d.frames[-1], *d.after[:1]]}
    for name in sorted(used):
        image = cv2.imdecode(np.fromfile(str(out_dir / name), dtype=np.uint8), cv2.IMREAD_COLOR)
        image = mask_names(image)
        if image.shape[1] > width:
            height = round(image.shape[0] * width / image.shape[1])
            image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise OSError(f"画像を書き出せませんでした: {name}")
        (dest / name).write_bytes(buf.tobytes())
    path = write_html(dest, decisions, advice, title)
    index = dest / "index.html"
    index.write_bytes(path.read_bytes())
    path.unlink()
    return index
