<script setup>
import { computed, onUnmounted, ref, watch } from "vue";
import {
  candidateEvidenceLabel,
  downloadProgress,
  fileName,
  formatBytes,
  formatDuration,
  formatPercent,
  formatSpeed,
  formatTime,
  mikanSourceLabel,
  mikanRowKey,
  mikanStatusLabel,
  mikanSubtitle,
  mikanSubtitleStateLabel,
  mikanTitle,
  mikanPipelineSummary,
  newestCompletedFirst,
  nextActionLabel,
  problemDescription,
  problemRecommendedAction,
  problemSystemAction,
  problemTitle,
  friendlyTechnicalMessage,
  statusTone,
} from "../dashboard.js";

const props = defineProps({
  downloads: { type: Object, default: () => ({ recent: [], counts: {} }) },
  stateDb: { type: Object, default: () => ({}) },
  operation: { type: Object, default: () => ({ busy: false, active_operations: [] }) },
  busy: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  requestedPage: { type: Number, default: 1 },
  query: { type: Object, default: () => ({ status: "", search: "" }) },
  nowSeconds: { type: Number, default: () => Date.now() / 1000 },
  cancelingJobKey: { type: String, default: "" },
  retryingJobKey: { type: String, default: "" },
});

const emit = defineEmits(["page", "query", "process-completed", "cancel-extract", "retry-extract"]);
const search = ref(props.query.search || "");
const statusFilter = ref(props.query.status || "all");
const extractViewMode = ref("active");
let searchTimer = null;
let syncingQuery = false;

const extractCounts = computed(() => props.stateDb.extract_jobs?.counts || {});
const pipelineSourceCounts = computed(() => {
  const live = props.stateDb.counts || {};
  return Object.keys(live).length ? live : (props.downloads.counts || {});
});
const listCounts = computed(() => props.downloads.counts || {});
const pipeline = computed(() => mikanPipelineSummary({
  pipeline: props.stateDb.pipeline || {},
  mikanCounts: pipelineSourceCounts.value,
  extractCounts: extractCounts.value,
}));
const monitoredStalls = computed(() => Math.max(
  Number(props.stateDb.stalled || 0),
  Number(props.stateDb.zero_speed_downloading || 0),
));
const statusOptions = computed(() => {
  const counts = listCounts.value;
  return [
    { key: "all", label: "全部", count: props.downloads.total || 0, unit: "來源群組" },
    { key: "downloading", label: "下載中", count: counts.downloading || 0, unit: "來源群組" },
    { key: "queued", label: "等待下載", count: counts.queued || 0, unit: "來源群組" },
    { key: "deferred", label: "延後處理", count: counts.deferred || 0, unit: "來源群組" },
    { key: "extracting_subtitles", label: "提取字幕中", count: counts.extracting_subtitles || 0, unit: "來源群組" },
    { key: "completed_waiting_extract", label: "等待提取字幕", count: counts.completed_waiting_extract || 0, unit: "來源群組" },
    { key: "target_missing", label: "找不到影片", count: counts.target_missing || 0, unit: "來源群組" },
    { key: "extract_failed", label: "來源提取失敗", count: counts.extract_failed || 0, unit: "來源群組" },
    { key: "failed_candidate", label: "先前候選不可用", count: counts.failed_candidate || 0, unit: "來源群組" },
    { key: "no_candidate_retry", label: "暫無候選，等待重試", count: counts.no_candidate_retry || 0, unit: "來源群組" },
    { key: "review", label: "等待來源配對審核", count: counts.review || 0, unit: "來源群組" },
    { key: "unknown", label: "尚未分類", count: counts.unknown || 0, unit: "來源群組" },
    { key: "completed", label: "字幕已匯入", count: counts.completed || 0, unit: "來源群組" },
    { key: "failed", label: "提取失敗，可重試", count: extractCounts.value.failed || 0, unit: "提取工作" },
    { key: "replaced", label: "已換替補來源", count: extractCounts.value.replaced || 0, unit: "提取工作" },
    { key: "terminal_failed", label: "提取終止", count: extractCounts.value.terminal_failed || 0, unit: "提取工作" },
  ].filter((option) => option.key === "all" || Number(option.count) > 0);
});

