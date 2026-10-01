"""録画から「画面が変わった瞬間」のフレームだけを取り出す。

一定間隔でフレームを見て、直前に採用したフレームとほぼ同じ画面は捨てる。
選択待ちで画面が止まっている間は1枚だけ残るので、Claude に見せる枚数を大きく減らせる。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class Frame:
    index: int  # 採用フレームの通し番号
    time_sec: float
    file: str  # out_dir からの相対パス

    @property
    def timestamp(self) -> str:
        m, s = divmod(int(self.time_sec), 60)
        return f"{m:02d}:{s:02d}"


def _signature(image: np.ndarray) -> np.ndarray:
    small = cv2.resize(image, (160, 90), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.int16)


def extract_keyframes(
    video: Path,
    out_dir: Path,
    *,
    interval: float = 2.0,
    diff_threshold: float = 4.0,
    start: float = 0.0,
    end: float | None = None,
    max_frames: int | None = None,
) -> list[Frame]:
    """interval 秒ごとに画面を見て、変化があったフレームを out_dir/frames に保存する。

    diff_threshold: 縮小グレースケール画像の平均絶対差(0〜255)。これ以下なら同じ画面とみなす。
    """
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise OSError(
            f"動画を開けませんでした: {video}\n"
            "パスに日本語が含まれていると失敗することがあります。英数字だけのパスに移して試してください。"
        )
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, round(fps * interval))
    first = int(start * fps)
    last = total if end is None else min(total, int(end * fps))

    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    kept: list[Frame] = []
    previous: np.ndarray | None = None
    pos = first
    cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
    while pos < last:
        ok, image = cap.read()
        if not ok:
            break
        sig = _signature(image)
        if previous is None or float(np.abs(sig - previous).mean()) > diff_threshold:
            previous = sig
            name = f"frames/f{len(kept):05d}.jpg"
            ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not ok:
                raise OSError("フレームのJPEG変換に失敗しました")
            (out_dir / name).write_bytes(buf.tobytes())  # imwrite は日本語パスで失敗するため
            kept.append(Frame(len(kept), pos / fps, name))
            if max_frames is not None and len(kept) >= max_frames:
                break
        # 次のサンプル位置まで読み飛ばす(seek より grab のほうが多くのコーデックで正確)
        for _ in range(step - 1):
            if not cap.grab():
                break
        pos += step
    cap.release()

    manifest = {
        "video": str(video),
        "fps": fps,
        "interval": interval,
        "diff_threshold": diff_threshold,
        "frames": [asdict(f) for f in kept],
    }
    (out_dir / "frames.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return kept


def load_frames(out_dir: Path) -> list[Frame] | None:
    path = out_dir / "frames.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Frame(**f) for f in data["frames"]]


# ---------------------------------------------------------------------------
# ゲーム内リプレイ再生の録画: 「誰の手番か」で区間に分ける
#   A) 再生バーが出ている録画 … バーの手番アイコンで区切る
#   B) 再生バーを畳んだ録画(視点が手番プレイヤーを追う) … 左下の立ち絵で区切る
# ---------------------------------------------------------------------------
# 画面サイズに対する割合 (x0, y0, x1, y1)
_LABEL_BOX = (0.062, 0.845, 0.185, 0.912)  # 再生バー左のラベル「ラウンドN [アイコン]のターン…」のピンク地
_ICON_BOX = (0.196, 0.848, 0.232, 0.908)  # 手番キャラのドット絵アイコン
_MENU_BOX = (0.006, 0.012, 0.038, 0.066)  # 左上のメニューボタン(マップ画面でだけ出る)
_PORTRAIT_BOX = (0.03, 0.74, 0.15, 0.90)  # 左下の立ち絵の顔のあたり


@dataclass
class TurnSegment:
    """同じ手番が続く区間。cluster は手番の目印(アイコン/立ち絵)の種類ごとの通し番号。"""

    index: int
    cluster: int
    frames: list[Frame]

    @property
    def id(self) -> str:
        return f"t{self.index:03d}"


def _crop(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = box
    return image[int(y0 * h) : int(y1 * h), int(x0 * w) : int(x1 * w)]


def _signature(patch: np.ndarray) -> np.ndarray:
    return cv2.resize(patch, (24, 24), interpolation=cv2.INTER_AREA).astype(np.int16)


def turn_icon(image: np.ndarray) -> np.ndarray | None:
    """再生バーが出ていれば手番アイコンの特徴量を返す。出ていなければ None。"""
    b, g, r = _crop(image, _LABEL_BOX).reshape(-1, 3).mean(axis=0)
    if not (r > 180 and b > 120 and g < 130):  # ラベルのピンク色
        return None
    return _signature(_crop(image, _ICON_BOX))


def turn_portrait(image: np.ndarray) -> np.ndarray | None:
    """マップ画面(左上にメニューボタンがある)なら左下の立ち絵の特徴量を返す。戦闘画面などでは None。"""
    gray = cv2.cvtColor(_crop(image, _MENU_BOX), cv2.COLOR_BGR2GRAY)
    bright, dark = float((gray > 200).mean()), float((gray < 70).mean())
    if not (0.10 < bright < 0.30 and dark > 0.25):  # 白い三本線と黒い地
        return None
    return _signature(_crop(image, _PORTRAIT_BOX))


def _cluster(signatures: list[np.ndarray | None], threshold: float) -> list[int]:
    """特徴量の近いもの同士に同じ番号を付ける。None は -1。"""
    centroids: list[np.ndarray] = []
    out = []
    for sig in signatures:
        if sig is None:
            out.append(-1)
            continue
        distances = [float(np.abs(sig - c).mean()) for c in centroids]
        if distances and min(distances) < threshold:
            out.append(int(np.argmin(distances)))
        else:
            centroids.append(sig)
            out.append(len(centroids) - 1)
    return out


def segment_turns(out_dir: Path, frames: list[Frame]) -> list[TurnSegment]:
    """フレーム列を手番ごとの区間に分ける。

    目印が見えないフレーム(戦闘画面など)や、切り替わりの途中の一瞬だけ現れる見た目は、直前の手番に含める。
    """
    icons: list[np.ndarray | None] = []
    portraits: list[np.ndarray | None] = []
    for frame in frames:  # 画像は1枚ずつ読んで捨てる(長い録画で全部を持つとメモリが足りなくなる)
        image = cv2.imdecode(np.fromfile(str(out_dir / frame.file), dtype=np.uint8), cv2.IMREAD_COLOR)
        icons.append(turn_icon(image))
        portraits.append(turn_portrait(image))
    gaps = sorted(b.time_sec - a.time_sec for a, b in zip(frames, frames[1:]))
    spacing = max(gaps[len(gaps) // 2] if gaps else 1.0, 0.1)

    by_portrait = sum(icon is not None for icon in icons) < 0.3 * len(frames)
    if by_portrait:
        clusters = _cluster(portraits, 25.0)
        # 切り替え演出で一瞬だけ現れる見た目を手番と数えない(合計6秒ぶん未満は無視)
        min_size = max(4, round(6.0 / spacing))
    else:
        clusters, min_size = _cluster(icons, 22.0), 1
    counts: dict[int, int] = {}
    for c in clusters:
        counts[c] = counts.get(c, 0) + 1

    segments: list[TurnSegment] = []
    for frame, cluster in zip(frames, clusters):
        stable = cluster >= 0 and counts[cluster] >= min_size
        if stable and (not segments or segments[-1].cluster != cluster):
            segments.append(TurnSegment(len(segments), cluster, [frame]))
        elif segments:
            segments[-1].frames.append(frame)
    if by_portrait:
        segments = _absorb_cut_ins(segments, max_frames=round(4.0 / spacing))
    return segments


def _absorb_cut_ins(segments: list[TurnSegment], max_frames: int) -> list[TurnSegment]:
    """「LV UP」のように立ち絵を数秒だけ隠す演出を、手番の切り替わりと数えないようにする。

    どの区間も短い(max_frames 以下)見た目は演出とみなし、直前の手番に含める。
    その結果、同じ手番が続いたら1つにまとめる。
    """
    longest: dict[int, int] = {}
    for s in segments:
        longest[s.cluster] = max(longest.get(s.cluster, 0), len(s.frames))
    merged: list[TurnSegment] = []
    for s in segments:
        cut_in = longest[s.cluster] <= max_frames
        if merged and (cut_in or merged[-1].cluster == s.cluster):
            merged[-1].frames.extend(s.frames)
        elif not cut_in:
            merged.append(TurnSegment(len(merged), s.cluster, list(s.frames)))
    return merged


def pick_key_frames(out_dir: Path, frames: list[Frame], limit: int) -> list[Frame]:
    """1手番のフレームから、画面の内容が互いに違うものを limit 枚選ぶ(時刻順で返す)。

    等間隔に間引くと、短い戦闘画面やカード表示を取りこぼす。ここでは
    「すでに選んだどの画面とも似ていないフレーム」を1枚ずつ足していくので、
    カード使用・戦闘の前後・ショップなど、場面が変わったところが優先して残る。
    切り替え演出の途中(前後どちらのフレームとも大きく違う)は、情報が少ないので後回しにする。
    """
    if len(frames) <= limit:
        return list(frames)
    sigs = []
    for f in frames:
        image = cv2.imdecode(np.fromfile(str(out_dir / f.file), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        sigs.append(cv2.resize(image, (48, 27), interpolation=cv2.INTER_AREA).astype(np.int16))
    n = len(frames)

    def dist(i: int, j: int) -> float:
        return float(np.abs(sigs[i] - sigs[j]).mean())

    step = [dist(i, i + 1) for i in range(n - 1)]
    busy = float(np.percentile(step, 60)) if step else 0.0
    # 前後どちらとも大きく違うフレーム = 演出の途中
    settled = [
        i for i in range(n) if min(step[i - 1] if i > 0 else 1e9, step[i] if i < n - 1 else 1e9) <= busy
    ]
    chosen = [0, n - 1]
    # 場面が切り替わる直前のフレームを先に確保する。ダメージの数字や「倒されました」などの
    # 結果表示は場面の最後に出るので、ここを落とすと何が起きたか分からなくなる
    cut = float(np.percentile(step, 80)) if step else 0.0
    scene_ends = sorted((i for i in settled if i < n - 1 and step[i] > cut), key=lambda i: -step[i])
    for i in scene_ends[: limit // 2]:
        if i not in chosen:
            chosen.append(i)
    nearest = [min(dist(i, j) for j in chosen) for i in range(n)]
    for pool in (settled, list(range(n))):
        while len(chosen) < limit:
            candidates = [i for i in pool if i not in chosen]
            if not candidates:
                break
            best = max(candidates, key=lambda i: nearest[i])
            chosen.append(best)
            nearest = [min(nearest[i], dist(i, best)) for i in range(n)]
    return [frames[i] for i in sorted(chosen)]
