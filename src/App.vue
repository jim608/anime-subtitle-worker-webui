<script setup>
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref, watch } from "vue";
import {
  actionLabel,
  fileName,
  friendlyApiError,
  friendlyTaskMessage,
  formatCompactCount,
  formatBytes,
  formatDuration,
  groupReviewItems,
  mikanAttentionSummary,
  mikanPipelineSummary,
  mikanRowKey,
  taskProgress,
} from "./dashboard.js";

const ActionsPanel = defineAsyncComponent(() => import("./components/ActionsPanel.vue"));
const EventsTimeline = defineAsyncComponent(() => import("./components/EventsTimeline.vue"));
const MikanDownloads = defineAsyncComponent(() => import("./components/MikanDownloads.vue"));
const ReviewCenter = defineAsyncComponent(() => import("./components/ReviewCenter.vue"));
const SeriesMetadata = defineAsyncComponent(() => import("./components/SeriesMetadata.vue"));
const TaskDashboard = defineAsyncComponent(() => import("./components/TaskDashboard.vue"));

const PANEL_KEYS = new Set(["overview", "downloads", "queue", "reviews", "series", "actions", "events"]);
const AI_DELIVERY_SLO_TARGET = 0.9999;
const AI_DELIVERY_SLO_TARGET_LABEL = "99.99%";
const AI_DELIVERY_SLO_CARD_LABEL = `AI 繁中字 30 天準時交付率（目標 ${AI_DELIVERY_SLO_TARGET_LABEL}）`;
const AI_DELIVERY_EVIDENCE_CARD_LABEL = `AI 繁中字準時交付累積證據（目標 ${AI_DELIVERY_SLO_TARGET_LABEL}）`;

