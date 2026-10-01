@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 先に setup.bat を実行してください。
    pause
    exit /b 1
)
if "%~1"=="" (
    echo 使い方: 録画ファイル^(mp4^)を、この analyze.bat の上にドラッグ^&ドロップしてください。
    pause
    exit /b 1
)

echo ============================================
echo  リプレイ録画の解析
echo ============================================
echo 録画: %~nx1
echo.
echo 講評するプレイヤーの名前を入力してください。
echo (ゲーム画面の左に並ぶ4人のうち、自分の表示名。例: hogehoge)
set "PLAYER="
set /p "PLAYER=プレイヤー名: "
if not defined PLAYER (
    echo プレイヤー名が入力されませんでした。
    pause
    exit /b 1
)

echo.
echo 難易度を入力してください(普通 / 困難 / 悪夢 / 狂気 / 極限)。分からなければそのまま Enter。
set "DIFFICULTY="
set /p "DIFFICULTY=難易度: "
echo.
echo マップ名を入力してください(例: 夢想号、異変図書館)。分からなければそのまま Enter。
set "MAPNAME="
set /p "MAPNAME=マップ: "

echo.
echo 最初はお試しで3手番だけ解析できます(費用は1ドル未満の見込み)。
echo 続きは、同じ録画をもう一度ドラッグ^&ドロップして「全部」を選べば、済んだ分を飛ばして再開します。
set "SCOPE="
set /p "SCOPE=3手番だけ試す場合は 3、全部解析する場合はそのまま Enter: "

set "ARGS=--player "%PLAYER%" --model claude-sonnet-5-5"
if defined DIFFICULTY set "ARGS=%ARGS% --difficulty %DIFFICULTY%"
if defined MAPNAME set "ARGS=%ARGS% --map "%MAPNAME%""
if defined SCOPE set "ARGS=%ARGS% --max-turns %SCOPE%"

echo.
echo 解析を始めます。1手番につき1〜2分かかります。ウィンドウは閉じないでください。
echo.
".venv\Scripts\apadvisor.exe" analyze "%~1" --out "reports\%~n1" %ARGS% %APADVISOR_EXTRA_ARGS%
if errorlevel 1 (
    echo.
    echo 解析が途中で止まりました。上のメッセージを確認してください。
    echo もう一度ドラッグ^&ドロップすると、済んだところから再開します。
    pause
    exit /b 1
)

if exist "reports\%~n1\report.html" start "" "reports\%~n1\report.html"
echo.
echo 終わりました。レポートがブラウザで開きます。
echo (場所: reports\%~n1\report.html)
pause
