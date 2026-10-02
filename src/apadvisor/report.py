"""解析結果を HTML / Markdown のレポートにする。"""

from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path

from .analyze import Decision
from .schemas import Advice

TYPE_LABEL = {
    "turn_action": "手番の行動",
    "branch": "分岐",
    "skill_target": "対象選択",
    "encounter": "戦う/スルー",
    "shop": "ショップ",
    "chip_shop": "チップ購入",
    "chip_select": "チップ選択",
    "battle_attack": "戦闘(攻撃)",
    "battle_defense": "戦闘(防御)",
    "level_up": "レベルアップ",
    "other_decision": "その他",
    "none": "-",
}
VERDICT_ORDER = ["悪手", "疑問手", "許容", "最善", "判定不能"]
VERDICT_CLASS = {"悪手": "bad", "疑問手": "doubt", "許容": "ok", "最善": "best", "判定不能": "na"}

_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1d2330;--muted:#5f6b7c;--line:#dde2ea;
--bad:#c62828;--doubt:#e07b00;--ok:#2f7d5b;--best:#1565c0;--na:#7a8494}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1d222b;--text:#e6e9ee;--muted:#9aa5b5;--line:#2e3542}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font-family:system-ui,"Yu Gothic UI","Hiragino Sans",sans-serif;line-height:1.7}
main{max-width:1000px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 20px}
.summary{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:24px}
.pill{border-radius:999px;padding:4px 14px;font-weight:600;color:#fff;font-size:.9rem}
.bad{background:var(--bad)}.doubt{background:var(--doubt)}.ok{background:var(--ok)}.best{background:var(--best)}.na{background:var(--na)}
article{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
header{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:10px}
.time{font-variant-numeric:tabular-nums;font-weight:700}.type{color:var(--muted)}
.shots{display:flex;gap:8px;overflow-x:auto;margin-bottom:12px}.shots img{height:220px;border-radius:8px;border:1px solid var(--line)}
dl{display:grid;grid-template-columns:7em 1fr;gap:6px 12px;margin:0}dt{color:var(--muted)}dd{margin:0}
.rec{font-weight:700}table{border-collapse:collapse;width:100%;margin-top:8px}
td,th{border-top:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top;font-size:.92rem}
.note{color:var(--muted);font-size:.9rem}
.highlights{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:24px}
.highlights h2{font-size:1.1rem;margin:0 0 8px}.highlights ol{margin:0;padding-left:1.4em}
.highlights li{margin-bottom:12px}.highlights a{color:inherit;text-decoration:none;font-weight:600}
"""


def _highlights(decisions: list[Decision], advice: dict[str, Advice]) -> str:
    """冒頭に置く「見直したい場面」。悪手・疑問手だけを、重いものから並べる。"""
    flagged = [(d, advice[d.id]) for d in _sorted(decisions) if d.id in advice and advice[d.id].verdict in ("悪手", "疑問手")]
    if not flagged:
        return ""
    flagged.sort(key=lambda pair: VERDICT_ORDER.index(pair[1].verdict))
    items = "".join(
        f'<li><a href="#{d.id}"><span class="pill {VERDICT_CLASS[a.verdict]}">{a.verdict}</span> '
        f'<span class="time">{d.timestamp}</span> {escape(TYPE_LABEL.get(a.decision_type, a.decision_type))}</a>'
        f'<div>実際: {escape(a.actual_action or "(読み取れず)")}</div>'
        f'<div class="rec">推奨: {escape(a.recommended)}</div></li>'
        for d, a in flagged
    )
    return f'<section class="highlights"><h2>見直したい場面({len(flagged)}件)</h2><ol>{items}</ol></section>'


def _sorted(decisions: list[Decision]) -> list[Decision]:
    return sorted(decisions, key=lambda d: d.frames[0].time_sec)


def write_html(out_dir: Path, decisions: list[Decision], advice: dict[str, Advice], title: str) -> Path:
    counts = Counter(a.verdict for a in advice.values())
    pills = "".join(
        f'<span class="pill {VERDICT_CLASS[v]}">{v} {counts[v]}</span>' for v in VERDICT_ORDER if counts.get(v)
    )
    cards = []
    for d in _sorted(decisions):
        a = advice.get(d.id)
        if a is None:
            continue
        shots = "".join(
            f'<a href="{escape(f.file)}"><img loading="lazy" src="{escape(f.file)}" alt="{f.timestamp}の画面"></a>'
            for f in [d.frames[-1], *d.after[:1]]
        )
        rows = "".join(f"<tr><td>{escape(o.option)}</td><td>{escape(o.evaluation)}</td></tr>" for o in a.options)
        unreadable = (
            f'<dt>読めなかった点</dt><dd class="note">{escape("、".join(a.unreadable))}</dd>' if a.unreadable else ""
        )
        cards.append(
            f"""<article id="{d.id}">
<header><span class="time">{d.timestamp}</span><span class="type">{TYPE_LABEL.get(a.decision_type, a.decision_type)}</span>
<span class="pill {VERDICT_CLASS[a.verdict]}">{a.verdict}</span><span class="note">確度: {a.confidence}</span></header>
<div class="shots">{shots}</div>
<dl>
<dt>状況</dt><dd>{escape(a.situation)}</dd>
<dt>推奨</dt><dd class="rec">{escape(a.recommended)}</dd>
<dt>実際の行動</dt><dd>{escape(a.actual_action or "(読み取れず)")}</dd>
<dt>理由</dt><dd>{escape(a.reasoning)}</dd>
{unreadable}
</dl>
<table><thead><tr><th>選択肢</th><th>評価</th></tr></thead><tbody>{rows}</tbody></table>
</article>"""
        )
    html = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>{_CSS}</style></head>
<body><main><h1>{escape(title)}</h1>
<p class="sub">自分の判断場面 {len(advice)} 件。評価は選択した時点での妥当性で、出目の結果ではありません。</p>
<div class="summary">{pills}</div>
{_highlights(decisions, advice)}
{''.join(cards)}
</main></body></html>"""
    path = out_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path


def write_markdown(out_dir: Path, decisions: list[Decision], advice: dict[str, Advice], title: str) -> Path:
    counts = Counter(a.verdict for a in advice.values())
    lines = [f"# {title}", "", " / ".join(f"{v}: {counts[v]}" for v in VERDICT_ORDER if counts.get(v)), ""]
    for d in _sorted(decisions):
        a = advice.get(d.id)
        if a is None:
            continue
        lines += [
            f"## {d.timestamp} {TYPE_LABEL.get(a.decision_type, a.decision_type)} — {a.verdict}",
            "",
            f"![{d.timestamp}]({d.frames[-1].file})",
            "",
            f"- 状況: {a.situation}",
            f"- 推奨: **{a.recommended}**",
            f"- 実際の行動: {a.actual_action or '(読み取れず)'}",
            f"- 理由: {a.reasoning}",
        ]
        if a.unreadable:
            lines.append(f"- 読めなかった点: {'、'.join(a.unreadable)}")
        if a.options:
            lines += ["", "| 選択肢 | 評価 |", "|---|---|"]
            lines += [f"| {o.option.replace('|', '/')} | {o.evaluation.replace('|', '/')} |" for o in a.options]
        lines.append("")
    path = out_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
