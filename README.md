# Game Store System (HW3)

Python 3.10+ implementation of the HW3 requirements: developer platform, game store / lobby, versioned downloads, rooms, and ratings with menu-driven clients.

## Repository Layout
- `server/` – Flask backend (`python -m server.server`)
- `developer/` – 開發者端 codebase
  - `client.py` – developer CLI for register/login, upload, update, remove games
  - `games/` – 開發者本地測試與上架來源（玩家不應直接執行）
    - `sample_cli/` – ready-to-upload sample CLI dice duel with `manifest.json`
    - `sample_gui/` – GUI 井字棋雙人對戰 (關卡 B)
    - `sample_multi_gui/` – GUI 多人骰子賽跑 3–4 人 (關卡 C)
  - `template/` – starter files for building your own game package
  - `create_game_template.py` – 從 `template/` 快速複製骨架到 `games/<name>/`
- `player/` – 玩家端 codebase
  - `client.py` – lobby/player CLI for browsing, download/update, rooms, launch, rating
  - `downloads/` – player downloads are stored here under `<player>/<game>/<version>` (inside `player/`)
- `requirements.txt` – dependencies (`flask`, `requests`)
- `run_server.py` / `run_developer.py` / `run_player.py` – 一鍵啟動腳本（避免手動輸入指令）

## Setup
```bash
pip install -r requirements.txt
```

## Start the Server
```bash
python run_server.py
```
- Defaults to `0.0.0.0:5000`. Override with `PORT=8000 python -m server.server`.
- Data persists in `server/data.json`. Uploaded game bundles are stored under `server/storage/games/`.
- 若部署在 linux1.cs.nycu.edu.tw，建議設定環境變數：
  - `PORT=5000`（或自訂）
  - `GAME_SERVER_HOST=0.0.0.0`（game server 綁定位址）
  - `GAME_SERVER_PUBLIC_HOST=linux1.cs.nycu.edu.tw`（玩家用來連線的公開位址）

## Developer Client (上架 / 更新 / 下架)
```bash
python run_developer.py
```
- Follow the menu to **註冊** 或 **登入**。
- 上架新遊戲時，輸入遊戲資料並提供遊戲資料夾路徑（例如 `developer/games/sample_cli`）。程式會自動壓縮並上傳。
- 更新版本：選擇自己的遊戲，給新版號與資料夾路徑即可。
- 下架：選擇遊戲，確認後會從商城移除（玩家仍可保留已下載版本）。
- 若要建立新遊戲骨架：`python developer/create_game_template.py my_game`，會從 `developer/template/` 複製到 `developer/games/my_game/` 供開發與上架。
- 環境變數 `GAME_SERVER_URL` 可指定後端位址（預設 `http://127.0.0.1:5000`）。
  - 若連線到 linux1 公網服務，請設 `GAME_SERVER_URL=http://linux1.cs.nycu.edu.tw:5000`。

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
    "name": "Game Name",
    "entry": "main.py",
    "type": "cli",
    "description": "short intro",
    "max_players": 2,
    "min_players": 2,
    "server_entry": "server.py"
  }
  ```
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

## Demo 事前準備與部署
1) 推上 GitHub：包含 server / developer / player；README 保持最新。
2) Linux 部署（例如 linux1.cs.nycu.edu.tw）：
   ```bash
   git clone <repo_url> hw3 && cd hw3
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   export PORT=5000
   export GAME_SERVER_HOST=0.0.0.0
   export GAME_SERVER_PUBLIC_HOST=linux1.cs.nycu.edu.tw   # 或實際對外 IP
   python run_server.py
   ```
   - Demo 前如需清空資料：`python -m server.reset_data`
3) 助教端操作：依 README 只需 `python run_server.py`、`python run_developer.py`、`python run_player.py`，不需手動輸入其他指令。
4) 若網段/防火牆限制 game server 連線，請確認對外 IP 與 port 已開放，並設定 `GAME_SERVER_PUBLIC_HOST`。

## 重要設計說明
- **帳號分流**：開發者與玩家帳號獨立管理；登入後會驗證身分。
- **版本管理**：每次上架/更新都會保存版本號及檔案路徑；玩家下載時自動取得最新版本。
- **房間邏輯**：建立房間時綁定遊戲與版本，檢查人數上限/下限，開始遊戲時標記玩家已玩過以啟用評分資格。
- **資料持久化**：所有元資料存放於 `server/data.json`，伺服器重啟不會遺失；遊戲封包放在 `server/storage/games/`。
- **Menu-driven**：所有操作均以選單進行，不需額外命令列參數（除非改變伺服器位址）。
- **大廳狀態**：`/rooms` 提供房間列表、`/players` 提供玩家列表、`/games` 提供上架遊戲列表，方便大廳展示。
- **房間生命週期**：玩家端啟動遊戲後會呼叫 `/rooms/<id>/close` 關閉房間；房間列表於玩家端會依已安裝遊戲做篩選，避免看到無法加入的房間。

## 限制與後續可做
- 目前遊戲啟動為本地執行樣板（`manifest.entry`），若要支援更複雜多人同步，可在該入口內自行實作與伺服器溝通。
- Plugin 清單/安裝可透過擴充 API 完成；目前未預設範例 Plugin。

Enjoy hacking!
