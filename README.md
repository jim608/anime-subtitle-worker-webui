# Anime Subtitle Worker WebUI

Dashboard control panel for `anime-subtitle-worker`.

It is intentionally a separate container. If the WebUI fails, the subtitle worker keeps running.

The frontend is a Vite + Vue 3 app. FastAPI serves the API and the built frontend assets.

## Features

- Live dashboard for worker, AI queue, Mikan/qB downloads, actions, and events
- Pause/resume the global AI queue without interrupting the current video or Mikan work
- Show AI throughput, recent completion samples, remaining work, and estimated queue drain time
- Show live Mikan redownload-all progress and request a cooperative safe stop
- Real-time task dashboard showing file name, current stage, status, progress, and queue actions
- Inspect per-video audio selection, model, Prompt signature, ASR, metadata, quality and failure provenance
- Review cached Japanese/Chinese SRT line-by-line and trigger a single-line retranslation
- See current AI failure root causes without double-counting repeated history events
- Monitor SQLite reclaimable space and run backup-first idle database maintenance
- Show actionable runtime recommendations without counting automatic source replacement as a manual failure
- Copy a safe runtime diagnostic snapshot without exposing API keys or qBittorrent credentials
- Configure bounded AI retries, fair backlog scheduling, and automatic idle database maintenance
- Track multi-episode subtitle extraction progress and live Linux I/O pressure throttling
- Retranslate selected subtitle lines without rerunning the full episode
- Manage AniList/Mikan series identity, lock manual matches, and edit per-series Japanese-to-Chinese terminology
- View queue status from `/work/scanner_state.sqlite3`
- Edit common `config.yaml` fields
- Tail `app.log` and `failed.log`
- Restart the worker container when Docker socket is mounted
- View Mikan download processing state from `/work/mikan_pending.json`
- Run `--refresh-ass`
- Run `--cleanup-generated-artifacts`
- Run `--mikan-process-completed` to process completed qB/Mikan downloads
- Create and verify Worker state backups from the system tools page
- Run `--mikan-reset-all` to clear Mikan state and requeue missing official subtitles
- Mobile-first layout with bottom navigation and grouped settings

## Run On Unraid

Put this folder at:

```bash
/mnt/user/appdata/anime-subtitle-worker-webui
```

Then build and start:

```bash
cd /mnt/user/appdata/anime-subtitle-worker-webui
docker compose up -d --build
```

For later updates, run:

```bash
sh safe-update-webui.sh
```

Open:

```text
http://SERVER_IP:8765
```

本機開發時可把 API 與即時狀態串流代理到現有 WebUI，不必修改來源碼：

```bash
VITE_DEV_BACKEND=http://SERVER_IP:8765 npm run dev
```

If Portainer tries to pull `anime-subtitle-worker-webui:latest` from Docker Hub, keep `pull_policy: never` in `docker-compose.yml` and make sure this folder exists on the Unraid host before deploying the stack.

## Volumes

The default `docker-compose.yml` expects:

```yaml
/mnt/user/appdata/anime-subtitle-worker/config.yaml:/config/config.yaml
/mnt/user/appdata/anime-subtitle-worker/work:/work
/mnt/user/appdata/anime-subtitle-worker/logs:/logs
/var/run/docker.sock:/var/run/docker.sock
```

The Docker socket is only needed for restart/action buttons. Status, config editing, and logs work without it.

## Optional Login

Set these environment variables:

```yaml
WEBUI_USERNAME: "admin"
WEBUI_PASSWORD: "change-me"
```

If `WEBUI_PASSWORD` is empty, login is disabled.

## Important

Saving config does not hot-reload the worker. Use the `Restart Worker` button after changing runtime settings.

## Local Checks

Run these before deploying changes:

```bash
npm ci
npm run build
npm run test:frontend
python -m compileall -q .
python tests/test_webui_backend.py
```

## v2 API 與操作安全

WebUI 保留舊 `/api/*` 相容路徑，主要畫面可使用下列精簡介面：

- `GET /api/v2/overview`：總覽、瓶頸、ETA、提取延遲 SLO、資源與審核數。
- `GET /api/v2/ai/tasks`、`GET /api/v2/mikan/items`：游標分頁、搜尋、狀態篩選與 compact/detail 欄位。
- `POST /api/v2/commands`、`GET /api/v2/commands/{id}`：具 `Idempotency-Key` 的原子操作。
- `GET /api/v2/review-items`、`POST /api/v2/review-items/{id}/resolve`：AI 品質、ASR 與來源歧義審核。
- `GET /api/v2/series/{series_id}`：穩定 ID 的作品資料，不公開絕對路徑作為識別碼。
- `GET /api/v2/stream`：帶 revision 的實體增量事件。

區網免登入模式仍會檢查同源 `Origin`、CSRF nonce、操作防重送與安全標頭。WebUI 不直接寫入 AI 狀態資料庫；所有變更先寫原子命令信箱，由 Worker 驗證、去重、執行並留下稽核紀錄。

手機版保留四個主要入口，其餘功能位於「更多」。失敗卡只有一個建議主要動作，其餘放在操作選單；品質審核中心可查看問題行、單行重翻、區段重轉錄與還原版本，來源配對中心可確認季度映射並重試提取。

## Worker + WebUI 一次性安全更新

在 Unraid 主機執行：

```bash
cd /mnt/user/appdata/anime-subtitle-worker-webui
sh safe-update-stack.sh
```

預設最多等待目前工作四小時到達安全終點，不會強殺 AI 或提取。腳本會：

1. 在不中斷線上服務時建置 Worker 與 WebUI，並用新映像跑完整測試。
2. 啟用 deployment hold 與 AI pause，等待 AI、Mikan 與提取穩定閒置 15 秒。
3. 備份設定、四個狀態資料庫（Scanner、Mikan、Control、Series）、重要快取與目前映像 ID，建立並驗證 SHA-256 manifest。
4. 在資料庫副本演練所有加法遷移。
5. 帶 hold 同時重建兩個容器，核對來源 hash、健康狀態、v2 overview 大小與 p95、系列 API 及 Worker health command。
6. 全部成功才解除 hold；任何錯誤自動還原舊映像與部署前資料。

可調整 `IDLE_WAIT_SECONDS`、`IDLE_STABLE_SECONDS`、`BACKUP_AI_CACHE` 與 `RUN_TESTS`。正式環境不建議設定 `RUN_TESTS=0`。
