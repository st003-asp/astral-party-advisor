"""コマンドライン入口。

    apadvisor analyze replay.mp4 --me ミサキ --difficulty 悪夢 --map 夢想号
    apadvisor advise screenshot.png --me ルル
    apadvisor calc defend --enemy-atk 6 --die 2 --def 2 --hp 5 --cards "防御(中)"
    apadvisor lookup バッファーシールド
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic

from . import chips, combat, events, knowledge_base, movement

# 料金(USD / 100万トークン)。費用の目安表示用。キャッシュ書き込みは入力の1.25倍(1時間保持は2倍)で概算
_PRICES = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.2},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.1},
}


def load_dotenv() -> None:
    """カレントフォルダとプロジェクト直下の .env から環境変数を読む(既に設定済みのものは上書きしない)。

    APIキーをシステムの環境変数に登録しなくても、.env に ANTHROPIC_API_KEY=... と書けば使える。
    """
    for path in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _base_context(args) -> str:
    parts = []
    if args.me and getattr(args, "mode", "live") != "replay":  # replay では対象プレイヤーの行にまとめて書く
        parts.append(f"録画者の使用キャラ: {args.me}")
    if args.party:
        parts.append(f"パーティ(手番順): {args.party}")
    if args.difficulty:
        parts.append(f"難易度: {args.difficulty}")
    if args.map:
        parts.append(f"マップ: {args.map}")
    if args.note:
        parts.append(f"メモ: {args.note}")
    return "\n".join(parts)


def _add_context_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--me", help="自分の使用キャラ(例: ミサキ)")
    p.add_argument("--party", help="パーティを手番順に(例: カイセイ,ルル,ミサキ,ナンシーロー)")
    p.add_argument("--difficulty", choices=["普通", "困難", "悪夢", "狂気", "極限"])
    p.add_argument("--map", help="マップ名(例: 夢想号)")
    p.add_argument("--note", help="潜在解放の有無など、画面から分からない補足")
    p.add_argument("--model", help="使用モデル(既定: claude-opus-5-5、環境変数 APADVISOR_MODEL でも指定可)")
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"], help="助言の思考の深さ")


def _make_advisor(args):
    from .llm import DEFAULT_MODEL, Advisor

    return Advisor(model=args.model or DEFAULT_MODEL, scan_model=getattr(args, "scan_model", None))


def _report_usage(usage, model: str) -> None:
    price = _PRICES.get(model, _PRICES["claude-opus-5-5"])
    cost = (
        usage.input_tokens * price["input"]
        + usage.output_tokens * price["output"]
        + usage.cache_read_tokens * price["cache_read"]
        + usage.cache_write_tokens * price["input"] * 2
    ) / 1_000_000
    print(
        f"API利用: {usage.requests}リクエスト / 入力{usage.input_tokens:,} 出力{usage.output_tokens:,} "
        f"キャッシュ読{usage.cache_read_tokens:,} 書{usage.cache_write_tokens:,} トークン "
        f"({model} の定価換算で約 ${cost:.2f}。キャッシュ書き込みは高めに見積もり)",
        file=sys.stderr,
    )


def cmd_analyze(args) -> int:
    from .analyze import analyze
    from .report import write_html, write_markdown

    video = Path(args.video)
    out_dir = Path(args.out) if args.out else Path("reports") / video.stem
    advisor = None if args.frames_only else _make_advisor(args)
    if args.mode == "replay":
        from .replay import analyze_replay

        decisions, advice = analyze_replay(
            video,
            out_dir,
            advisor,
            player=args.player,
            order=args.order,
            me_character=args.me,
            base_context=_base_context(args),
            interval=args.interval if args.interval is not None else 0.5,
            diff_threshold=args.threshold if args.threshold is not None else 2.0,
            frames_per_turn=args.frames_per_turn,
            start=args.start,
            end=args.end,
            max_turns=args.max_turns,
            include_enemy_turns=not args.skip_enemy_turns,
            effort=args.effort,
            frames_only=args.frames_only,
        )
    else:
        decisions, advice = analyze(
            video,
            out_dir,
            advisor,
            base_context=_base_context(args),
            interval=args.interval if args.interval is not None else 2.0,
            diff_threshold=args.threshold if args.threshold is not None else 4.0,
            start=args.start,
            end=args.end,
            max_frames=args.max_frames,
            max_decisions=args.max_decisions,
            effort=args.effort,
            frames_only=args.frames_only,
        )
    if args.frames_only:
        print(f"フレームを {out_dir / 'frames'} に保存しました(APIは呼んでいません)。")
        return 0
    title = f"{video.stem} の解析"
    html = write_html(out_dir, decisions, advice, title)
    write_markdown(out_dir, decisions, advice, title)
    _report_usage(advisor.usage, advisor.model)
    print(f"レポート: {html}")
    return 0


def cmd_advise(args) -> int:
    advisor = _make_advisor(args)
    advice = advisor.advise([Path(p) for p in args.images], context=_base_context(args), effort=args.effort)
    if args.json:
        _print(advice.model_dump())
    else:
        print(f"【状況】{advice.situation}")
        print(f"【推奨】{advice.recommended}")
        print(f"【理由】{advice.reasoning}")
        for o in advice.options:
            print(f"  - {o.option}: {o.evaluation}")
        if advice.unreadable:
            print(f"【読めなかった点】{'、'.join(advice.unreadable)}")
        print(f"【確度】{advice.confidence}")
    _report_usage(advisor.usage, advisor.model)
    return 0


def _cards(text: str | None, side: str) -> list[combat.BattleCard]:
    if not text:
        return []
    specs = []
    for item in text.split(","):
        item = item.strip()
        if "=" in item:  # 名前=コスト:最小-最大 例: 噛みつく=2:4-4
            name, rest = item.split("=", 1)
            cost, rng = rest.split(":")
            lo, _, hi = rng.partition("-")
            specs.append({"name": name, "cost": int(cost), "lo": int(lo), "hi": int(hi or lo), "side": side})
        else:
            specs.append(item)
    return combat.parse_cards(specs)


def cmd_calc(args) -> int:
    if args.what == "attack":
        results = combat.engage_options(
            args.atk,
            args.enemy_def,
            args.enemy_hp,
            _cards(args.cards, "attack"),
            args.cost,
            enemy_atk=args.enemy_atk or 0,
            enemy_counters=args.enemy_atk is not None,
            my_def=args.my_def,
            my_hp=args.hp,
            bonus=args.mark,
        )
        _print([r.summary() for r in results])
    elif args.what == "defend":
        analysis = combat.defense_options(
            args.enemy_atk,
            args.my_def,
            args.hp,
            _cards(args.cards, "defense"),
            args.cost,
            attacker_die=args.die,
            reduction=args.reduction,
            shield=args.shield,
        )
        _print(analysis.summary())
    elif args.what == "move":
        _print(movement.reach_summary(args.distance, args.bonus, args.dice, args.sides))
    elif args.what == "chips":
        _print(chips.chip_offer_summary(args.level, args.difficulty, args.purchases))
    elif args.what == "event":
        _print(events.event_odds(args.progress))
    return 0


def cmd_export(args) -> int:
    from .export import export_report

    src = Path(args.report_dir)
    index = export_report(
        src,
        Path(args.dest),
        title=args.title or "解析レポートの例",
        extra_names=[n.strip() for n in (args.names or "").split(",") if n.strip()],
    )
    print(f"書き出しました: {index}")
    print("公開する前に、名前が残っていないか画像と文章を自分の目で確認してください。")
    return 0


def cmd_lookup(args) -> int:
    print(knowledge_base.lookup(args.name))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apadvisor", description="アストラルパーティー PvE のプレイ解析")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="録画を解析してレポートを作る")
    p.add_argument("video")
    p.add_argument("--out", help="出力フォルダ(既定: reports/<動画名>)")
    p.add_argument(
        "--mode",
        choices=["replay", "live"],
        default="replay",
        help="replay: ゲーム内のリプレイ再生を録画したもの(既定) / live: プレイ中の画面を録画したもの",
    )
    p.add_argument("--player", help="[replay] 講評する対象プレイヤーの表示名(画面左の一覧の名前)")
    p.add_argument("--order", type=int, choices=[1, 2, 3, 4], help="[replay] 対象プレイヤーの手番順(表示名の代わりに指定できる)")
    p.add_argument("--max-turns", type=int, help="[replay] 講評する手番の数の上限(お試し用)")
    p.add_argument("--skip-enemy-turns", action="store_true", help="[replay] 敵の手番(防御/回避の講評)を省く")
    p.add_argument(
        "--frames-per-turn", type=int, default=20, help="[replay] 1手番の講評に使う画像の最大枚数(増やすと費用も増える)"
    )
    p.add_argument("--interval", type=float, help="フレームを見る間隔(秒)。既定は replay 0.5 / live 2.0")
    p.add_argument("--threshold", type=float, help="画面が変わったとみなす差分のしきい値")
    p.add_argument("--start", type=float, default=0.0, help="解析開始位置(秒)")
    p.add_argument("--end", type=float, help="解析終了位置(秒)")
    p.add_argument("--max-frames", type=int, help="[live] 抽出するフレーム数の上限")
    p.add_argument("--max-decisions", type=int, help="[live] 助言する場面数の上限(お試し用)")
    p.add_argument("--frames-only", action="store_true", help="フレーム抽出だけ行う(APIを呼ばない)")
    p.add_argument("--scan-model", help="画面分類だけ別のモデルにする(例: claude-haiku-4-5)")
    _add_context_args(p)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("advise", help="スクリーンショット1場面に助言する")
    p.add_argument("images", nargs="+")
    p.add_argument("--json", action="store_true")
    _add_context_args(p)
    p.set_defaults(func=cmd_advise)

    p = sub.add_parser("calc", help="確率計算だけ行う(APIを使わない)")
    calc = p.add_subparsers(dest="what", required=True)
    c = calc.add_parser("attack", help="攻撃カードごとの撃破率")
    c.add_argument("--atk", type=int, required=True)
    c.add_argument("--enemy-def", type=int, required=True)
    c.add_argument("--enemy-hp", type=int, required=True)
    c.add_argument("--cards", help='カンマ区切り。例: "攻撃(中),攻撃(大)" / 独自カードは 名前=コスト:最小-最大')
    c.add_argument("--cost", type=int, default=3, help="バトルコスト上限")
    c.add_argument("--mark", type=int, default=0, help="敵の被ダメ+N")
    c.add_argument("--enemy-atk", type=int, help="指定すると反撃持ちとして被害も計算")
    c.add_argument("--my-def", type=int, default=0)
    c.add_argument("--hp", type=int, default=99)
    c = calc.add_parser("defend", help="防御か回避か")
    c.add_argument("--enemy-atk", type=int, required=True)
    c.add_argument("--die", type=int, help="敵のダイス目(見えていれば)")
    c.add_argument("--def", dest="my_def", type=int, required=True)
    c.add_argument("--hp", type=int, required=True)
    c.add_argument("--cards", help="手札の防御カード(カンマ区切り)")
    c.add_argument("--cost", type=int, default=3)
    c.add_argument("--reduction", type=int, default=0, help="被ダメ軽減")
    c.add_argument("--shield", action="store_true", help="ジュジュシールドあり")
    c = calc.add_parser("move", help="あるマスに止まれる/届く確率")
    c.add_argument("--distance", type=int, required=True)
    c.add_argument("--bonus", type=int, default=0)
    c.add_argument("--dice", type=int, default=1, help="ダイスの数(早すぎるおんなで2)")
    c.add_argument("--sides", type=int, default=10, help="ダイスの面数(PvEの移動は10)")
    c = calc.add_parser("chips", help="チップのレアリティ出現率")
    c.add_argument("--level", type=int, required=True)
    c.add_argument("--difficulty", default="悪夢", choices=["普通", "困難", "悪夢", "狂気", "極限"])
    c.add_argument("--purchases", type=int, default=0)
    c = calc.add_parser("event", help="イベントマスの発生率")
    c.add_argument("--progress", type=int, required=True)
    p.set_defaults(func=cmd_calc)

    p = sub.add_parser("export", help="解析結果を、プレイヤー名を伏せたHTMLとして書き出す(APIは使わない)")
    p.add_argument("report_dir", help="解析結果のフォルダ(例: reports/録画の名前)")
    p.add_argument("--dest", required=True, help="書き出し先のフォルダ")
    p.add_argument("--title", help="レポートの題名")
    p.add_argument("--names", help="ほかに伏せたい名前(カンマ区切り)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("lookup", help="カード・チップ・敵などの説明を引く")
    p.add_argument("name")
    p.set_defaults(func=cmd_lookup)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except anthropic.AuthenticationError:
        print("APIキーが無効です。環境変数 ANTHROPIC_API_KEY を確認してください。", file=sys.stderr)
    except anthropic.RateLimitError:
        print("レート制限に達しました。少し待って同じコマンドを再実行すると続きから再開します。", file=sys.stderr)
    except anthropic.APIStatusError as exc:
        print(f"APIエラー({exc.status_code}): {exc.message}", file=sys.stderr)
    except anthropic.APIConnectionError:
        print("APIに接続できませんでした。ネットワークを確認してください。", file=sys.stderr)
    except anthropic.AnthropicError as exc:
        print(exc, file=sys.stderr)
        print("APIキー(環境変数 ANTHROPIC_API_KEY)が設定されているか確認してください。", file=sys.stderr)
    except (OSError, KeyError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