function panelFromLocation() {
  const hashPanel = window.location.hash.replace(/^#\/?/, "");
  if (PANEL_KEYS.has(hashPanel)) return hashPanel;
  const saved = window.localStorage.getItem("subtitle-workbench-panel") || "";
  return PANEL_KEYS.has(saved) ? saved : "overview";
}

const status = ref(null);
const taskPayload = ref({ tasks: [], recent_completed: [], counts: {}, page: 1, page_count: 1, mode: "active" });
const downloads = ref({ recent: [], counts: {}, page: 1, page_count: 1 });
const events = ref({ recent: [] });
const reviewPayload = ref({ items: [], total: 0, next_cursor: null });
const reviewRecovery = ref({});
const reviewDetails = ref({});
const reviewDetailLoadingIds = ref([]);
const seriesPayload = ref({ exists: false, items: [], total: 0, page: 1, page_count: 0 });
const seriesDetail = ref(null);
const aiDiagnostics = ref(null);
const aiDiagnosticsPath = ref("");
const action = ref({});
const loading = ref(true);
const error = ref("");
const toast = ref("");
const lastUpdated = ref(null);
const activePanel = ref(panelFromLocation());
const refreshing = ref(false);
const downloadsLoading = ref(false);
const tasksLoading = ref(false);
const seriesLoading = ref(false);
const seriesDetailLoading = ref(false);
const seriesOperation = ref({ busy: false, status: "idle", action: "", target: "", message: "", error: "" });
const aiDiagnosticsLoading = ref(false);
const reviewsLoading = ref(false);
const streamState = ref("connecting");
const nowSeconds = ref(Date.now() / 1000);
const pendingQueuePaths = ref([]);
const aiControlBusy = ref(false);
const mikanCancelBusy = ref(false);
const mikanExtractCancelKey = ref("");
const mikanExtractRetryKey = ref("");
const pendingReviewIds = ref([]);
const reviewOperations = ref({});
const commandActionBusy = ref(false);
const csrfToken = ref("");
const mobileMoreOpen = ref(false);
const workerRuntimeLog = ref(null);
const workerRuntimeLogLoading = ref(false);
const aiSweepBusy = ref(false);
const aiSweepPreview = ref(null);

const taskQuery = ref({ mode: "active", page: 1, pageSize: 30, status: "", search: "" });
const reviewQuery = ref({ state: "needs_action", kind: "", search: "", sort: "priority" });
const seriesQuery = ref({ page: 1, pageSize: 30, search: "" });
const mikanPage = ref(1);
const mikanStatusFilter = ref("");
const mikanSearch = ref("");

let timer = null;
let clockTimer = null;
let stream = null;
let refreshTimer = null;
let refreshInFlight = false;
let refreshQueued = false;
let refreshQueuedForceAll = false;
let refreshQueuedShowIndicator = false;
let refreshQueuedFullPanel = false;
let refreshQueuedEntities = new Set();
let refreshTimerForceAll = false;
let refreshTimerFullPanel = false;
let refreshTimerEntities = new Set();
let downloadController = null;
let taskController = null;
let reviewController = null;
let downloadRequestId = 0;
let taskRequestId = 0;
let reviewRequestId = 0;
const pendingForcedReviewDetailIds = new Set();
let toastTimer = null;
let seriesRefreshQueued = false;
let disposed = false;

const tasks = computed(() => taskPayload.value.tasks || []);
const completedTasks = computed(() => taskPayload.value.recent_completed || []);
const openReviews = computed(() => groupReviewItems(reviewPayload.value.items || []));
const openReviewCount = computed(() => Number(reviewPayload.value?.state_counts?.open ?? openReviews.value.length));
const queueCounts = computed(() => {
  const live = status.value?.queue_counts || {};
  return Object.keys(live).length ? live : (taskPayload.value.counts || {});
});
const mikanStateDb = computed(() => status.value?.mikan?.state_db || {});
const mikanCounts = computed(() => {
  const live = mikanStateDb.value.counts || {};
  return Object.keys(live).length ? live : (downloads.value.counts || {});
});
const extractJobs = computed(() => mikanStateDb.value?.extract_jobs || {
  counts: {}, active: 0, recent: [], recent_failed: [], recent_retryable: [],
  recent_attention: [], recent_replaced: [], recent_completed: [],
});
const mikanPipeline = computed(() => mikanPipelineSummary({
  pipeline: mikanStateDb.value.pipeline || {},
  mikanCounts: mikanCounts.value,
  extractCounts: extractJobs.value.counts || {},
}));
const actionBusy = computed(() => Boolean(action.value?.running) || commandActionBusy.value || aiSweepBusy.value);
const liteCurrentAiTask = computed(() => {
  const item = status.value?.current_ai;
  if (!item?.path) return null;
  return {
    ...item,
    status: item.status || "Running",
    file_name: item.file_name || fileName(item.path),
  };
});
const activeTask = computed(() => liteCurrentAiTask.value || tasks.value.find((task) => task.status === "Running"));
const activeTaskProgress = computed(() => activeTask.value ? taskProgress(activeTask.value) : null);
const activeTaskElapsed = computed(() => {
  const startedAt = Number(activeTask.value?.running_started_at || 0);
  return startedAt ? formatDuration(Math.max(0, nowSeconds.value - startedAt)) : "";
});
const versionInfo = computed(() => status.value?.version || {});
const healthInfo = computed(() => status.value?.health || {});
const healthChecks = computed(() => healthInfo.value?.checks || []);
const failedHealthChecks = computed(() => healthChecks.value.filter((check) => !check.ok));
const mikanOperation = computed(() => status.value?.mikan || {});
const aiControl = computed(() => status.value?.ai_control || { paused: false });
const aiPaused = computed(() => Boolean(aiControl.value.paused));
const aiScheduler = computed(() => status.value?.ai_scheduler || { exists: false, state: "unavailable" });
const aiSchedulerNeedsAttention = computed(() => Boolean(aiScheduler.value.problem));
const aiSchedulerRetryIn = computed(() => {
  const nextRetryAt = Number(aiScheduler.value.next_retry_at || 0);
  return nextRetryAt > 0 ? Math.max(0, Math.ceil(nextRetryAt - nowSeconds.value)) : 0;
});
const aiSchedulerProblemDetail = computed(() => {
  const reason = String(aiScheduler.value.reason_code || "");
  const retry = aiSchedulerRetryIn.value > 0
    ? `系統會在 ${aiSchedulerRetryIn.value} 秒內自動重試。`
    : "系統正在自動恢復。";
  if (aiScheduler.value.stale) {
    return "Worker 容器仍在執行，但 AI 排程心跳已停止。請立即重試；若仍無回應，再執行安全更新或重啟 Worker。";
  }
  if (reason === "scanner_database_disk_io") {
    return `AI 排程暫時讀不到工作清單，這次沒有領取任何影片。${retry}`;
  }
  if (reason === "scanner_database_busy") {
    return `AI 工作清單目前被其他安全操作占用，這次沒有領取影片。${retry}`;
  }
  return `AI 排程暫時無法讀取工作清單，沒有工作會被誤標完成。${retry}`;
});
const ioPolicy = computed(() => status.value?.io_policy || {});
const resourceEnvelope = computed(() => status.value?.resources || {});
const resourceTelemetry = computed(() => resourceEnvelope.value.telemetry || status.value?.resource_telemetry || {
  sampled_at: null,
  max_age_seconds: 45,
  stale: false,
  refreshing: false,
  cpu: { available: false, error_code: "probe_pending" },
  ram: { available: false, error_code: "probe_pending" },
  gpu: { available: false, error_code: "probe_pending" },
});
const resourceDisk = computed(() => resourceEnvelope.value.disk || {
  available: false,
  error_code: "probe_pending",
  sampled_at: null,
  max_age_seconds: 45,
});
const resourceAdmission = computed(() => resourceEnvelope.value.admission || {
  available: false,
  error_code: "state_missing",
  sampled_at: null,
  max_age_seconds: null,
});
const etaSummary = computed(() => status.value?.eta || {});
const failureSummary = computed(() => status.value?.failure_summary || { buckets: [] });
const completedDelivery = computed(() => status.value?.completed_delivery || {
  enabled: false,
  available: false,
  state: "disabled",
  final_path: "",
  committed_at: 0,
  size: 0,
  hash: "",
  error: "",
});
function completedDeliveryErrorText(code) {
  return ({
    receipt_missing: "Worker 尚未提交成品收據",
    delivery_in_progress: "Worker 正在提交成品",
    delivery_marker_stale: "成品提交中斷或已逾時",
    final_artifact_missing: "收據指向的成品不存在",
    final_artifact_stale: "成品檔案身分已改變",
    final_artifact_hash_stale: "成品雜湊與 Worker 收據不符",
    publication_manifest_missing: "字幕發布證據不存在",
    publication_manifest_stale: "字幕發布證據已改變",
    worker_ledger_unavailable: "Worker 交付台帳目前無法讀取",
    worker_delivery_evidence_missing: "Worker 台帳沒有這筆成品交付",
    worker_delivery_evidence_invalid: "Worker 台帳與成品收據不一致",
    overview_unavailable: "成品交付監控目前無法讀取",
  }[String(code || "")] || "成品證據目前無法通過驗證");
}
const completedDeliveryCard = computed(() => {
  const delivery = completedDelivery.value;
  if (!delivery.enabled) return null;
  const state = String(delivery.state || "unavailable");
  const labels = {
    committed: "已交付",
    delivering: "交付中",
    waiting: "等待首個成品",
    pending: "等待處理完成",
    missing: "成品證據缺失",
    stale: "成品證據失效",
    invalid: "成品證據無效",
    unavailable: "無法驗證",
  };
  const committed = delivery.available === true && state === "committed";
  const size = Number(delivery.size || 0);
  return {
    label: "成品影片交付",
    value: labels[state] || "無法驗證",
    detail: committed
      ? `${delivery.final_path}${size > 0 ? ` · ${formatBytes(size)}` : ""}`
      : `未提供可用成品路徑 · ${completedDeliveryErrorText(delivery.error)}`,
    tone: committed ? "success" : state === "delivering" || state === "waiting" || state === "pending" ? "muted" : "warn",
  };
});
const aiDeliverySlo = computed(() => status.value?.ai_delivery_slo || {
  window_days: 30,
  target: AI_DELIVERY_SLO_TARGET,
  numerator: 0,
  denominator: 0,
  confidence_lower_bound: null,
  confidence_target_met: null,
  coverage_active_queue_total: null,
  coverage_active_queue_tracked: null,
  coverage_active_queue_untracked: null,
  coverage_active_queue_complete: null,
  coverage_inventory_available: false,
  coverage_inventory_state: "unavailable",
  coverage_inventory_epoch_id: null,
  coverage_inventory_completed_at: 0,
  coverage_inventory_age_seconds: null,
  coverage_inventory_total: null,
  coverage_inventory_delivery_required: null,
  coverage_inventory_tracked: null,
  coverage_inventory_untracked: null,
  coverage_inventory_legacy_grandfathered: null,
  coverage_inventory_complete: null,
  coverage_complete: null,
  rolling_operational: {
    mode: "rolling_observed_media_census",
    numerator: 0,
    denominator: 0,
    point_target_met: null,
    fixed_sample_descriptive_only: true,
    proof_eligible: false,
    state: "unavailable",
  },
  cumulative_evidence: {
    mode: "fixed_measurement_revision_cumulative_media_cohort",
    scope: "strict_on_time_delivery_not_semantic_accuracy",
    numerator: 0,
    denominator: 0,
    lower_confidence_bound: null,
    target_evidence_met: null,
    anytime_valid: true,
    state: "unavailable",
  },
  publication_breakdown: {
    translated_chinese: {
      publication_kinds: ["adopted_zh_tw", "converted_zh_cn", "translated_trilingual"],
      verified_on_time: 0,
      by_publication_kind: { adopted_zh_tw: 0, converted_zh_cn: 0, translated_trilingual: 0 },
      required_output_language: "zh-TW",
    },
    source_language: {
      verified_on_time: 0,
      by_output_language: {},
      counts_as_traditional_chinese_success: false,
    },
    unclassified_misses: 0,
    invalid_success_evidence: 0,
  },
  sample_state: "unavailable",
});
const aiDeliverySloCard = computed(() => {
  const slo = aiDeliverySlo.value;
  const rolling = slo.rolling_operational || {};
  const numerator = Math.max(0, Number(rolling.numerator || 0));
  const denominator = Math.max(0, Number(rolling.denominator || 0));
  const rate = denominator > 0 ? numerator / denominator : null;
  const state = String(rolling.state || slo.sample_state || "unavailable");
  const pointMet = rolling.point_target_met === true;
  const overallVerified = slo.target_met === true;
  if (state === "coverage_incomplete" || state === "unavailable") {
    return {
      label: AI_DELIVERY_SLO_CARD_LABEL,
      value: "無法判定",
      detail: "完整媒體 inventory / ledger coverage 尚未連續成立；不宣稱準時交付率達標。",
      tone: "warn",
    };
  }
  if (state === "warming" || state === "no_matured_obligations" || rate === null) {
    return {
      label: AI_DELIVERY_SLO_CARD_LABEL,
      value: "觀測中",
      detail: "近 30 天 rolling census 尚未形成完整成熟窗口；此卡只反映可用繁中字準時交付，不代表 ASR 或翻譯正確率。",
      tone: "muted",
    };
  }
  return {
    label: AI_DELIVERY_SLO_CARD_LABEL,
    value: `${(rate * 100).toFixed(4)}%`,
    detail: `${numerator.toLocaleString("en-US")}/${denominator.toLocaleString("en-US")} 個 eligible media 已準時交付可用繁中字；來源語言字幕不算成功。固定樣本 Clopper–Pearson 僅供描述，不是連續監看的證明。`,
    tone: overallVerified ? "success" : pointMet ? "muted" : "warn",
  };
});
const aiDeliveryEvidenceCard = computed(() => {
  const evidence = aiDeliverySlo.value.cumulative_evidence || {};
  const numerator = Math.max(0, Number(evidence.numerator || 0));
  const denominator = Math.max(0, Number(evidence.denominator || 0));
  const state = String(evidence.state || "unavailable");
  const overallVerified = aiDeliverySlo.value.target_met === true;
  const lower = Number(evidence.lower_confidence_bound);
  const lowerLabel = Number.isFinite(lower) && lower >= 0 && lower <= 1
    ? `${(lower * 100).toFixed(6)}%`
    : "尚無";
  if (state === "coverage_incomplete" || state === "unavailable") {
    return {
      label: AI_DELIVERY_EVIDENCE_CARD_LABEL,
      value: "無法判定",
      detail: "28,800 秒 freshness 的連續 full-inventory coverage 已中斷或不可驗證；舊區段不得接回。",
      tone: "warn",
    };
  }
  if (state === "warming" || denominator === 0) {
    return {
      label: AI_DELIVERY_EVIDENCE_CARD_LABEL,
      value: "累積中",
      detail: "從目前 measurement revision 的首個完整 inventory epoch 起，等待 eligible media 的 72 小時期限成熟。",
      tone: "muted",
    };
  }
  const supported = evidence.target_evidence_met === true;
  return {
    label: AI_DELIVERY_EVIDENCE_CARD_LABEL,
    value: overallVerified
      ? "95% anytime 證據達標"
      : supported
        ? "證據門檻已達，整體尚未驗證"
        : "證據收集中",
    detail: `${numerator.toLocaleString("en-US")}/${denominator.toLocaleString("en-US")} 個 media；anytime-valid 單尾下界 ${lowerLabel}。只證明可用繁中字 strict 準時交付；source-only／source_language 明確不算成功，也不證明 WER 或翻譯語意正確。`,
    tone: overallVerified ? "success" : state === "below_target" ? "warn" : "muted",
  };
});
const aiFailedRetrySweep = computed(() => status.value?.ai_failed_retry_sweep || {
  available: true,
  state: "idle",
  campaign_id: "",
  counters: {},
  current_item: null,
});
const aiSweepStateLabel = computed(() => ({
  idle: "尚未啟動",
  running: "逐筆安全修復中",
  paused: "已在首個問題停止",
  completed: "本輪已完成",
  cancelled: "已停止",
  failed: "無法繼續",
  unavailable: "狀態暫時無法讀取",
}[aiFailedRetrySweep.value.state] || aiFailedRetrySweep.value.state || "尚未啟動"));
const aiSweepCounters = computed(() => aiFailedRetrySweep.value.counters || {});
const recommendations = computed(() => status.value?.recommendations || []);
const aiRateLabel = computed(() => {
  const rate = Number(etaSummary.value.rate_per_hour || 0);
  if (!rate) return "估算中";
  return rate >= 10 ? `${Math.round(rate)} 部／小時` : `${rate.toFixed(1)} 部／小時`;
});
const aiEtaLabel = computed(() => {
  const hours = Number(etaSummary.value.eta_hours);
  if (!Number.isFinite(hours) || hours <= 0) return "尚無足夠樣本";
  return formatDuration(hours * 3600);
});
const aiEtaMethodLabel = computed(() => {
  if (etaSummary.value.eta_method === "recent_throughput") return "依最近實際完成速度";
  if (etaSummary.value.eta_method === "historical_median") {
    const samples = Number(etaSummary.value.duration_sample_count || 0);
    return `依最近 ${samples} 部完成時間中位數`;
  }
  return "等待更多完整處理樣本";
});
const redownloadActive = computed(() => {
  const active = mikanOperation.value?.redownload_active || {};
  return active.exists && active.active ? active : null;
});
const redownloadCancelRequested = computed(() => Boolean(mikanOperation.value?.redownload_cancel?.exists));
const redownloadProgress = computed(() => {
  const current = Number(redownloadActive.value?.current || 0);
  const total = Number(redownloadActive.value?.total || 0);
  return total > 0 ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : null;
});
const actionElapsed = computed(() => {
  if (!action.value?.running || !action.value?.started_at) return "";
  return formatDuration(Math.max(0, nowSeconds.value - Number(action.value.started_at)));
});
const freshnessLabel = computed(() => {
  if (!lastUpdated.value) return "尚未更新";
  const age = Math.max(0, nowSeconds.value - lastUpdated.value.getTime() / 1000);
  if (age < 8) return "剛剛更新";
  return `${formatDuration(age)}前更新`;
});

const workerState = computed(() => {
  if (!status.value) return { label: "讀取中", tone: "running", detail: "WebUI 正在讀取 Worker 狀態。" };
  const worker = status.value?.worker || {};
  if (!worker.available) return { label: "Worker 不存在", tone: "danger", detail: "WebUI 找不到 Worker 容器。" };
  if (worker.restarting) {
    const retries = Number(worker.restart_count || 0);
    return {
      label: "Worker 持續重啟，工作已停止",
      tone: "danger",
      detail: retries > 0 ? `容器已重試 ${retries.toLocaleString()} 次，請查看啟動錯誤。` : "容器無法完成啟動，請查看啟動錯誤。",
    };
  }
  if (!worker.running) return { label: "Worker 沒有執行", tone: "danger", detail: "字幕來源與 AI 佇列不會自動處理。" };
  return { label: "Worker 正常", tone: "success", detail: "下載、字幕提取與 AI 佇列可正常排程。" };
});
const workerNeedsRuntimeHelp = computed(() => {
  const worker = status.value?.worker || {};
  return Boolean(status.value && (!worker.available || !worker.running || worker.restarting));
});

const processingCount = computed(() => (
  mikanPipeline.value.downloading
  + mikanPipeline.value.extracting
  + (queueCounts.value.running || 0)
));
const waitingCount = computed(() => (
  mikanPipeline.value.queuedDownloads
  + mikanPipeline.value.waitingExtract
  + mikanPipeline.value.candidateRetry
  + (queueCounts.value.queued || 0)
));
const attentionSummary = computed(() => mikanAttentionSummary({
  mikanCounts: mikanCounts.value,
  extractCounts: extractJobs.value.counts || {},
  stateDb: mikanStateDb.value,
  queueCounts: queueCounts.value,
}));
const failureOutcomes = computed(() => failureSummary.value?.outcomes_7d || {
  queued: 0,
  failed_retry: 0,
  done: 0,
  missing: 0,
  other: 0,
});
const failureReviewOverlap = computed(() => failureSummary.value?.review_overlap || {});
const deduplicatedCoreAttentionCount = computed(() => {
  const rawValue = failureReviewOverlap.value.deduplicated_attention_total;
  if (rawValue === null || rawValue === undefined || rawValue === "") return null;
  const value = Number(rawValue);
  return Number.isFinite(value) ? value : null;
});
const openAttentionReviewCount = computed(() => (
  failureReviewOverlap.value.available
    ? Number(failureReviewOverlap.value.open_total || 0)
    : openReviewCount.value
));
const reviewedAiFailureCount = computed(() => (
  deduplicatedCoreAttentionCount.value === null
    ? 0
    : Number(failureReviewOverlap.value.ai_failed_retry?.video_count || 0)
));
const reviewedTerminalExtractCount = computed(() => (
  deduplicatedCoreAttentionCount.value === null
    ? 0
    : Number(failureReviewOverlap.value.terminal_extract?.job_count || 0)
));
const unreviewedAiFailureCount = computed(() => Math.max(
  0,
  attentionSummary.value.aiRetryFailures - reviewedAiFailureCount.value,
));
const unreviewedTerminalExtractCount = computed(() => Math.max(
  0,
  attentionSummary.value.terminalExtractFailures - reviewedTerminalExtractCount.value,
));
// Count unique current problem roots. Reviews, failed AI videos, and terminal
// extraction jobs may describe the same underlying item, so the backend union
// is authoritative. The retry backlog below is a separate operational view and
// may intentionally overlap this attention count.
const manualAttentionCount = computed(() => (
  (deduplicatedCoreAttentionCount.value ?? (
    openReviewCount.value
    + attentionSummary.value.aiRetryFailures
    + attentionSummary.value.terminalExtractFailures
  ))
  + Number(queueCounts.value.paused || 0)
  + attentionSummary.value.targetMissing
));
const retryableExtractCount = computed(() => {
  const backendCount = extractJobs.value?.retryable_count;
  if (backendCount === null || backendCount === undefined || backendCount === "") {
    return attentionSummary.value.retryableExtractFailures;
  }
  const value = Number(backendCount);
  return Number.isFinite(value)
    ? Math.max(0, value)
    : attentionSummary.value.retryableExtractFailures;
});
const retryBacklogCount = computed(() => (
  attentionSummary.value.aiRetryFailures
  + retryableExtractCount.value
));
const retryableFailureCount = computed(() => retryBacklogCount.value);
const automaticRecoveryCount = computed(() => (
  mikanPipeline.value.autoReplacing + attentionSummary.value.blockedDownloads
));
const aiStandbyMessage = computed(() => {
  const count = queueCounts.value.queued || 0;
  if (aiSchedulerNeedsAttention.value) {
    return aiSchedulerProblemDetail.value;
  }
  if (aiPaused.value) {
    return `AI 已暫停，${count} 筆工作保留中。`;
  }
  if (mikanOperation.value?.busy) {
    return `${count} 筆等待；字幕來源作業執行中。`;
  }
  return `${count} 筆等待，Worker 會自動領取下一筆。`;
});

const healthState = computed(() => {
  if (!status.value) return workerState.value;
  if (!status.value?.worker?.available || !status.value?.worker?.running) return workerState.value;
  const failedErrors = Number(healthInfo.value.failed_errors || 0);
  const failedWarnings = Number(healthInfo.value.failed_warnings || 0);
  if (aiSchedulerNeedsAttention.value) {
    return {
      label: aiScheduler.value.stale ? "AI 排程心跳已停止" : "AI 排程暫時無法讀取佇列",
      tone: "danger",
      detail: aiSchedulerProblemDetail.value,
    };
  }
  if (failedErrors) {
    return {
      label: `${failedErrors} 項系統檢查失敗`,
      tone: "danger",
      detail: failedHealthChecks.value.map((check) => check.name).join("、") || "請展開健康檢查查看原因。",
    };
  }
  if (activeTask.value?.running_stale) {
    return {
      label: "AI 任務可能卡住",
      tone: "danger",
      detail: `目前任務已超過 ${formatDuration(activeTask.value.stale_after_seconds || 0)} 沒有更新。`,
    };
  }
  if (aiPaused.value) {
    return {
      label: "AI 佇列已暫停",
      tone: "warn",
      detail: activeTask.value
        ? "目前影片會處理完成，之後不再啟動下一部。Mikan 下載與字幕提取不受影響。"
        : "AI 不會啟動新影片；Mikan 下載與字幕提取仍會繼續。",
    };
  }
  if (processingCount.value) {
    return {
      label: "字幕自動化處理中",
      tone: "running",
      detail: `目前有 ${processingCount.value} 個項目正在下載、提取或 AI 處理。`,
    };
  }
  if (failedWarnings) {
    return {
      label: `${failedWarnings} 項檢查提醒`,
      tone: "warn",
      detail: failedHealthChecks.value.map((check) => check.name).join("、") || "系統仍可執行，但建議查看健康檢查。",
    };
  }
  return {
    label: "目前沒有阻塞項目",
    tone: "success",
    detail: waitingCount.value ? `還有 ${waitingCount.value} 個項目等待排程。` : "沒有下載、提取或 AI 任務在排隊。",
  };
});

const RESOURCE_ERROR_LABELS = {
  probe_pending: "監控資料正在讀取",
  probe_start_failed: "監控背景工作無法啟動",
  probe_failed: "監控探針執行失敗",
  overview_unavailable: "資源總覽暫時無法更新",
  overview_field_missing: "資源總覽缺少必要欄位",
  cpu_warming_up: "CPU 使用率正在建立基準",
  cpu_probe_missing: "此環境沒有可用的 CPU 探針",
  cpu_permission_denied: "沒有權限讀取 CPU 資料",
  cpu_probe_failed: "CPU 探針執行失敗",
  cpu_parse_error: "CPU 資料格式無法解析",
  cpu_sample_invalid: "CPU 取樣尚未形成有效區間",
  ram_probe_missing: "此環境沒有可用的 RAM 探針",
  ram_permission_denied: "沒有權限讀取 RAM 資料",
  ram_probe_failed: "RAM 探針執行失敗",
  ram_parse_error: "RAM 資料格式無法解析",
  nvidia_smi_missing: "未找到 nvidia-smi 或 NVIDIA GPU",
  nvidia_smi_timeout: "nvidia-smi 讀取逾時",
  nvidia_smi_execute_failed: "nvidia-smi 無法執行",
  nvidia_smi_failed: "nvidia-smi 回報錯誤",
  nvidia_smi_parse_error: "nvidia-smi 資料格式無法解析",
  disk_probe_failed: "無法讀取工作磁碟用量",
  state_missing: "Worker 尚未寫入資源准入狀態",
  state_path_invalid: "資源准入狀態路徑無效",
  state_unreadable: "無法讀取資源准入狀態",
  state_too_large: "資源准入狀態超過安全大小",
  state_invalid_json: "資源准入狀態不是有效 JSON",
  state_invalid_root: "資源准入狀態格式無效",
  state_schema_mismatch: "資源准入狀態版本不相容",
  state_invalid_payload: "資源准入狀態內容無效",
  state_invalid_freshness: "資源准入狀態時間欄位無效",
  state_decision_missing: "Worker 尚未產生資源准入決策",
  state_invalid_decision: "資源准入決策欄位無效",
  state_stale: "Worker 資源准入狀態已過期",
};

function resourceUnavailableDetail(metric, fallback = "監控不可用") {
  const code = String(metric?.error_code || "unknown");
  return RESOURCE_ERROR_LABELS[code] || `${fallback}（${code}）`;
}

function formatResourceBytes(value) {
  if (value === null || value === undefined || value === "") return "無資料";
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "無資料";
  if (bytes >= 1024 ** 3) return `${(bytes / (1024 ** 3)).toFixed(bytes >= 10 * (1024 ** 3) ? 1 : 2)} GiB`;
  if (bytes >= 1024 ** 2) return `${(bytes / (1024 ** 2)).toFixed(1)} MiB`;
  return `${Math.round(bytes / 1024)} KiB`;
}

function resourceMetricNumber(value) {
  if (value === null || value === undefined || value === "") return Number.NaN;
  const number = Number(value);
  return Number.isFinite(number) ? number : Number.NaN;
}

function resourceMetricTone(percent) {
  if (!Number.isFinite(percent)) return "muted";
  if (percent >= 90) return "warn";
  if (percent >= 70) return "running";
  return "muted";
}

function resourceSnapshotAgeSeconds(snapshot, now = Date.now() / 1000) {
  const sampledAt = resourceMetricNumber(snapshot?.sampled_at);
  const current = resourceMetricNumber(now);
  if (!Number.isFinite(sampledAt) || sampledAt <= 0 || !Number.isFinite(current)) return Number.NaN;
  return Math.max(0, current - sampledAt);
}

function resourceSnapshotIsStale(snapshot, now = Date.now() / 1000, fallbackMaxAge = 45) {
  if (snapshot?.stale === true || snapshot?.transport_stale === true) return true;
  const age = resourceSnapshotAgeSeconds(snapshot, now);
  const configuredMaxAge = resourceMetricNumber(snapshot?.max_age_seconds);
  const maxAge = Number.isFinite(configuredMaxAge) && configuredMaxAge > 0
    ? configuredMaxAge
    : fallbackMaxAge;
  return !Number.isFinite(age) || age > maxAge;
}

function unavailableResourceSnapshot(kind, errorCode) {
  const common = {
    available: false,
    error_code: errorCode,
    sampled_at: null,
    max_age_seconds: kind === "admission" ? null : 45,
    stale: true,
    transport_stale: errorCode === "overview_unavailable",
  };
  if (kind !== "telemetry") return common;
  return {
    ...common,
    cpu: { available: false, error_code: errorCode },
    ram: { available: false, error_code: errorCode },
    gpu: { available: false, error_code: errorCode },
  };
}

function retainStaleResourceSnapshot(previous, kind) {
  if (!previous || typeof previous !== "object" || !previous.sampled_at) {
    return unavailableResourceSnapshot(kind, "overview_unavailable");
  }
  return {
    ...previous,
    stale: true,
    refreshing: false,
    transport_stale: true,
    transport_error_code: "overview_unavailable",
  };
}

const resourceStats = computed(() => {
  const telemetry = resourceTelemetry.value || {};
  const disk = resourceDisk.value || {};
  const admission = resourceAdmission.value || {};
  const cpu = telemetry.cpu || {};
  const ram = telemetry.ram || {};
  const gpu = telemetry.gpu || {};
  const aggregate = gpu.aggregate || {};
  const devices = Array.isArray(gpu.devices) ? gpu.devices : [];
  const telemetryStale = resourceSnapshotIsStale(telemetry, nowSeconds.value);
  const stalePrefix = telemetryStale ? "資料已過期，顯示上一筆；" : "";

  const cpuPercent = resourceMetricNumber(cpu.utilization_percent);
  const cpuAvailable = cpu.available === true && Number.isFinite(cpuPercent);
  const ramPercent = resourceMetricNumber(ram.utilization_percent);
  const ramAvailable = ram.available === true && Number.isFinite(ramPercent);
  const gpuPercent = resourceMetricNumber(aggregate.utilization_percent);
  const gpuAvailable = gpu.available === true && Number.isFinite(gpuPercent);
  const memoryUsedMib = resourceMetricNumber(aggregate.memory_used_mib);
  const memoryTotalMib = resourceMetricNumber(aggregate.memory_total_mib);
  const vramAvailable = gpu.available === true
    && Number.isFinite(memoryUsedMib)
    && Number.isFinite(memoryTotalMib)
    && memoryTotalMib > 0;
  const vramPercent = vramAvailable ? 100 * memoryUsedMib / memoryTotalMib : Number.NaN;
  const temperature = resourceMetricNumber(aggregate.temperature_celsius);
  const processCount = resourceMetricNumber(aggregate.process_count);
  const gpuDetail = [
    devices.length ? `${devices.length} 張 GPU` : "",
    devices[0]?.name || "",
    Number.isFinite(temperature) ? `${temperature.toFixed(0)}°C` : "",
    Number.isInteger(processCount) && processCount >= 0
      ? `${processCount} 個計算程序`
      : gpu.process_error_code ? `程序數不可用（${gpu.process_error_code}）` : "",
  ].filter(Boolean).join(" · ");
  const diskPercent = resourceMetricNumber(disk.utilization_percent);
  const diskFreeGb = resourceMetricNumber(disk.free_gb);
  const diskTotalGb = resourceMetricNumber(disk.total_gb);
  const diskAvailable = disk.available === true
    && Number.isFinite(diskPercent)
    && Number.isFinite(diskFreeGb)
    && Number.isFinite(diskTotalGb)
    && !resourceSnapshotIsStale(disk, nowSeconds.value);
  const admissionFresh = admission.available === true
    && !resourceSnapshotIsStale(admission, nowSeconds.value, 15);
  const selectedRoute = admission.selected_route || {};
  const effective = admission.effective || {};
  const retryAfter = resourceMetricNumber(admission.retry_after_seconds);
  const admissionDetail = admissionFresh
    ? [
      admission.job_stage ? `階段 ${admission.job_stage}` : "",
      Array.isArray(admission.reason_codes) && admission.reason_codes.length
        ? `原因 ${admission.reason_codes.join(", ")}`
        : "",
      !admission.allow_new_job && Number.isFinite(retryAfter) && retryAfter > 0
        ? `${formatDuration(retryAfter)}後重試`
        : "",
    ].filter(Boolean).join(" · ") || "Worker 已提供有效決策"
    : resourceUnavailableDetail(
      admission.available === true && resourceSnapshotIsStale(admission, nowSeconds.value, 15)
        ? { error_code: "state_stale" }
        : admission,
      "資源准入狀態不可用",
    );
  const routeDetail = admissionFresh
    ? [
      effective.batch_size ? `batch ${effective.batch_size}` : "batch 無資料",
      effective.context_max_blocks && effective.context_max_chars
        ? `context ${effective.context_max_blocks} 段 / ${effective.context_max_chars} 字`
        : "context 無資料",
      effective.concurrency ? `concurrency ${effective.concurrency}` : "concurrency 無資料",
      selectedRoute.fallback_selected ? "已使用低資源 fallback" : "",
    ].filter(Boolean).join(" · ")
    : "等待 Worker 的有效資源決策";

  return [
    {
      label: "CPU",
      value: cpuAvailable ? `${cpuPercent.toFixed(1)}%` : "無法讀取",
      detail: cpuAvailable
        ? `${stalePrefix}${cpu.logical_processors || "?"} 個邏輯處理器`
        : resourceUnavailableDetail(cpu, "CPU 監控不可用"),
      tone: resourceMetricTone(cpuAvailable ? cpuPercent : Number.NaN),
    },
    {
      label: "RAM",
      value: ramAvailable ? `${ramPercent.toFixed(1)}%` : "無法讀取",
      detail: ramAvailable
        ? `${stalePrefix}${formatResourceBytes(ram.used_bytes)} / ${formatResourceBytes(ram.total_bytes)}`
        : resourceUnavailableDetail(ram, "RAM 監控不可用"),
      tone: resourceMetricTone(ramAvailable ? ramPercent : Number.NaN),
    },
    {
      label: "GPU",
      value: gpuAvailable ? `${gpuPercent.toFixed(1)}%` : "無法讀取",
      detail: gpu.available === true
        ? `${stalePrefix}${gpuAvailable ? "" : "GPU 使用率感測器不可用；"}${gpuDetail}`
        : resourceUnavailableDetail(gpu, "GPU 監控不可用"),
      tone: resourceMetricTone(gpuAvailable ? gpuPercent : Number.NaN),
    },
    {
      label: "VRAM",
      value: vramAvailable ? `${vramPercent.toFixed(1)}%` : "無法讀取",
      detail: vramAvailable
        ? `${stalePrefix}${formatResourceBytes(memoryUsedMib * (1024 ** 2))} / ${formatResourceBytes(memoryTotalMib * (1024 ** 2))}`
        : gpu.available === true
          ? "VRAM 感測器不可用"
          : resourceUnavailableDetail(gpu, "VRAM 監控不可用"),
      tone: resourceMetricTone(vramPercent),
    },
    {
      label: "Disk",
      value: diskAvailable ? `${diskPercent.toFixed(1)}%` : "無法讀取",
      detail: diskAvailable
        ? `${diskFreeGb.toFixed(1)} GiB 可用 / ${diskTotalGb.toFixed(1)} GiB`
        : resourceUnavailableDetail(
          disk.available === true && resourceSnapshotIsStale(disk, nowSeconds.value)
            ? { error_code: "overview_unavailable" }
            : disk,
          "Disk 監控不可用",
        ),
      tone: resourceMetricTone(diskAvailable ? diskPercent : Number.NaN),
    },
    {
      label: "資源准入",
      value: admissionFresh
        ? admission.allow_new_job ? "允許新工作" : admission.allow_running_job ? "僅允許續跑" : "延後"
        : "無法讀取",
      detail: admissionDetail,
      tone: !admissionFresh ? "muted" : admission.allow_new_job ? "success" : "warn",
    },
    {
      label: "執行路由",
      value: admissionFresh
        ? selectedRoute.model || (admission.job_stage ? "非 ASR 階段" : "未選擇模型")
        : "無法讀取",
      detail: admissionFresh && selectedRoute.compute_type
        ? `${selectedRoute.compute_type} · ${routeDetail}`
        : routeDetail,
      tone: admissionFresh && selectedRoute.fallback_selected ? "running" : "muted",
    },
  ];
});

const overviewPrimaryStats = computed(() => [
  { label: "正在處理", value: processingCount.value, detail: "下載、提取與 AI", tone: "running" },
  { label: "等待處理", value: waitingCount.value, detail: "已排入自動流程", tone: "queued" },
  {
    label: "24 小時完成",
    value: Number(etaSummary.value.completed_last_24h || 0),
    detail: "AI 字幕實際完成數",
    tone: "success",
  },
]);
const overviewResourceStats = computed(() => resourceStats.value.slice(0, 5));
const overviewDetailStats = computed(() => [
  { label: "自動處理", value: automaticRecoveryCount.value, detail: "監看停滯並更換來源", tone: "muted" },
  {
    label: "磁碟讀取",
    value: `${ioPolicy.value.extract_workers_effective || 1} 路`,
    detail: ioPolicy.value.pressure_busy
      ? `偵測到硬碟壓力，已降載（some ${Number(ioPolicy.value.pressure?.some_avg10 || 0).toFixed(1)}%）`
      : ioPolicy.value.ai_disk_active
        ? "AI 轉錄中，字幕提取已自動降載"
        : `字幕提取上限 ${ioPolicy.value.extract_workers_idle || 2} 路`,
    tone: ioPolicy.value.pressure_busy ? "warn" : ioPolicy.value.ai_disk_active ? "running" : "muted",
  },
  ...resourceStats.value.slice(5),
  aiDeliverySloCard.value,
  aiDeliveryEvidenceCard.value,
  ...(completedDeliveryCard.value ? [completedDeliveryCard.value] : []),
]);

const attentionItems = computed(() => [
  { label: "待審核", value: openAttentionReviewCount.value, panel: "reviews" },
  { label: "AI 失敗", value: unreviewedAiFailureCount.value, panel: "queue", taskStatus: "failed_retry" },
  { label: "AI／ASR 待確認", value: Number(queueCounts.value.paused || 0), panel: "queue", taskStatus: "paused" },
  { label: "媒體缺失", value: attentionSummary.value.targetMissing, panel: "downloads", mikanStatus: "target_missing" },
  { label: "提取失敗", value: unreviewedTerminalExtractCount.value, panel: "downloads", mikanStatus: "terminal_failed" },
].filter((item) => item.value > 0));
const runtimeFacts = computed(() => {
  const worker = status.value?.worker || {};
  return [
    ["WebUI", versionInfo.value.webui_fingerprint],
    ["Config", versionInfo.value.config_sha256],
    ["Worker image", worker.image_id || worker.image],
    ["Worker start", worker.started_at],
    ["Worker restarts", Number(worker.restart_count || 0) || ""],
    ["Worker exit", worker.exit_code !== undefined && worker.exit_code !== null ? worker.exit_code : ""],
    ["Worker error", worker.state_error],
    ["Health", healthInfo.value.overall],
    ["AI scheduler", aiScheduler.value.exists ? aiScheduler.value.state : ""],
  ].filter(([, value]) => value);
});

const pages = computed(() => [
  { key: "overview", label: "總覽", icon: "⌂", count: 0 },
  {
    key: "downloads",
    label: "影片來源",
    icon: "↓",
    count: mikanPipeline.value.downloading + mikanPipeline.value.extracting + mikanPipeline.value.waitingExtract,
  },
  { key: "queue", label: "AI 字幕", icon: "AI", count: (queueCounts.value.running || 0) + (queueCounts.value.queued || 0) },
  { key: "reviews", label: "人工審核", icon: "✓", count: openReviewCount.value },
  { key: "series", label: "作品資訊", icon: "書", count: Number(seriesPayload.value.total || 0) },
  { key: "actions", label: "系統工具", icon: "⚙", count: actionBusy.value ? 1 : 0 },
  { key: "events", label: "處理紀錄", icon: "≡", count: 0 },
]);
const mobilePrimaryPages = computed(() => pages.value.filter((page) => (
  ["overview", "downloads", "queue", "reviews"].includes(page.key)
)));
const mobileMorePages = computed(() => pages.value.filter((page) => (
  ["series", "actions", "events"].includes(page.key)
)));
const mobileMoreActive = computed(() => mobileMorePages.value.some((page) => page.key === activePanel.value));

const streamLabel = computed(() => ({
  live: "即時連線",
  retrying: "重新連線中",
  connecting: "連線中",
  unsupported: "定時更新",
}[streamState.value] || "定時更新"));

function showToast(message) {
  toast.value = message;
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toast.value = ""; }, 4500);
}

