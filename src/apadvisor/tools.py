"""Claude に渡す計算ツール。確率は必ずここ(Python)で計算させる。"""

from __future__ import annotations

import json
from typing import Any, Callable

from . import chips, combat, events, knowledge_base, movement
from .dice import Dist

_CARD_SCHEMA = {
    "type": "object",
    "description": "バトルカード。標準カードは name だけでよい。それ以外は cost/lo/hi を画面のテキストから指定する。",
    "properties": {
        "name": {"type": "string", "description": "例: 攻撃(中)、防御(大)、シャドウアタック、名刀：ガオー切り、会心の一撃、龍の咆哮"},
        "cost": {"type": "integer"},
        "lo": {"type": "integer", "description": "上昇値の最小(固定値ならその値)"},
        "hi": {"type": "integer", "description": "上昇値の最大"},
        "no_counter": {"type": "boolean", "description": "反撃無効が付くカードか"},
        "multiplier": {"type": "number", "description": "最終的な攻撃値に掛かる倍率(例: 1.5)。なければ省略"},
    },
    "required": ["name"],
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "attack_odds",
        "description": (
            "自分が敵を攻撃するとき、手札の攻撃カードの組み合わせごとに撃破率・期待ダメージ・"
            "反撃で受ける被害を厳密に計算する。どの攻撃カードを使うか、そもそも殴るかを決めるときに使う。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "my_atk": {"type": "integer", "description": "自分の現在の攻撃力(バフ込み、ダイスとカードは含めない)"},
                "enemy_def": {"type": "integer"},
                "enemy_hp": {"type": "integer"},
                "attack_cards": {"type": "array", "items": _CARD_SCHEMA, "description": "手札の攻撃カード(同名が複数あれば複数入れる)"},
                "cost_limit": {"type": "integer", "description": "バトルコスト上限(スターレベル0=3、以降+1、最大6)"},
                "damage_bonus": {"type": "integer", "description": "敵の被ダメ+N(マーク、フェイトエコー等の合計)。なければ0"},
                "enemy_reduction": {"type": "integer", "description": "敵のダメージ軽減。なければ0"},
                "enemy_die_zero": {"type": "boolean", "description": "敵のダイスが0になる状態(モーゼスの弱点)か"},
                "enemy_evades": {"type": "boolean", "description": "回避してくる敵(金ちゃん、キャンディピニャータ)か"},
                "my_die_fixed": {"type": "integer", "description": "自分の攻撃ダイスが確定している場合の目(パッドマンの6確定など)"},
                "enemy_counters": {"type": "boolean", "description": "敵が反撃持ちか"},
                "enemy_atk": {"type": "integer", "description": "敵の攻撃力(反撃の被害計算用)"},
                "my_def": {"type": "integer"},
                "my_hp": {"type": "integer"},
                "my_reduction": {"type": "integer", "description": "自分の被ダメ軽減(ヘルメット等)"},
                "my_damage_bonus": {"type": "integer", "description": "自分の被ダメ+N(狂暴等)"},
                "shield": {"type": "boolean", "description": "ジュジュシールドを持っているか"},
            },
            "required": ["my_atk", "enemy_def", "enemy_hp", "cost_limit"],
        },
    },
    {
        "name": "defense_odds",
        "description": (
            "敵に攻撃されたとき、「防御(防御カードの組み合わせごと)」と「回避」それぞれの"
            "倒される確率・期待ダメージを厳密に計算する。防御か回避か、どの防御カードを使うかを決めるときに使う。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "enemy_atk": {"type": "integer", "description": "敵の攻撃力(ダイスを含めない基礎値)"},
                "attacker_die": {"type": "integer", "description": "画面に見えている敵のダイス目。まだ振られていなければ省略"},
                "my_def": {"type": "integer"},
                "my_hp": {"type": "integer"},
                "defense_cards": {"type": "array", "items": _CARD_SCHEMA},
                "cost_limit": {"type": "integer"},
                "my_reduction": {"type": "integer", "description": "被ダメ軽減(ヘルメット等)"},
                "my_damage_bonus": {"type": "integer", "description": "被ダメ+N(狂暴等)"},
                "shield": {"type": "boolean", "description": "ジュジュシールドを持っているか"},
                "dodge_min_exclusive": {"type": "integer", "description": "回避ダイスが必ずこの値より大きくなる(モーゼスの弱点看破スタック数)"},
                "can_dodge": {"type": "boolean", "description": "回避を選べるか(選べない攻撃なら false)"},
            },
            "required": ["enemy_atk", "my_def", "my_hp"],
        },
    },
    {
        "name": "move_odds",
        "description": (
            "移動ダイス(PvEは10面)で、あるマスに「ちょうど止まれる確率」と「通過または停止できる確率」を計算する。"
            "分岐やリモコンダイス/早すぎるおんなを使うかの判断に使う。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "distance": {"type": "integer", "description": "目的のマスまでの歩数"},
                "move_bonus": {"type": "integer", "description": "移動補正(スターレベルやチップの合計)"},
                "dice": {"type": "integer", "description": "振るダイスの数(通常1、早すぎるおんな等で2)"},
                "sides": {"type": "integer", "description": "ダイスの面数(通常の移動は10。ルカのスキル中は6)"},
            },
            "required": ["distance"],
        },
    },
    {
        "name": "chip_odds",
        "description": "スターレベルと難易度から、チップ3択に青/紫/金が出る確率、次のチップショップ価格、次のレベルアップ費用を返す。",
        "input_schema": {
            "type": "object",
            "properties": {
                "star_level": {"type": "integer"},
                "difficulty": {"type": "string", "enum": ["普通", "困難", "悪夢", "狂気", "極限"]},
                "chip_shop_purchases": {"type": "integer", "description": "これまでにチップショップで買った回数"},
                "target_rarity": {"type": "string", "enum": ["blue", "purple", "gold"], "description": "狙っている特定チップのレアリティ"},
                "target_pool_size": {"type": "integer", "description": "そのレアリティで今出現しうるチップのおおよその枚数"},
            },
            "required": ["star_level", "difficulty"],
        },
    },
    {
        "name": "event_tile_odds",
        "description": "イベントマス(全員イベント)を踏んだとき、現在の進捗バーの値でどのイベントが何%で起きるか(有志の実測)を返す。",
        "input_schema": {
            "type": "object",
            "properties": {"progress": {"type": "integer", "description": "現在の進捗バーの値(1〜15。Round の数字ではない)"}},
            "required": ["progress"],
        },
    },
    {
        "name": "lookup",
        "description": (
            "カード・チップ・モンスター・マス・イベント・マップ・キャラクターの名前から、wikiの説明と攻略メモを引く。"
            "画面に出ている名前の効果が分からないとき、判断前に必ず引く。まず画面どおりの日本語名で引く。"
            "英語wiki・中国語wikiも英語名/中国語名で引ける(敵の難易度別ステータス表やチップの正確な効果文がある)。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"names": {"type": "array", "items": {"type": "string"}, "description": "調べたい名前(複数可)"}},
            "required": ["names"],
        },
    },
]


