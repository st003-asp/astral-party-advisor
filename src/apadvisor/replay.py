"""ゲーム内リプレイ再生の録画を、手番ごとに講評する。

流れ: フレーム抽出 → 再生バーのアイコンで手番ごとに区切る(API不要)
      → 各アイコンが誰の手番かを1回ずつ読み取る → 対象プレイヤーの手番と敵の手番を講評。
途中結果は out_dir の JSON に保存し、再実行時は続きから再開する。
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Callable

from . import board, knowledge_base
from .analyze import Decision
from .llm import Advisor, Refused, Truncated
from .schemas import Advice, TurnOwner, TurnReview
from .video import Frame, TurnSegment, extract_keyframes, load_frames, segment_turns, select_turn_frames

Progress = Callable[[str], None]
MAX_FRAMES_PER_TURN = 20
OWNER_FRAMES = 3
ROSTER_FRAMES = 6
DIFFICULTIES = ("普通", "困難", "悪夢", "狂気", "極限")
FULL_EDGE, MAP_EDGE, BATTLE_EDGE = 1376, 1152, 800
EXTRA_FRAMES_FOR_LONG_TURN = 16


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _norm(text: str | None) -> str:
    return unicodedata.normalize("NFKC", text or "").strip().lower()


def pick_frames(frames: list[Frame], limit: int = MAX_FRAMES_PER_TURN) -> list[Frame]:
    """長い手番は等間隔に間引く(最初と最後は必ず残す)。"""
    if len(frames) <= limit:
        return list(frames)
    step = (len(frames) - 1) / (limit - 1)
    return [frames[round(i * step)] for i in range(limit)]


def image_sizes(is_map: list[bool]) -> list[int]:
    """画像ごとの送信サイズ(長辺)。画像の量がそのまま費用になるので、読める範囲で小さくする。

    - 最初のマップ画面: 元の大きさ(手札やチップの小さい文字を読ませる)
    - 2枚目以降のマップ画面: やや縮小(何が起きたかと、HP・コイン・手札の変化が分かればよい)
    - 戦闘画面など: 大きく縮小(数字もメッセージも文字が大きい)
    """
    sizes = []
    seen_map = False
    for flag in is_map:
        if not flag:
            sizes.append(BATTLE_EDGE)
        elif not seen_map:
            sizes.append(FULL_EDGE)
            seen_map = True
        else:
            sizes.append(MAP_EDGE)
    return sizes


def identify_owners(
    advisor: Advisor, out_dir: Path, segments: list[TurnSegment], log: Progress
) -> dict[int, TurnOwner]:
    """手番アイコンの種類ごとに、誰の手番かを1回だけ読み取る。"""
    cache_path = out_dir / "owners.json"
    cache = _load(cache_path)
    for cluster in sorted({s.cluster for s in segments}):
        key = str(cluster)
        if key in cache:
            continue
        # 手番の最初には「〇〇ターン開始」の表示とプレイヤー一覧が映るので、いちばん長い区間の冒頭を見せる
        longest = max((s for s in segments if s.cluster == cluster), key=lambda s: len(s.frames))
        owner = advisor.identify_owner([out_dir / f.file for f in longest.frames[:OWNER_FRAMES]])
        cache[key] = owner.model_dump()
        _save(cache_path, cache)
        who = owner.player_name or {"enemy": "敵", "none": "(手番なし)"}.get(owner.kind, "?")
        log(f"  手番アイコン{cluster}: {who}" + (f"({owner.turn_order}番手)" if owner.turn_order else ""))
    return {int(k): TurnOwner(**v) for k, v in cache.items()}


def read_match(
    advisor: Advisor, out_dir: Path, frames: list[Frame], segments: list[TurnSegment], log: Progress
) -> dict:
    """録画の冒頭(リプレイ選択画面と試合結果の画面)から、参加者・マップ・難易度を読み取る。

    戻り値: {"players": [{"player_name","label","character"}], "map_name", "difficulty"}。写っていない項目は空/None。
    選択画面にはキャラ名ではなく二つ名が出るので、characters.json の対応表でキャラ名に直す。
    """
    cache_path = out_dir / "roster.json"
    if cache_path.exists():
        cached = _load(cache_path)
        if "map_name" in cached:
            return cached
    first_turn = segments[0].frames[0].index
    intro = [f for f in frames if f.index < first_turn]
    match: dict = {"players": [], "map_name": None, "difficulty": None}
    if intro:
        roster = advisor.read_roster([out_dir / f.file for f in pick_frames(intro, ROSTER_FRAMES)])
        if roster.found:
            match["players"] = [
                {
                    "player_name": e.player_name,
                    "label": e.label,
                    "character": knowledge_base.character_from_label(e.label),
                }
                for e in roster.players
            ]
        match["map_name"] = roster.map_name
        match["difficulty"] = roster.difficulty if roster.difficulty in DIFFICULTIES else None
    _save(cache_path, match)
    players = match["players"]
    if players:
        log("参加者: " + "、".join(f"{p['player_name']}={p['character'] or p['label'] + '(不明)'}" for p in players))
    else:
        log("参加者一覧の画面は見つかりませんでした(リプレイ選択画面から録画すると、キャラを自動で判定できます)。")
    if match["map_name"] or match["difficulty"]:
        log(f"マップ: {match['map_name'] or '不明'}、難易度: {match['difficulty'] or '不明'}")
    return match


class PositionTracker:
    """盤面上の現在地を手番から手番へ引き継ぐ。

    見失ったあとは古い位置を現在地として渡さない。代わりに「何手番前にどこにいたか」を伝え、
    止まったマスの表示から位置を取り戻す手がかりにする。
    """

    def __init__(self, start: tuple[str, str | None] | None):
        self.known: str | None = None
        self.missed = 0  # 最後に位置が分かってから、位置を追えなかった自分の手番の数
        if start:
            self.known = f"スタートポイント {start[0]}" + (f"(最初の向きは {start[1]})" if start[1] else "")

    def update(self, entry: dict | None) -> None:
        """entry: 講評の結果(講評できなかった手番は None)。"""
        end = entry["review"].get("end_position") if entry else None
        if end:
            came = entry["review"].get("came_from")
            self.known = f"{end}" + (f"(直前に通ったマスは {came})" if came else "")
            self.missed = 0
        elif entry is None or entry["phase"] != "enemy_turn":  # 敵の手番では対象プレイヤーは動かない
            self.missed += 1

    def text(self) -> str:
        if self.known is None:
            return "不明"
        if self.missed == 0:
            return self.known
        return (
            f"不明。{self.missed}手番前の開始時点では {self.known} にいたが、その後の移動を追えていない"
            "(1手番の移動はふつう1〜10マス。そこから届く範囲で、止まったマスの表示から位置を取り戻すこと)"
        )


def _plain(value, map_name: str | None):
    """講評の文章に残ったマスの名前を、マスの種類に置き換える(位置の項目はそのまま)。"""
    if isinstance(value, str):
        return board.without_tile_ids(value, map_name)
    if isinstance(value, list):
        return [_plain(v, map_name) for v in value]
    if isinstance(value, dict):
        return {k: v if k in ("end_position", "came_from") else _plain(v, map_name) for k, v in value.items()}
    return value


def _turn_context(context: str, segment: TurnSegment, board_note: str, position: str) -> str:
    lines = [context, f"動画内の時刻: {segment.frames[0].timestamp}〜{segment.frames[-1].timestamp}"]
    if board_note:
        lines.append(f"この手番の開始時点の現在地(推定): {position}")
        lines.append("\n# 盤面\n" + board_note)
    return "\n".join(lines)


def find_my_cluster(owners: dict[int, TurnOwner], player: str | None, order: int | None) -> int:
    for cluster, owner in owners.items():
        if owner.kind != "player":
            continue
        if order is not None and owner.turn_order == order:
            return cluster
        # 「〇〇ターン開始」の表示は文字が重なって、名前の一部が二重に読まれることがあるので、部分一致で探す
        if player and _norm(player) and (
            _norm(player) in _norm(owner.player_name) or _norm(player) == _norm(owner.character)
        ):
            return cluster
    found = "、".join(
        f"{o.player_name}({o.turn_order}番手)" for o in owners.values() if o.kind == "player"
    ) or "なし"
    raise KeyError(
        f"対象プレイヤーが見つかりませんでした。--player に表示名、または --order に手番順(1〜4)を指定してください。"
        f"読み取れたプレイヤー: {found}"
    )


def analyze_replay(
    video: Path,
    out_dir: Path,
    advisor: Advisor | None,
    *,
    player: str | None = None,
    order: int | None = None,
    me_character: str | None = None,
    map_name: str | None = None,
    difficulty: str | None = None,
    base_context: str = "",
    interval: float = 0.5,
    diff_threshold: float = 2.0,
    start: float = 0.0,
    end: float | None = None,
    max_turns: int | None = None,
    include_enemy_turns: bool = True,
    frames_per_turn: int = MAX_FRAMES_PER_TURN,
    effort: str = "high",
    frames_only: bool = False,
    log: Progress = print,
) -> tuple[list[Decision], dict[str, Advice]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = load_frames(out_dir)
    if frames is None:
        log("フレームを抽出中…")
        frames = extract_keyframes(
            video, out_dir, interval=interval, diff_threshold=diff_threshold, start=start, end=end
        )
    segments = segment_turns(out_dir, frames)
    log(f"フレーム {len(frames)} 枚、手番の区間 {len(segments)} 個")
    if frames_only:
        for s in segments:
            log(f"  {s.id} {s.frames[0].timestamp}〜 アイコン{s.cluster} {len(s.frames)}枚")
        return [], {}
    if not segments:
        raise KeyError("再生バーが見つかりませんでした。リプレイ再生の録画でなければ --mode live を指定してください。")

    match = read_match(advisor, out_dir, frames, segments, log)
    party = match["players"]
    # マップと難易度は、指定があればそれを、なければ録画の冒頭から読んだものを使う
    map_name = map_name or match["map_name"]
    difficulty = difficulty or match["difficulty"]
    advisor.map_name = map_name  # 参照資料にこのマップの攻略データを入れる
    board_note = board.board_text(map_name)
    tile_kinds = board.get_board(map_name).kinds() if board_note else None
    if map_name and not board_note:
        log(f"  マップ「{map_name}」の盤面データはありません(分岐の確率計算は行いません)。")
    log("手番の持ち主を読み取り中…")
    owners = identify_owners(advisor, out_dir, segments, log)
    mine = find_my_cluster(owners, player, order)
    me = owners[mine]
    if player and _norm(player) in _norm(me.player_name):
        me = me.model_copy(update={"player_name": player})  # 読み取りの揺れを指定の表記に揃える
    # 使用キャラは、指定(--me) > 冒頭の参加者一覧の二つ名 の順で決める。画面の見た目からの推測は使わない
    from_roster = next((p["character"] for p in party if _norm(p["player_name"]) == _norm(me.player_name)), None)
    if from_roster is None and me.turn_order and len(party) >= me.turn_order:
        from_roster = party[me.turn_order - 1]["character"]
    me = me.model_copy(update={"character": me_character or from_roster})
    if me.character is None:
        log("  使用キャラが分かりません。--me でキャラ名を指定すると講評が正確になります。")
    targets = [
        (s, "my_turn" if s.cluster == mine else "enemy_turn")
        for s in segments
        if s.cluster == mine or (include_enemy_turns and owners[s.cluster].kind == "enemy")
    ]
    if max_turns is not None:
        targets = targets[:max_turns]
    log(
        f"対象プレイヤー: {me.player_name}({me.turn_order}番手、{me.character or 'キャラ不明'})。"
        f"講評する区間 {len(targets)} 個"
    )

    cache_path = out_dir / "reviews.json"
    cache = _load(cache_path)
    party_line = "、".join(
        f"{i}番手 {p['player_name']}={p['character'] or p['label']}" for i, p in enumerate(party, 1)
    )
    context = "\n".join(
        x
        for x in (
            base_context.strip(),
            f"マップ: {map_name}" if map_name else "",
            f"難易度: {difficulty}" if difficulty else "",
            f"パーティ(手番順): {party_line}" if party_line else "",
            f"対象プレイヤー: {me.player_name}(手番 {me.turn_order}番手、"
            + (f"使用キャラ {me.character}" if me.character else "使用キャラ不明。見た目から決めつけず、キャラ固有の指摘は控える")
            + ")",
        )
        if x
    )
    # 盤面上の現在地。最初は自分のスタートポイント、以後は前の手番の講評が出した終了位置を引き継ぐ
    position = PositionTracker(board.start_tile(map_name, me.turn_order))
    for i, (segment, phase) in enumerate(targets, 1):
        if segment.id in cache:
            position.update(cache[segment.id])
            continue
        # 画面の内容が変わったところを優先して選ぶ(等間隔だと短い戦闘画面やカード表示を取りこぼす)
        # 長い手番(あとに敵の手番が続く4番手など)は、取りこぼさないよう枚数を増やす
        limit = min(max(frames_per_turn, round(len(segment.frames) / 3)), frames_per_turn + EXTRA_FRAMES_FOR_LONG_TURN)
        selected = select_turn_frames(out_dir, segment.frames, limit)
        shown = [f for f, _ in selected]
        try:
            review = advisor.review_turn(
                [out_dir / f.file for f in shown],
                max_edges=image_sizes([is_map for _, is_map in selected]),
                phase=phase,
                context=_turn_context(context, segment, board_note, position.text()),
                tile_kinds=tile_kinds,
                effort=effort,
            )
        except Refused as exc:
            log(f"  [{i}/{len(targets)}] {segment.frames[0].timestamp} 拒否されたためスキップ: {exc}")
            continue
        except Truncated as exc:
            log(f"  [{i}/{len(targets)}] {segment.frames[0].timestamp} スキップ({exc})。もう一度実行すると、この手番だけやり直します")
            position.update(None)
            continue
        cache[segment.id] = {"phase": phase, "frames": [f.index for f in shown], "review": review.model_dump()}
        _save(cache_path, cache)
        position.update(cache[segment.id])
        label = "自分の手番" if phase == "my_turn" else "敵の手番"
        summary = board.without_tile_ids(review.turn_summary, map_name)
        log(f"  [{i}/{len(targets)}] {segment.frames[0].timestamp} {label}: 判断{len(review.decisions)}件 — {summary}")
    _save(out_dir / "usage.json", advisor.usage.as_dict())

    by_index = {f.index: f for f in frames}
    decisions: list[Decision] = []
    advice: dict[str, Advice] = {}
    for segment, _phase in targets:
        entry = cache.get(segment.id)
        if entry is None:
            continue
        shown = [by_index[i] for i in entry["frames"]]
        # マスの名前(C4 など)は読む人に通じないので、レポートに出す文章からは除く
        review = TurnReview(**_plain(entry["review"], map_name))
        for n, item in enumerate(review.decisions):
            pos = max(1, min(item.image_number, len(shown))) - 1
            decision = Decision(f"{segment.id}-{n}", item.decision_type, [shown[pos]], after=shown[pos + 1 : pos + 2])
            decisions.append(decision)
            advice[decision.id] = item
    return decisions, advice