async function api(path, options = {}) {
  const {
    timeoutMs = 20000,
    signal: upstreamSignal,
    idempotencyKey = "",
    headers: suppliedHeaders = {},
    ...fetchOptions
  } = options;
  const controller = new AbortController();
  let timedOut = false;
  const abortFromUpstream = () => controller.abort(upstreamSignal?.reason);
  if (upstreamSignal?.aborted) abortFromUpstream();
  else upstreamSignal?.addEventListener("abort", abortFromUpstream, { once: true });
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    const method = String(fetchOptions.method || "GET").toUpperCase();
    const headers = { Accept: "application/json", "Content-Type": "application/json", ...suppliedHeaders };
    if (path.startsWith("/api/v2/") && !["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken.value) {
      headers["X-CSRF-Token"] = csrfToken.value;
    }
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    const response = await fetch(path, {
      headers,
      ...fetchOptions,
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch {
        // Keep the HTTP message.
      }
      const requestError = new Error(detail);
      requestError.status = response.status;
      throw requestError;
    }
    return await response.json();
  } catch (err) {
    if (timedOut && err?.name === "AbortError") throw new Error(`請求逾時（${Math.round(timeoutMs / 1000)} 秒）`);
    throw err;
  } finally {
    window.clearTimeout(timeout);
    upstreamSignal?.removeEventListener("abort", abortFromUpstream);
  }
}

async function copyDiagnostics() {
  const snapshot = {
    captured_at: new Date().toISOString(),
    version: versionInfo.value,
    worker: status.value?.worker || {},
    health: healthInfo.value,
    queue_counts: queueCounts.value,
    current_ai: status.value?.current_ai || null,
    io_policy: ioPolicy.value,
    mikan_pipeline: mikanPipeline.value,
    extract_jobs: extractJobs.value?.counts || {},
    failure_summary: failureSummary.value,
    database_health: status.value?.database_health || {},
    recommendations: recommendations.value,
  };
  const text = JSON.stringify(snapshot, null, 2);
  try {
    await navigator.clipboard.writeText(text);
    showToast("診斷摘要已複製，可直接貼給 Codex");
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
    showToast("診斷摘要已複製，可直接貼給 Codex");
  }
}

function handleRecommendation(item) {
  if (item?.action) {
    runAction(item.action);
    return;
  }
  if (item?.panel === "queue") {
    navigate("queue", { taskStatus: item.status_filter || "" });
    return;
  }
  if (item?.panel === "downloads") {
    navigate("downloads", { mikanStatus: item.status_filter || "" });
    return;
  }
  if (item?.panel) navigate(item.panel);
}

async function loadSummary() {
  const [summaryResult, overviewResult] = await Promise.allSettled([
    api("/api/dashboard/summary"),
    api("/api/v2/overview"),
  ]);
  if (summaryResult.status === "rejected") throw summaryResult.reason;
  const payload = summaryResult.value;
  const overview = overviewResult.status === "fulfilled" ? overviewResult.value : {};
  const previousResources = status.value?.resources || {
    telemetry: status.value?.resource_telemetry || null,
    disk: status.value?.resource_disk || null,
    admission: status.value?.resource_admission || null,
  };
  const overviewResources = overviewResult.status === "fulfilled"
    && overview.resources
    && typeof overview.resources === "object"
    ? overview.resources
    : null;
  const resources = overviewResources
    ? {
      telemetry: overviewResources.telemetry
        || unavailableResourceSnapshot("telemetry", "overview_field_missing"),
      disk: overviewResources.disk
        || unavailableResourceSnapshot("disk", "overview_field_missing"),
      admission: overviewResources.admission
        || unavailableResourceSnapshot("admission", "overview_field_missing"),
    }
    : {
      telemetry: retainStaleResourceSnapshot(previousResources.telemetry, "telemetry"),
      disk: retainStaleResourceSnapshot(previousResources.disk, "disk"),
      admission: retainStaleResourceSnapshot(previousResources.admission, "admission"),
    };
  status.value = {
    ...payload,
    ai_delivery_slo: overview.ai_delivery_slo || { sample_state: "unavailable" },
    completed_delivery: overviewResult.status === "fulfilled" && overview.completed_delivery
      ? overview.completed_delivery
      : {
        enabled: Boolean(status.value?.completed_delivery?.enabled),
        available: false,
        state: "unavailable",
        final_path: "",
        committed_at: 0,
        size: 0,
        hash: "",
        error: "overview_unavailable",
      },
    resources,
    resource_telemetry: resources.telemetry,
    resource_disk: resources.disk,
    resource_admission: resources.admission,
  };
  action.value = payload.action || action.value || {};
  lastUpdated.value = new Date();
  return payload;
}

async function loadWorkerRuntimeLog() {
  if (workerRuntimeLogLoading.value) return;
  workerRuntimeLogLoading.value = true;
  try {
    workerRuntimeLog.value = await api("/api/v2/worker/runtime-log?tail=120", { timeoutMs: 12000 });
  } catch (err) {
    workerRuntimeLog.value = { ok: false, lines: [], error: friendlyApiError("讀取 Worker 啟動錯誤", err) };
  } finally {
    workerRuntimeLogLoading.value = false;
  }
}

async function loadBootstrap() {
  const payload = await api("/api/v2/bootstrap");
  csrfToken.value = String(payload.csrf_token || "");
  return payload;
}

function preserveRowsByKey(previousRows, nextRows, keyFor) {
  const byKey = new Map((nextRows || []).map((row) => [String(keyFor(row) || ""), row]));
  const ordered = [];
  for (const row of previousRows || []) {
    const key = String(keyFor(row) || "");
    if (!key || !byKey.has(key)) continue;
    ordered.push(byKey.get(key));
    byKey.delete(key);
  }
  ordered.push(...byKey.values());
  return ordered;
}

async function loadReviews({ preserveOrder = true, append = false, supersede = true } = {}) {
  if (reviewsLoading.value && !supersede) return;
  if (supersede && reviewController) reviewController.abort();
  const requestId = ++reviewRequestId;
  const controller = new AbortController();
  reviewController = controller;
  reviewsLoading.value = true;
  const query = new URLSearchParams({
    view: "summary",
    limit: "30",
    state: reviewQuery.value.state,
    sort: reviewQuery.value.sort,
  });
  if (reviewQuery.value.kind) query.set("kind", reviewQuery.value.kind);
  if (reviewQuery.value.search) query.set("search", reviewQuery.value.search);
  if (append && reviewPayload.value.next_cursor) query.set("cursor", reviewPayload.value.next_cursor);
  try {
    const payload = await api(`/api/v2/review-items?${query.toString()}`, { signal: controller.signal });
    if (requestId !== reviewRequestId) return;
    if (append) {
      const current = reviewPayload.value.items || [];
      const combined = preserveRowsByKey(current, [...current, ...(payload.items || [])], (row) => row.review_id);
      reviewPayload.value = { ...payload, items: combined };
      return;
    }
    reviewPayload.value = preserveOrder
      ? { ...payload, items: preserveRowsByKey(reviewPayload.value.items, payload.items, (row) => row.review_id) }
      : payload;
    const selectedReviewId = String(window.localStorage.getItem("review-selected-id") || "");
    if (
      selectedReviewId
      && (payload.items || []).some((item) => String(item.review_id || "") === selectedReviewId)
      && reviewDetails.value[selectedReviewId]
    ) {
      await loadReviewDetail(selectedReviewId, { force: true });
    }
  } catch (err) {
    if (err?.name !== "AbortError" && requestId === reviewRequestId) {
      error.value = friendlyApiError("讀取人工審核", err);
    }
  } finally {
    if (requestId === reviewRequestId) {
      reviewsLoading.value = false;
      reviewController = null;
    }
  }
}

function setReviewQuery(query) {
  const next = {
    ...reviewQuery.value,
    ...query,
  };
  if (JSON.stringify(next) === JSON.stringify(reviewQuery.value)) return;
  reviewQuery.value = next;
  loadReviews({ preserveOrder: false, supersede: true });
}

function loadMoreReviews() {
  if (!reviewPayload.value.next_cursor) return;
  loadReviews({ preserveOrder: false, append: true, supersede: false });
}

async function loadReviewDetail(reviewId, { force = false } = {}) {
  const normalizedId = String(reviewId || "");
  if (!normalizedId) return;
  if (reviewDetailLoadingIds.value.includes(normalizedId)) {
    if (force) pendingForcedReviewDetailIds.add(normalizedId);
    return;
  }
  if (!force && reviewDetails.value[normalizedId]) return;
  reviewDetailLoadingIds.value = [...reviewDetailLoadingIds.value, normalizedId];
  try {
    const payload = await api(`/api/v2/review-items/${encodeURIComponent(normalizedId)}`);
    if (payload?.item) {
      reviewDetails.value = { ...reviewDetails.value, [normalizedId]: payload.item };
    }
  } catch (err) {
    error.value = friendlyApiError("讀取審核詳情", err);
  } finally {
    reviewDetailLoadingIds.value = reviewDetailLoadingIds.value.filter((value) => value !== normalizedId);
    if (pendingForcedReviewDetailIds.delete(normalizedId)) {
      void loadReviewDetail(normalizedId, { force: true });
    }
  }
}

async function searchReviewSeries({ review, query }) {
  const reviewId = String(review?.review_id || "");
  const normalizedQuery = String(query || "").trim();
  if (!reviewId || !normalizedQuery) return;
  const previous = reviewRecovery.value[reviewId] || {};
  reviewRecovery.value = {
    ...reviewRecovery.value,
    [reviewId]: { ...previous, query: normalizedQuery, loading: true, error: "" },
  };
  try {
    const params = new URLSearchParams({ page: "1", page_size: "20", search: normalizedQuery });
    const payload = await api(`/api/series?${params.toString()}`);
    reviewRecovery.value = {
      ...reviewRecovery.value,
      [reviewId]: {
        query: normalizedQuery,
        loading: false,
        error: "",
        items: payload.items || [],
      },
    };
  } catch (err) {
    reviewRecovery.value = {
      ...reviewRecovery.value,
      [reviewId]: {
        ...previous,
        query: normalizedQuery,
        loading: false,
        error: friendlyApiError("搜尋本地作品", err),
      },
    };
  }
}

async function loadTasks({ supersede = false, preserveOrder = false } = {}) {
  if (tasksLoading.value && !supersede) return;
  if (supersede && taskController) taskController.abort();
  const requestId = ++taskRequestId;
  const controller = new AbortController();
  taskController = controller;
  tasksLoading.value = true;
  const query = new URLSearchParams({
    mode: taskQuery.value.mode,
    page: String(taskQuery.value.page),
    page_size: String(taskQuery.value.pageSize),
  });
  if (taskQuery.value.status) query.set("status_filter", taskQuery.value.status);
  if (taskQuery.value.search) query.set("search", taskQuery.value.search);
  try {
    const payload = await api(`/api/dashboard/tasks?${query.toString()}`, { signal: controller.signal });
    if (requestId === taskRequestId) {
      taskPayload.value = preserveOrder
        ? { ...payload, tasks: preserveRowsByKey(taskPayload.value.tasks, payload.tasks, (row) => row.path) }
        : payload;
    }
  } catch (err) {
    if (err?.name !== "AbortError" && requestId === taskRequestId) {
      error.value = friendlyApiError("讀取 AI 佇列", err);
    }
  } finally {
    if (requestId === taskRequestId) {
      tasksLoading.value = false;
      taskController = null;
    }
  }
}

function preserveMikanRowOrder(previousRows, nextRows) {
  const buckets = new Map();
  for (const row of nextRows || []) {
    const key = mikanRowKey(row);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(row);
  }
  const ordered = [];
  for (const row of previousRows || []) {
    const bucket = buckets.get(mikanRowKey(row));
    if (!bucket?.length) continue;
    ordered.push(bucket.shift());
  }
  for (const bucket of buckets.values()) ordered.push(...bucket);
  return ordered;
}

async function loadDownloads({ supersede = false, preserveOrder = false } = {}) {
  if (downloadsLoading.value && !supersede) return;
  if (supersede && downloadController) downloadController.abort();
  const requestId = ++downloadRequestId;
  const controller = new AbortController();
  downloadController = controller;
  downloadsLoading.value = true;
  const query = new URLSearchParams({ page: String(mikanPage.value), page_size: "20", compact: "true" });
  if (mikanStatusFilter.value) query.set("status_filter", mikanStatusFilter.value);
  if (mikanSearch.value) query.set("search", mikanSearch.value);
  try {
    const payload = await api(`/api/mikan/downloads?${query.toString()}`, { signal: controller.signal });
    if (requestId === downloadRequestId) {
      downloads.value = preserveOrder
        ? { ...payload, recent: preserveMikanRowOrder(downloads.value.recent, payload.recent) }
        : payload;
    }
  } catch (err) {
    if (err?.name !== "AbortError" && requestId === downloadRequestId) {
      error.value = friendlyApiError("讀取字幕來源", err);
    }
  } finally {
    if (requestId === downloadRequestId) {
      downloadsLoading.value = false;
      downloadController = null;
    }
  }
}

async function loadEvents() {
  events.value = await api("/api/v2/events?limit=20");
}

async function loadSeries({ supersede = false } = {}) {
  if (seriesLoading.value) {
    seriesRefreshQueued ||= supersede;
    return;
  }
  seriesLoading.value = true;
  const query = new URLSearchParams({
    page: String(seriesQuery.value.page),
    page_size: String(seriesQuery.value.pageSize),
  });
  if (seriesQuery.value.search) query.set("search", seriesQuery.value.search);
  try {
    seriesPayload.value = await api(`/api/series?${query.toString()}`);
  } catch (err) {
    error.value = friendlyApiError("讀取作品資訊", err);
  } finally {
    seriesLoading.value = false;
    if (seriesRefreshQueued) {
      seriesRefreshQueued = false;
      window.setTimeout(() => loadSeries(), 0);
    }
  }
}

async function loadSeriesDetail(seriesIdOrPath) {
  if (!seriesIdOrPath || seriesDetailLoading.value) return;
  seriesDetailLoading.value = true;
  try {
    seriesDetail.value = String(seriesIdOrPath).startsWith("series_")
      ? await api(`/api/v2/series/${encodeURIComponent(seriesIdOrPath)}`)
      : await api(`/api/series/detail?${new URLSearchParams({ path: seriesIdOrPath }).toString()}`);
  } catch (err) {
    error.value = friendlyApiError("讀取作品詳細資料", err);
  } finally {
    seriesDetailLoading.value = false;
  }
}

async function updateSeries(action, body, successMessage) {
  if (seriesOperation.value.busy) return;
  const target = String(body?.path || "");
  seriesOperation.value = {
    busy: true,
    status: "submitting",
    action,
    target,
    command_id: "",
    message: "正在安全送出作品資料更新…",
    error: "",
    started_at: Date.now() / 1000,
  };
  try {
    if (!csrfToken.value) throw new Error("安全權杖尚未載入，請重新整理後再試。");
    const { path, series_id: seriesId, ...parameters } = body;
    const payload = await api("/api/v2/commands", {
      method: "POST",
      body: JSON.stringify({ action, target: path, parameters }),
      idempotencyKey: idempotencyKey(`${action}-${seriesId || path}`),
    });
    seriesOperation.value = {
      ...seriesOperation.value,
      status: "running",
      command_id: String(payload.command_id || ""),
      message: "已送出，Worker 正在更新作品資料…",
    };
    const command = await waitForCommand(payload.command_id, { maxAttempts: 60, intervalMs: 1000 });
    if (command.status !== "completed") throw new Error(command.error || "Worker 未完成作品資料更新");
    seriesOperation.value = {
      ...seriesOperation.value,
      busy: false,
      status: "completed",
      message: successMessage,
      error: "",
      finished_at: Date.now() / 1000,
    };
    showToast(successMessage);
    await Promise.allSettled([loadSeries({ supersede: true }), loadSeriesDetail(seriesId || path)]);
  } catch (err) {
    seriesOperation.value = {
      ...seriesOperation.value,
      busy: false,
      status: "failed",
      message: "作品資料沒有變更",
      error: err instanceof Error ? err.message : String(err),
      finished_at: Date.now() / 1000,
    };
    error.value = friendlyApiError(successMessage, err);
  }
}

function setSeriesLock(payload) {
  return updateSeries("series.lock", payload, payload.locked ? "作品資料已鎖定" : "作品資料已解鎖");
}

function setSeriesMatch(payload) {
  return updateSeries("series.match", payload, "作品匹配已更新");
}

function upsertSeriesGlossary(payload) {
  return updateSeries("series.glossary_upsert", payload, "作品術語已儲存");
}

function deleteSeriesGlossary(payload) {
  return updateSeries("series.glossary_delete", payload, "作品術語已刪除");
}

function setSeriesQuery(query) {
  seriesQuery.value = {
    ...seriesQuery.value,
    ...query,
    page: Math.max(1, Number(query.page || 1)),
  };
  loadSeries();
}

async function loadAiDiagnostics(path) {
  if (!path || aiDiagnosticsLoading.value) return;
  aiDiagnosticsLoading.value = true;
  aiDiagnosticsPath.value = path;
  aiDiagnostics.value = null;
  try {
    aiDiagnostics.value = await api(`/api/ai/diagnostics?${new URLSearchParams({ path }).toString()}`);
  } catch (err) {
    error.value = friendlyApiError("讀取 AI 進階診斷", err);
  } finally {
    aiDiagnosticsLoading.value = false;
  }
}

async function refresh(options = {}) {
  const forceAll = Boolean(options.forceAll);
  const showIndicator = options.showIndicator === undefined ? forceAll : Boolean(options.showIndicator);
  const changedEntities = Array.isArray(options.changedEntities)
    ? [...new Set(options.changedEntities.map((item) => String(item || "").trim()).filter(Boolean))]
    : null;
  if (refreshInFlight) {
    refreshQueued = true;
    refreshQueuedForceAll ||= forceAll;
    refreshQueuedShowIndicator ||= showIndicator;
    if (changedEntities === null) refreshQueuedFullPanel = true;
    else changedEntities.forEach((entity) => refreshQueuedEntities.add(entity));
    return;
  }
  refreshInFlight = true;
  if (showIndicator) refreshing.value = true;
  if (showIndicator) error.value = "";
  try {
    const requests = [loadSummary()];
    const changed = new Set(changedEntities || []);
    const refreshesEntity = (entity) => forceAll || changedEntities === null || changed.has(entity);
    if (activePanel.value === "queue" && refreshesEntity("ai")) {
      requests.push(loadTasks({ supersede: forceAll, preserveOrder: !forceAll }));
    }
    if (activePanel.value === "downloads" && refreshesEntity("mikan")) {
      requests.push(loadDownloads({ supersede: forceAll, preserveOrder: !forceAll }));
    }
    if (activePanel.value === "reviews" && refreshesEntity("reviews")) requests.push(loadReviews());
    if (activePanel.value === "series" && refreshesEntity("series")) requests.push(loadSeries());
    if (activePanel.value === "events" && refreshesEntity("events")) requests.push(loadEvents());

    const results = await Promise.allSettled(requests);
    const failures = results.filter((result) => result.status === "rejected");
    if (failures.length === results.length) {
      error.value = friendlyApiError("更新 WebUI", failures[0].reason);
    } else if (failures.length) {
      error.value = friendlyApiError("更新部分資料", failures[0].reason);
    }
  } finally {
    loading.value = false;
    if (showIndicator) refreshing.value = false;
    refreshInFlight = false;
    if (refreshQueued) {
      const queuedForceAll = refreshQueuedForceAll;
      const queuedShowIndicator = refreshQueuedShowIndicator;
      const queuedChangedEntities = refreshQueuedFullPanel ? null : [...refreshQueuedEntities];
      refreshQueued = false;
      refreshQueuedForceAll = false;
      refreshQueuedShowIndicator = false;
      refreshQueuedFullPanel = false;
      refreshQueuedEntities = new Set();
      window.setTimeout(() => refresh({
        forceAll: queuedForceAll,
        showIndicator: queuedShowIndicator,
        changedEntities: queuedChangedEntities,
      }), 0);
    }
  }
}

function scheduleRefresh(forceAll = false, changedEntities = null) {
  refreshTimerForceAll ||= Boolean(forceAll);
  if (!Array.isArray(changedEntities)) refreshTimerFullPanel = true;
  else changedEntities.forEach((entity) => refreshTimerEntities.add(String(entity || "")));
  if (refreshTimer) return;
  refreshTimer = window.setTimeout(() => {
    const scheduledForceAll = refreshTimerForceAll;
    const scheduledChangedEntities = refreshTimerFullPanel ? null : [...refreshTimerEntities].filter(Boolean);
    refreshTimer = null;
    refreshTimerForceAll = false;
    refreshTimerFullPanel = false;
    refreshTimerEntities = new Set();
    refresh({
      forceAll: scheduledForceAll,
      showIndicator: scheduledForceAll,
      changedEntities: scheduledChangedEntities,
    });
  }, 700);
}

async function runAction(name) {
  if (actionBusy.value) return;
  if (name === "ai-safe-retry-sweep") {
    await runAiFailedRetrySweep("start");
    return;
  }
  if (name === "retry-all-failures" && !window.confirm(`確定要一鍵重試 ${retryableFailureCount.value} 個 AI／字幕提取失敗項目？`)) return;
  if (name === "ai-retry-all-failures" && !window.confirm(`確定要重試 ${Number(queueCounts.value.failed_retry || 0)} 個 AI 失敗項目？`)) return;
  if (name === "mikan-reset-all" && !window.confirm("確定要重設全部字幕來源狀態？這會讓候選重新排程。")) return;
  if (name === "mikan-redownload-all" && !window.confirm("確定要重新檢查並下載全部字幕來源？這會影響大量 qBittorrent 任務。")) return;
  if (name === "mikan-requeue-failed-extracts" && !window.confirm("確定要把提取失敗的字幕來源重新排隊？")) return;
  const workerCommand = {
    "retry-all-failures": "system.retry_all_failures",
    "ai-retry-all-failures": "system.ai_retry_all_failures",
    "ai-scheduler-retry": "system.ai_scheduler_retry",
    "refresh-ass": "system.refresh_ass",
    "cleanup-generated": "system.cleanup_generated",
    "ai-refresh-queue-state": "system.refresh_ai_queue_state",
    "mikan-process-completed": "mikan.process_completed",
    "mikan-reset-all": "mikan.request_reset_all",
    "mikan-redownload-all": "mikan.request_redownload_all",
    "mikan-requeue-failed-extracts": "mikan.requeue_failed_extracts",
    "backup-state": "system.backup_state",
    "database-maintenance": "system.database_maintenance",
    "series-sync": "series.sync",
  }[name];
  try {
    if (workerCommand) {
      if (!csrfToken.value) throw new Error("安全權杖尚未載入，請重新整理後再試");
      commandActionBusy.value = true;
      const payload = await api("/api/v2/commands", {
        method: "POST",
        body: JSON.stringify({
          action: workerCommand,
          parameters: workerCommand === "mikan.request_redownload_all" ? { delete_files: false } : {},
        }),
        idempotencyKey: idempotencyKey(`system-${name}`),
      });
      showToast(`已安全送交 Worker：${actionLabel(name)}`);
      const command = await waitForCommand(payload.command_id);
      if (command.status !== "completed") throw new Error(command.error || "Worker 未完成操作");
      showToast(`已完成：${actionLabel(name)}`);
      await Promise.allSettled([loadSummary(), loadTasks({ supersede: true }), loadDownloads()]);
      scheduleRefresh(true);
      return;
    }
    const payload = await api(`/api/actions/${name}`, { method: "POST", body: "{}" });
    if (payload.started === false || payload.ok === false) {
      throw new Error(payload.message || "背景任務未啟動");
    }
    action.value = payload.action_state || payload;
    showToast(`已送出：${actionLabel(name)}`);
    scheduleRefresh(true);
  } catch (err) {
    error.value = friendlyApiError(actionLabel(name), err);
  } finally {
    if (workerCommand) commandActionBusy.value = false;
  }
}

async function runAiFailedRetrySweep(operation = "preview") {
  if (aiSweepBusy.value) return;
  if (!csrfToken.value) {
    error.value = "安全權杖尚未載入，請重新整理後再試。";
    return;
  }
  const normalized = String(operation || "preview").toLowerCase();
  const parameters = { operation: normalized };
  if (normalized === "preview" || normalized === "start") {
    Object.assign(parameters, {
      max_items: 1,
      interval_seconds: 300,
      min_age_seconds: 0,
      max_attempts: 3,
    });
  } else {
    parameters.campaign_id = String(aiFailedRetrySweep.value.campaign_id || "");
  }
  aiSweepBusy.value = true;
  try {
    const payload = await api("/api/v2/commands", {
      method: "POST",
      body: JSON.stringify({
        action: "system.ai_failed_retry_sweep",
        parameters,
      }),
      idempotencyKey: idempotencyKey(`ai-safe-retry-${normalized}`),
    });
    const command = await waitForCommand(payload.command_id, { maxAttempts: 30, intervalMs: 1000 });
    if (command.status !== "completed") throw new Error(command.error || "Worker 未完成安全修復控制操作");
    if (normalized === "preview") {
      aiSweepPreview.value = command.result || null;
      const eligible = Number(command.result?.counters?.eligible || 0);
      showToast(`安全預覽完成：${eligible} 筆符合條件，尚未變更佇列`);
    } else if (normalized === "start") {
      showToast("已啟動單筆安全修復；不重設重試次數，遇到問題會立即停止");
    } else {
      showToast(normalized === "pause" ? "安全修復已暫停" : "安全修復已恢復");
    }
    await loadSummary();
    scheduleRefresh(true, ["ai"]);
  } catch (err) {
    error.value = friendlyApiError("安全自動修復", err);
  } finally {
    aiSweepBusy.value = false;
  }
}

async function setAiQueuePaused(paused) {
  if (aiControlBusy.value) return;
  if (!csrfToken.value) {
    error.value = "安全權杖尚未載入，請重新整理後再試。";
    return;
  }
  aiControlBusy.value = true;
  try {
    const payload = await api("/api/v2/commands", {
      method: "POST",
      body: JSON.stringify({ action: paused ? "system.ai_queue_pause" : "system.ai_queue_resume" }),
      idempotencyKey: idempotencyKey(`ai-queue-${paused ? "pause" : "resume"}`),
    });
    const command = await waitForCommand(payload.command_id, { maxAttempts: 30, intervalMs: 1000 });
    if (command.status !== "completed") throw new Error(command.error || "Worker 未完成操作");
    await loadSummary();
    showToast(paused
      ? (activeTask.value ? "目前影片完成後暫停 AI" : "AI 佇列已暫停")
      : "AI 佇列已恢復");
    scheduleRefresh(true);
  } catch (err) {
    error.value = friendlyApiError(paused ? "暫停 AI 佇列" : "恢復 AI 佇列", err);
  } finally {
    aiControlBusy.value = false;
  }
}

async function cancelMikanRedownload() {
  if (mikanCancelBusy.value || redownloadCancelRequested.value) return;
  if (!window.confirm("確定停止目前的 Mikan 全量重抓？已加入 qBittorrent 的任務會保留。")) return;
  mikanCancelBusy.value = true;
  try {
    if (!csrfToken.value) throw new Error("安全權杖尚未載入，請重新整理後再試。");
    const payload = await api("/api/v2/commands", {
      method: "POST",
      body: JSON.stringify({ action: "mikan.cancel_redownload" }),
      idempotencyKey: idempotencyKey("mikan-cancel-redownload"),
    });
    const command = await waitForCommand(payload.command_id, { maxAttempts: 60, intervalMs: 1000 });
    if (command.status !== "completed") throw new Error(command.error || "Worker 未完成取消要求");
    showToast(command.result?.cancelled_pending ? "已取消尚未開始的全量重抓" : "已要求安全停止全量重抓");
    scheduleRefresh(true);
  } catch (err) {
    error.value = friendlyApiError("停止字幕來源全量重抓", err);
  } finally {
    mikanCancelBusy.value = false;
  }
}

async function cancelMikanExtract(job) {
  const jobKey = String(job?.job_key || "");
  if (!jobKey || mikanExtractCancelKey.value) return;
  const name = fileName(job?.torrent_name || jobKey);
  if (!window.confirm(`確定安全中斷目前的字幕提取？\n${name}\n\n已下載的檔案與已發布字幕不會刪除，工作會保留供修正後重試。`)) return;
  mikanExtractCancelKey.value = jobKey;
  try {
    if (!csrfToken.value) throw new Error("安全權杖尚未載入，請重新整理後再試");
    const payload = await api("/api/v2/commands", {
      method: "POST",
      body: JSON.stringify({
        action: "mikan.cancel_extract",
        parameters: { job_key: jobKey },
      }),
      idempotencyKey: idempotencyKey(`mikan-cancel-extract-${jobKey}`),
    });
    const command = await waitForCommand(payload.command_id, { maxAttempts: 60, intervalMs: 1000 });
    if (command.status !== "completed") throw new Error(command.error || "Worker 未接受字幕提取中斷要求");
    showToast("已要求在下一個安全檢查點中斷；工作會保留供重試");
    await loadSummary();
    scheduleRefresh(true);
  } catch (err) {
    error.value = friendlyApiError("安全中斷字幕提取", err);
  } finally {
    mikanExtractCancelKey.value = "";
  }
}

async function retryMikanExtract(job) {
  const jobKey = String(job?.job_key || "");
  if (!jobKey || mikanExtractRetryKey.value) return;
  mikanExtractRetryKey.value = jobKey;
  try {
    if (!csrfToken.value) throw new Error("安全權杖尚未載入，請重新整理後再試");
    const payload = await api("/api/v2/commands", {
      method: "POST",
      body: JSON.stringify({
        action: "mikan.requeue_extract",
        parameters: { job_key: jobKey },
      }),
      idempotencyKey: idempotencyKey(`mikan-retry-extract-${jobKey}`),
    });
    const command = await waitForCommand(payload.command_id, { maxAttempts: 60, intervalMs: 1000 });
    if (command.status !== "completed") throw new Error(command.error || "Worker 未完成單筆重排");
    showToast("這筆字幕提取已重新排隊");
    await Promise.allSettled([loadSummary(), loadDownloads()]);
    scheduleRefresh(true);
  } catch (err) {
    error.value = friendlyApiError("重新排隊字幕提取", err);
  } finally {
    mikanExtractRetryKey.value = "";
  }
}

async function queueAction(name, path, requestedLines = "") {
  if (pendingQueuePaths.value.includes(path)) return;
  if (name === "retranslate" && !window.confirm("確定只保留日文轉錄並重新翻譯？目前的簡中／繁中 AI 字幕會先封存。")) return;
  if (name === "retranscribe" && !window.confirm("確定要重新轉錄？日文與中文 AI 字幕快取會先封存，再從影片音訊重新跑 Whisper。")) return;
  let lines = "";
  if (name === "retranslate-lines") {
    const entered = requestedLines || window.prompt("輸入要重翻的字幕行，例如：12,18,25-31");
    if (entered === null) return;
    lines = entered.trim();
    if (!/^\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*$/.test(lines)) {
      error.value = "字幕行格式錯誤，請使用 12,18,25-31 這類格式。";
      return;
    }
  }
  if (!csrfToken.value) {
    error.value = "安全權杖尚未載入，請重新整理後再試。";
    return;
  }
  const commandAction = {
    "priority": "ai.prioritize",
    "force-ai": "ai.force",
    "recover-running": "ai.recover",
    "retry": "ai.retry",
    "clear-failure": "ai.retry",
    "pause": "ai.pause",
    "skip": "ai.skip",
    "retranslate": "ai.retranslate",
    "retranslate-lines": "ai.retranslate_lines",
    "retranscribe": "ai.retranscribe",
  }[name];
  if (!commandAction) {
    error.value = "這個 AI 操作目前不支援，工作狀態沒有變更。";
    return;
  }
  pendingQueuePaths.value = [...pendingQueuePaths.value, path];
  try {
    const payload = await api("/api/v2/commands", {
      method: "POST",
      body: JSON.stringify({
        action: commandAction,
        target: path,
        parameters: lines ? { lines } : {},
      }),
      idempotencyKey: idempotencyKey(`ai-${name}`),
    });
    showToast(name === "retranslate-lines" ? `已送出字幕行重翻：${lines}` : "AI 操作已安全送交 Worker");
    pollQueueCommand(payload.command_id, path, name === "retranslate-lines" ? `指定行重翻完成：${lines}` : "AI 佇列已更新");
  } catch (err) {
    pendingQueuePaths.value = pendingQueuePaths.value.filter((item) => item !== path);
    error.value = friendlyApiError("更新 AI 佇列", err);
  }
}

function idempotencyKey(scope) {
  const random = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${scope}-${random}`;
}

async function waitForCommand(commandId, { maxAttempts = 450, intervalMs = 2000 } = {}) {
  let lastError = "";
  for (let attempt = 0; attempt < maxAttempts && !disposed; attempt += 1) {
    try {
      const command = await api(`/api/v2/commands/${encodeURIComponent(commandId)}`, { timeoutMs: 10000 });
      if (!["accepted", "queued", "running"].includes(command.status)) return command;
      lastError = "";
    } catch (err) {
      // A container recreate or short network interruption must not submit a
      // duplicate command. Continue polling this same command id.
      lastError = err instanceof Error ? err.message : String(err);
    }
    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
  }
  return {
    command_id: commandId,
    status: "timeout",
    error: lastError ? `暫時無法讀取操作狀態：${lastError}` : "操作仍在背景執行",
  };
}

async function pollQueueCommand(commandId, path, successMessage) {
  try {
    const command = await waitForCommand(commandId);
    if (command.status === "completed") {
      showToast(successMessage);
      await Promise.allSettled([loadSummary(), loadTasks({ supersede: true })]);
    } else {
      error.value = friendlyApiError("更新 AI 佇列", command.error || "Worker 未完成操作");
    }
  } catch (err) {
    error.value = friendlyApiError("確認 AI 操作結果", err);
  } finally {
    pendingQueuePaths.value = pendingQueuePaths.value.filter((item) => item !== path);
  }
}

function reviewCompletionMessage(reviewId, mode) {
  if (mode === "dismiss") return "這筆審核已從待辦移除";
  if (mode === "auto-rebuild") return "已找到唯一安全影片，請確認後重新提取字幕";
  if (mode === "rebuild") return "已核對作品與季度，請確認影片";
  const item = reviewDetails.value[reviewId]
    || (reviewPayload.value.items || []).find((candidate) => String(candidate.review_id) === String(reviewId))
    || {};
  if (item.kind === "target_ambiguity") return "配對已確認，字幕提取已排入處理";
  if (item.kind === "asr_quality") return "問題片段已排入重新轉錄";
  if (item.kind === "subtitle_quality") return "問題字幕已排入重新翻譯";
  return "審核操作已安全交給 Worker";
}

async function pollReviewCommand(commandId, reviewId, mode = "resolve") {
  const normalizedCommandId = String(commandId || "");
  if (!normalizedCommandId) {
    pendingReviewIds.value = pendingReviewIds.value.filter((value) => value !== reviewId);
    setReviewOperation(reviewId, {
      status: "failed",
      mode,
      error: "Worker 未回傳操作識別碼，WebUI 沒有重複送出。",
      finished_at: Date.now() / 1000,
    });
    return;
  }
  let consecutiveNetworkErrors = 0;
  let lastStatus = "";
  for (let attempt = 0; attempt < 7200 && !disposed; attempt += 1) {
    try {
      const command = await api(`/api/v2/commands/${encodeURIComponent(normalizedCommandId)}`, { timeoutMs: 10000 });
      const backendStatus = String(command.status || "unknown").toLowerCase();
      const commandStatus = ["accepted", "queued", "running", "completed", "failed"].includes(backendStatus)
        ? backendStatus
        : (backendStatus === "unknown" ? "reconnecting" : "failed");
      consecutiveNetworkErrors = 0;
      setReviewOperation(reviewId, {
        ...command,
        command_id: normalizedCommandId,
        mode,
        status: commandStatus,
        backend_status: backendStatus,
        error: String(command.error || (commandStatus === "failed" ? `Worker 回報操作狀態：${backendStatus}` : "")),
        last_confirmed_at: Date.now() / 1000,
      });
      if (["accepted", "queued", "running", "reconnecting"].includes(commandStatus)) {
        if (commandStatus !== lastStatus) {
          lastStatus = commandStatus;
          await loadReviewDetail(reviewId, { force: true });
        }
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        continue;
      }
      pendingReviewIds.value = pendingReviewIds.value.filter((value) => value !== reviewId);
      if (commandStatus === "completed") {
        showToast(reviewCompletionMessage(reviewId, mode));
        await Promise.allSettled([
          loadReviewDetail(reviewId, { force: true }),
          loadReviews(),
          loadSummary(),
          loadTasks({ supersede: true }),
        ]);
      } else {
        if (mode === "auto-rebuild") {
          const previous = reviewRecovery.value[reviewId] || {};
          reviewRecovery.value = {
            ...reviewRecovery.value,
            [reviewId]: {
              ...previous,
              autoFailed: true,
              loading: false,
              error: "自動核對仍無法唯一判斷，請使用進階選擇。",
            },
          };
          showToast("自動核對未找到唯一影片");
        } else {
          error.value = friendlyApiError("執行審核修復", command.error || "Worker 未完成操作");
        }
        await Promise.allSettled([
          loadReviewDetail(reviewId, { force: true }),
          loadReviews(),
          loadSummary(),
        ]);
      }
      return;
    } catch (err) {
      // A recreate or short network interruption must not duplicate the
      // command. Keep polling the same idempotent command id.
      consecutiveNetworkErrors += 1;
      if (consecutiveNetworkErrors >= 2) {
        setReviewOperation(reviewId, {
          command_id: normalizedCommandId,
          mode,
          status: "reconnecting",
          backend_status: lastStatus,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    }
    await new Promise((resolve) => window.setTimeout(resolve, 2000));
  }
  if (!disposed) {
    setReviewOperation(reviewId, {
      command_id: normalizedCommandId,
      mode,
      status: "unknown",
      error: "操作時間較長，WebUI 仍會保留操作 ID，且不會重複送出。",
    });
  }
}

function setReviewOperation(reviewId, patch) {
  const normalizedId = String(reviewId || "");
  if (!normalizedId) return;
  const previous = reviewOperations.value[normalizedId] || {};
  reviewOperations.value = {
    ...reviewOperations.value,
    [normalizedId]: {
      ...previous,
      ...patch,
      review_id: normalizedId,
      updated_at: Date.now() / 1000,
    },
  };
}

async function resolveReview({ review, body }) {
  const reviewId = String(review?.review_id || "");
  if (!reviewId || pendingReviewIds.value.includes(reviewId)) return;
  if (!csrfToken.value) {
    try {
      await loadBootstrap();
    } catch (err) {
      error.value = friendlyApiError("初始化安全操作", err);
      return;
    }
    if (!csrfToken.value) {
      error.value = "安全權杖尚未載入，請重新整理後再試。";
      return;
    }
  }
  const requestedAction = String(body?.action || "review.resolve");
  const requestedMode = requestedAction === "review.dismiss"
    ? "dismiss"
    : (requestedAction.includes("rebuild") ? "rebuild" : "resolve");
  pendingReviewIds.value = [...pendingReviewIds.value, reviewId];
  setReviewOperation(reviewId, {
    status: "submitting",
    action: requestedAction,
    mode: requestedMode,
    command_id: "",
    requested_at: Date.now() / 1000,
    error: "",
  });
  try {
    const reviewAttempt = review.kind === "target_ambiguity"
      ? String(body?.action || "target")
      : String(body?.action || "quality");
    const payload = await api(`/api/v2/review-items/${encodeURIComponent(reviewId)}/resolve`, {
      method: "POST",
      body: JSON.stringify(body),
      idempotencyKey: idempotencyKey(`review-${reviewId}-${reviewAttempt}`),
    });
    const commandId = String(payload.command_id || "");
    if (!commandId) throw new Error("Worker 未回傳操作識別碼，WebUI 沒有重複送出。");
    const autoRebuild = body?.action === "target.auto_rebuild_candidates";
    const rebuild = body?.action === "target.rebuild_candidates";
    const dismiss = body?.action === "review.dismiss";
    if (autoRebuild) {
      const previous = reviewRecovery.value[reviewId] || {};
      reviewRecovery.value = {
        ...reviewRecovery.value,
        [reviewId]: { ...previous, autoFailed: false, error: "" },
      };
    }
    setReviewOperation(reviewId, {
      status: "accepted",
      action: String(body?.action || reviewAttempt),
      mode: dismiss ? "dismiss" : (autoRebuild ? "auto-rebuild" : (rebuild ? "rebuild" : "resolve")),
      command_id: commandId,
      requested_at: Number(payload.requested_at || Date.now() / 1000),
      error: "",
    });
    showToast(dismiss
      ? "正在從待辦移除這筆審核"
      : (autoRebuild
        ? "正在安全核對作品、季度與實際影片"
        : (rebuild ? "正在核對作品、季度與實際影片" : "審核決定已安全送交 Worker")));
    pollReviewCommand(
      commandId,
      reviewId,
      dismiss ? "dismiss" : (autoRebuild ? "auto-rebuild" : (rebuild ? "rebuild" : "resolve")),
    );
  } catch (err) {
    pendingReviewIds.value = pendingReviewIds.value.filter((value) => value !== reviewId);
    setReviewOperation(reviewId, {
      status: "failed",
      error: err instanceof Error ? err.message : String(err),
      finished_at: Date.now() / 1000,
    });
    error.value = friendlyApiError("送出審核決定", err);
  }
}

async function resolveReviewBatch({ reviewIds, action = "safe.default" }) {
  const ids = [...new Set((reviewIds || []).map(String).filter(Boolean))]
    .filter((reviewId) => !pendingReviewIds.value.includes(reviewId));
  if (!ids.length) return;
  if (!csrfToken.value) {
    try {
      await loadBootstrap();
    } catch (err) {
      error.value = friendlyApiError("初始化安全操作", err);
      return;
    }
  }
  if (!csrfToken.value) {
    error.value = "安全權杖尚未載入，請重新整理後再試。";
    return;
  }
  pendingReviewIds.value = [...new Set([...pendingReviewIds.value, ...ids])];
  const submittedAt = Date.now() / 1000;
  ids.forEach((reviewId) => setReviewOperation(reviewId, {
    status: "submitting",
    action,
    mode: "resolve",
    command_id: "",
    requested_at: submittedAt,
    error: "",
  }));
  try {
    const payload = await api("/api/v2/review-items/batch-resolve", {
      method: "POST",
      body: JSON.stringify({ review_ids: ids, action }),
      idempotencyKey: idempotencyKey(`review-batch-${action}`),
      timeoutMs: 30000,
    });
    const queued = payload.queued || [];
    const queuedIds = new Set(queued.map((item) => String(item.review_id || "")));
    pendingReviewIds.value = pendingReviewIds.value.filter((value) => !ids.includes(value) || queuedIds.has(value));
    if (queued.length) {
      showToast(`已安全送出 ${queued.length} 個審核項目`);
      for (const item of queued) {
        const queuedReviewId = String(item.review_id || "");
        const queuedCommandId = String(item.command_id || "");
        if (!queuedReviewId || !queuedCommandId) {
          pendingReviewIds.value = pendingReviewIds.value.filter((value) => value !== queuedReviewId);
          setReviewOperation(queuedReviewId, {
            status: "failed",
            action,
            mode: "resolve",
            error: "Worker 未回傳完整操作識別資料，WebUI 沒有重複送出。",
            finished_at: Date.now() / 1000,
          });
          continue;
        }
        setReviewOperation(queuedReviewId, {
          status: "accepted",
          action,
          mode: "resolve",
          command_id: queuedCommandId,
          requested_at: Number(item.requested_at || submittedAt),
          error: "",
        });
        pollReviewCommand(queuedCommandId, queuedReviewId, "resolve");
      }
    }
    if (payload.rejected_count) {
      const firstReason = String(payload.rejected?.[0]?.reason || "需要人工選擇");
      error.value = `${payload.rejected_count} 個項目未執行：${firstReason}`;
      for (const item of payload.rejected || []) {
        setReviewOperation(String(item.review_id || ""), {
          status: "failed",
          action,
          mode: "resolve",
          error: String(item.reason || firstReason),
          finished_at: Date.now() / 1000,
        });
      }
    }
    await loadReviews({ preserveOrder: true, supersede: true });
  } catch (err) {
    pendingReviewIds.value = pendingReviewIds.value.filter((value) => !ids.includes(value));
    ids.forEach((reviewId) => setReviewOperation(reviewId, {
      status: "failed",
      action,
      mode: "resolve",
      error: err instanceof Error ? err.message : String(err),
      finished_at: Date.now() / 1000,
    }));
    error.value = friendlyApiError("批次處理人工審核", err);
  }
}

function setTaskQuery(query) {
  const nextQuery = {
    ...taskQuery.value,
    ...query,
    page: Number(query.page || 1),
  };
  if (JSON.stringify(nextQuery) === JSON.stringify(taskQuery.value)) return;
  taskQuery.value = nextQuery;
  loadTasks({ supersede: true });
}

function setTaskPage(page) {
  taskQuery.value = { ...taskQuery.value, page };
  loadTasks({ supersede: true });
}

function setMikanPage(page) {
  mikanPage.value = Math.max(1, Number(page || 1));
  loadDownloads({ supersede: true });
}

function setMikanQuery(query) {
  const nextStatus = query.status || "";
  const nextSearch = query.search || "";
  if (nextStatus === mikanStatusFilter.value && nextSearch === mikanSearch.value && mikanPage.value === 1) return;
  mikanStatusFilter.value = nextStatus;
  mikanSearch.value = nextSearch;
  mikanPage.value = 1;
  loadDownloads({ supersede: true });
}

function openReviewWork({ panel, target } = {}) {
  if (panel === "queue") {
    navigate("queue", { taskStatus: "", taskSearch: fileName(target || "") });
    return;
  }
  navigate("downloads", { mikanStatus: "" });
}

function navigate(panel, options = {}) {
  if (!PANEL_KEYS.has(panel)) return;
  mobileMoreOpen.value = false;
  if (options.taskStatus !== undefined) {
    taskQuery.value = { ...taskQuery.value, mode: "active", status: options.taskStatus, page: 1 };
  }
  if (options.taskSearch !== undefined) {
    taskQuery.value = { ...taskQuery.value, mode: "active", search: options.taskSearch, page: 1 };
  }
  if (options.mikanStatus !== undefined) {
    mikanStatusFilter.value = options.mikanStatus;
    mikanSearch.value = "";
    mikanPage.value = 1;
  }
  activePanel.value = panel;
}

function handleHashChange() {
  const panel = window.location.hash.replace(/^#\/?/, "");
  if (PANEL_KEYS.has(panel) && panel !== activePanel.value) activePanel.value = panel;
}

function handleVisibilityChange() {
  if (!document.hidden) scheduleRefresh(true);
}

function connectStream() {
  if (!window.EventSource) {
    streamState.value = "unsupported";
    return;
  }
  stream = new EventSource("/api/v2/stream");
  stream.onopen = () => { streamState.value = "live"; };
  stream.addEventListener("revision", (event) => {
    try {
      const changed = JSON.parse(event.data || "{}").changed || [];
      if (changed.includes("reviews") && activePanel.value === "reviews") loadReviews();
      const actionable = changed.filter((entity) => entity && entity !== "heartbeat" && entity !== "reviews");
      if (actionable.length) scheduleRefresh(false, actionable);
    } catch {
      // A malformed incremental event falls back to the normal refresh.
      scheduleRefresh(false);
    }
  });
  // Retain the v1 event listener during the compatibility window.
  stream.addEventListener("state", () => scheduleRefresh(false));
  stream.onerror = () => {
    streamState.value = "retrying";
    // EventSource reconnects automatically. The 30-second poll remains as a
    // fallback while the connection is recovering.
  };
}

watch(activePanel, (panel) => {
  error.value = "";
  window.localStorage.setItem("subtitle-workbench-panel", panel);
  const targetHash = `#${panel}`;
  if (window.location.hash !== targetHash) window.history.pushState(null, "", targetHash);
  if (panel === "queue") loadTasks({ supersede: true });
  if (panel === "downloads") loadDownloads({ supersede: true });
  if (panel === "reviews") loadReviews();
  if (panel === "series") loadSeries();
  if (panel === "events") loadEvents().catch((err) => {
    error.value = friendlyApiError("讀取處理紀錄", err);
  });
});

