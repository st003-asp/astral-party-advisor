import json
from fractions import Fraction as F

from apadvisor import chips, events, knowledge_base, movement, tools
from apadvisor.analyze import group_decisions
from apadvisor.schemas import SceneInfo
from apadvisor.video import Frame


def test_move_probabilities():
    # PvEの移動ダイスは10面
    assert movement.p_land_exact(4) == F(1, 10)
    assert movement.p_reach(6) == F(1, 2)
    assert movement.p_reach(6, bonus=2) == F(7, 10)
    assert movement.p_land_exact(11, dice=2) == F(1, 10)  # 早すぎるおんな(2d10)
    assert movement.p_land_exact(1, dice=2) == 0
    assert movement.p_land_exact(7, dice=2, sides=6) == F(1, 6)  # ルカのスキル(2d6)
    assert movement.move_dist(bonus=-2).min() == 0  # パッドマンの下振れ


def test_chip_rates():
    assert chips.rarity_rates(0, "悪夢")["gold"] == F(3, 100)
    assert chips.rarity_rates(3, "普通")["gold"] == F(30, 100)
    # 悪夢Lv1: 通常抽選2枠のどちらかに金 = 1 - 0.97^2
    assert chips.p_rarity_in_offer("gold", 1, "悪夢") == 1 - F(97, 100) ** 2
    assert chips.p_rarity_in_offer("gold", 3, "悪夢") == 1  # 左枠が金
    assert [chips.chip_shop_price(n) for n in range(3)] == [10, 15, 20]
    assert chips.round_bonus("悪夢", starlight=4) == 9
    assert chips.rounds_until_bonus(4) == 2


def test_event_odds():
    early, late = events.event_odds(1), events.event_odds(14)
    assert early["sample_size"] == 241
    assert early["by_kind_estimated_pct"]["損"] > late["by_kind_estimated_pct"]["損"]
    assert any(e["event"] == "熱雷" for e in early["events"])
    assert not any(e["event"] == "熱雷" for e in late["events"])
    assert next(e for e in late["events"] if e["event"] == "神兵天降")["estimated_pct"] > 10
    stats = events.load_stats()
    for i, total in enumerate(stats["totals"]):
        assert sum(e["counts"][i] for e in stats["events"].values()) == total


def test_tools_dispatch():
    text, err = tools.run_tool(
        "defense_odds",
        {"enemy_atk": 6, "attacker_die": 3, "my_def": 3, "my_hp": 1, "defense_cards": [{"name": "防御(大)"}]},
    )
    assert not err and json.loads(text)["recommended"]["action"] == "回避"
    text, err = tools.run_tool(
        "attack_odds",
        {
            "my_atk": 3,
            "enemy_def": 2,
            "enemy_hp": 6,
            "cost_limit": 3,
            "attack_cards": [{"name": "攻撃(中)"}],
            "enemy_counters": True,
            "enemy_atk": 5,
            "my_def": 1,
            "my_hp": 6,
        },
    )
    assert not err and len(json.loads(text)["options"]) == 2
    assert tools.run_tool("attack_odds", {"my_atk": 1})[1] is True
    assert tools.run_tool("nope", {})[1] is True
    text, err = tools.run_tool("move_odds", {"distance": 6})
    assert json.loads(text)["land_exact_pct"] == 10.0
    for tool in tools.TOOLS:
        assert tool["name"] in tools._HANDLERS


def test_knowledge():
    assert len(knowledge_base.characters()) == 35
    for ch in knowledge_base.characters():
        assert len(ch["stats"]) == 4 and all(len(s) == 4 for s in ch["stats"])
    assert "カイセイ" in knowledge_base.lookup("カイセイ")
    assert "ミサキ" in knowledge_base.reference_text()


def test_group_decisions():
    frames = [Frame(i, i * 2.0, f"frames/f{i}.jpg") for i in range(7)]
    kinds = ["none", "turn_action", "turn_action", "battle_attack", "none", "none", "shop"]
    scenes = {
        f.file: SceneInfo(decision_type=k, is_my_decision=k != "none", whose_turn="me", summary=k)
        for f, k in zip(frames, kinds)
    }
    decisions = group_decisions(frames, scenes)
    assert [d.decision_type for d in decisions] == ["turn_action", "battle_attack", "shop"]
    assert [len(d.frames) for d in decisions] == [2, 1, 1]
    assert [f.index for f in decisions[0].after] == [3, 4]
    assert decisions[2].after == []


def test_name_map_is_consistent():
    table = knowledge_base.name_map()
    assert table[knowledge_base._norm("バッファーシールド")] == {"ja": "バッファーシールド", "en": "Buffer Shield", "zh": "缓冲盾牌"}
    assert table[knowledge_base._norm("海賊精鋭")]["en"] == "Elite Pirate"
    data = json.loads(knowledge_base._read("name_map.json"))
    for category, rows in data.items():
        if category.startswith("_"):
            continue
        names = [row["ja"] for row in rows]
        assert len(names) == len(set(names)), category  # 日本語名の重複なし
        zh = [row["zh"] for row in rows if "zh" in row]
        if category in ("chip", "monster", "character", "event"):
            assert len(zh) == len(set(zh)), category  # 中国語側は1対1


def test_lookup_uses_bundled_catalog_first(tmp_path, monkeypatch):
    # 同梱の一覧(catalog.json)にあれば、wikiデータがなくても引ける。英語名・中国語名でも引ける
    monkeypatch.setenv("APADVISOR_DATA_DIR", str(tmp_path))
    knowledge_base._wiki_keys.cache_clear()
    for query in ("バッファーシールド", "Buffer Shield"):
        text = knowledge_base.lookup(query)
        assert "【チップ: バッファーシールド】" in text and "レアリティ: 青" in text
    text = knowledge_base.lookup("ゼリーウィザード")
    assert "狂気 攻3 防1 HP21" in text and "撃破コイン: 8" in text
    assert "## 異変図書館" in knowledge_base.lookup("異変図書館(協力チャレンジ)")
    assert "【カード: 防御(特大)】" in knowledge_base.lookup("ガード(特大)")  # 画面の呼び名でも引ける


def test_lookup_falls_back_to_wiki_pages(tmp_path, monkeypatch):
    # 一覧にないものは、取得済みのwikiページを探す
    d = tmp_path / "wiki"
    d.mkdir()
    (d / "page.md").write_text("日本語の説明", encoding="utf-8")
    (d / "index.json").write_text(json.dumps({"チップ/ふしぎな石": "page.md"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("APADVISOR_DATA_DIR", str(tmp_path))
    knowledge_base._wiki_keys.cache_clear()
    try:
        text = knowledge_base.lookup("ふしぎな石")
    finally:
        knowledge_base._wiki_keys.cache_clear()
    assert "日本語の説明" in text


def test_reference_text_includes_map_guide():
    plain = knowledge_base.reference_text()
    library = knowledge_base.reference_text("異変図書館")
    assert "隠れた仕様" in plain and "このマップの攻略データ" not in plain
    assert "## 異変図書館" in library and "## 夢想号" not in library


def test_override_note_prefers_higher_values():
    note = knowledge_base.override_note("海賊精鋭")
    assert "普通: 攻撃4 防御2 HP25" in note  # 日本語wikiは20、中国語wikiは25
    assert "+4" in knowledge_base.override_note("龍の咆哮")
    assert knowledge_base.override_note("バッファーシールド") == ""
