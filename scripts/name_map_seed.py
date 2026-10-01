"""name_map.json を作り直す(対応表の元データ)。

    python scripts/name_map_seed.py

1行が「日本語名|英語wikiのページ名|中国語wikiのページ名」。対応がないところは空欄。
名前の対応は翻訳として人が確認して決めたもの。直したら build_name_map.py verify で数値を照合する。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "apadvisor" / "knowledge" / "name_map.json"

CHIPS = """\
8面ダイス|8-Sided Die|“8面”骰子
ATM|ATM|ATM机
おいしいキャンディー|Tasty Candy|可口糖果
お年玉|Red Festival Envelopes|节日红包
しおり|Bookmark|书签
アダプティブコア|Adaptive Core|自适应核心
アドレナリン注射液-一般|Adrenaline|肾上腺素-一般
アドレナリン注射液-高効率|Adrenaline|肾上腺素-高效
イーグルアイ|Sight|鹰眼瞄具
エアバッグ|Air Bag|安全气囊
エクストラ-バッテリー|Extra Battery|额外电池
エナジーソード|Electric Blade|电流剑
エネルギー回収|Energy Recycler|能量回收
エンタメターミナル|Entertainment Terminal|娱乐终端
オシドリペンダント|Lovebird's Jade Pendant|鸳鸯玉佩
カッターナイフ-シャープ|Utility Knife|美工刀-锋利
カッターナイフ-ベーシック|Utility Knife|美工刀-初级
キャッシュカード-アンリミテッド|Bank Card|银行卡-用不完
キャッシュカード-残高多い|Bank Card|银行卡-余额多
キャッシュカード-残高少ない|Bank Card|银行卡-余额少
キャンディー会員証|Candy Rewards Card|糖果会员证
ギガントアンカー|Iron Anchor|大铁锚
サンドクッキー-なかなか|Sandwich Cookie|夹心饼干-可口
サンドクッキー-ウマすぎ|Sandwich Cookie|夹心饼干-美味
サンドクッキー-普通|Sandwich Cookie|夹心饼干-一般
スタンガン|Taser|电击枪
スタンダードサイト|Sight|普通瞄具
スターコインハンマー|Star Coin Hammer|星币锤
スピードローラースケート-上級|Speed Roller Skates|速度轮滑-高级
スピードローラースケート-中級|Speed Roller Skates|速度轮滑-中级
スピードローラースケート-初級|Speed Roller Skates|速度轮滑-初级
スプレー缶|Marking Spray Can|标记喷罐
スポーツリストバンド|Sports Wristband|运动手环
スマートウォッチ|Smartwatch|智能手表
タイマー|Chronometer|计时器
トライデント|Trident|三叉戟
トンポーロウ|Gourmet Stew|大碗炖肉
ハンナの人形|Hanna Doll|汉娜人偶
バッファーシールド|Buffer Shield|缓冲盾牌
ビタミン剤|Vitamin Pills|维生素药丸
ビッグバッグ|Large Backpack|大背包
ヘルメット-一般|Motorcycle Helmet|摩托头盔-一般
ヘルメット-普通|Motorcycle Helmet|摩托头盔-中级
ヘルメット-高級|Motorcycle Helmet|摩托头盔-高级
ボクシンググローブ-上級|Boxing Gloves|拳击手套-高级
ボクシンググローブ-中級|Boxing Gloves|拳击手套-中级
ボクシンググローブ-初級|Boxing Gloves|拳击手套-初级
マジックトーム|Magic Tome|魔法秘典
ライトニングコア|Electric Core|电流核心
レールガン|Electromagnetic Gun|电磁炮
ワープエンジン|Warp Engine|跃迁引擎
会員推薦状|Membership Recommendation Letter|会员推荐信
優雅の羽|Graceful Feather|优雅之羽
原初の意識|Original Consciousness|原初意识
友情バッジ|Friendship Badge|友情微章
古の魔法杖|Ancient Staff|古老法杖
呪いの剣|Cursed Sword|诅咒之剑
夢想号プラモデル|Dreamliner Model|梦想号模型
大鉦|Giant Grand Gong|大铜锣
幻のシーフードスープ|Psychedelic Seafood Soup|迷幻海鲜汤
彩り羽のブレスレット|Colourful Feather Bracelet|彩羽手环
復活人形|Revival Doll|重生人偶
復讐の戟|Revenge Halberd|复仇之戟
循環往来|Endless Cycle|循环往复
忍術手裏剣|Ninja Throwing Darts|忍术飞镖
懐中時計|Timely Pocket Watch|时间怀表
懐中時計（破損）|Timely Pocket Watch|
懐中電灯-強光|Flashlight|手电筒-强光
懐中電灯-爆閃|Flashlight|手电筒-爆闪
手持ち扇風機-大|Handheld Fan|手持风扇-大
手持ち扇風機-小|Handheld Fan|手持风扇-小
探天衛星|Survey Satellite|探天卫星
救急箱-完備治療|Medical Kit|医疗箱-完备治疗
救急箱-緊急治療|Medical Kit|医疗箱-紧急治疗
標的|Target Board|标靶
永久機関|Perpetual Motion Machine|永动机
無限の蛇|Infinite Serpent|无限之蛇
砥石|Sharpening Stone|磨刀石
精良な装備セット|Premium Sword and Shield|精品剑盾
紅茶にケーキ|Tea Cake|佐茶蛋糕
紫色の飛星|Shooting Star|紫色飞星
荒波の御守り|Raging Wave Charm|惊涛御守
虫眼鏡|Magnifying Glass|放大镜
蛇のぬいぐるみ|Snake Plushie|小蛇玩偶
豚の貯金箱|Piggy Bank|小猪存钱罐
貫通の銃|Piercing Gun|贯穿之铳
金色の飛星|Shooting Star|金色飞星
陰陽鯉|Intertwined Twin Carps|交错双鲤
電撃グローブ|Shock Gloves|电击手套
魔法の矢袋|Magic Quiver|魔法箭袋
鯉のぬいぐるみ|Carp Plushie|小鲤鱼玩偶
"""

CARDS = """\
あなたもどうぞ？|What you have, I have.|你有我有
この人です|It's their fault!|是他干的
エナジーバー|Energy Bar|能量补充棒
エリア拒止|Enhanced Barricade|强化拒止
ゴミ拾い|Scavenging|拾荒
シャドウアタック|Shadow Attack|暗影突袭
スリングショット|Slingshot|皮筋弹弓
ソウルリンク|Soul Link|灵魂链接
チャージ|Charge|蓄力
チョコレートケーキ|Chocolate Cake|巧克力蛋糕
ハンバーガー|Hamburger|汉堡
バリア|Barrier|保护屏障
ブリック|Brick|板砖
ボムパス|Time Bomb|传递炸弹
ライフ・ブック(PvE)|Living Page|活体书页
ライフ・ブック(PvP)|Living Page|活体书页
ランダムテレポーター|Random Portal|随机传送门
リモコンダイス|Smart Dice|遥控骰子
レーザー|Laser Beam|激光
ロードブロック|Barricade|路障
不動明王|Immovable|岿然不动
人機融合|Human-Machine Integration|人机交融
会心の一撃||全力攻击
吉星高照|Overflowing Fortune|吉星高照
名刀：ガオー切り|Legendary Sword: Gawu Cut|名刀：嘎呜切
噛みつく|Bite|撕咬
奪取|Snatch|抢夺
定方向発破|Blast|定向爆破
対モンスター用ブリック|Brick|对怪板砖
対モンスター用レーザー|Laser Beam|对怪激光
彩りの羽|Colourful Feather|彩羽
支援|Support|支援
支援タイプのガム|Support Gum|支援口香糖
攻撃(中)|Attack (Card)|攻击(中)
攻撃(大)|Attack (Card)|攻击(大)
攻撃(特大)|Attack (Card)|攻击(特大)
方向選択|Redirection|方向抉择
早すぎるおんな|Hurry Hurry|加急加快
残り火の羽|Ashen Feather|灰烬之羽
毒で毒を制する|Fight Fire with Fire|以毒攻毒
毒牙|Poison Fang|毒牙
爆破専門家|Demolition|爆破专家
爆竹|Firecrackers|高升炮
狂暴|Berserk|狂暴
狂気を抱く|All or Nothing|丧心病狂
王の力|King's Power|王之力
目には目を|Eye for an Eye|以牙还牙
符カード-禍(PvE)|Talisman Card|符卡-祸
符カード-禍(PvP)|Talisman Card|符卡-祸
符カード-福|Talisman Card|符卡-福
罠を仕掛ける|Entrapment|钓鱼执法
罪の疑惑||罪证嫌疑
腐った弁当|Expired Bento|过期便当
自爆|Self-Explosion|自爆
華奢な奢り|Luxurious Feast|奢华大餐
誤った目標||错误的目标
軌道レールキャノン|Railgun|轨道炮
運命の導き|Guidance of Fate|命运的指引
防御(中)|Defense (Card)|防御(中)
防御(大)|Defense (Card)|防御(大)
防御(特大)|Defense (Card)|防御(特大)
頭が良くなるグミ|Smartie Gummy|大聪明软糖
龍の咆哮||龙之咆哮
"""

MONSTERS = """\
お出迎え係|Receptionist Swallow|迎宾燕
さるのびっくり箱|Jack-in-the-Monkey|惊喜猴子盒
さるの助手君|Buzzsaw Monkey Assistant|圆锯猴助手
オシドリ係|Bronze Lovebird Chalice|铜盏鸳鸯
ガオー||
キャンディピニャータ|Candy Pinata|糖果皮纳塔
クジャク係|Bronze Peacock Chalice|铜盏孔雀
グリーティーポット|Sinful Teapot|罪恶茶壶
コエデカフグ|Loudmouth|大嗓门
ゴクチョー|Warden|典狱长
ゴリフランケンちゃん|Mad Ape|科学怪猿
サムライの化身-怨|Martial Spirit - Enmity|武魂灵体-怨
サムライの化身-戮|Martial Spirit - Murder|武魂灵体-戮
サメタラシ|King Shark|海王鲨
センズル係|Bronze Crane Chalice|铜盏仙鹤
ゼリーウィザード|Jelly Wizard|果冻巫师
ゼリーガーディアン|Jelly Guard|果冻护卫
ゼリーファイター|Jelly Fighter|果冻战士
ダーク・フェニックス|Rampant Phoenix|失控凤凰
テックジャイアントガオー|High-Tech Tycoon Gawu|科技巨头嘎呜
トレジャーダル|Treasure Barrel|宝藏桶
ドクター・マサオシュタイン|Mad Scientist|科学怪人
ニワトリ係|Poultry Waiter|家禽服务员
ヒメ(孔雀)|Fen (Peacock Conscious)|姬梦枫（孔雀意识）
ヒメ(鴛鴦)|Fen (Lovebird Conscious)|姬梦枫（鸳鸯意识）
マインドエアシップ|Mindscape Airship|思维飞艇
ムキムキフグ|Martial Trainee|练家子
メカたらし|Mecha Oceanlord|智械海王鲨
メカクォーターバック|Mechanical Quarterback|机械四分卫
メカピッチャー|Mechanical Javelin Thrower|机械投手
メカ中枢|Mechanical Mastermind|智械中枢
メカ海賊サメ|Mecha Pirate Shark|智械海盗鲨
メカ監視員|Mechanical Monitor|机械监控师
人工生命体－厄兆|Synthetic-Omen|人工生命体-噩兆
人工生命体－天崩|Synthetic-Cataclysm|人工生命体-天崩
人工生命体－混乱|Synthetic-Chaos|人工生命体-混乱
凶暴赤霊|Fever Red Spirit|狂躁红灵
刀剣霊|Sword Spirit|刀剑灵体
変なティーポット|Mutant Teapot|变异茶壶
大怪盗|Legendary Thief|大盗
天川真夢梓|Amakawa Mamushi|天川真梦梓
天川真夢梓(味方)|Amakawa Mamushi (Ally)|天川真梦梓(友方)
天川蒼鯉|Amakawa Souri|天川苍鲤
天川蒼鯉(味方)|Amakawa Souri (Ally)|天川苍鲤(友方)
天王寺雅央|Tennoji Masao (Soul Celebration)|天王寺雅央
天王寺雅央(学院警備官)|Tennoji Masao (Security Officer)|学院安全官
天王寺雅央(学院長代理)|Tennoji Masao (Acting Dean)|代理院长
審判長エイミー|Referee Amy|裁判艾米
指揮センター|Ground Control|指挥平台
機械蛇龍|Mechanical Snake Dragon|机械蛇龙
氷刻青霊|Frozen Blue Spirit|冰冷蓝灵
泥棒|Thief|小偷
海賊サメ|Shark Pirate|海盗鲨鱼
海賊王ガオー|King Gawu|海盗王嘎呜
海賊精鋭|Elite Pirate|海盗精锐
爆竹ゲロゲロ|Cracker Croak|爆竹呱呱
獅子舞いガオー|Lion Gawu|舞狮嘎呜
看守|Watcher|看守
蝦兄ぃ|Prawn Soldier|虾布罗
蟹兄ぃ|Crab Soldier|蟹布罗
金ちゃん|Golden Fish|金小鱼
長戟霊|Spear Spirit|长戟灵体
雰囲気系ロボ|Ambient Enlivener Robot|氛围机器人
魔法のティーポット|Magic Teapot|魔法茶壶
"""

CHARACTERS = """\
Z3000|Z3000|Z3000
あめちゃん|Ame|主播女孩
アランナ|Alana|阿兰娜
アル|A.L.|阿尔
カイセイ|HaiQing|蓝海晴
コマチ|Komachi|小町
サイクス|Sykes|赛克斯
シェリー|Sherry|橘雪莉
ジャスミン|Jasmine|茉莉
ジル|Jill|吉尔·斯汀雷
スミカゲ|InkShadow|墨影
テル|Teru|照
ドロシー|Dorothy|多萝西·海兹
ナンシー|Nancy Lu|南希露
ナーディス|Nardis|娜蒂斯
ハンナ|Hanna|远野汉娜
パッドマン|Padman|派德曼
パパラ|Papara|帕帕拉
パルナン|Parunan|帕露南
パンダマン|Pandaman|潘大猛
ヒメ|Fen|姬梦枫
ファニィ|Fanny|芬妮
ボニー|Bonnie|邦妮
マムシ|Mamushi|真梦梓
ミサキ|Misaki|美咲
ミミ|Mimi|米米
メガス|Megas|梅加斯
モーゼス|Moses|摩西
ユメ|Zhao|姬梦朝
リン|Rin|凛
リンリン|LingLing|玲玲
ルカ|Luka|星魅琉华
ルル|Lulu|璐璐
レン|Ren|恋
超てんちゃん|KAngel|超绝最可爱天使酱
"""

EVENTS = """\
さて、戦う！||战斗，爽！
アライグマ繁殖期||小浣熊繁殖季
サーバーエラー||服务器BUG
ショップ大特価||商店大酬宾
ポケットに穴||口袋漏了
不安定なキャンディピニャータ||失控的糖果皮纳塔
取捨選択||取长补短
天からの贈り物||天降之物
天使降臨||天使降临
天崩地裂||天崩地裂
平均配分||平均分配
感電危険||导电危机
手札抹殺||手牌抹×
海賊襲撃||海盗偷袭
混雑した通路||拥挤通道
潜入調査||隐匿调查
熱雷||雷暴将至
疾走(イベント)||疾走
神兵天降||天降神兵
隔離対策||集中隔离
雷区||雷区
食品安全||食品安全
高温警告||红温警告
"""

MAPS = """\
夢想号|Dreama|星趴·梦想号
御魂の祭|Soul Celebration|御魂庆典
水郷古鎮|Water Town|水乡古镇
魔法学院(チクリ場)|Magic Academy|魔法学院
魔法学院(スイーツ場)|Magic Academy|魔法学院
龍宮遊園地|Dragon Palace Amusement Park|龙宫游乐园
幽魂路地|Ghost Alley|幽魂暗巷
龍星の中庭|Garden Courtyard|园林中庭
予選運動場|Qualifier's Field|海选赛运动场
トーナメント運動場|Knockout Round Stadium|淘汰赛运动场
決勝大会場|Grand Final Arena|决赛大赛场
異変図書館||异变图书馆
"""

COMMENT = (
    "日本語名(日本語wikiのページ名)と、英語wiki(astralparty.miraheze.org)・中国語wiki(wiki.biligame.com/starengine)の"
    "ページ名の対応表。英語wikiは等級違いのチップやカードを1ページにまとめているので、複数の日本語名が同じ英語ページを指すことがある。"
    "scripts/name_map_seed.py から生成し、scripts/build_name_map.py verify で各wikiの数値と照合する。"
)


def rows(text: str) -> list[dict[str, str]]:
    out = []
    for line in text.strip().splitlines():
        ja, en, zh = line.split("|")
        row = {"ja": ja}
        if en:
            row["en"] = en
        if zh:
            row["zh"] = zh
        out.append(row)
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    result = {
        "_comment": COMMENT,
        "chip": rows(CHIPS),
        "card": rows(CARDS),
        "monster": rows(MONSTERS),
        "character": rows(CHARACTERS),
        "event": rows(EVENTS),
        "map": rows(MAPS),
    }
    text = json.dumps(result, ensure_ascii=False, indent=1)
    # 1行に1件で読みやすく
    text = re.sub(r"\{\n\s+(\"ja\"[^{}]*?)\n\s+\}", lambda m: "{" + re.sub(r"\n\s+", " ", m.group(1)) + "}", text)
    OUT.write_text(text + "\n", encoding="utf-8")
    print({c: len(v) for c, v in result.items() if c != "_comment"}, "→", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
