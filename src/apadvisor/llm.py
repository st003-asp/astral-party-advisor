"""Claude API の呼び出し(画面の分類と、判断場面への助言)。"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import anthropic
from PIL import Image

from . import knowledge_base
from .replay_prompts import OWNER_SYSTEM, REVIEW_INSTRUCTIONS, REVIEW_TASK, ROSTER_SYSTEM
from .schemas import Advice, Roster, SceneInfo, TurnOwner, TurnReview
from .tools import TOOLS, run_tool

DEFAULT_MODEL = os.environ.get("APADVISOR_MODEL", "claude-opus-5-5")
# 安全分類器に誤って拒否された場合、サーバー側で別モデルに自動で振り替える
FALLBACK_BETAS = ["server-side-fallback-2026-07-01"]
MAX_TOOL_ROUNDS = 8
FULL_EDGE = 1376  # 手札の小さい文字まで読ませたい画面の解像度
HAIKU_THINKING_BUDGET = 6000
_ADVICE_SCHEMA = anthropic.transform_schema(Advice)
_REVIEW_SCHEMA = anthropic.transform_schema(TurnReview)

CLASSIFY_SYSTEM = """\
あなたはボードゲーム「アストラルパーティー」(Steam版、PvE協力モード)のプレイ録画を解析する助手です。
渡された1枚のスクリーンショットが、録画者本人(画面下に自分の手札が表示されているプレイヤー)にとって
「選択を求められている場面」かどうかを分類してください。

- 自分の手番でカード/スキル/ダイスを選べる状態 → turn_action
- 分かれ道の矢印を選ぶ画面 → branch
- スキルやカードの対象(味方・敵・マス)を選ぶ画面 → skill_target
- 敵のいるマスで「戦う/スルー」を選ぶ画面 → encounter
- ショップ(カード購入・振込み) → shop、強化チップショップ → chip_shop、チップ3択 → chip_select
- 戦闘画面で自分が攻撃側 → battle_attack、自分が防御側(防御/回避の選択) → battle_defense
- スタート/セーフティポイントで止まるか選ぶ → level_up
- 味方や敵の手番、演出、ロード、リザルト、メニューなど → none(is_my_decision は false)

演出の途中やダイスが回っている最中など、まだ選択肢が出ていない画面は none にしてください。"""

ADVISE_INSTRUCTIONS = """\
あなたは「アストラルパーティー」PvE(協力チャレンジ)の指導役です。将棋や麻雀の解析AIのように、
録画の1場面について「どうするのが最善だったか」を根拠つきで示します。

# 進め方
1. 画像から状況を読み取る。自キャラ、スターレベル、HP、攻撃/防御、コイン、手札、所持チップ、スキルの状態、
   味方と敵の位置・HP、進行(ラウンド/進捗)、いま出ている選択肢。読めないものは推測せず unreadable に書く。
2. 画面に出ているカード・チップ・敵・マスの効果に確信が持てなければ lookup で調べる。
3. 数値で比べられる判断(攻撃カード、防御か回避か、止まれる確率、チップの出現率、イベントマス)は
   必ず計算ツールを呼ぶ。確率や期待値を暗算で書かない。
4. 下の参照資料の指針と計算結果から推奨を1つ決める。指針はセオリーなので、盤面と矛盾するなら盤面を優先し、その理由を書く。
5. 「このあとの画面」が渡されていれば、実際に選ばれた行動を読み取り、推奨と比べて評価する。
   結果(出目)の良し悪しではなく、選択の時点での妥当性で評価する。

# 評価の基準
- 最善: 推奨と同じ、または同等。
- 許容: 最善ではないが損が小さい、あるいは好みの範囲。
- 疑問手: はっきり損だが致命的ではない。
- 悪手: 倒されるリスクを不必要に負う、味方のレベルアップを潰すなど、大きな損。
- 判定不能: 実際の行動が読み取れない。

