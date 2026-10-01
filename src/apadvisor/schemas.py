"""Claude に返させる構造化データ。"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

DecisionType = Literal[
    "turn_action",  # 自分の手番: カード/スキルを使うか、ダイスを振るか
    "branch",  # 分岐の行き先
    "skill_target",  # スキルやカードの対象選択
    "encounter",  # 敵と戦うかスルーするか
    "shop",  # ショップ: カード購入・振込み
    "chip_shop",  # チップ購入マス
    "chip_select",  # チップ3択・リフレッシュ
    "battle_attack",  # 攻撃カード選択
    "battle_defense",  # 防御/回避・防御カード選択
    "level_up",  # 止まってレベルアップするか
    "other_decision",  # 上記以外で自分が選ぶ場面
    "none",  # 自分の判断場面ではない
]

Verdict = Literal["最善", "許容", "疑問手", "悪手", "判定不能"]


class SceneInfo(BaseModel):
    """1フレームの分類結果。"""

    decision_type: DecisionType = Field(description="この画面で録画者本人が迫られている判断の種類。なければ none")
    is_my_decision: bool = Field(description="録画者本人が今まさに選択を求められている画面なら true")
    whose_turn: Literal["me", "ally", "enemy", "unknown"]
    summary: str = Field(description="画面で起きていることを1文で")


class Option(BaseModel):
    option: str = Field(description="選択肢(例: 防御(大)を使って防御 / 回避 / 左の道)")
    evaluation: str = Field(description="その選択肢の評価。計算結果があれば数値を入れる")


class Advice(BaseModel):
    """1つの判断場面に対する助言。"""

    decision_type: DecisionType
    situation: str = Field(description="読み取った状況(自キャラ、HP、コイン、手札、敵、進行など判断に使った情報)")
    unreadable: list[str] = Field(description="画面から読み取れず推測した情報。なければ空")
    recommended: str = Field(description="推奨する行動を具体的に")
    reasoning: str = Field(description="理由。計算ツールの数値と、どの指針に基づくかを書く")
    options: list[Option] = Field(description="比較した選択肢")
    actual_action: Optional[str] = Field(description="後続の画面から読み取れた、実際に選ばれた行動。分からなければ null")
    verdict: Verdict = Field(description="実際の行動の評価。実際の行動が分からなければ 判定不能")
    confidence: Literal["高", "中", "低"] = Field(description="この助言の確からしさ")


class TurnOwner(BaseModel):
    """リプレイ再生の1フレームから読み取った「いま誰の手番か」。"""

    kind: Literal["player", "enemy", "none"] = Field(
        description="プレイヤーの手番なら player、敵(モンスター)の手番なら enemy、手番表示がなければ none"
    )
    player_name: Optional[str] = Field(description="手番プレイヤーの表示名(画面左のプレイヤー一覧の名前)。プレイヤーでなければ null")
    turn_order: Optional[int] = Field(description="手番プレイヤーの順番(1〜4。一覧の 1st/2nd/3rd/4th)。プレイヤーでなければ null")
    character: Optional[str] = Field(description="手番プレイヤーの使用キャラ名。分からなければ null")


class RosterEntry(BaseModel):
    player_name: str = Field(description="プレイヤーの表示名(下の行)")
    label: str = Field(description="その上の行に書かれている二つ名またはキャラ名(例: 暗躍する忍者、ハンナ)")


class Roster(BaseModel):
    """リプレイ選択画面(-WIN- の横に二つ名とプレイヤー名が4人ぶん並ぶ画面)から読み取った参加者。"""

    found: bool = Field(description="4人ぶんの二つ名とプレイヤー名が並ぶ画面が写っていれば true")
    players: list[RosterEntry] = Field(description="上から順に。写っていなければ空")


class ReviewedDecision(Advice):
    image_number: int = Field(description="この判断が最もよく分かる画像の番号(渡した画像の1始まりの通し番号)")


class TurnReview(BaseModel):
    """1手番ぶんの講評。"""

    round: Optional[int] = Field(description="ラウンド数。読めなければ null")
    turn_summary: str = Field(description="この手番で起きたことを時系列で簡潔に")
    decisions: list[ReviewedDecision] = Field(description="この手番で対象プレイヤーが行った判断と、その評価。なければ空")
