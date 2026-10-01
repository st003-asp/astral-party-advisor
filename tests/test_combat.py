from fractions import Fraction as F

import pytest

from apadvisor import combat
from apadvisor.combat import STANDARD_CARDS as C
from apadvisor.dice import D6, Dist


def test_dist_basics():
    two = D6 + D6
    assert two.p_eq(7) == F(1, 6)
    assert two.mean() == 7
    assert Dist.uniform(1, 3).p_ge(2) == F(2, 3)
    with pytest.raises(ValueError):
        Dist({1: F(1, 2)})


def test_damage_is_at_least_one():
    assert combat.resolve_damage(3, 10) == 1
    assert combat.resolve_damage(12, 7) == 5
    assert combat.resolve_damage(12, 7, bonus=1) == 6  # マーク
    assert combat.resolve_damage(3, 10, reduction=1) == 0  # 軽減は最低1のあと


def test_evade_table_matches_wiki():
    # 攻撃側の目 1..6 → 必要な目 2,3,4,5,6,6
    expected = [F(5, 6), F(4, 6), F(3, 6), F(2, 6), F(1, 6), F(1, 6)]
    assert [combat.evade_success_prob(a) for a in range(1, 7)] == expected


def test_hp1_enemy_always_dies_without_cards():
    result = combat.analyze_attack(atk=0, enemy_def=10, enemy_hp=1)
    assert result.p_kill == 1


def test_attack_matches_brute_force():
    atk, enemy_def, hp = 3, 2, 6
    hits = total = 0
    for a in range(1, 7):
        for d in range(1, 7):
            for c in range(1, 4):  # 攻撃(中)
                total += 1
                hits += max(1, atk + a + c - enemy_def - d) >= hp
    result = combat.analyze_attack(atk, enemy_def, hp, [C["攻撃(中)"]])
    assert result.p_kill == F(hits, total)


def test_attack_options_respect_cost_limit_and_keep_no_counter():
    hand = [C["攻撃(中)"], C["攻撃(大)"], C["シャドウアタック"], C["名刀：ガオー切り"]]
    options = combat.attack_options(3, 2, 6, hand, cost_limit=3)
    assert all(o.cost <= 3 for o in options)
    assert options[0].cards == ()
    assert not any("名刀：ガオー切り" in o.cards for o in options)
    assert any(o.no_counter for o in options)
    kills = [o.p_kill for o in options if not o.no_counter]
    assert kills == sorted(kills)  # コストを払うほど撃破率が上がるものだけ残る


def test_mark_bonus_and_weakness():
    base = combat.analyze_attack(3, 2, 6)
    marked = combat.analyze_attack(3, 2, 6, bonus=1)
    weak = combat.analyze_attack(3, 2, 6, enemy_die=Dist.const(0))
    assert marked.p_kill > base.p_kill
    assert weak.p_kill > base.p_kill


def test_evading_enemy():
    # 回避してくる敵: 自分の目が1なら 5/6 で避けられる。当たれば防御0
    result = combat.attack_damage_dist(5, 99, enemy_evades=True, my_die=Dist.const(1))
    assert result.p_eq(0) == F(5, 6)
    assert result.p_eq(6) == F(1, 6)


def test_defend_vs_dodge_known_die():
    # 敵攻撃4+出目1=5、自分の防御2
    analysis = combat.defense_options(4, 2, 9, attacker_die=1)
    defend = next(o for o in analysis.options if o.action == "defend")
    dodge = next(o for o in analysis.options if o.action == "dodge")
    assert defend.expected_damage == F(7, 6)  # 出目1のときだけ2ダメージ
    assert dodge.p_dodge_success == F(5, 6)
    assert dodge.expected_damage == F(5, 6)
    assert dodge.damage.max() == 5
    assert "安定" in analysis.note  # 期待値で回避を推すときは分散の注意を添える


def test_hp1_must_dodge():
    analysis = combat.defense_options(6, 3, 1, [C["防御(特大)"]], attacker_die=3)
    assert analysis.best.action == "dodge"
    assert analysis.best.p_death == F(1, 2)
    assert all(o.p_death == 1 for o in analysis.options if o.action == "defend")


def test_defense_card_used_when_it_prevents_death():
    # 敵合計13、防御1、HP6: カードなしだと 13-1-d>=6 が常に成り立ち必ず倒れる
    analysis = combat.defense_options(8, 1, 6, [C["防御(特大)"]], attacker_die=5)
    assert analysis.best.action == "defend"
    assert analysis.best.cards == ("防御(特大)",)
    assert analysis.best.p_death < 1


def test_shield_makes_dodge_free():
    analysis = combat.defense_options(20, 0, 3, attacker_die=6, shield=True)
    assert analysis.best.p_death == 0
    assert analysis.best.damage.max() == 0


def test_moses_dodge_die():
    # 弱点看破2 → 回避ダイスは3〜6。敵の目が2以下なら確定回避
    die = Dist.uniform(3, 6)
    assert combat.evade_success_prob(2, evader_die=die) == 1
    assert combat.evade_success_prob(3, evader_die=die) == F(3, 4)


def test_counter_only_when_enemy_survives():
    options = combat.engage_options(10, 0, 1, [], 3, enemy_atk=9, enemy_counters=True, my_def=0, my_hp=5)
    assert options[0].p_counter == 0  # HP1は必ず倒せるので反撃なし
    options = combat.engage_options(
        0, 9, 20, [C["シャドウアタック"]], 3, enemy_atk=9, enemy_counters=True, my_def=0, my_hp=5
    )
    plain = next(o for o in options if not o.attack.cards)
    shadow = next(o for o in options if o.attack.no_counter)
    assert plain.p_counter == 1 and plain.p_death > 0
    assert shadow.p_counter == 0 and shadow.p_death == 0


def test_critical_strike_multiplies_final_attack():
    # 攻撃力3、出目4固定、会心の一撃(+6、1.5倍) → (3+4+6)*1.5 = 19.5 → 19。敵の防御合計は2+0
    result = combat.attack_damage_dist(
        3, 2, [C["会心の一撃"]], my_die=Dist.const(4), enemy_die=Dist.const(0)
    )
    assert result == Dist.const(17)


def test_parse_cards():
    cards = combat.parse_cards(["攻撃（中）", {"name": "ガオー斬り"}, {"name": "噛みつく", "cost": 2, "lo": 4}])
    assert [c.name for c in cards] == ["攻撃(中)", "名刀：ガオー切り", "噛みつく"]
    assert cards[2].lo == cards[2].hi == 4
    with pytest.raises(KeyError):
        combat.parse_cards(["存在しないカード"])
