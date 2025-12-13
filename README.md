## Developer Client (上架 / 更新 / 下架)
```bash
python run_developer.py
```
- 先註冊 / 登入玩家帳號。
- 上架新遊戲時，輸入遊戲資料並提供遊戲資料夾路徑（例如 `developer/games/sample_cli`）。程式會自動壓縮並上傳。
- 更新版本：選擇自己的遊戲，給新版號與資料夾路徑即可。
- 下架：選擇遊戲，確認後會從商城移除（玩家仍可保留已下載版本）。
- 若要建立新遊戲骨架：`python developer/create_game_template.py my_game`，會從 `developer/template/` 複製到 `developer/games/my_game/` 供開發與上架。
- 按照Template中的註解說明完成遊戲後即可上架，當前的games中已有4款遊戲可供測試。

## Player Client (商城 / 下載 / 房間 / 評分)
```bash
python run_player.py
```
- 先註冊 / 登入玩家帳號。
- 商城/下載子選單：瀏覽、詳細、下載/更新（安裝到 `player/downloads/<player>/<game>/<version>`）。
- 房間子選單：建立房間、加入房間、啟動房間遊戲（會檢查已下載版本，再啟動本地入口）。
- 狀態看板：列出玩家列表（在線/離線）、房間列表、上架遊戲列表。
- 評分與評論：必須曾經啟動並開始過該遊戲，後端會驗證。
- `GAME_SERVER_URL` 同樣可覆寫伺服器位址（預設 `http://linux1.cs.nycu.edu.tw:5000`；若本機測試改成 `http://127.0.0.1:5000`）。

## 統一遊戲封裝規格
- 每個遊戲資料夾需包含 `manifest.json`，至少具備：
  ```json
  {
    "entry": "main.py",
    "max_players": 2,
    "min_players": 2,
    "server_entry": "server.py"
  }
  ```
- 遊戲的 `name` / `description` 由開發者上架時在 Developer Client 填入（不需寫在 manifest）。
- `entry` 指向可執行的 Python 檔案。玩家客戶端啟動時會以 `python <entry> --player <name> --server <platform_url> --game-server <game_server_url> --room <room_id>` 執行。
- `server_entry`（可選）：若有提供，平台在房間啟動時會解壓並啟動此檔案作為獨立 game server（傳入 `--room <id> --port <port>`），讓遊戲邏輯完全由上傳檔案提供，平台不內建遊戲邏輯。
- 開發者可從 `template/` 複製作為腳手架，或直接上傳自己的資料夾。
- `developer/games/` 目錄僅供開發者本地開發與上架來源，玩家請一律透過下載取得，不應直接在此執行。
- 部署時請確認 game server 可被玩家端連線：設定 `GAME_SERVER_PUBLIC_HOST` 為實際 IP/域名（如 linux1.cs.nycu.edu.tw），平台會將此值回傳給客戶端作為 `--game-server`。

## Demo 快速路徑
1) 啟動後端：`python run_server.py`
2) 開發者端：`python run_developer.py`
   - 註冊/登入 → 上架 `developer/games/sample_cli`、`developer/games/sample_gui`、`developer/games/sample_multi_gui`
   - 或執行 `python developer/create_game_template.py my_game` 先建立新骨架，再開發/上架
3) 玩家端：`python run_player.py`
   - 註冊/登入 → 商城瀏覽 → 下載任一遊戲
   - 建立房間 → 啟動房間遊戲（會執行本地 `main.py`）→ 返回後對遊戲評分留言
4) 可再開第二/第三個玩家客戶端，模擬多人房間/版本差異；多人 GUI 範例用 `developer/games/sample_multi_gui`。

## Linux 部署：
   ```bash
   git clone <repo_url> hw3 && cd hw3
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   export PORT=5000
   export GAME_SERVER_HOST=0.0.0.0
   export GAME_SERVER_PUBLIC_HOST=linux1.cs.nycu.edu.tw
   python run_server.py
   ```
   - 清空資料：`python -m server.reset_data`