# 書き方
- recommended は「防御(大)を使って防御」「ミサキに振り込む」「右の道(セーフティポイント側)」のように具体的に。
- reasoning は結論から。計算ツールの数値(撃破率◯%、期待ダメージ◯など)を入れ、日本語で簡潔に。
- 画面の表示と参照資料の数値が食い違うときは画面を信じる(アップデートで変わるため)。
- 最後の出力は指定のJSON形式だけにする。"""


@dataclass
class Usage:
    """APIの利用量の累計(費用の目安を出すため)。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: int = 0

    def add(self, usage) -> None:
        self.requests += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class Refused(RuntimeError):
    """モデルが応答を拒否した(stop_reason == "refusal")。"""


@dataclass
class Advisor:
    client: anthropic.Anthropic = field(default_factory=anthropic.Anthropic)
    model: str = DEFAULT_MODEL
    scan_model: str | None = None  # 画面分類に使うモデル。None なら model と同じ
    usage: Usage = field(default_factory=Usage)

    # ---- 画面の分類 ----
    def classify(self, image: Path) -> SceneInfo:
        response = self.client.beta.messages.parse(
            model=self.scan_model or self.model,
            max_tokens=2000,
            **_model_options(self.scan_model or self.model, "low", thinking=False),
            output_format=SceneInfo,
            system=CLASSIFY_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [image_block(image, max_edge=1280), {"type": "text", "text": "この画面を分類してください。"}],
                }
            ],
        )
        self.usage.add(response.usage)
        return _parsed(response)

    # ---- 助言 ----
    def advise(
        self,
        frames: Sequence[Path],
        *,
        after_frames: Sequence[Path] = (),
        context: str = "",
        effort: str = "high",
    ) -> Advice:
        """判断場面の画像(frames)に助言する。after_frames は選択後の画面(あれば実際の行動を読む)。"""
        content: list[dict] = [{"type": "text", "text": "# 判断場面の画面"}]
        content += [image_block(p, max_edge=1920) for p in frames]
        if after_frames:
            content.append({"type": "text", "text": "# このあとの画面(実際に何を選んだかの手がかり)"})
            content += [image_block(p, max_edge=1280) for p in after_frames]
        note = context.strip() or "(なし)"
        content.append(
            {
                "type": "text",
                "text": f"# 補足情報\n{note}\n\nこの場面で録画者本人はどうするのが最善かを助言してください。",
            }
        )
        text = self._run_with_tools(_system(ADVISE_INSTRUCTIONS), content, _ADVICE_SCHEMA, effort)
        return Advice.model_validate_json(text)

    # ---- リプレイ再生の録画 ----
    def identify_owner(self, images: Sequence[Path]) -> TurnOwner:
        """リプレイ再生の、ある手番の最初の数フレームから、誰の手番かを読み取る。"""
        response = self.client.beta.messages.parse(
            model=self.scan_model or self.model,
            max_tokens=2000,
            **_model_options(self.scan_model or self.model, "low", thinking=False),
            output_format=TurnOwner,
            system=OWNER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        *[image_block(p, max_edge=1376) for p in images],
                        {"type": "text", "text": "これらは同じ手番の連続した画面です。誰の手番かを読み取ってください。"},
                    ],
                }
            ],
        )
        self.usage.add(response.usage)
        return _parsed(response)

    def read_roster(self, images: Sequence[Path]) -> Roster:
        """録画の冒頭(リプレイ選択画面)から、参加者の二つ名とプレイヤー名を読み取る。"""
        response = self.client.beta.messages.parse(
            model=self.scan_model or self.model,
            max_tokens=2000,
            **_model_options(self.scan_model or self.model, "low", thinking=False),
            output_format=Roster,
            system=ROSTER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        *[image_block(p, max_edge=1376) for p in images],
                        {"type": "text", "text": "参加者の一覧が写っていれば読み取ってください。"},
                    ],
                }
            ],
        )
        self.usage.add(response.usage)
        return _parsed(response)

    def review_turn(
        self,
        frames: Sequence[Path],
        *,
        phase: str,
        context: str = "",
        effort: str = "high",
        max_edges: Sequence[int] | None = None,
    ) -> TurnReview:
        """1手番ぶんの連続フレームを見て、対象プレイヤーの判断を講評する。

        phase: "my_turn"(対象プレイヤーの手番) / "enemy_turn"(敵の手番。対象プレイヤーが攻撃された場面だけ見る)
        max_edges: 画像ごとの長辺の上限(ピクセル)。文字の大きい戦闘画面などを縮小して送り、費用を抑える。
        """
        content: list[dict] = []
        for i, path in enumerate(frames, 1):
            content.append({"type": "text", "text": f"画像{i}"})
            content.append(image_block(path, max_edge=max_edges[i - 1] if max_edges else FULL_EDGE))
        note = context.strip() or "(なし)"
        content.append({"type": "text", "text": f"# 補足情報\n{note}\n\n# 依頼\n{REVIEW_TASK[phase]}"})
        text = self._run_with_tools(_system(REVIEW_INSTRUCTIONS), content, _REVIEW_SCHEMA, effort)
        return TurnReview.model_validate_json(text)

    def _run_with_tools(self, system: list[dict], content: list[dict], schema: dict, effort: str) -> str:
        """計算ツールを使わせながら、最後に schema どおりのJSON本文を返させる。

        ツール呼び出しの途中のターンでは本文がJSONとは限らないので、parse() ではなく
        create() + output_config.format を使い、最後のターンの本文だけを呼び出し側で検証する。
        """
        # 画像を含む最初の依頼もキャッシュする(ツールを呼ぶたびに画像を定価で送り直さないため)
        content = [*content[:-1], {**content[-1], "cache_control": {"type": "ephemeral"}}]
        messages: list[dict] = [{"role": "user", "content": content}]
        options = _model_options(self.model, effort, thinking=True)
        options["output_config"] = {**options.get("output_config", {}), "format": {"type": "json_schema", "schema": schema}}
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                **options,
                system=system,
                tools=TOOLS,
                messages=messages,
            )
            self.usage.add(response.usage)
            _check_stop(response)
            if response.stop_reason != "tool_use":
                text = next((b.text for b in reversed(response.content) if b.type == "text"), None)
                if text is None:
                    raise RuntimeError(f"本文がありません(stop_reason={response.stop_reason})")
                return text

            # thinking ブロックも含め、応答の content はそのまま返す
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    text, is_error = run_tool(block.name, dict(block.input))
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": is_error}
                    )
            messages.append({"role": "user", "content": results})
        raise RuntimeError("ツール呼び出しが上限回数を超えました")


