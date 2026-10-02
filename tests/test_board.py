import json
from apadvisor import board as board_module
from apadvisor.board import Board
from apadvisor.dice import Dist
from apadvisor.movement import move_dist

SMALL = {
    "tiles": {
        "s": {"kind": "金儲け", "next": ["a", "z"]},
        "z": {"kind": "災厄", "next": ["s"]},
        "a": {"kind": "疾走", "next": ["s", "l1", "r1"]},
        "l1": {"kind": "イベント", "next": ["a", "l2"]},
        "l2": {"kind": "ショップ", "next": ["l1", "l3"]},
        "l3": {"kind": "カード報酬", "next": ["l2"]},
        "r1": {"kind": "モンスター", "next": ["a", "r2"]},
        "r2": {"kind": "セーフティポイント", "next": ["r1", "r3"]},
        "r3": {"kind": "リカバリー", "next": ["r2"]},
    }
}


def test_routes_on_small_board():
    board = Board.from_dict(SMALL)
    # z から来て s にいる。引き返せないので a へ進み、a で左右に分かれる
    assert board.exits("s", "z") == ["a"]
    assert board.first_choices("s", "z", 6) == ["l1", "r1"]
    table = board.route_table("s", "z", Dist.uniform(1, 3))
    left = table["l1 イベント の方向"]
    right = table["r1 モンスター の方向"]
    assert left["止まるマスの種類(%)"] == {"疾走": 33.3, "イベント": 33.3, "ショップ": 33.3}
    assert left["ショップを通る(%)"] == 33.3
    assert left["セーフティポイントか自分のスタートポイントで止まれる(%)"] == 0
    assert right["セーフティポイントか自分のスタートポイントで止まれる(%)"] == 33.3
    assert right["出目ごとの止まるマス"]["3"] == ["r2 セーフティポイント"]
    assert left["この先のマス"][:2] == ["l1 イベント", "l2 ショップ"]
    # 行き止まりでは引き返す
    assert board.paths("l3", "l2", 1) == [("l2",)]


def test_no_branch_within_reach():
    board = Board.from_dict(SMALL)
    table = board.route_table("l1", "a", Dist.uniform(1, 2))
    assert list(table) == ["(分岐なし)"]
    assert table["(分岐なし)"]["止まるマスの種類(%)"] == {"ショップ": 50.0, "カード報酬": 50.0}


def test_real_maps_are_consistent():
    names = board_module.map_names()
    assert "夢想号" in names and "異変図書館" in names
    for name in names:
        board = board_module.get_board(name)
        for tid, tile in board.tiles.items():
            assert tile["next"], (name, tid)
            for nxt in tile["next"]:
                assert tid in board.tiles[nxt]["next"], (name, tid, nxt)  # つながりは双方向
    assert board_module.find_map("異変図書館(協力チャレンジ)") == "異変図書館"
    assert board_module.find_map("どこにもないマップ") is None
    assert board_module.start_tile("夢想号", 1) == ("E6", "F6")
    assert board_module.start_tile("夢想号", None) is None
    assert "E6: スタートポイント(1番手)" in board_module.board_text("夢想号")
    assert board_module.board_text("どこにもないマップ") == ""


def test_route_on_dreama_from_start():
    board = board_module.get_board("夢想号")
    tile, facing = board_module.start_tile("夢想号", 1)
    # 1番手は E6 から F6(疾走)の方向へ。facing の反対側から来た扱いにする
    behind = next(n for n in board.tiles[tile]["next"] if n != facing)
    table = board.route_table(tile, behind, move_dist())
    first = next(iter(table.values()))
    assert first["出目ごとの止まるマス"]["1"] == ["F6 疾走"]
    total = sum(v for v in first["止まるマスの種類(%)"].values())
    assert abs(total - 100) < 0.5


def test_locate_tile_lists_candidates():
    from apadvisor.tools import run_tool

    text, is_error = run_tool(
        "locate_tile", {"map": "異変図書館", "landed_kind": "セーフティポイント", "position": "H3", "came_from": "H2"}
    )
    assert not is_error
    found = {c["マス"]: c["歩数"] for c in json.loads(text)["候補"]}
    assert found == {"J5 セーフティポイント": 3, "C5 セーフティポイント": 7}

    # 開始位置が分からなければ、その種類のマスをすべて返す
    text, _ = run_tool("locate_tile", {"map": "異変図書館", "landed_kind": "ショップ"})
    assert [c["マス"] for c in json.loads(text)["候補"]] == ["H3 ショップ", "H7 ショップ"]

    text, is_error = run_tool("locate_tile", {"map": "異変図書館", "landed_kind": "温泉"})
    assert is_error and "セーフティポイント" in text


def test_route_from_start_point_follows_initial_facing():
    from apadvisor.tools import run_tool

    # 4番手のスタート F0 は G1 を向いている。3歩で H3 のショップ
    text, is_error = run_tool("route_odds", {"map": "異変図書館", "position": "F0", "fixed_move": 3})
    assert not is_error
    table = json.loads(text)
    assert list(table) == ["(分岐なし)"] and table["(分岐なし)"]["出目ごとの止まるマス"] == {"3": ["H3 ショップ"]}
    # 来た方向を指定すれば、そちらが優先(試合の途中でスタートポイントを通るとき)
    text, _ = run_tool("route_odds", {"map": "異変図書館", "position": "F0", "came_from": "G1", "fixed_move": 3})
    assert json.loads(text)["(分岐なし)"]["出目ごとの止まるマス"] == {"3": ["C0 リカバリー"]}


def test_without_tile_ids_hides_wiki_coordinates():
    plain = board_module.without_tile_ids
    text = "F0からH3(ショップ)へ進み、次はI3方向。J5のセーフティポイントまで3歩。HP3、Lv2、A案"
    assert plain(text, "異変図書館") == (
        "スタートポイント(4番手)からショップへ進み、次は災厄方向。セーフティポイントまで3歩。HP3、Lv2、A案"
    )
    assert plain("疾走A1に止まり、自分のスタートポイントF0へ。J5セーフティポイント側", "異変図書館") == (
        "疾走に止まり、自分のスタートポイント(4番手)へ。セーフティポイント側"
    )
    assert plain("C4に止まった", None) == "C4に止まった"  # 盤面データがなければ何もしない


def test_long_move_reports_only_what_is_passed():
    from apadvisor.tools import run_tool

    # 早すぎるおんな(ダイス2個、最大20歩): 止まるマスの内訳は出さず、通過の確率だけにする
    text, is_error = run_tool("route_odds", {"map": "異変図書館", "position": "H3", "came_from": "H2", "dice": 2})
    assert not is_error
    table = json.loads(text)
    assert "止まるマスは狙えない" in table.pop("注")
    for row in table.values():
        assert "出目ごとの止まるマス" not in row and "止まるマスの種類(%)" not in row
        assert "ショップを通る(%)" in row
    # 歩数が決まっていれば(出目が見えた、リモコンダイス)、長くても止まるマスを出す
    text, _ = run_tool("route_odds", {"map": "異変図書館", "position": "H3", "came_from": "H2", "fixed_move": 17})
    assert all("出目ごとの止まるマス" in row for row in json.loads(text).values())
    # ダイス1個に補正+2(最大12歩)はふつうの移動として扱う
    text, _ = run_tool("route_odds", {"map": "異変図書館", "position": "H3", "came_from": "H2", "move_bonus": 2})
    assert "注" not in json.loads(text)
