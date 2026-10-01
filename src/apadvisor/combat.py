"""戦闘の厳密な確率計算。

前提にしているルール(出典: 英語wiki "Combat" / 攻略note):

* 攻撃合計 = 攻撃力 + 6面ダイス + バトルカードの上昇値
* 防御合計 = 防御力 + 6面ダイス + バトルカードの上昇値
* ダメージ = max(1, 攻撃合計 - 防御合計)。どれだけ防御が高くても最低1。
* マークなどの「被ダメ+1」や、ヘルメットなどの「被ダメ軽減」は上の計算のあとに適用。
  軽減後は0になりうる。
* 回避: 攻撃側のダイス目より大きい目を出せば成功(攻撃側が6のときは6で成功)。
  失敗すると防御合計が0になり、攻撃合計がそのままダメージになる。
* 防御側は攻撃側のダイス目を見てから「防御 / 回避」を選べる。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
from typing import Iterable, Literal, Sequence

from .dice import D6, Dist, pct

Side = Literal["attack", "defense"]


@dataclass(frozen=True)
class BattleCard:
    """バトルカード。上昇値は lo〜hi の一様乱数(固定値なら lo == hi)。"""

    name: str
    cost: int
    lo: int
    hi: int
    side: Side
    no_counter: bool = False  # 反撃無効が付くか
    multiplier: Fraction = Fraction(1)  # 最終的な攻撃値に掛かる倍率(会心の一撃の1.5倍など。端数切り捨て)

    @property
    def dist(self) -> Dist:
        return Dist.uniform(self.lo, self.hi)


# ゲーム内のカードテキストどおりの値。名前は日本語版の表記。
STANDARD_CARDS: dict[str, BattleCard] = {
    c.name: c
    for c in [
        BattleCard("攻撃(中)", 1, 1, 3, "attack"),
        BattleCard("攻撃(大)", 2, 1, 6, "attack"),
        BattleCard("攻撃(特大)", 3, 1, 10, "attack"),
        BattleCard("シャドウアタック", 2, 3, 3, "attack", no_counter=True),
        BattleCard("名刀：ガオー切り", 4, 1, 20, "attack"),
        BattleCard("チャージ", 5, 5, 5, "attack"),
        BattleCard("会心の一撃", 3, 6, 6, "attack", multiplier=Fraction(3, 2)),
        BattleCard("龍の咆哮", 3, 4, 4, "attack", no_counter=True),
        BattleCard("防御(中)", 1, 1, 3, "defense"),
        BattleCard("防御(大)", 2, 1, 6, "defense"),
        BattleCard("防御(特大)", 3, 1, 10, "defense"),
    ]
}

# スターレベルごとのバトルコスト上限(Lv0=3、レベルアップごとに+1、最大6)
BATTLE_COST_BY_LEVEL = (3, 4, 5, 6)


def battle_cost_limit(star_level: int) -> int:
    return BATTLE_COST_BY_LEVEL[max(0, min(star_level, 3))]


def _cards_dist(cards: Iterable[BattleCard]) -> Dist:
    total = Dist.const(0)
    for card in cards:
        total = total + card.dist
    return total


def resolve_damage(attack_total: int, defense_total: int, *, bonus: int = 0, reduction: int = 0) -> int:
    """1回の戦闘の最終ダメージ。"""
    return max(0, max(1, attack_total - defense_total) + bonus - reduction)


def evade_success_prob(attacker_die: int, *, evader_die: Dist = D6) -> Fraction:
    """攻撃側のダイス目が attacker_die のとき、回避が成功する確率。

    攻撃側 1〜5: それより大きい目で成功。攻撃側 6: 6 で成功。
    """
    if not 0 <= attacker_die <= 6:
        raise ValueError("attacker_die は 0〜6")
    need = 6 if attacker_die == 6 else attacker_die + 1
    return evader_die.p_ge(need)


# ---------------------------------------------------------------------------
# 攻撃側の分析
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttackResult:
    cards: tuple[str, ...]
    cost: int
    damage: Dist
    p_kill: Fraction
    no_counter: bool

    @property
    def expected_damage(self) -> Fraction:
        return self.damage.mean()

    def summary(self) -> dict:
        return {
            "cards": list(self.cards),
            "cost": self.cost,
            "kill_pct": pct(self.p_kill),
            "expected_damage": round(float(self.expected_damage), 2),
            "min_damage": self.damage.min(),
            "max_damage": self.damage.max(),
            "no_counter": self.no_counter,
        }


def attack_damage_dist(
    atk: int,
    enemy_def: int,
    cards: Sequence[BattleCard] = (),
    *,
    bonus: int = 0,
    reduction: int = 0,
    my_die: Dist = D6,
    enemy_die: Dist = D6,
    enemy_evades: bool = False,
) -> Dist:
    """自分が攻撃したときのダメージ分布。

    bonus: マークなどの被ダメ+N。reduction: 敵側のダメージ軽減。
    enemy_die: モーゼスの弱点状態なら Dist.const(0)。
    enemy_evades: 金ちゃん等「回避してくる敵」。回避されると0ダメージ、
        失敗させれば防御0で全ダメージが通る。
    """
    card_bonus = _cards_dist(cards)
    factor = Fraction(1)
    for card in cards:
        factor *= card.multiplier
    parts: list[tuple[Fraction, Dist]] = []
    for a, pa in my_die.pmf.items():
        attack_total = card_bonus + (atk + a)
        if factor != 1:
            # 「最終的な攻撃値を1.5倍」は、攻撃力+ダイス+カードの合計に掛かるものとして扱う
            attack_total = attack_total.map(lambda t: int(t * factor))
        if enemy_evades:
            p_evaded = evade_success_prob(a, evader_die=enemy_die)
            hit = attack_total.map(lambda t: resolve_damage(t, 0, bonus=bonus, reduction=reduction))
            parts.append((pa * p_evaded, Dist.const(0)))
            parts.append((pa * (1 - p_evaded), hit))
        else:
            defense_total = enemy_die + enemy_def
            dmg = attack_total.combine(
                defense_total, lambda t, d: resolve_damage(t, d, bonus=bonus, reduction=reduction)
            )
            parts.append((pa, dmg))
    return Dist.mix(p for p in parts if p[0])


def analyze_attack(
    atk: int,
    enemy_def: int,
    enemy_hp: int,
    cards: Sequence[BattleCard] = (),
    **kwargs,
) -> AttackResult:
    damage = attack_damage_dist(atk, enemy_def, cards, **kwargs)
    return AttackResult(
        cards=tuple(c.name for c in cards),
        cost=sum(c.cost for c in cards),
        damage=damage,
        p_kill=damage.p_ge(enemy_hp),
        no_counter=any(c.no_counter for c in cards),
    )


def attack_options(
    atk: int,
    enemy_def: int,
    enemy_hp: int,
    hand: Sequence[BattleCard],
    cost_limit: int,
    **kwargs,
) -> list[AttackResult]:
    """手札の攻撃カードの全組み合わせ(コスト上限内)を評価し、効率的なものだけ返す。

    「より安いコストで同等以上の撃破率」が他にある組み合わせは除外する(パレート最適)。
    戻り値はコストの小さい順。先頭は必ず「カードなし」。
    """
    attack_cards = [c for c in hand if c.side == "attack"]
    seen: dict[tuple[str, ...], AttackResult] = {}
    for r in range(len(attack_cards) + 1):
        for combo in combinations(attack_cards, r):
            if sum(c.cost for c in combo) > cost_limit:
                continue
            key = tuple(sorted(c.name for c in combo))
            if key not in seen:
                seen[key] = analyze_attack(atk, enemy_def, enemy_hp, combo, **kwargs)

    results = sorted(seen.values(), key=lambda r: (r.cost, len(r.cards), -r.p_kill, -r.expected_damage))
    frontier: list[AttackResult] = []
    for cand in results:
        dominated = any(
            o.p_kill >= cand.p_kill
            and o.expected_damage >= cand.expected_damage
            and o.cost <= cand.cost
            and (o.no_counter or not cand.no_counter)  # 反撃無効は別の価値なので残す
            for o in frontier
        )
        if not dominated or not cand.cards:
            frontier.append(cand)
    return frontier


# ---------------------------------------------------------------------------
# 防御側の分析
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DefenseResult:
    action: Literal["defend", "dodge"]
    cards: tuple[str, ...]
    cost: int
    damage: Dist
    p_death: Fraction
    p_dodge_success: Fraction | None = None

    @property
    def expected_damage(self) -> Fraction:
        return self.damage.mean()

    def summary(self) -> dict:
        out = {
            "action": "防御" if self.action == "defend" else "回避",
            "cards": list(self.cards),
            "cost": self.cost,
            "death_pct": pct(self.p_death),
            "expected_damage": round(float(self.expected_damage), 2),
            "max_damage": self.damage.max(),
            "no_damage_pct": pct(self.damage.p_eq(0)),
        }
        if self.p_dodge_success is not None:
            out["dodge_success_pct"] = pct(self.p_dodge_success)
        return out


@dataclass(frozen=True)
class DefenseAnalysis:
    options: list[DefenseResult]
    best: DefenseResult
    note: str = ""

    def summary(self) -> dict:
        return {
            "recommended": self.best.summary(),
            "options": [o.summary() for o in self.options],
            "note": self.note,
        }


def _apply_shield(damage: Dist) -> Dist:
    """ジュジュシールド(1回だけ99軽減)。"""
    return damage.map(lambda d: max(0, d - 99))


def defense_options(
    enemy_atk: int,
    my_def: int,
    my_hp: int,
    hand: Sequence[BattleCard] = (),
    cost_limit: int = 3,
    *,
    attacker_die: int | None = None,
    enemy_card_bonus: Dist | None = None,
    bonus: int = 0,
    reduction: int = 0,
    shield: bool = False,
    dodge_die: Dist = D6,
    can_dodge: bool = True,
) -> DefenseAnalysis:
    """攻撃されたときの「防御(+防御カード) / 回避」を比較する。

    enemy_atk: 敵の攻撃力(ダイスを含まない基礎値)。
    attacker_die: 画面に見えている敵のダイス目。まだ見えていなければ None。
    bonus: 自分に付いている被ダメ+N(狂暴など)。reduction: 自分の被ダメ軽減(ヘルメットなど)。
    shield: ジュジュシールドの有無。
    dodge_die: 回避ダイスの分布(モーゼスの弱点看破などで変わる)。

    推奨は「倒される確率が最小 → 期待ダメージが最小 → 使うコストが最小」の順で選ぶ。
    """
    die_dist = D6 if attacker_die is None else Dist.const(attacker_die)
    extra = enemy_card_bonus or Dist.const(0)
    defense_cards = [c for c in hand if c.side == "defense"]

    def finalize(dist: Dist) -> Dist:
        return _apply_shield(dist) if shield else dist

    options: list[DefenseResult] = []
    seen: set[tuple[str, ...]] = set()
    for r in range(len(defense_cards) + 1):
        for combo in combinations(defense_cards, r):
            if sum(c.cost for c in combo) > cost_limit:
                continue
            key = tuple(sorted(c.name for c in combo))
            if key in seen:
                continue
            seen.add(key)
            attack_total = die_dist + extra + enemy_atk
            defense_total = D6 + _cards_dist(combo) + my_def
            dmg = attack_total.combine(
                defense_total, lambda t, d: resolve_damage(t, d, bonus=bonus, reduction=reduction)
            )
            dmg = finalize(dmg)
            options.append(
                DefenseResult("defend", key, sum(c.cost for c in combo), dmg, dmg.p_ge(my_hp))
            )

    if can_dodge:
        parts: list[tuple[Fraction, Dist]] = []
        p_success_total = Fraction(0)
        for a, pa in die_dist.pmf.items():
            p_ok = evade_success_prob(a, evader_die=dodge_die)
            p_success_total += pa * p_ok
            full = (extra + (enemy_atk + a)).map(
                lambda t: resolve_damage(t, 0, bonus=bonus, reduction=reduction)
            )
            parts.append((pa * p_ok, Dist.const(0)))
            parts.append((pa * (1 - p_ok), full))
        dmg = finalize(Dist.mix(p for p in parts if p[0]))
        options.append(DefenseResult("dodge", (), 0, dmg, dmg.p_ge(my_hp), p_success_total))

    best = min(options, key=lambda o: (o.p_death, o.expected_damage, o.cost, o.action == "dodge"))
    note = ""
    if shield:
        note = "ジュジュシールドがあるので回避が得(失敗してもシールドが受け、成功すればシールドが残る)。"
    elif my_hp == 1 and can_dodge:
        note = "HP1では防御しても最低1ダメージで倒れるため、回避しか生き残る手段がない。"
    elif best.action == "dodge":
        defend = min((o for o in options if o.action == "defend"), key=lambda o: (o.p_death, o.expected_damage))
        if defend.p_death == best.p_death:
            note = (
                f"倒される確率は同じで、期待ダメージは回避 {float(best.expected_damage):.2f} / "
                f"防御 {float(defend.expected_damage):.2f}。回避は失敗すると {best.damage.max()} ダメージなので、"
                "差が小さいときやHPに余裕がないときは防御のほうが安定する。"
            )
    return DefenseAnalysis(options=options, best=best, note=note)


# ---------------------------------------------------------------------------
# 反撃を含めた「殴るべきか」の分析
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngageResult:
    attack: AttackResult
    p_counter: Fraction
    expected_damage_taken: Fraction
    p_death: Fraction

    def summary(self) -> dict:
        out = self.attack.summary()
        out.update(
            counter_pct=pct(self.p_counter),
            expected_damage_taken=round(float(self.expected_damage_taken), 2),
            death_pct=pct(self.p_death),
        )
        return out


def counter_damage_dist(
    enemy_atk: int,
    my_def: int,
    my_hp: int,
    *,
    bonus: int = 0,
    reduction: int = 0,
    shield: bool = False,
) -> Dist:
    """敵の反撃1回で受けるダメージ分布(防御カードなし、ダイスを見て最善手を選ぶ前提)。"""
    parts = []
    for a, pa in D6.pmf.items():
        best = defense_options(
            enemy_atk, my_def, my_hp, attacker_die=a, bonus=bonus, reduction=reduction, shield=shield
        ).best
        parts.append((pa, best.damage))
    return Dist.mix(parts)


def engage_options(
    atk: int,
    enemy_def: int,
    enemy_hp: int,
    hand: Sequence[BattleCard],
    cost_limit: int,
    *,
    enemy_atk: int,
    enemy_counters: bool,
    my_def: int,
    my_hp: int,
    my_bonus_taken: int = 0,
    my_reduction: int = 0,
    shield: bool = False,
    **attack_kwargs,
) -> list[EngageResult]:
    """攻撃カードの各候補について、撃破率と「反撃で受ける被害」をまとめて返す。

    敵が反撃持ちで、かつ倒しきれなかったときだけ反撃が来る。
    """
    counter = (
        counter_damage_dist(
            enemy_atk, my_def, my_hp, bonus=my_bonus_taken, reduction=my_reduction, shield=shield
        )
        if enemy_counters
        else Dist.const(0)
    )
    results = []
    for option in attack_options(atk, enemy_def, enemy_hp, hand, cost_limit, **attack_kwargs):
        p_counter = Fraction(0) if (not enemy_counters or option.no_counter) else 1 - option.p_kill
        results.append(
            EngageResult(
                attack=option,
                p_counter=p_counter,
                expected_damage_taken=p_counter * counter.mean(),
                p_death=p_counter * counter.p_ge(my_hp),
            )
        )
    return results


def parse_cards(specs: Iterable[str | dict | BattleCard]) -> list[BattleCard]:
    """カード名、または {"name","cost","lo","hi","side"} の辞書からバトルカードを作る。

    標準カード以外(キャラ固有カードなど)は辞書で値を直接渡す。
    """
    out: list[BattleCard] = []
    for spec in specs:
        if isinstance(spec, BattleCard):
            out.append(spec)
        elif isinstance(spec, str):
            card = STANDARD_CARDS.get(normalize_card_name(spec))
            if card is None:
                raise KeyError(f"未知のバトルカード: {spec}(cost/lo/hi/side を辞書で指定してください)")
            out.append(card)
        else:
            name = normalize_card_name(str(spec["name"]))
            base = STANDARD_CARDS.get(name)
            if base is not None and "lo" not in spec:
                out.append(base)
                continue
            out.append(
                BattleCard(
                    name=name,
                    cost=int(spec.get("cost", 0)),
                    lo=int(spec["lo"]),
                    hi=int(spec.get("hi", spec["lo"])),
                    side=spec.get("side", "attack"),
                    no_counter=bool(spec.get("no_counter", False)),
                    multiplier=Fraction(str(spec.get("multiplier", 1))),
                )
            )
    return out


_NAME_ALIASES = {
    "ガオー斬り": "名刀：ガオー切り",
    "ガオー切り": "名刀：ガオー切り",
    "名刀:ガオー切り": "名刀：ガオー切り",
    "名刀：ガオー斬り": "名刀：ガオー切り",
}


def normalize_card_name(name: str) -> str:
    name = name.strip().replace("（", "(").replace("）", ")")
    return _NAME_ALIASES.get(name, name)