def _model_options(model: str, effort: str, *, thinking: bool) -> dict:
    """モデルごとに受け付けるパラメータが違うので、ここで出し分ける。

    Opus / Sonnet / Fable 系: 思考は adaptive、深さは effort、拒否時のサーバー側フォールバックあり。
    Haiku 4.5: effort と adaptive とフォールバックは使えない。思考は予算(budget_tokens)で指定する。
    """
    if model.startswith("claude-haiku"):
        return {"thinking": {"type": "enabled", "budget_tokens": HAIKU_THINKING_BUDGET}} if thinking else {}
    options: dict = {"betas": FALLBACK_BETAS, "fallbacks": "default", "output_config": {"effort": effort}}
    if thinking:
        options["thinking"] = {"type": "adaptive"}
    return options


def _system(instructions: str) -> list[dict]:
    return [
        {"type": "text", "text": instructions},
        # 参照資料は毎回同じなのでキャッシュする(2回目以降の入力料金が約1/10になる)
        {
            "type": "text",
            "text": "# 参照資料\n\n" + knowledge_base.reference_text(),
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]


def _check_stop(response) -> None:
    if response.stop_reason == "refusal":
        raise Refused(getattr(response.stop_details, "explanation", None) or "モデルが応答を拒否しました")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("出力が max_tokens に達して途中で切れました")


def _parsed(response):
    _check_stop(response)
    if response.parsed_output is None:
        raise RuntimeError(f"構造化出力を取得できませんでした(stop_reason={response.stop_reason})")
    return response.parsed_output


def image_block(path: Path, max_edge: int) -> dict:
    """画像を長辺 max_edge 以下に縮小し、JPEGのbase64ブロックにする。"""
    with Image.open(path) as img:
        img = img.convert("RGB")
        if max(img.size) > max_edge:
            img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
    data = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}
