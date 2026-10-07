@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 先に setup.bat を実行してください。
    pause
    exit /b 1
)

echo カード・チップ・敵・マップの説明を wiki から取得します。
echo 取得したデータはこのPCの data フォルダに保存され、解析のときに参照されます。
echo.
echo  1. 英語wiki・中国語wiki(数分)
echo  2. 日本語wiki(1時間以上。サーバーに負荷をかけないよう、ゆっくり取得します)
echo.
echo 途中でウィンドウを閉じても、もう一度実行すれば続きから再開します。
echo.
pause

".venv\Scripts\python.exe" scripts\scrape_mediawiki.py
".venv\Scripts\python.exe" scripts\scrape_wiki.py

echo.
echo 取得が終わりました。
pause
