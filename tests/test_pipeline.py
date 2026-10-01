"""API を呼ばずに、動画→分類→助言→レポートの流れを偽のクライアントで通す。"""

import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from apadvisor.analyze import analyze
from apadvisor.llm import Advisor, Refused
from apadvisor.report import write_html, write_markdown
from apadvisor.schemas import SceneInfo
from apadvisor.video import extract_keyframes

USAGE = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)

ADVICE_JSON = json.dumps(
    {
        "decision_type": "battle_defense",
        "situation": "ミサキ HP1。敵の出目3",
        "unreadable": [],
        "recommended": "回避",
        "reasoning": "HP1では防御しても倒れる。回避成功率50%",
        "options": [{"option": "回避", "evaluation": "生存50%"}, {"option": "防御", "evaluation": "生存0%"}],
        "actual_action": "防御",
        "verdict": "悪手",
        "confidence": "高",
    },
    ensure_ascii=False,
)


def make_video(path, colors, seconds_each=2, fps=10):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 180))
    assert writer.isOpened()
    for color in colors:
        frame = np.full((180, 320, 3), color, dtype=np.uint8)
        for _ in range(seconds_each * fps):
            writer.write(frame)
    writer.release()


class FakeMessages:
    """classify は2枚目だけ判断場面にし、advise は1回ツールを呼んでから答える。"""

    def __init__(self):
        self.classify_calls = 0
        self.create_calls = []

    def parse(self, **kwargs):
        assert kwargs["output_format"] is SceneInfo
        self.classify_calls += 1
        is_decision = self.classify_calls == 2
        scene = SceneInfo(
            decision_type="battle_defense" if is_decision else "none",
            is_my_decision=is_decision,
            whose_turn="me" if is_decision else "enemy",
            summary=f"画面{self.classify_calls}",
        )
        return SimpleNamespace(stop_reason="end_turn", parsed_output=scene, usage=USAGE)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if len(self.create_calls) == 1:
            block = SimpleNamespace(
                type="tool_use",
                id="tu_1",
                name="defense_odds",
                input={"enemy_atk": 6, "attacker_die": 3, "my_def": 2, "my_hp": 1},
            )
            return SimpleNamespace(stop_reason="tool_use", content=[block], usage=USAGE)
        text = SimpleNamespace(type="text", text=ADVICE_JSON)
        return SimpleNamespace(stop_reason="end_turn", content=[text], usage=USAGE)


def fake_advisor():
    messages = FakeMessages()
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return Advisor(client=client), messages


def test_extract_keyframes_drops_duplicates(tmp_path):
    video = tmp_path / "replay.mp4"
    make_video(video, [(0, 0, 0), (0, 0, 0), (255, 255, 255), (0, 0, 255)])
    frames = extract_keyframes(video, tmp_path / "out", interval=1.0)
    assert len(frames) == 3  # 黒が続く区間は1枚にまとまる
    assert [round(f.time_sec) for f in frames] == [0, 4, 6]
    assert all((tmp_path / "out" / f.file).stat().st_size > 0 for f in frames)


def test_japanese_output_path(tmp_path):
    video = tmp_path / "replay.mp4"
    make_video(video, [(0, 0, 0), (255, 255, 255)])
    out = tmp_path / "解析結果"
    frames = extract_keyframes(video, out, interval=1.0)
    assert (out / frames[0].file).exists()


def test_full_pipeline_with_fake_client(tmp_path):
    video = tmp_path / "replay.mp4"
    make_video(video, [(0, 0, 0), (255, 255, 255), (0, 0, 255)])
    out = tmp_path / "out"
    advisor, messages = fake_advisor()
    logs = []
    decisions, advice = analyze(video, out, advisor, base_context="録画者の使用キャラ: ミサキ", interval=1.0, log=logs.append)

    assert len(decisions) == 1 and decisions[0].decision_type == "battle_defense"
    assert advice[decisions[0].id].verdict == "悪手"

    # 2回目のリクエストに、ツール結果(回避が推奨)が tool_result として返っている
    second = messages.create_calls[1]["messages"]
    assert second[1]["role"] == "assistant"
    result = second[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1" and not result["is_error"]
    assert json.loads(result["content"])["recommended"]["action"] == "回避"

    # 判断場面の画像1枚 + このあとの画面1枚、補足情報にキャラ名
    first_content = messages.create_calls[0]["messages"][0]["content"]
    assert sum(1 for b in first_content if b["type"] == "image") == 2
    assert "ミサキ" in first_content[-1]["text"]
    # 参照資料はキャッシュ対象
    assert messages.create_calls[0]["system"][-1]["cache_control"]["type"] == "ephemeral"

    html = write_html(out, decisions, advice, "テスト").read_text(encoding="utf-8")
    assert "悪手" in html and "回避" in html
    assert "回避" in write_markdown(out, decisions, advice, "テスト").read_text(encoding="utf-8")

    # 再実行では API を呼ばない(キャッシュから再開)
    calls = (messages.classify_calls, len(messages.create_calls))
    analyze(video, out, advisor, interval=1.0, log=logs.append)
    assert (messages.classify_calls, len(messages.create_calls)) == calls


def test_refusal_is_reported(tmp_path):
    image = tmp_path / "a.jpg"
    cv2.imencode(".jpg", np.zeros((90, 160, 3), np.uint8))[1].tofile(str(image))

    class Refusing:
        def create(self, **kwargs):
            return SimpleNamespace(
                stop_reason="refusal", stop_details=SimpleNamespace(explanation="拒否"), content=[], usage=USAGE
            )

    advisor = Advisor(client=SimpleNamespace(beta=SimpleNamespace(messages=Refusing())))
    with pytest.raises(Refused):
        advisor.advise([image])