onMounted(() => {
  disposed = false;
  if (!window.location.hash) window.history.replaceState(null, "", `#${activePanel.value}`);
  loadBootstrap().catch((err) => {
    error.value = friendlyApiError("初始化安全操作", err);
  });
  loadReviews();
  refresh({ forceAll: true });
  connectStream();
  timer = window.setInterval(() => {
    if (!document.hidden) refresh({ forceAll: false });
  }, 30000);
  clockTimer = window.setInterval(() => { nowSeconds.value = Date.now() / 1000; }, 5000);
  window.addEventListener("hashchange", handleHashChange);
  window.addEventListener("popstate", handleHashChange);
  document.addEventListener("visibilitychange", handleVisibilityChange);
});

onUnmounted(() => {
  disposed = true;
  if (timer) window.clearInterval(timer);
  if (clockTimer) window.clearInterval(clockTimer);
  if (refreshTimer) window.clearTimeout(refreshTimer);
  if (toastTimer) window.clearTimeout(toastTimer);
  stream?.close();
  downloadController?.abort();
  taskController?.abort();
  reviewController?.abort();
  window.removeEventListener("hashchange", handleHashChange);
  window.removeEventListener("popstate", handleHashChange);
  document.removeEventListener("visibilitychange", handleVisibilityChange);
});
</script>

