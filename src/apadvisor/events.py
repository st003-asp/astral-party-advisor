"""イベントマス(全員イベント)で何が起きやすいかの統計。

有志プレイヤーの実測データ(knowledge/event_tile_stats.json)を進捗バーの値別に引く。
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

KIND_LABEL = {"good": "得", "bad": "損", "mixed": "状況次第"}


@lru_cache(maxsize=1)
def load_stats() -> dict:
    text = resources.files("apadvisor.knowledge").joinpath("event_tile_stats.json").read_text(encoding="utf-8")
    return json.loads(text)


def event_odds(progress: int) -> dict:
    """進捗バーが progress(1〜15、範囲外は端に丸める)のときにイベントマスを踏んだ場合の発生率。

    estimated_pct は提供データの推定値、observed_pct はその進捗での実測回数から出した素の割合。
    実測は1列あたり50〜240回なので、数%の差は誤差の範囲。
    """
    stats = load_stats()
    columns = stats["columns"]
    col = max(columns[0], min(progress, columns[-1])) - columns[0]
    total = stats["totals"][col]

    events = []
    by_kind_est = {"good": 0.0, "bad": 0.0, "mixed": 0.0}
    by_kind_obs = {"good": 0.0, "bad": 0.0, "mixed": 0.0}
    est_sum = sum(e["estimated_pct"][col] for e in stats["events"].values())
    for name, e in stats["events"].items():
        est = e["estimated_pct"][col] * 100 / est_sum if est_sum else 0.0
        obs = e["counts"][col] * 100 / total if total else 0.0
        by_kind_est[e["kind"]] += est
        by_kind_obs[e["kind"]] += obs
        if est or obs:
            events.append(
                {
                    "event": name,
                    "kind": KIND_LABEL[e["kind"]],
                    "effect": e.get("effect", ""),
                    "estimated_pct": round(est, 1),
                    "observed_pct": round(obs, 1),
                    "observed_count": e["counts"][col],
                }
            )
    events.sort(key=lambda x: -x["estimated_pct"])
    return {
        "progress": columns[col],
        "sample_size": total,
        "by_kind_estimated_pct": {KIND_LABEL[k]: round(v, 1) for k, v in by_kind_est.items()},
        "by_kind_observed_pct": {KIND_LABEL[k]: round(v, 1) for k, v in by_kind_obs.items()},
        "events": events,
    }


def summary_table() -> str:
    """システムプロンプト用の短い表(進捗ごとの 得/損/状況次第 の割合)。"""
    lines = ["進捗 | 得% | 損% | 状況次第% | 特記"]
    for p in load_stats()["columns"]:
        odds = event_odds(p)
        kinds = odds["by_kind_estimated_pct"]
        notable = [
            f"{e['event']}{e['estimated_pct']:.0f}%"
            for e in odds["events"]
            if e["event"] in ("熱雷", "天崩地裂", "高温警告", "神兵天降") and e["estimated_pct"] >= 5
        ]
        lines.append(f"{p} | {kinds['得']:.0f} | {kinds['損']:.0f} | {kinds['状況次第']:.0f} | {'、'.join(notable)}")
    return "\n".join(lines)


def event_reference() -> str:
    """システムプロンプト用: 各イベントの効果と、進捗ごとの損得の割合。"""
    effects = "\n".join(
        f"- {name}({KIND_LABEL[e['kind']]}): {e['effect']}" for name, e in load_stats()["events"].items()
    )
    return effects + "\n\n" + summary_table()
