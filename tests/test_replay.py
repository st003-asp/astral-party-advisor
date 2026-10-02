"""リプレイ再生の録画: 手番の区切りと講評の流れを、偽のクライアントで通す。"""

import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from apadvisor.llm import Advisor
from apadvisor.replay import analyze_replay, find_my_cluster, pick_frames
from apadvisor.report import write_html
from apadvisor.schemas import Roster, RosterEntry, TurnOwner
from apadvisor.video import Frame, extract_keyframes, segment_turns

USAGE = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)
W, H = 640, 360
PINK = (200, 80, 237)  # BGR。再生バーのラベルの色


def replay_frame(icon_color, noise: int, bar: bool = True) -> np.ndarray:
    """再生バー(ピンクのラベル+手番アイコン)のある画面を作る。noise は画面中央の模様。"""
    img = np.full((H, W, 3), 40, dtype=np.uint8)
    cv2.rectangle(img, (200, 80), (440, 220), (noise, 255 - noise, noise // 2), -1)
    if bar:
        cv2.rectangle(img, (int(0.06 * W), int(0.84 * H)), (int(0.30 * W), int(0.92 * H)), PINK, -1)
        cv2.rectangle(img, (int(0.196 * W), int(0.848 * H)), (int(0.232 * W), int(0.908 * H)), icon_color, -1)
    return img


def make_replay_video(path, turns, fps=10):
    """turns: [(アイコン色, 秒数, バーあり)]。1秒ごとに中央の模様を変える。"""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    assert writer.isOpened()
    second = 0
    for color, seconds, bar in turns:
        for _ in range(seconds):
            frame = replay_frame(color, (second * 53) % 256, bar)
            for _ in range(fps):
                writer.write(frame)
            second += 1
    writer.release()


RED, GREEN, BLUE, GRAY = (0, 0, 255), (0, 255, 0), (255, 0, 0), (90, 90, 90)
TURNS = [(RED, 3, True), (GREEN, 3, True), (GREEN, 1, False), (BLUE, 2, True), (GRAY, 2, True), (RED, 2, True)]

REVIEW = {
    "round": 1,
    "turn_summary": "レーザーを使ってから移動し、ティーポットを倒した",
    "decisions": [
        {
            "decision_type": "battle_attack",
            "situation": "攻撃3、敵 防御1 HP7",
            "unreadable": [],
            "recommended": "攻撃(中)を使う",
            "reasoning": "撃破率が上がる",
            "options": [{"option": "カードなし", "evaluation": "撃破率低"}],
            "actual_action": "カードなしで攻撃",
            "verdict": "疑問手",
            "confidence": "中",
            "image_number": 2,
        }
    ],
}


class FakeMessages:
    def __init__(self):
        self.owner_calls = 0
        self.roster_calls = 0
        self.roster = Roster(found=False, players=[])
        self.create_calls = []
        self.position = {}

    def parse(self, **kwargs):
        if kwargs["output_format"] is Roster:
            self.roster_calls += 1
            return SimpleNamespace(stop_reason="end_turn", parsed_output=self.roster, usage=USAGE)
        assert kwargs["output_format"] is TurnOwner
        owners = [
            TurnOwner(kind="player", player_name="fuga", turn_order=1, character="ハンナ"),
            TurnOwner(kind="player", player_name="hogehoge", turn_order=2, character="テル"),
            TurnOwner(kind="player", player_name="piyo", turn_order=3, character=None),
            TurnOwner(kind="enemy", player_name=None, turn_order=None, character=None),
        ]
        owner = owners[self.owner_calls % len(owners)]
        self.owner_calls += 1
        return SimpleNamespace(stop_reason="end_turn", parsed_output=owner, usage=USAGE)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        task = kwargs["messages"][0]["content"][-1]["text"]
        enemy = "これは敵(モンスター)の手番です" in task
        review = dict(REVIEW, decisions=[] if enemy else REVIEW["decisions"])
        if not enemy:
            review.update(self.position)
        text = SimpleNamespace(type="text", text=json.dumps(review, ensure_ascii=False))
        return SimpleNamespace(stop_reason="end_turn", content=[text], usage=USAGE)


def test_segment_turns_by_bar_icon(tmp_path):
    video = tmp_path / "replay.mp4"
    make_replay_video(video, TURNS)
    out = tmp_path / "out"
    frames = extract_keyframes(video, out, interval=1.0, diff_threshold=2.0)
    segments = segment_turns(out, frames)
    assert [s.cluster for s in segments] == [0, 1, 2, 3, 0]  # 最後は1人目の2周目
    # バーが見えない1秒は直前(GREEN)の手番に含まれる
    assert [len(s.frames) for s in segments] == [3, 4, 2, 2, 2]


def test_pick_frames_keeps_ends():
    frames = [Frame(i, float(i), f"f{i}.jpg") for i in range(40)]
    picked = pick_frames(frames, 16)
    assert len(picked) == 16 and picked[0].index == 0 and picked[-1].index == 39
    assert pick_frames(frames[:5], 16) == frames[:5]


def test_find_my_cluster():
    owners = {
        0: TurnOwner(kind="none", player_name=None, turn_order=None, character=None),
        1: TurnOwner(kind="player", player_name="fuga", turn_order=1, character="ハンナ"),
        2: TurnOwner(kind="player", player_name="HOGEHOGE", turn_order=3, character="テル"),
    }
    assert find_my_cluster(owners, "hogehoge", None) == 2
    assert find_my_cluster(owners, None, 1) == 1
    assert find_my_cluster(owners, "テル", None) == 2
    with pytest.raises(KeyError, match="fuga"):
        find_my_cluster(owners, "だれか", None)


def test_replay_pipeline_with_fake_client(tmp_path):
    video = tmp_path / "replay.mp4"
    make_replay_video(video, TURNS)
    out = tmp_path / "out"
    messages = FakeMessages()
    advisor = Advisor(client=SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    logs = []
    decisions, advice = analyze_replay(video, out, advisor, player="hogehoge", base_context="難易度: 狂気", log=logs.append)

    assert messages.owner_calls == 4  # アイコンの種類ごとに1回だけ
    # hogehoge の手番1回 + 敵の手番1回を講評
    assert len(messages.create_calls) == 2
    my_turn = messages.create_calls[0]["messages"][0]["content"]
    assert sum(1 for b in my_turn if b["type"] == "image") == 4
    assert "対象プレイヤー: hogehoge" in my_turn[-1]["text"] and "狂気" in my_turn[-1]["text"]

    assert len(decisions) == 1 and advice[decisions[0].id].verdict == "疑問手"
    # image_number=2 → その手番の2枚目のフレーム
    assert decisions[0].frames[0].index == 4
    assert "疑問手" in write_html(out, decisions, advice, "テスト").read_text(encoding="utf-8")

    # 再実行では API を呼ばない
    analyze_replay(video, out, advisor, player="hogehoge", log=logs.append)
    assert (messages.owner_calls, len(messages.create_calls)) == (4, 2)


def test_frames_only_needs_no_client(tmp_path):
    video = tmp_path / "replay.mp4"
    make_replay_video(video, TURNS)
    logs = []
    assert analyze_replay(video, tmp_path / "out", None, frames_only=True, log=logs.append) == ([], {})
    assert any("手番の区間 5 個" in line for line in logs)


def test_roster_gives_character_from_title(tmp_path):
    video = tmp_path / "replay.mp4"
    # 冒頭2秒は再生バーのない画面(リプレイ選択画面のつもり)
    make_replay_video(video, [(GRAY, 2, False), *TURNS])
    messages = FakeMessages()
    messages.roster = Roster(
        found=True,
        players=[
            RosterEntry(player_name="fuga", label="ハンナ"),
            RosterEntry(player_name="hogehoge", label="暗躍する忍者"),
            RosterEntry(player_name="piyo", label="デスティニーガール"),  # wikiの表記は「ディスティニーガール」
            RosterEntry(player_name="foo", label="看板娘"),
        ],
    )
    advisor = Advisor(client=SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    logs = []
    analyze_replay(video, tmp_path / "out", advisor, player="hogehoge", log=logs.append)
    assert messages.roster_calls == 1
    text = messages.create_calls[0]["messages"][0]["content"][-1]["text"]
    assert "使用キャラ コマチ" in text  # FakeMessages が返す見た目からの推測(テル)ではなく、二つ名から決まる
    assert "1番手 fuga=遠野ハンナ" in text and "3番手 piyo=カイセイ" in text and "4番手 foo=ミミ" in text

    # --me を指定したらそちらを優先する
    analyze_replay(video, tmp_path / "out2", advisor, player="hogehoge", me_character="リン", log=logs.append)
    assert "使用キャラ リン" in messages.create_calls[-2]["messages"][0]["content"][-1]["text"]


def test_map_from_intro_gives_board_and_position(tmp_path):
    video = tmp_path / "replay.mp4"
    make_replay_video(video, [(GRAY, 2, False), *TURNS])
    messages = FakeMessages()
    messages.roster = Roster(found=False, players=[], map_name="異変図書館", difficulty="狂気")
    messages.position = {"end_position": "A8", "came_from": "A9"}
    advisor = Advisor(client=SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    logs = []
    analyze_replay(video, tmp_path / "out", advisor, player="hogehoge", log=logs.append)
    assert any("マップ: 異変図書館、難易度: 狂気" in line for line in logs)

    my_turn, enemy_turn = (c["messages"][0]["content"][-1]["text"] for c in messages.create_calls)
    assert "マップ: 異変図書館" in my_turn and "難易度: 狂気" in my_turn
    # hogehoge は2番手 → 2番手のスタートポイントから始まる
    assert "現在地(推定): スタートポイント A10(最初の向きは A9)" in my_turn
    assert "A10: スタートポイント(2番手)" in my_turn
    # 次の講評には、前の手番の終了位置が渡る
    assert "現在地(推定): A8(直前に通ったマスは A9)" in enemy_turn
    assert "end_position" in messages.create_calls[0]["output_config"]["format"]["schema"]["required"]

    # 位置を見失ったあとは、最後に分かっていた位置を手がかりとして渡す
    from apadvisor.replay import PositionTracker

    tracker = PositionTracker(("F0", "G1"))
    assert tracker.text() == "スタートポイント F0(最初の向きは G1)"
    tracker.update({"phase": "my_turn", "review": {"end_position": "H3", "came_from": "H2"}})
    tracker.update({"phase": "enemy_turn", "review": {"end_position": None}})  # 敵の手番では動かない
    assert tracker.text() == "H3(直前に通ったマスは H2)"
    tracker.update({"phase": "my_turn", "review": {"end_position": None}})
    tracker.update(None)
    assert tracker.text().startswith("不明。2手番前の開始時点では H3(直前に通ったマスは H2) にいた")

    # 指定したマップが優先。盤面データのないマップでは盤面を渡さない
    analyze_replay(video, tmp_path / "out2", advisor, player="hogehoge", map_name="決勝大会場", log=logs.append)
    text = messages.create_calls[-2]["messages"][0]["content"][-1]["text"]
    assert "マップ: 決勝大会場" in text and "# 盤面" not in text


def test_pick_key_frames_keeps_brief_distinct_screen(tmp_path):
    from apadvisor.video import pick_key_frames

    (tmp_path / "frames").mkdir()
    frames = []
    # 30枚のうち、12枚目と13枚目だけ別の画面(短い戦闘画面のつもり)。等間隔に4枚選ぶと取りこぼす
    for i in range(30):
        value = 220 if i in (12, 13) else 40 + (i % 3)
        image = np.full((90, 160, 3), value, dtype=np.uint8)
        name = f"frames/f{i:05d}.jpg"
        (tmp_path / name).write_bytes(cv2.imencode(".jpg", image)[1].tobytes())
        frames.append(Frame(i, i * 0.5, name))
    even = {f.index for f in pick_frames(frames, 4)}
    assert not even & {12, 13}
    picked = pick_key_frames(tmp_path, frames, 4)
    indexes = [f.index for f in picked]
    assert indexes == sorted(indexes) and indexes[0] == 0 and indexes[-1] == 29
    assert set(indexes) & {12, 13}
    assert pick_key_frames(tmp_path, frames[:3], 4) == frames[:3]


def test_export_hides_player_names(tmp_path):
    from apadvisor.export import anonymizer, export_report, mask_names

    replace = anonymizer(["foo_bar", "Abc", "ぴよぴよ", "hogehoge"])
    assert replace("hogehogeがAbcに振り込み、ぴよぴよを守った") == "プレイヤーDがプレイヤーBに振り込み、プレイヤーCを守った"
    assert replace("Abcde と foo_bar2 は別の単語") == "Abcde と foo_bar2 は別の単語"
    assert replace(None) is None

    image = np.random.default_rng(0).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    masked = mask_names(image)
    box = masked[int(0.105 * 360) : int(0.135 * 360), int(0.09 * 640) : int(0.21 * 640)]
    assert box.std() < image.std() / 3  # 名前の場所は細部が消えている
    assert (masked[300:, 300:] == image[300:, 300:]).all()  # ほかの場所はそのまま

    video = tmp_path / "replay.mp4"
    make_replay_video(video, TURNS)
    out = tmp_path / "out"
    advisor = Advisor(client=SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages())))
    analyze_replay(video, out, advisor, player="hogehoge", log=lambda _: None)
    index = export_report(out, tmp_path / "public", title="例")
    html = index.read_text(encoding="utf-8")
    assert "hogehoge" not in html and "疑問手" in html
    assert len(list((tmp_path / "public" / "frames").glob("*.jpg"))) >= 1