<template>
  <div class="app-shell">
    <header class="site-header">
      <button type="button" class="brand" aria-label="回到總覽" @click="navigate('overview')">
        <span class="brand-mark">字</span>
        <span>
          <strong>字幕工作台</strong>
        </span>
      </button>

      <nav class="main-nav desktop-nav" aria-label="主選單">
        <button
          v-for="page in pages"
          :key="page.key"
          type="button"
          :class="{ active: activePanel === page.key }"
          @click="navigate(page.key)"
        >
          <span class="nav-icon" aria-hidden="true">{{ page.icon }}</span>
          <span class="nav-label">{{ page.label }}</span>
        </button>
      </nav>

      <div class="header-actions">
        <span :class="['live-indicator', streamState]"><i></i>{{ streamLabel }}</span>
        <span v-if="['danger', 'warn'].includes(healthState.tone)" :class="['system-pill', healthState.tone]">{{ healthState.label }}</span>
        <button type="button" class="refresh-button" :disabled="refreshing" aria-label="立即更新所有資料" @click="refresh({ forceAll: true })">
          <span aria-hidden="true">↻</span><span class="refresh-label">{{ refreshing ? "更新中" : "更新" }}</span>
        </button>
      </div>
    </header>

    <nav class="mobile-nav" aria-label="手機主要選單">
      <button
        v-for="page in mobilePrimaryPages"
        :key="page.key"
        type="button"
        :class="{ active: activePanel === page.key }"
        @click="navigate(page.key)"
      >
        <span class="nav-icon" aria-hidden="true">{{ page.icon }}</span>
        <span class="nav-label">{{ page.label }}</span>
        <span v-if="page.count" class="nav-count">{{ formatCompactCount(page.count) }}</span>
      </button>
      <button type="button" :class="{ active: mobileMoreActive || mobileMoreOpen }" @click="mobileMoreOpen = !mobileMoreOpen">
        <span class="nav-icon" aria-hidden="true">•••</span>
        <span class="nav-label">更多</span>
      </button>
    </nav>
    <aside v-if="mobileMoreOpen" class="mobile-more-menu" aria-label="更多頁面">
      <button v-for="page in mobileMorePages" :key="page.key" type="button" @click="navigate(page.key)">
        <span>{{ page.icon }} {{ page.label }}</span>
        <strong v-if="page.count">{{ formatCompactCount(page.count) }}</strong>
      </button>
    </aside>

    <aside v-if="actionBusy" class="global-operation" role="status" aria-live="polite">
      <span class="operation-spinner" aria-hidden="true"></span>
      <div>
        <strong>{{ actionLabel(action.action) }}</strong>
        <small>背景執行中<span v-if="actionElapsed"> · 已執行 {{ actionElapsed }}</span></small>
      </div>
      <button type="button" @click="navigate('actions')">查看進度</button>
    </aside>

    <main class="workspace">
      <div v-if="toast" class="toast" role="status"><span>{{ toast }}</span><button type="button" aria-label="關閉通知" @click="toast = ''">×</button></div>
      <div v-if="error" class="alert" role="alert"><span>{{ error }}</span><button type="button" aria-label="關閉錯誤" @click="error = ''">×</button></div>
      <div v-if="loading" class="loading loading-skeleton"><i></i><span>正在讀取字幕系統狀態...</span></div>

      <template v-else>
        <section v-if="activePanel === 'overview'" class="overview-page">
          <section v-if="['danger', 'warn'].includes(healthState.tone)" :class="['overview-status-bar', healthState.tone]" aria-live="polite">
            <div>
              <strong>{{ healthState.label }}</strong>
              <span>{{ healthState.detail }}</span>
            </div>
            <div class="overview-status-actions">
              <small class="freshness"><i></i>{{ streamLabel }} · {{ freshnessLabel }}</small>
              <button
                v-if="aiSchedulerNeedsAttention"
                type="button"
                class="primary"
                :disabled="actionBusy"
                @click="runAction('ai-scheduler-retry')"
              >
                {{ actionBusy ? "正在送出..." : "立即重試 AI 排程" }}
              </button>
              <button
                v-if="workerNeedsRuntimeHelp"
                type="button"
                class="primary"
                :disabled="workerRuntimeLogLoading"
                @click="loadWorkerRuntimeLog"
              >
                {{ workerRuntimeLogLoading ? "正在讀取..." : "查看 Worker 啟動錯誤" }}
              </button>
            </div>
          </section>

          <section v-if="workerRuntimeLog" class="worker-runtime-log" aria-live="polite">
            <div>
              <span class="section-label">Worker 啟動診斷</span>
              <strong>{{ workerRuntimeLog.ok ? "最近的容器輸出" : "暫時無法讀取容器輸出" }}</strong>
              <button type="button" aria-label="關閉 Worker 啟動診斷" @click="workerRuntimeLog = null">×</button>
            </div>
            <p v-if="workerRuntimeLog.error">{{ workerRuntimeLog.error }}</p>
            <pre v-else>{{ (workerRuntimeLog.lines || []).join('\n') || "目前沒有容器輸出。" }}</pre>
          </section>

          <article v-if="activeTask" class="current-task-card overview-current-task">
            <span class="pulse-dot"></span>
            <div class="current-task-copy">
              <span class="section-label">目前 AI 任務</span>
              <h2>{{ activeTask.file_name || fileName(activeTask.path) }}</h2>
              <div :class="['task-progress', { indeterminate: activeTaskProgress === null }]">
                <div class="progress-track">
                  <span v-if="activeTaskProgress !== null" :style="{ width: `${activeTaskProgress}%` }"></span>
                  <span v-else class="indeterminate-bar"></span>
                </div>
              </div>
            </div>
            <div class="current-task-time">
              <strong>{{ activeTask.stage || "running" }}</strong>
              <small v-if="activeTaskElapsed">已跑 {{ activeTaskElapsed }}</small>
              <small v-if="activeTask.heartbeat_age_seconds !== undefined">{{ formatDuration(activeTask.heartbeat_age_seconds) }}前回報</small>
            </div>
          </article>

          <article v-else-if="queueCounts.queued" class="queue-standby-card overview-current-task">
            <span class="standby-mark" aria-hidden="true">AI</span>
            <div>
              <strong>AI 佇列等待 Worker</strong>
              <p>{{ aiStandbyMessage }}</p>
            </div>
            <button type="button" @click="navigate('queue')">查看佇列</button>
          </article>

          <section class="overview-stats overview-stats-primary" aria-label="核心狀態">
            <article v-for="item in overviewPrimaryStats" :key="item.label" :class="['metric-card', item.tone]">
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
              <small>{{ item.detail }}</small>
            </article>
          </section>

          <section v-if="attentionItems.length" class="overview-exception-strip" aria-label="需要處理">
            <div class="overview-exception-label">
              <strong>例外</strong>
            </div>
            <button
              v-for="item in attentionItems"
              :key="item.label"
              type="button"
              @click="navigate(item.panel, item)"
            >
              <span>{{ item.label }}</span><b>{{ item.value }}</b><i aria-hidden="true">›</i>
            </button>
          </section>

          <details
            class="overview-detail-drawer runtime-drawer"
            :open="aiPaused || ['running', 'paused'].includes(aiFailedRetrySweep.state) || Boolean(redownloadActive)"
          >
            <summary>
              <span>
                <strong>進階資訊</strong>
                <small>資源、SLO 與診斷</small>
              </span>
              <b v-if="aiPaused">AI 已暫停</b>
              <b v-else-if="['running', 'paused'].includes(aiFailedRetrySweep.state)">{{ aiSweepStateLabel }}</b>
              <b v-else-if="recommendations.length">{{ recommendations.length }} 項建議</b>
              <b v-else>展開</b>
            </summary>
            <section class="overview-resource-strip" aria-label="資源使用">
              <strong>資源</strong>
              <article v-for="item in overviewResourceStats" :key="item.label" :title="item.detail">
                <span>{{ item.label }}</span><b>{{ item.value }}</b>
              </article>
            </section>
            <section class="overview-stats overview-stats-detail" aria-label="資源與執行細節">
              <article v-for="item in overviewDetailStats" :key="item.label" :class="['metric-card', item.tone]">
                <span>{{ item.label }}</span>
                <strong>{{ item.value }}</strong>
                <small>{{ item.detail }}</small>
              </article>
            </section>
            <section class="runtime-facts" aria-label="runtime version and health">
              <article v-for="[label, value] in runtimeFacts" :key="label">
                <span>{{ label }}</span>
                <strong>{{ value }}</strong>
              </article>
            </section>
            <button type="button" class="copy-diagnostics-button" @click="copyDiagnostics">複製診斷摘要</button>
            <section v-if="recommendations.length" class="recommendation-panel" aria-label="系統最佳化建議">
              <button
                v-for="item in recommendations"
                :key="item.key"
                type="button"
                :class="['recommendation-card', item.tone || 'muted']"
                :disabled="Boolean(item.action) && actionBusy"
                @click="handleRecommendation(item)"
              >
                <span>{{ item.title }}</span>
                <small>{{ item.detail }}</small>
                <i aria-hidden="true">{{ item.action ? "執行" : "查看" }} →</i>
              </button>
            </section>
            <section class="control-deck" aria-label="AI 與長任務控制">
            <article :class="['control-card', 'ai-control-card', { paused: aiPaused }]">
              <div>
                <span class="section-label">AI 佇列控制</span>
                <h2>{{ aiPaused ? "已暫停" : "自動執行中" }}</h2>
                <p v-if="aiPaused">不會啟動下一部影片；Mikan 與目前執行中的影片不受影響。</p>
                <p v-else>可安全暫停新工作，不會中斷目前正在產生的字幕。</p>
              </div>
              <button
                type="button"
                :class="{ primary: aiPaused }"
                :disabled="aiControlBusy"
                @click="setAiQueuePaused(!aiPaused)"
              >
                {{ aiControlBusy ? "更新中…" : aiPaused ? "恢復 AI" : activeTask ? "完成目前影片後暫停" : "暫停 AI" }}
              </button>
            </article>

            <article class="control-card throughput-card">
              <div>
                <span class="section-label">AI 處理速度</span>
                <h2>{{ aiRateLabel }}</h2>
                <p>剩餘 {{ etaSummary.remaining ?? queueCounts.queued ?? 0 }} 部 · 預估清空 {{ aiEtaLabel }}</p>
                <small>{{ aiEtaMethodLabel }}</small>
              </div>
              <div class="throughput-samples" aria-label="完成樣本">
                <span>1 小時 <b>{{ etaSummary.completed_last_1h || 0 }}</b></span>
                <span>6 小時 <b>{{ etaSummary.completed_last_6h || 0 }}</b></span>
                <span>24 小時 <b>{{ etaSummary.completed_last_24h || 0 }}</b></span>
              </div>
            </article>

            <article :class="['control-card', 'safe-sweep-card', aiFailedRetrySweep.state]">
              <div>
                <span class="section-label">AI 安全自動修復</span>
                <h2>{{ aiSweepStateLabel }}</h2>
                <p>
                  每次只處理 1 筆可證明為暫時性失敗的工作，不重設嘗試次數；
                  品質或配對歧義會轉交人工審核，首個失敗即停。
                </p>
                <small v-if="aiFailedRetrySweep.campaign_id">
                  本輪 {{ aiSweepCounters.processed || 0 }} / {{ aiSweepCounters.selected || 0 }} ·
                  成功 {{ aiSweepCounters.succeeded || 0 }} ·
                  轉審核 {{ aiSweepCounters.blocked_review || 0 }}
                </small>
                <small v-if="aiSweepPreview">
                  最近預覽：{{ aiSweepPreview.counters?.eligible || 0 }} 筆符合、
                  {{ aiSweepPreview.counters?.unsupported || 0 }} 筆不自動處理。
                </small>
              </div>
              <div class="safe-sweep-actions">
                <button type="button" :disabled="aiSweepBusy" @click="runAiFailedRetrySweep('preview')">
                  {{ aiSweepBusy ? "處理中…" : "唯讀預覽" }}
                </button>
                <button
                  v-if="!['running', 'paused'].includes(aiFailedRetrySweep.state)"
                  type="button"
                  class="primary"
                  :disabled="aiSweepBusy"
                  @click="runAiFailedRetrySweep('start')"
                >
                  安全處理下一筆
                </button>
                <button
                  v-else
                  type="button"
                  :class="{ primary: aiFailedRetrySweep.state === 'paused' }"
                  :disabled="aiSweepBusy"
                  @click="runAiFailedRetrySweep(aiFailedRetrySweep.state === 'paused' ? 'resume' : 'pause')"
                >
                  {{ aiFailedRetrySweep.state === "paused" ? "確認後恢復" : "暫停" }}
                </button>
              </div>
            </article>

            <article v-if="failureSummary.buckets?.length" class="control-card failure-root-card">
              <div>
                <span class="section-label">AI 失敗根因</span>
                <h2>目前失敗待重試 {{ failureSummary.current_total || 0 }} 部</h2>
                <p>
                  近 {{ failureSummary.window_days || 7 }} 天曾遇到失敗 {{ failureSummary.affected_videos_7d || 0 }} 部：
                  {{ failureOutcomes.queued || 0 }} 已重新排隊、{{ failureOutcomes.failed_retry || 0 }} 仍失敗、{{ failureOutcomes.done || 0 }} 已完成<template v-if="failureOutcomes.missing">、{{ failureOutcomes.missing }} 已不在佇列</template><template v-if="failureOutcomes.other">、{{ failureOutcomes.other }} 為其他狀態</template>。
                </p>
              </div>
              <div class="failure-root-list">
                <button
                  v-for="bucket in failureSummary.buckets.slice(0, 4)"
                  :key="bucket.key"
                  type="button"
                  @click="navigate('queue', { taskStatus: 'failed_retry' })"
                >
                  <span>{{ bucket.label }}</span>
                  <b>{{ bucket.current }} 目前</b>
                  <small>近 7 天曾遇到 {{ bucket.affected_videos_7d }}</small>
                </button>
              </div>
            </article>

            <article v-if="redownloadActive" class="control-card redownload-control-card">
              <div>
                <span class="section-label">Mikan 全量重抓</span>
                <h2>{{ redownloadActive.stage_label || redownloadActive.stage || "執行中" }}</h2>
                <p>
                  {{ redownloadActive.current || 0 }} / {{ redownloadActive.total || "?" }} ·
                  已排入 {{ redownloadActive.queued || 0 }} 筆
                </p>
                <div :class="['task-progress', { indeterminate: redownloadProgress === null }]">
                  <div class="progress-track">
                    <span v-if="redownloadProgress !== null" :style="{ width: `${redownloadProgress}%` }"></span>
                    <span v-else class="indeterminate-bar"></span>
                  </div>
                </div>
              </div>
              <button
                type="button"
                class="danger"
                :disabled="mikanCancelBusy || redownloadCancelRequested"
                @click="cancelMikanRedownload"
              >
                {{ redownloadCancelRequested ? "停止要求已送出" : mikanCancelBusy ? "送出中…" : "安全停止" }}
              </button>
            </article>
            </section>
          </details>

          <section v-if="failedHealthChecks.length" class="home-grid">
            <article class="home-card recent-card">
              <h2>健康檢查</h2>
              <div class="recent-list">
                <article v-for="check in failedHealthChecks" :key="check.name">
                  <span :class="['event-mark', check.ok ? 'success' : check.severity === 'warn' ? 'warn' : 'danger']">
                    {{ check.ok ? "✓" : "!" }}
                  </span>
                  <div>
                    <strong>{{ check.name }}</strong>
                    <small>{{ check.detail }}</small>
                  </div>
                </article>
              </div>
            </article>
          </section>

        </section>

        <MikanDownloads
          v-else-if="activePanel === 'downloads'"
          :downloads="downloads"
          :state-db="mikanStateDb"
          :operation="mikanOperation"
          :busy="actionBusy"
          :loading="downloadsLoading"
          :requested-page="mikanPage"
          :query="{ status: mikanStatusFilter, search: mikanSearch }"
          :now-seconds="nowSeconds"
          :canceling-job-key="mikanExtractCancelKey"
          :retrying-job-key="mikanExtractRetryKey"
          @page="setMikanPage"
          @query="setMikanQuery"
          @process-completed="runAction('mikan-process-completed')"
          @cancel-extract="cancelMikanExtract"
          @retry-extract="retryMikanExtract"
        />

        <TaskDashboard
          v-else-if="activePanel === 'queue'"
          :payload="taskPayload"
          :tasks="tasks"
          :completed-tasks="completedTasks"
          :counts="queueCounts"
          :busy="actionBusy"
          :ai-control="aiControl"
          :control-busy="aiControlBusy"
          :query="taskQuery"
          :pending-paths="pendingQueuePaths"
          :diagnostics="aiDiagnostics"
          :diagnostics-path="aiDiagnosticsPath"
          :diagnostics-loading="aiDiagnosticsLoading"
          :retry-sweep="aiFailedRetrySweep"
          @safe-retry="runAiFailedRetrySweep('start')"
          @ai-control="setAiQueuePaused"
          @query="setTaskQuery"
          @page="setTaskPage"
          @queue-action="queueAction"
          @diagnostics="loadAiDiagnostics"
        />

        <ReviewCenter
          v-else-if="activePanel === 'reviews'"
          :payload="reviewPayload"
          :loading="reviewsLoading"
          :busy-ids="pendingReviewIds"
          :operations-by-review="reviewOperations"
          :now-seconds="nowSeconds"
          :recovery-by-review="reviewRecovery"
          :query="reviewQuery"
          :details-by-review="reviewDetails"
          :detail-loading-ids="reviewDetailLoadingIds"
          @refresh="() => loadReviews({ preserveOrder: true, supersede: true })"
          @resolve="resolveReview"
          @batch-resolve="resolveReviewBatch"
          @query="setReviewQuery"
          @load-more="loadMoreReviews"
          @select="loadReviewDetail"
          @search-series="searchReviewSeries"
          @open-work="openReviewWork"
        />

        <SeriesMetadata
          v-else-if="activePanel === 'series'"
          :payload="seriesPayload"
          :detail="seriesDetail"
          :loading="seriesLoading"
          :detail-loading="seriesDetailLoading"
          :busy="actionBusy"
          :operation="seriesOperation"
          :query="seriesQuery"
          @query="setSeriesQuery"
          @page="(page) => setSeriesQuery({ page })"
          @select="loadSeriesDetail"
          @lock="setSeriesLock"
          @match="setSeriesMatch"
          @glossary-upsert="upsertSeriesGlossary"
          @glossary-delete="deleteSeriesGlossary"
          @sync="runAction('series-sync')"
        />

        <ActionsPanel
          v-else-if="activePanel === 'actions'"
          :action="action"
          :busy="actionBusy"
          :database-health="status?.database_health"
          @run-action="runAction"
        />

        <EventsTimeline v-else :events="events" />
      </template>
    </main>
  </div>
</template>
