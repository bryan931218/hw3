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
- 商城/下載子選單：瀏覽遊戲、下載/更新（安裝到 `player/downloads/<player>/<game>/<version>`）。
- 房間子選單：建立房間、加入房間、啟動房間遊戲
- 狀態看板：列出玩家列表（在線/離線）、房間列表、上架遊戲列表。
- 評分與評論：必須曾經啟動並開始過該遊戲才能評論。

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
- `entry` 指向可執行的 Python 檔案。玩家客戶端啟動時會以 `python <entry> --player <name> --server <platform_url> --game-server <game_server_url> --room <room_id>` 執行。
- `server_entry`（可選）：若有提供，平台在房間啟動時會解壓並啟動此檔案作為獨立 game server（傳入 `--room <id> --port <port>`）。

## 伺服器部署：
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

## 連線資訊設定（IP / Port）
- Developer Client 與 Player Client 皆使用環境變數 `GAME_SERVER_URL` 作為平台後端位址（預設 `http://linux1.cs.nycu.edu.tw:5000`）。
  - Linux/macOS：
    ```bash
    export GAME_SERVER_URL="http://<server_ip>:<port>"
    python run_player.py
    python run_developer.py
    ```
  - Windows PowerShell：
    ```powershell
    $env:GAME_SERVER_URL="http://<server_ip>:<port>"
    python run_player.py
    python run_developer.py
    ```
- 後端監聽 Port 由環境變數 `PORT` 決定（預設 5000）：
  ```bash
  export PORT=5000
  python run_server.py
  ```

## Demo 快速流程
1) 啟動後端：`python run_server.py`
2) 開發者端：`python run_developer.py`
   - 註冊/登入 → 上架 `developer/games/sample_cli`、`developer/games/tetris`、`developer/games/sample_multi_gui`
3) 玩家端：`python run_player.py`
   - 註冊/登入 → 商城瀏覽 → 下載任一遊戲
   - 建立房間 → 啟動房間遊戲（會執行本地 `main.py`）→ 返回後對遊戲評分留言