const visibleRows = computed(() => props.downloads.recent || []);
const extractActive = computed(() => props.stateDb.extract_jobs?.recent || []);
const extractAttention = computed(() => newestCompletedFirst(
  props.stateDb.extract_jobs?.recent_attention
    || (props.stateDb.extract_jobs?.recent_failed || []).filter((job) => job.status === "terminal_failed"),
));
const extractCompleted = computed(() => newestCompletedFirst(props.stateDb.extract_jobs?.recent_completed || []));
const visibleExtractJobs = computed(() => {
  if (extractViewMode.value === "completed") return extractCompleted.value;
  if (extractViewMode.value === "attention") return extractAttention.value;
  return extractActive.value;
});
const hasFilters = computed(() => statusFilter.value !== "all" || Boolean(search.value.trim()));

function emitQuery() {
  emit("query", { status: statusFilter.value === "all" ? "" : statusFilter.value, search: search.value });
}

watch(statusFilter, () => {
  if (!syncingQuery) emitQuery();
}, { flush: "sync" });
watch(search, () => {
  if (syncingQuery) return;
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(emitQuery, 350);
}, { flush: "sync" });

watch(() => props.query, (query) => {
  syncingQuery = true;
  const nextStatus = query.status || "all";
  const nextSearch = query.search || "";
  if (nextStatus !== statusFilter.value) statusFilter.value = nextStatus;
  if (nextSearch !== search.value) search.value = nextSearch;
  syncingQuery = false;
}, { deep: true, flush: "sync" });

onUnmounted(() => {
  if (searchTimer) window.clearTimeout(searchTimer);
});

function nextStep(row) {
  if (row.status === "downloading") {
    return row.dlspeed ? `下載中，目前速度 ${formatSpeed(row.dlspeed)}` : "下載中，但目前沒有速度；Worker 會在逾時後更換來源。";
  }
  if (row.status === "extracting_subtitles") return "正在從來源檔提取字幕，完成後會辨識是否有中文。";
  if (row.status === "completed_waiting_extract") return "字幕來源已下載，等待 Worker 提取字幕。";
  if (row.status === "target_missing") return problemDescription(row, "字幕來源已下載，但還找不到媒體庫中的對應影片。");
  if (row.status === "extract_failed") return problemDescription(row, "提取失敗，系統正在尋找替補來源。");
  if (row.status === "failed") return problemDescription(row, "字幕提取失敗，系統會自動尋找替補來源。");
  if (row.status === "replaced") return problemDescription(row, "這個來源已排除，系統會改找替補來源。");
  if (row.status === "terminal_failed") return problemDescription(row, "字幕提取已停止，需要人工確認。");
  if (row.status === "failed_candidate") return "候選來源不可用，Worker 會繼續尋找替補。";
  if (row.status === "no_candidate_retry") {
    return row.no_candidate_until
      ? `目前找不到候選來源，預計 ${formatTime(row.no_candidate_until)} 後重試。`
      : "目前找不到候選來源，等待下一次重試。";
  }
  if (row.status === "completed" || row.status === "success") return "字幕已提取並匯入媒體庫。";
  return row.next_action ? nextActionLabel(row.next_action) : "等待下一步處理。";
}

function rowMeta(row) {
  const items = [];
  if (row.source_published_at) items.push(`種子發佈 ${sourcePublishedText(row)}`);
  if (row.torrent_created_at) items.push(`種子建立 ${formatTime(row.torrent_created_at)}`);
  if (row.torrent_added_at) {
    items.push(`加入 qB ${formatTime(row.torrent_added_at)}`);
  } else if (row.queued_at) {
    items.push(`送入下載佇列 ${formatTime(row.queued_at)}`);
  }
  if (row.torrent_completed_at) items.push(`下載完成 ${formatTime(row.torrent_completed_at)}`);
  if (row.extract_file_timestamp) {
    items.push(`${fileTimeLabel(row.extract_file_time_kind, "提取檔案")} ${formatTime(row.extract_file_timestamp)}`);
  }
  if (row.downloaded) items.push(`已下載 ${formatBytes(row.downloaded)}`);
  if (row.age_seconds) items.push(`經過 ${formatDuration(row.age_seconds)}`);
  if (row.last_extracted_count) items.push(`取得 ${row.last_extracted_count} 份字幕`);
  if (row.last_qbit_sync_at) items.push(`同步 ${formatTime(row.last_qbit_sync_at)}`);
  if (row.extract_job_attempts) items.push(`嘗試 ${row.extract_job_attempts} 次`);
  if (row.child_count > 1) items.push(`包含 ${row.child_count} 個來源項目`);
  return items;
}