def _attack_odds(a: dict) -> dict:
    hand = combat.parse_cards([{**c, "side": "attack"} for c in a.get("attack_cards", [])])
    kwargs: dict[str, Any] = {
        "bonus": a.get("damage_bonus", 0),
        "reduction": a.get("enemy_reduction", 0),
        "enemy_evades": a.get("enemy_evades", False),
    }
    if a.get("enemy_die_zero"):
        kwargs["enemy_die"] = Dist.const(0)
    if a.get("my_die_fixed") is not None:
        kwargs["my_die"] = Dist.const(a["my_die_fixed"])
    results = combat.engage_options(
        a["my_atk"],
        a["enemy_def"],
        a["enemy_hp"],
        hand,
        a["cost_limit"],
        enemy_atk=a.get("enemy_atk", 0),
        enemy_counters=bool(a.get("enemy_counters")) and "enemy_atk" in a,
        my_def=a.get("my_def", 0),
        my_hp=a.get("my_hp", 99),
        my_bonus_taken=a.get("my_damage_bonus", 0),
        my_reduction=a.get("my_reduction", 0),
        shield=a.get("shield", False),
        **kwargs,
    )
    out = {"options": [r.summary() for r in results]}
    if a.get("enemy_counters") and "enemy_atk" not in a:
        out["warning"] = "enemy_atk が未指定のため反撃の被害は計算していない"
    return out


def _defense_odds(a: dict) -> dict:
    hand = combat.parse_cards([{**c, "side": "defense"} for c in a.get("defense_cards", [])])
    dodge_die = combat.D6
    k = a.get("dodge_min_exclusive", 0)
    if k:
        dodge_die = Dist.uniform(min(k + 1, 6), 6)
    analysis = combat.defense_options(
        a["enemy_atk"],
        a["my_def"],
        a["my_hp"],
        hand,
        a.get("cost_limit", 3),
        attacker_die=a.get("attacker_die"),
        bonus=a.get("my_damage_bonus", 0),
        reduction=a.get("my_reduction", 0),
        shield=a.get("shield", False),
        dodge_die=dodge_die,
        can_dodge=a.get("can_dodge", True),
    )
    return analysis.summary()


def _move_odds(a: dict) -> dict:
    return movement.reach_summary(a["distance"], a.get("move_bonus", 0), a.get("dice", 1), a.get("sides", movement.MOVE_DIE_SIDES))


def _chip_odds(a: dict) -> dict:
    out = chips.chip_offer_summary(a["star_level"], a["difficulty"], a.get("chip_shop_purchases", 0))
    if a.get("target_rarity") and a.get("target_pool_size"):
        p = chips.p_specific_chip(a["target_rarity"], a["target_pool_size"], a["star_level"], a["difficulty"])
        out["target_chip_pct_approx"] = round(float(p) * 100, 1)
    return out


def _event_tile_odds(a: dict) -> dict:
    return events.event_odds(a["progress"])


def _lookup(a: dict) -> str:
    return "\n\n---\n\n".join(knowledge_base.lookup(name) for name in a["names"][:8])


_HANDLERS: dict[str, Callable[[dict], Any]] = {
    "attack_odds": _attack_odds,
    "defense_odds": _defense_odds,
    "move_odds": _move_odds,
    "chip_odds": _chip_odds,
    "event_tile_odds": _event_tile_odds,
    "lookup": _lookup,
}


def run_tool(name: str, tool_input: dict) -> tuple[str, bool]:
    """ツールを実行して (結果テキスト, エラーか) を返す。"""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"未知のツール: {name}", True
    try:
        result = handler(tool_input)
    except (KeyError, ValueError, TypeError) as exc:
        return f"入力エラー: {exc}", True
    if isinstance(result, str):
        return result, False
    return json.dumps(result, ensure_ascii=False), False
