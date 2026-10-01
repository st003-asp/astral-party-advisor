"""録画1本を解析して、判断場面ごとの助言を作る。

流れ: フレーム抽出 → 各フレームを分類 → 自分の判断場面をまとめる → 場面ごとに助言。
途中結果は out_dir の JSON に保存するので、中断しても続きから再開できる(API料金を二重に払わない)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .llm import Advisor, Refused
from .schemas import Advice, SceneInfo
from .video import Frame, extract_keyframes, load_frames

Progress = Callable[[str], None]


@dataclass
class Decision:
    """連続した同じ種類の判断フレームをまとめた1場面。"""

    id: str
    decision_type: str
    frames: list[Frame]
    after: list[Frame] = field(default_factory=list)
    recent: list[str] = field(default_factory=list)  # 直前の場面の要約

    @property
    def timestamp(self) -> str:
        return self.frames[0].timestamp


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def classify_frames(advisor: Advisor, out_dir: Path, frames: list[Frame], log: Progress) -> dict[str, SceneInfo]:
    cache_path = out_dir / "scenes.json"
    cache = _load(cache_path)
    for i, frame in enumerate(frames, 1):
        if frame.file in cache:
            continue
        try:
            scene = advisor.classify(out_dir / frame.file)
        except Refused as exc:
            log(f"  [{frame.timestamp}] 分類を拒否されたためスキップ: {exc}")
            scene = SceneInfo(decision_type="none", is_my_decision=False, whose_turn="unknown", summary="(分類不能)")
        cache[frame.file] = scene.model_dump()
        _save(cache_path, cache)
        mark = "★" if scene.is_my_decision else " "
        log(f"  [{i}/{len(frames)}] {frame.timestamp} {mark} {scene.decision_type}: {scene.summary}")
    return {f.file: SceneInfo(**cache[f.file]) for f in frames}


def group_decisions(frames: list[Frame], scenes: dict[str, SceneInfo], after_count: int = 2) -> list[Decision]:
    """自分の判断場面のフレームを、連続する同種ごとに1場面へまとめる。"""
    decisions: list[Decision] = []
    current: Decision | None = None
    for i, frame in enumerate(frames):
        scene = scenes[frame.file]
        is_decision = scene.is_my_decision and scene.decision_type != "none"
        if is_decision and current is not None and current.decision_type == scene.decision_type:
            current.frames.append(frame)
            continue
        if current is not None:
            current.after = frames[i : i + after_count]
            decisions.append(current)
            current = None
        if is_decision:
            recent = [f"{f.timestamp} {scenes[f.file].summary}" for f in frames[max(0, i - 5) : i]]
            current = Decision(f"d{frame.index:05d}", scene.decision_type, [frame], recent=recent)
    if current is not None:
        decisions.append(current)
    return decisions


def _context(decision: Decision, base_context: str) -> str:
    lines = [base_context.strip()] if base_context.strip() else []
    lines.append(f"動画内の時刻: {decision.timestamp}")
    if decision.recent:
        lines.append("直前の画面の流れ:\n" + "\n".join(f"- {r}" for r in decision.recent))
    return "\n".join(lines)


def advise_decisions(
    advisor: Advisor,
    out_dir: Path,
    decisions: list[Decision],
    base_context: str,
    log: Progress,
    *,
    effort: str = "high",
) -> dict[str, Advice]:
    cache_path = out_dir / "advice.json"
    cache = _load(cache_path)
    for i, decision in enumerate(decisions, 1):
        if decision.id in cache:
            continue
        # 同じ場面が長く続いたときは、選択肢が出そろった最後のフレームを主に、最初のフレームも添える
        shown = decision.frames[-1:] if len(decision.frames) == 1 else [decision.frames[0], decision.frames[-1]]
        try:
            advice = advisor.advise(
                [out_dir / f.file for f in shown],
                after_frames=[out_dir / f.file for f in decision.after],
                context=_context(decision, base_context),
                effort=effort,
            )
        except Refused as exc:
            log(f"  [{i}/{len(decisions)}] {decision.timestamp} 拒否されたためスキップ: {exc}")
            continue
        cache[decision.id] = advice.model_dump()
        _save(cache_path, cache)
        log(f"  [{i}/{len(decisions)}] {decision.timestamp} {advice.decision_type}: {advice.verdict} → {advice.recommended}")
    return {d.id: Advice(**cache[d.id]) for d in decisions if d.id in cache}


def analyze(
    video: Path,
    out_dir: Path,
    advisor: Advisor,
    *,
    base_context: str = "",
    interval: float = 2.0,
    diff_threshold: float = 4.0,
    start: float = 0.0,
    end: float | None = None,
    max_frames: int | None = None,
    max_decisions: int | None = None,
    effort: str = "high",
    frames_only: bool = False,
    log: Progress = print,
) -> tuple[list[Decision], dict[str, Advice]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = load_frames(out_dir)
    if frames is None:
        log("フレームを抽出中…")
        frames = extract_keyframes(
            video, out_dir, interval=interval, diff_threshold=diff_threshold, start=start, end=end, max_frames=max_frames
        )
    log(f"フレーム {len(frames)} 枚")
    if frames_only:
        return [], {}

    log("画面を分類中…")
    scenes = classify_frames(advisor, out_dir, frames, log)
    decisions = group_decisions(frames, scenes)
    if max_decisions is not None:
        decisions = decisions[:max_decisions]
    log(f"自分の判断場面 {len(decisions)} 件。助言を作成中…")
    advice = advise_decisions(advisor, out_dir, decisions, base_context, log, effort=effort)
    _save(out_dir / "usage.json", advisor.usage.as_dict())
    return decisions, advice
