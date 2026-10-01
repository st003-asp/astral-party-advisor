@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo  astral-party-advisor セットアップ
echo ============================================
echo.

rem --- Python を探す(py ランチャー優先) ---
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Python が見つかりませんでした。
    echo https://www.python.org/downloads/ から Python をインストールしてください。
    echo インストール画面の最初で「Add python.exe to PATH」にチェックを入れてください。
    echo インストール後、もう一度この setup.bat をダブルクリックしてください。
    echo.
    pause
    exit /b 1
)

echo [1/3] 専用の実行環境を作っています...
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 (
        echo 実行環境の作成に失敗しました。
        pause
        exit /b 1
    )
)

echo [2/3] 必要な部品をインストールしています(数分かかります)...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -e ".[scrape]"
if errorlevel 1 (
    echo インストールに失敗しました。インターネットに接続されているか確認してください。
    pause
    exit /b 1
)

echo [3/3] APIキーを書くファイルを用意しています...
if not exist ".env" (
    > ".env" echo ANTHROPIC_API_KEY=ここにAPIキーを貼り付けて上書き保存
    echo.
    echo メモ帳が開きます。「ここにAPIキーを貼り付けて上書き保存」の部分を
    echo 自分のAPIキー^(sk-ant- で始まる文字列^)に書き換えて、上書き保存して閉じてください。
    echo.
    pause
    notepad ".env"
) else (
    echo .env はすでにあります。APIキーを変えたいときはメモ帳で開いて書き換えてください。
)

echo.
echo セットアップが終わりました。
echo  - wikiのデータを取りに行く: fetch_wiki.bat をダブルクリック(初回だけ。1時間以上かかります)
echo  - 録画を解析する: 録画ファイルを analyze.bat の上にドラッグ^&ドロップ
echo.
pause
