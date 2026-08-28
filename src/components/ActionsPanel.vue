<script setup>
import { actionLabel, formatDuration, formatTime, statusTone } from "../dashboard.js";

defineProps({
  action: { type: Object, default: () => ({}) },
  busy: { type: Boolean, default: false },
  databaseHealth: { type: Object, default: () => ({ databases: [] }) },
});

const emit = defineEmits(["run-action"]);

const regularActions = [
  { key: "ai-safe-retry-sweep", description: "只處理下一筆符合安全條件的 AI 失敗；保留重試預算，遇到品質或配對問題立即停止。" },
  { key: "mikan-process-completed", description: "立即處理 qBittorrent 已完成下載，提取並匯入可用中文字幕。" },
  { key: "ai-refresh-queue-state", description: "只重新整理 AI 佇列分類，不啟動 AI。用來清掉已有一般中文字幕但誤列 AI 完成的舊狀態。" },
  { key: "series-sync", description: "從既有 Mikan 集數索引、匹配快取與 AI 佇列回填作品清單，不重新讀取影片內容。" },
  { key: "mikan-requeue-failed-extracts", description: "把提取失敗的字幕來源重新排隊；適合修正匹配或提取規則後重跑。" },
];

const maintenanceActions = [
  { key: "backup-state", description: "一致性備份 AI 佇列、Mikan、作品資料庫與必要快取；自動驗證並依保留代數清理舊備份。" },
  { key: "database-maintenance", description: "等待 AI 與字幕提取閒置後先備份，再回收 SQLite 未使用空間並驗證資料庫。" },
  { key: "refresh-ass", description: "從既有 SRT 重新輸出 ASS，套用目前字幕樣式設定。" },
  { key: "cleanup-generated", description: "清理 AI 暫存 SRT 與品質檢查報告，不刪除媒體庫影片。" },
  { key: "restart-worker", description: "重啟 Worker 容器。只在設定或程式更新後需要。" },
];

const resetActions = [
  { key: "mikan-redownload-all", description: "重新檢查並下載全部字幕來源。會影響大量 qBittorrent 任務。" },
  { key: "mikan-reset-all", description: "重設字幕來源下載狀態並重新找候選。通常只在狀態資料錯亂時使用。" },
];

function buttonLabel(key, action) {
  return action.running && action.action === key ? "執行中..." : "執行";
}

function databaseTone(item) {
  if (item.error || !item.exists) return "danger";
  return Number(item.freelist_ratio || 0) >= 0.2 ? "warn" : "success";
}
</script>

<template>
  <section class="page-panel tools-page">
    <header class="page-heading">
      <div>
        <span class="section-label">系統工具</span>
        <h1>系統工具</h1>
        <p>手動觸發 Worker 維護任務。優先使用安全操作；重設與重下載會改變大量狀態。</p>
      </div>
      <span :class="['status-chip', statusTone(action.ok === false ? 'Failed' : action.running ? 'Running' : 'Success')]">
        {{ action.running ? `執行中：${actionLabel(action.action)}` : action.action ? `最近執行：${actionLabel(action.action)}` : "目前沒有執行中任務" }}
      </span>
    </header>

    <section v-if="databaseHealth.databases?.length" class="tool-section">
      <div>
        <span class="section-label">SQLite 健康狀態</span>
        <h2>狀態資料庫</h2>
      </div>
      <div class="database-health-grid">
        <article v-for="item in databaseHealth.databases" :key="item.path" class="database-health-card">
          <span :class="['status-dot', databaseTone(item)]"></span>
          <div><strong>{{ item.label }}</strong><small>{{ item.size_mib ?? 0 }} MiB</small></div>
          <div><span>可回收</span><b>{{ item.reclaim_mib ?? 0 }} MiB</b></div>
        </article>
      </div>
    </section>

    <section class="tool-section">
      <div>
        <span class="section-label">安全操作</span>
        <h2>日常維護</h2>
      </div>
      <div class="tool-grid">
        <article v-for="item in regularActions" :key="item.key" class="tool-card">
          <div>
            <h3>{{ actionLabel(item.key) }}</h3>
            <p>{{ item.description }}</p>
          </div>
          <button type="button" :class="{ primary: ['retry-all-failures', 'mikan-process-completed'].includes(item.key) }" :disabled="busy" @click="emit('run-action', item.key)">
            {{ buttonLabel(item.key, action) }}
          </button>
        </article>
      </div>
    </section>

    <section class="tool-section">
      <div>
        <span class="section-label">維護操作</span>
        <h2>輸出與 Worker</h2>
      </div>
      <div class="tool-grid">
        <article v-for="item in maintenanceActions" :key="item.key" class="tool-card">
          <div>
            <h3>{{ actionLabel(item.key) }}</h3>
            <p>{{ item.description }}</p>
          </div>
          <button type="button" :disabled="busy" @click="emit('run-action', item.key)">
            {{ buttonLabel(item.key, action) }}
          </button>
        </article>
      </div>
    </section>

    <section class="tool-section danger-zone">
      <div>
        <span class="section-label">重設與重跑</span>
        <h2>大量狀態操作</h2>
      </div>
      <div class="tool-grid">
        <article v-for="item in resetActions" :key="item.key" class="tool-card">
          <div>
            <h3>{{ actionLabel(item.key) }}</h3>
            <p>{{ item.description }}</p>
          </div>
          <button type="button" class="danger" :disabled="busy" @click="emit('run-action', item.key)">{{ buttonLabel(item.key, action) }}</button>
        </article>
      </div>
    </section>

    <section v-if="action.started_at || action.finished_at || action.output || action.error" class="tool-result">
      <div class="card-heading">
        <h2>執行結果</h2>
        <div class="meta-list">
          <span v-if="action.started_at">開始 {{ formatTime(action.started_at) }}</span>
          <span v-if="action.finished_at">結束 {{ formatTime(action.finished_at) }}</span>
          <span v-if="action.elapsed_seconds">耗時 {{ formatDuration(action.elapsed_seconds) }}</span>
        </div>
      </div>
      <p v-if="action.running" class="tool-running-note">任務在背景執行，可以離開本頁；完成後結果會保留 15 分鐘。</p>
      <p v-else-if="action.ok === true" class="result-success">執行完成</p>
      <p v-else-if="action.ok === false" class="result-error">執行失敗，請查看下方訊息。</p>
      <pre v-if="action.output" class="terminal">{{ action.output }}</pre>
      <pre v-if="action.error" class="terminal terminal-error">{{ action.error }}</pre>
    </section>
  </section>
</template>
