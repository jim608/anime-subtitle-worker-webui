<script setup>
import { computed, onUnmounted, ref, watch } from "vue";
import {
  displayStatus,
  fileName,
  friendlyTaskMessage,
  formatDuration,
  formatPercent,
  formatTime,
  nodeLabel,
  problemRecommendedAction,
  qualityIssueLabel,
  statusTone,
  subtitleQualityLabel,
  subtitleQualitySummary,
  subtitleQualityTone,
  taskProgress,
} from "../dashboard.js";

const props = defineProps({
  payload: { type: Object, default: () => ({ page: 1, page_count: 1, total: 0 }) },
  tasks: { type: Array, default: () => [] },
  completedTasks: { type: Array, default: () => [] },
  counts: { type: Object, default: () => ({}) },
  busy: { type: Boolean, default: false },
  aiControl: { type: Object, default: () => ({ paused: false }) },
  controlBusy: { type: Boolean, default: false },
  query: { type: Object, default: () => ({ mode: "active", page: 1, pageSize: 30, status: "", search: "" }) },
  pendingPaths: { type: Array, default: () => [] },
  diagnostics: { type: Object, default: null },
  diagnosticsPath: { type: String, default: "" },
  diagnosticsLoading: { type: Boolean, default: false },
  retrySweep: { type: Object, default: () => ({ state: "idle", counters: {} }) },
});

const emit = defineEmits(["queue-action", "query", "page", "safe-retry", "ai-control", "diagnostics"]);
const search = ref(props.query.search || "");
const statusFilter = ref(props.query.status || "");
const viewMode = ref(props.query.mode || "active");
let searchTimer = null;
let syncingQuery = false;

const statusOptions = computed(() => [
  ["", "全部"],
  ["running", "處理中"],
  ["queued", "等待中"],
  ["failed_retry", "失敗待重試"],
  ["paused", "已暫停"],
  ["skipped", "已略過"],
]);

const visibleTasks = computed(() => props.tasks.length ? props.tasks : (viewMode.value === "completed" ? props.completedTasks : []));
const page = computed(() => Number(props.payload.page || props.query.page || 1));
const pageCount = computed(() => Math.max(1, Number(props.payload.page_count || 1)));
const total = computed(() => Number(props.payload.total ?? props.payload.filtered ?? visibleTasks.value.length));
const pendingPathSet = computed(() => new Set(props.pendingPaths));

watch(() => props.query, (query) => {
  syncingQuery = true;
  if (query.search !== search.value) search.value = query.search || "";
  if (query.status !== statusFilter.value) statusFilter.value = query.status || "";
  if (query.mode !== viewMode.value) viewMode.value = query.mode || "active";
  syncingQuery = false;
}, { deep: true, flush: "sync" });

watch(viewMode, () => {
  if (syncingQuery) return;
  syncingQuery = true;
  statusFilter.value = "";
  syncingQuery = false;
  emit("query", { mode: viewMode.value, status: "", search: search.value, page: 1 });
}, { flush: "sync" });

watch(statusFilter, () => {
  if (syncingQuery) return;
  emit("query", { mode: viewMode.value, status: statusFilter.value, search: search.value, page: 1 });
}, { flush: "sync" });

watch(search, () => {
  if (syncingQuery) return;
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    emit("query", { mode: viewMode.value, status: statusFilter.value, search: search.value, page: 1 });
  }, 350);
}, { flush: "sync" });

onUnmounted(() => {
  if (searchTimer) window.clearTimeout(searchTimer);
});

function actionsFor(task) {
  const raw = task.raw_status || task.effective_status || "";
  if (raw === "running") {
    return task.running_recoverable
      ? [{ key: "recover-running", label: "回收卡住任務" }, { key: "force-ai", label: "重新排 AI" }]
      : [{ key: "force-ai", label: "重新排 AI" }];
  }
  if (raw === "queued") {
    return [
      { key: "priority", label: "排到最前" },
      { key: "force-ai", label: "強制 AI" },
      { key: "retranscribe", label: "重新轉錄", quiet: true },
      { key: "pause", label: "暫停" },
      { key: "skip", label: "略過", quiet: true },
    ];
  }
  if (raw === "failed_retry") {
    return [
      { key: "retry", label: "重試" },
      { key: "priority", label: "排到最前" },
      { key: "retranslate", label: "只重翻譯" },
      { key: "retranslate-lines", label: "重翻指定行" },
      { key: "retranscribe", label: "重新轉錄" },
      { key: "clear-failure", label: "清除失敗" },
      { key: "force-ai", label: "強制 AI" },
      { key: "skip", label: "略過", quiet: true },
    ];
  }
  if (raw === "paused" || raw === "skipped") {
    return [
      { key: "retry", label: "重新排隊" },
      { key: "retranslate", label: "只重翻譯" },
      { key: "retranslate-lines", label: "重翻指定行" },
      { key: "retranscribe", label: "重新轉錄" },
    ];
  }
  if (raw === "done") {
    return [
      { key: "retranslate", label: "只重翻譯" },
      { key: "retranslate-lines", label: "重翻指定行" },
      { key: "retranscribe", label: "重新轉錄" },
      { key: "force-ai", label: "重新排 AI" },
    ];
  }
  return [{ key: "force-ai", label: "強制 AI" }];
}