function sourcePublishedText(row) {
  if (String(row?.source_published_precision || "") !== "date") {
    return formatTime(row?.source_published_at);
  }
  const date = new Date(Number(row.source_published_at) * 1000).toLocaleDateString("zh-TW", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return `${date}（來源網址日期）`;
}

function fileTimeLabel(kind, subject = "檔案") {
  return String(kind || "").toLowerCase() === "created"
    ? `${subject}建立時間`
    : `${subject}修改時間（建立時間不可用）`;
}

function extractFilePath(job) {
  return String(job?.current_file_path || job?.progress?.current || "");
}

function contextItems(row) {
  const context = row.last_extract_context || {};
  return [
    ["目前問題", problemTitle(row, "狀態已更新")],
    ["系統處理方式", problemSystemAction(row)],
    ["建議操作", problemRecommendedAction(row)],
    ["問題代碼", row.problem?.code],
    ["來源", row.source ? mikanSourceLabel(row.source) : ""],
    ["字幕狀態", row.subtitle_state ? mikanSubtitleStateLabel(row.subtitle_state) : ""],
    ["Bangumi / 集數", row.bangumi_id ? `${row.bangumi_id}:${row.episode || "-"}` : ""],
    ["qBit 狀態", row.last_qbit_state],
    ["qBit 名稱", row.last_qbit_name || row.torrent_name],
    ["字幕來源檔", context.source_video],
    ["目標影片", context.target_video],
    ["下載內容路徑", context.qbit_content_path],
    ["映射後路徑", context.mapped_root],
    ["候選影片", formatTargetCandidates(context.target_candidates)],
  ].filter(([, value]) => value);
}

function clearFilters() {
  statusFilter.value = "all";
  search.value = "";
}

function formatTargetCandidates(candidates) {
  if (!Array.isArray(candidates) || !candidates.length) return "";
  return candidates.slice(0, 3).map((item) => {
    const path = fileName(item.path || "");
    const season = Number(item.season || String(item.path || "").match(/[\\/]Season\s+(\d+)/i)?.[1] || 0);
    const reasons = Array.isArray(item.reasons)
      ? [...new Set(item.reasons.map(candidateEvidenceLabel))].slice(0, 2).join("、")
      : "";
    return [path, season ? `第 ${season} 季` : "", reasons].filter(Boolean).join(" · ");
  }).join(" / ");
}

function extractRuntime(job) {
  const startedAt = Number(job?.started_at || 0);
  return startedAt ? formatDuration(Math.max(0, props.nowSeconds - startedAt)) : "";
}

function extractHeartbeatAge(job) {
  const updatedAt = Number(job?.updated_at || 0);
  return updatedAt ? Math.max(0, props.nowSeconds - updatedAt) : null;
}

function extractActivityLabel(job) {
  if (job?.status !== "running") return "";
  const age = extractHeartbeatAge(job);
  if (age === null) return "尚未收到 Worker 心跳";
  if (age <= 90) return `Worker 心跳正常（${formatDuration(age)}前）`;
  return `Worker 心跳過舊（${formatDuration(age)}前），可能卡住`;
}
</script>

<template>
  <section class="page-panel downloads-page">
    <header class="page-heading">
      <div>
        <span class="section-label">自動字幕來源</span>
        <h1>字幕來源</h1>
        <p>追蹤字幕來源下載、提取、語言辨識與匯入狀態。失敗項目會保留原因，方便換來源或重跑。</p>
      </div>
      <button type="button" class="primary" :disabled="busy" @click="emit('process-completed')">
        {{ busy ? "背景任務執行中" : "立即處理已完成下載" }}
      </button>
    </header>

    <section class="source-stage-grid" aria-label="目前字幕來源流程">
      <article :class="{ active: pipeline.downloading }">
        <span class="stage-number">1</span><div><span>下載來源</span><strong>{{ pipeline.downloading }}</strong><small>qBittorrent 下載中</small></div>
      </article>
      <article :class="{ active: pipeline.waitingExtract }">
        <span class="stage-number">2</span><div><span>等待提取</span><strong>{{ pipeline.waitingExtract }}</strong><small>已完成下載</small></div>
      </article>
      <article :class="{ active: pipeline.extracting }">
        <span class="stage-number">3</span><div><span>提取字幕</span><strong>{{ pipeline.extracting }}</strong><small>{{ operation.busy ? "Worker 正在處理" : "等待或執行中" }}</small></div>
      </article>
    </section>

    <section class="source-secondary-summary" aria-label="字幕來源摘要">
      <span><b>{{ pipeline.candidateRetry }}</b> 個來源群組等待候選</span>
      <span><b>{{ pipeline.autoReplacing }}</b> 自動復原</span>
      <span><b>{{ monitoredStalls }}</b> 停滯監看</span>
      <span :class="{ attention: pipeline.needsAttention }"><b>{{ pipeline.needsAttention }}</b> 需人工確認</span>
      <span><b>{{ pipeline.imported }}</b> 累計匯入</span>
    </section>

    <p v-if="pipeline.candidateRetry || monitoredStalls || extractCounts.replaced" class="recovery-note">
      <span v-if="pipeline.candidateRetry">{{ pipeline.candidateRetry }} 個來源群組暫無候選，系統會定時重試。</span>
      <span v-if="monitoredStalls">{{ monitoredStalls }} 筆下載目前沒有進度，系統會依逾時規則自動處理，不需要手動介入。</span>
      <span v-if="extractCounts.replaced">累計已排除 {{ extractCounts.replaced }} 個失敗來源；這是歷史成果，不是目前故障。</span>
    </p>

    <section v-if="extractActive.length || extractAttention.length || extractCompleted.length" class="completion-section">
      <div class="card-heading">
        <div>
          <span class="section-label">字幕提取狀態</span>
          <h2>{{ extractViewMode === "completed" ? "最近完成" : extractViewMode === "attention" ? "需要人工確認" : "正在處理" }}</h2>
        </div>
        <div class="view-switch" aria-label="字幕提取檢視">
          <button type="button" :class="{ active: extractViewMode === 'active' }" @click="extractViewMode = 'active'">
            進行中 {{ stateDb.extract_jobs?.active || 0 }}
          </button>
          <button type="button" :class="{ active: extractViewMode === 'attention' }" @click="extractViewMode = 'attention'">
            需確認 {{ extractCounts.terminal_failed || 0 }}
          </button>
          <button type="button" :class="{ active: extractViewMode === 'completed' }" @click="extractViewMode = 'completed'">
            最近完成 {{ extractCompleted.length }}
          </button>
        </div>
      </div>
      <div v-if="visibleExtractJobs.length" class="completion-list">
        <article v-for="job in visibleExtractJobs" :key="job.job_key">
          <span :class="['complete-mark', statusTone(job.status)]">{{ job.status === "success" ? "✓" : job.status === "replaced" ? "↻" : ['queued', 'running'].includes(job.status) ? "…" : "!" }}</span>
          <div>
            <strong>{{ fileName(job.torrent_name) }}</strong>
            <small>
              {{ job.status === "success" ? "字幕已匯入" : mikanStatusLabel(job.status) }} ·
              {{ formatTime(job.finished_at || job.updated_at || job.started_at) }}
            </small>
            <small v-if="job.torrent_created_at">種子建立：{{ formatTime(job.torrent_created_at) }}</small>
            <small v-if="job.torrent_added_at">加入 qB：{{ formatTime(job.torrent_added_at) }}</small>
            <small v-if="job.torrent_completed_at">下載完成：{{ formatTime(job.torrent_completed_at) }}</small>
            <template v-if="job.progress?.total">
              <small>提取進度 {{ job.progress.processed || 0 }}/{{ job.progress.total }} · {{ Math.round(job.progress.percent || 0) }}%</small>
              <progress class="extract-progress" :value="job.progress.processed || 0" :max="job.progress.total"></progress>
            </template>
            <small v-if="extractFilePath(job)">
              {{ job.status === 'running' ? '目前檔案' : '最後處理檔案' }}：{{ fileName(extractFilePath(job)) }}
            </small>
            <small v-if="job.current_file_timestamp" class="file-time-note">
              {{ fileTimeLabel(job.current_file_time_kind, job.status === 'running' ? '目前檔案' : '最後處理檔案') }}：
              {{ formatTime(job.current_file_timestamp) }}
            </small>
            <small v-if="job.status === 'running' && extractRuntime(job)">已執行 {{ extractRuntime(job) }}</small>
            <small v-if="job.status === 'running'" :class="{ 'warning-text': Number(extractHeartbeatAge(job)) > 90 }">
              {{ extractActivityLabel(job) }}
            </small>
            <small v-if="job.last_error && extractViewMode === 'attention'" class="warning-text">
              {{ friendlyTechnicalMessage(job.last_error) }}
            </small>
          </div>
          <button
            v-if="job.status === 'running'"
            type="button"
            class="danger extract-cancel-button"
            :disabled="Boolean(cancelingJobKey)"
            @click="emit('cancel-extract', job)"
          >
            {{ cancelingJobKey === job.job_key ? "中斷要求送出中…" : "安全中斷" }}
          </button>
          <button
            v-else-if="['failed', 'terminal_failed'].includes(job.status)"
            type="button"
            class="primary extract-cancel-button"
            :disabled="Boolean(retryingJobKey)"
            @click="emit('retry-extract', job)"
          >
            {{ retryingJobKey === job.job_key ? "重新排隊中…" : "只重試這一筆" }}
          </button>
        </article>
      </div>
      <div v-else class="empty-state compact">目前沒有符合條件的提取 job</div>
    </section>

    <div class="list-toolbar">
      <label>
        <span>顯示</span>
        <select v-model="statusFilter" aria-label="篩選字幕來源">
          <option v-for="option in statusOptions" :key="option.key" :value="option.key">
            {{ option.label }}（{{ option.count }} {{ option.unit }}）
          </option>
        </select>
      </label>
      <label class="search-field">
        <span>搜尋</span>
        <input v-model="search" type="search" placeholder="輸入作品名稱、來源或錯誤訊息" />
      </label>
      <button v-if="hasFilters" type="button" class="quiet clear-filter" @click="clearFilters">清除篩選</button>
    </div>

    <div class="download-list">
      <article v-if="loading && !visibleRows.length" class="empty-state">正在讀取字幕來源...</article>
      <article v-else-if="!visibleRows.length" class="empty-state">目前沒有符合條件的字幕來源</article>
      <article v-for="row in visibleRows" :key="mikanRowKey(row)" class="download-card">
        <div class="download-copy">
          <div class="card-line">
            <span :class="['status-chip', statusTone(row.status)]">{{ mikanStatusLabel(row.status) }}</span>
            <span v-if="row.source" class="source-chip">{{ mikanSourceLabel(row.source) }}</span>
            <span v-if="row.subtitle_state" class="source-chip">{{ mikanSubtitleStateLabel(row.subtitle_state) }}</span>
            <small>{{ mikanSubtitle(row) }}</small>
          </div>
          <h3>{{ mikanTitle(row) }}</h3>
          <p :class="{ 'warning-text': ['target_missing', 'extract_failed', 'no_candidate_retry', 'failed', 'terminal_failed'].includes(row.status) }">
            {{ nextStep(row) }}
          </p>
          <p v-if="row.problem?.requires_user_action" class="recommended-action">
            <strong>你現在可以做：</strong>{{ problemRecommendedAction(row) }}
          </p>
          <div v-if="rowMeta(row).length" class="meta-list">
            <span v-for="item in rowMeta(row)" :key="item">{{ item }}</span>
          </div>
        </div>

        <div class="download-progress-cell">
          <b>{{ downloadProgress(row) === null ? "-" : formatPercent(downloadProgress(row)) }}</b>
          <div v-if="downloadProgress(row) !== null" class="progress-track">
            <span :style="{ width: formatPercent(downloadProgress(row)) }"></span>
          </div>
          <small>{{ row.dlspeed ? formatSpeed(row.dlspeed) : "目前沒有速度" }}</small>
        </div>

        <details v-if="contextItems(row).length" class="row-details">
          <summary>進階資訊（需要時再看）</summary>
          <dl>
            <template v-for="[label, value] in contextItems(row)" :key="label">
              <dt>{{ label }}</dt><dd>{{ value }}</dd>
            </template>
          </dl>
        </details>
      </article>
    </div>

    <footer class="pagination">
      <button type="button" :disabled="loading || requestedPage <= 1" @click="emit('page', requestedPage - 1)">上一頁</button>
      <span>第 {{ requestedPage }} / {{ downloads.page_count || 1 }} 頁</span>
      <button type="button" :disabled="loading || requestedPage >= Number(downloads.page_count || 1)" @click="emit('page', requestedPage + 1)">下一頁</button>
    </footer>
  </section>
</template>