function primaryActionFor(task) {
  return actionsFor(task)[0] || null;
}

function secondaryActionsFor(task) {
  return actionsFor(task).slice(1);
}

function diagnosticValue(value) {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function completionLabel(task) {
  if (task?.completion_label) return task.completion_label;
  if (task?.completion_kind === "detected_existing") return "掃描確認已有 AI 字幕";
  return "AI 字幕生成完成";
}

function elapsed(task) {
  const started = Number(task.running_started_at || 0);
  return started && task.status === "Running" ? formatDuration(Date.now() / 1000 - started) : "";
}

function plainStatus(task) {
  if (task.status === "Running") return `處理中：${nodeLabel(task.node_id)}`;
  if (task.status === "Queued") return "等待處理";
  if (task.status === "Failed") return "失敗，可重試";
  return displayStatus(task.status);
}

function progressLabel(task) {
  const batch = String(task.message || "").match(/\b(?:translating|translated)?\s*batch\s+(\d+)\s*\/\s*(\d+)\b/i);
  if (batch) return `批次 ${batch[1]} / ${batch[2]}`;
  const progress = taskProgress(task);
  if (progress === null) return `${nodeLabel(task.node_id)}進行中`;
  if (task.status === "Queued") return "等待開始";
  return formatPercent(progress);
}

function qualityIssueIndexes(issue) {
  const indexes = Array.isArray(issue?.indexes) ? issue.indexes : [];
  return [...new Set(indexes
    .map((value) => Number(value))
    .filter((value) => Number.isInteger(value) && value > 0))]
    .slice(0, 100);
}

function qualityIssueLineSpec(issue) {
  return qualityIssueIndexes(issue).join(",");
}
</script>

<template>
  <section class="page-panel ai-page">
    <header class="page-heading queue-page-header">
      <div class="queue-page-title">
        <h1>AI 字幕佇列</h1>
        <p>關鍵處理狀態與需要介入的例外</p>
      </div>
      <div class="queue-heading-actions">
        <div class="queue-summary" aria-label="佇列關鍵狀態">
          <span class="queue-stat running"><b>{{ counts.running || 0 }}</b> 處理中</span>
          <span class="queue-stat queued"><b>{{ counts.queued || 0 }}</b> 等待中</span>
          <span :class="['queue-stat', { danger: counts.failed_retry }]">
            <b>{{ counts.failed_retry || 0 }}</b> 失敗待重試
          </span>
        </div>
        <button
          v-if="counts.failed_retry"
          type="button"
          class="primary retry-failed-button"
          :disabled="busy"
          title="一次只處理一筆安全候選；不重設嘗試次數，遇到問題即停"
          @click="emit('safe-retry')"
        >
          {{ retrySweep.state === "running" ? "安全修復中" : `安全處理 1 筆（${counts.failed_retry} 待檢查）` }}
        </button>
        <details class="queue-utilities">
          <summary>{{ aiControl.paused ? "AI 已暫停" : "佇列控制" }}</summary>
          <div>
            <button
              type="button"
              class="queue-control-button"
              :class="{ primary: aiControl.paused }"
              :disabled="controlBusy"
              @click="emit('ai-control', !aiControl.paused)"
            >
              {{ controlBusy ? "更新中…" : aiControl.paused ? "恢復 AI 佇列" : "暫停 AI 佇列" }}
            </button>
            <small>正常情況由系統自動排程，不需要操作。</small>
          </div>
        </details>
      </div>
    </header>

    <div class="list-toolbar">
      <div class="view-switch" aria-label="AI 字幕檢視">
        <button type="button" :class="{ active: viewMode === 'active' }" @click="viewMode = 'active'">進行中</button>
        <button type="button" :class="{ active: viewMode === 'completed' }" @click="viewMode = 'completed'">最近完成</button>
      </div>
      <label v-if="viewMode === 'active'">
        <span>狀態</span>
        <select v-model="statusFilter" aria-label="篩選 AI 字幕狀態">
          <option v-for="[key, label] in statusOptions" :key="key || 'all'" :value="key">{{ label }}</option>
        </select>
      </label>
      <label class="search-field">
        <span>搜尋</span>
        <input v-model="search" type="search" placeholder="輸入作品名稱或路徑" />
      </label>
    </div>

    <div class="task-list">
      <div v-if="visibleTasks.length === 0" class="empty-state">
        <strong>{{ viewMode === "completed" ? "目前沒有完成紀錄" : "目前沒有符合條件的 AI 任務" }}</strong>
        <span v-if="search && viewMode === 'active'">這個項目目前不在進行中佇列，可能已完成或尚未建立工作。</span>
        <span v-else-if="search">找不到符合「{{ search }}」的紀錄。</span>
        <div v-if="search" class="empty-state-actions">
          <button
            v-if="viewMode === 'active'"
            type="button"
            class="primary"
            @click="emit('query', { mode: 'completed', status: '', search, page: 1 })"
          >
            查看最近完成
          </button>
          <button type="button" class="quiet" @click="search = ''">清除搜尋</button>
        </div>
      </div>
      <article
        v-for="task in visibleTasks"
        :key="task.path"
        :class="['task-card', `task-card--${statusTone(task.status)}`, { 'is-failed': task.status === 'Failed' }]"
      >
        <div class="task-card-main">
          <div class="card-line">
            <span :class="['status-chip', statusTone(task.status)]">{{ plainStatus(task) }}</span>
            <span
              v-if="task.subtitle_quality && task.status !== 'Failed' && task.subtitle_quality.status !== 'watchable'"
              :class="['status-chip', subtitleQualityTone(task.subtitle_quality)]"
            >
              字幕{{ subtitleQualityLabel(task.subtitle_quality) }}
            </span>
            <small v-if="elapsed(task)">已跑 {{ elapsed(task) }}</small>
          </div>
          <h3>{{ task.file_name || fileName(task.path) }}</h3>
          <p v-if="task.message || task.skip_reason || task.problem" class="task-summary" :class="{ 'warning-text': task.status === 'Failed' }">
            {{ friendlyTaskMessage(task) }}
          </p>
          <p v-if="task.problem?.requires_user_action && task.status !== 'Failed'" class="recommended-action">
            <strong>建議操作：</strong>{{ problemRecommendedAction(task) }}
          </p>
          <p v-if="task.subtitle_quality && task.status !== 'Failed' && task.subtitle_quality.status !== 'watchable'" class="warning-text">
            {{ subtitleQualitySummary(task.subtitle_quality) }}
          </p>
          <div v-if="viewMode === 'active' && task.status !== 'Failed'" :class="['task-progress', { indeterminate: taskProgress(task) === null }]">
            <div class="progress-track">
              <span v-if="taskProgress(task) !== null" :style="{ width: formatPercent(taskProgress(task)) }"></span>
              <span v-else class="indeterminate-bar"></span>
            </div>
            <b>{{ progressLabel(task) }}</b>
          </div>
          <div v-else-if="viewMode === 'active'" class="task-failure-state" role="status">
            <span aria-hidden="true">!</span><b>等待安全重試</b>
          </div>
        </div>

        <div class="row-actions">
          <button
            v-if="primaryActionFor(task)"
            :key="`${task.path}-${primaryActionFor(task).key}`"
            type="button"
            class="primary"
            :disabled="busy || pendingPathSet.has(task.path)"
            @click="emit('queue-action', primaryActionFor(task).key, task.path)"
          >
            {{ pendingPathSet.has(task.path) ? "更新中..." : primaryActionFor(task).label }}
          </button>
        </div>

        <details class="row-details task-card-details">
          <summary>技術資料與操作</summary>
          <dl>
            <dt>檔案路徑</dt><dd>{{ task.path }}</dd>
            <dt>更新時間</dt><dd>{{ formatTime(task.completed_at || task.job?.finished_at || task.updated_at) }}</dd>
            <template v-if="viewMode === 'completed'">
              <dt>完成方式</dt><dd>{{ completionLabel(task) }}</dd>
            </template>
            <dt>目前階段</dt><dd>{{ nodeLabel(task.node_id) }}</dd>
            <dt>原始狀態</dt><dd>{{ task.raw_status || task.status }}</dd>
            <dt>嘗試次數</dt><dd>{{ task.attempts || 0 }}</dd>
            <template v-if="task.message || task.skip_reason || task.problem">
              <dt>完整訊息</dt><dd>{{ friendlyTaskMessage(task) }}</dd>
            </template>
            <template v-if="task.problem?.requires_user_action">
              <dt>建議操作</dt><dd>{{ problemRecommendedAction(task) }}</dd>
            </template>
            <template v-if="task.language">
              <dt>語言偵測</dt><dd>{{ task.language }} {{ task.language_probability ? `${Math.round(task.language_probability * 100)}%` : "" }}</dd>
            </template>
            <template v-if="task.subtitle_quality">
              <dt>字幕品質</dt><dd>{{ subtitleQualitySummary(task.subtitle_quality) }}</dd>
            </template>
          </dl>
          <ul v-if="task.subtitle_quality?.issues?.length" class="quality-issue-list">
            <li v-for="(issue, issueIndex) in task.subtitle_quality.issues" :key="`${task.path}-quality-${issue.code || issueIndex}`">
              <div>
                <strong>{{ qualityIssueLabel(issue) }}</strong>
                <small v-if="issue.count">{{ issue.count }} 行</small>
                <small v-if="issue.samples?.length">{{ issue.samples.join("；") }}</small>
              </div>
              <button
                v-if="qualityIssueLineSpec(issue)"
                type="button"
                class="quiet"
                :disabled="busy || pendingPathSet.has(task.path)"
                @click="emit('queue-action', 'retranslate-lines', task.path, qualityIssueLineSpec(issue))"
              >
                只重翻這 {{ qualityIssueIndexes(issue).length }} 行
              </button>
            </li>
          </ul>

          <div class="task-secondary-actions task-action-menu" aria-label="其他操作">
            <span>其他操作</span>
            <button
              v-for="item in secondaryActionsFor(task)"
              :key="`${task.path}-${item.key}`"
              type="button"
              :class="{ quiet: item.quiet }"
              :disabled="busy || pendingPathSet.has(task.path)"
              @click="emit('queue-action', item.key, task.path)"
            >
              {{ item.label }}
            </button>
            <button
              type="button"
              class="quiet"
              :disabled="diagnosticsLoading && diagnosticsPath === task.path"
              @click="emit('diagnostics', task.path)"
            >
              {{ diagnosticsLoading && diagnosticsPath === task.path ? "讀取診斷…" : "載入技術診斷" }}
            </button>
          </div>

          <section v-if="diagnosticsPath === task.path && diagnostics" class="task-diagnostics">
          <div class="card-heading">
            <div><span class="section-label">處理履歷</span><h4>AI 診斷</h4></div>
            <span :class="['status-chip', diagnostics.provenance?.status === 'failed' ? 'danger' : 'success']">
              {{ diagnostics.provenance?.status || "尚無紀錄" }}
            </span>
          </div>
          <dl class="diagnostic-grid">
            <dt>目前階段</dt><dd>{{ diagnosticValue(diagnostics.provenance?.current_stage) }}</dd>
            <dt>Whisper／ASR</dt><dd><pre>{{ diagnosticValue(diagnostics.provenance?.asr) }}</pre></dd>
            <dt>翻譯模型</dt><dd><pre>{{ diagnosticValue(diagnostics.provenance?.translation) }}</pre></dd>
            <dt>作品資訊</dt><dd><pre>{{ diagnosticValue(diagnostics.provenance?.series_metadata) }}</pre></dd>
            <dt>音軌選擇</dt><dd><pre>{{ diagnosticValue(diagnostics.audio_selection) }}</pre></dd>
            <dt>失敗原因</dt><dd><pre>{{ diagnosticValue(diagnostics.provenance?.error) }}</pre></dd>
          </dl>
          <details v-if="diagnostics.provenance?.stages?.length" class="diagnostic-timeline">
            <summary>顯示完整階段紀錄（{{ diagnostics.provenance.stages.length }}）</summary>
            <ol>
              <li v-for="(stage, index) in diagnostics.provenance.stages" :key="`${stage.stage}-${index}`">
                <strong>{{ stage.stage }}</strong><span>{{ stage.status }}</span><small>{{ stage.message }}</small>
              </li>
            </ol>
          </details>
          <details v-if="diagnostics.review?.lines?.length" class="diagnostic-timeline review-lines-panel">
            <summary>字幕逐句檢查（{{ diagnostics.review.line_count }} 行）</summary>
            <div class="review-line-list">
              <article v-for="line in diagnostics.review.lines.slice(0, 120)" :key="line.index">
                <header><b>#{{ line.index }}</b><small>{{ line.timing }}</small></header>
                <p lang="ja">{{ line.japanese || "—" }}</p>
                <p lang="zh-Hant">{{ line.chinese || "尚未翻譯" }}</p>
                <button
                  type="button"
                  class="quiet"
                  :disabled="busy || pendingPathSet.has(task.path)"
                  @click="emit('queue-action', 'retranslate-lines', task.path, String(line.index))"
                >
                  {{ pendingPathSet.has(task.path) ? "更新中…" : "只重翻這一行" }}
                </button>
              </article>
            </div>
          </details>
          </section>
        </details>
      </article>
    </div>

    <footer class="pagination">
      <button type="button" :disabled="busy || page <= 1" @click="emit('page', page - 1)">上一頁</button>
      <span>第 {{ page }} / {{ pageCount }} 頁，共 {{ total }} 筆</span>
      <button type="button" :disabled="busy || page >= pageCount" @click="emit('page', page + 1)">下一頁</button>
    </footer>
  </section>
</template>
