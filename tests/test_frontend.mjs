import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  actionLabel,
  compatibleReviewBatchItems,
  displayStatus,
  downloadProgress,
  eventMark,
  eventNeedsAttention,
  eventSeverity,
  eventSucceeded,
  friendlyApiError,
  friendlyTaskMessage,
  friendlyTechnicalMessage,
  fileName,
  formatCompactCount,
  formatBytes,
  formatDuration,
  formatPercent,
  groupReviewItems,
  mikanSourceLabel,
  mikanStatusLabel,
  mikanSubtitle,
  mikanSubtitleStateLabel,
  mikanAttentionSummary,
  mikanPipelineSummary,
  mikanRowKey,
  newestCompletedFirst,
  nextActionLabel,
  nodeLabel,
  parentPath,
  problemDescription,
  problemRecommendedAction,
  qualityIssueLabel,
  reviewOperationIsActive,
  reviewOperationLabel,
  statusTone,
  repairDisplayText,
  subtitleQualityLabel,
  subtitleQualitySummary,
  subtitleQualityTone,
  taskProgress,
} from "../src/dashboard.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appVue = fs.readFileSync(path.join(root, "src", "App.vue"), "utf8");
const taskDashboard = fs.readFileSync(path.join(root, "src", "components", "TaskDashboard.vue"), "utf8");
const mikanDownloads = fs.readFileSync(path.join(root, "src", "components", "MikanDownloads.vue"), "utf8");
const actionsPanel = fs.readFileSync(path.join(root, "src", "components", "ActionsPanel.vue"), "utf8");
const seriesMetadata = fs.readFileSync(path.join(root, "src", "components", "SeriesMetadata.vue"), "utf8");
const reviewCenter = fs.readFileSync(path.join(root, "src", "components", "ReviewCenter.vue"), "utf8");
const styles = fs.readFileSync(path.join(root, "src", "styles.css"), "utf8");

assert.equal(fileName("/anime/Series/Season 1/Episode 01.mkv"), "Episode 01.mkv");
assert.equal(parentPath("/anime/Series/Season 1/Episode 01.mkv"), "/anime/Series/Season 1");
const mixedBatchItems = [
  ...Array.from({ length: 3 }, (_, index) => ({
    review_id: `translate-${index}`,
    state: "needs_action",
    batch_eligible: true,
    recommended_action: { action: "ai.retranslate_lines" },
  })),
  {
    review_id: "retranscribe-1",
    state: "needs_action",
    batch_eligible: true,
    recommended_action: { action: "ai.retranscribe" },
  },
];
assert.deepEqual(
  compatibleReviewBatchItems(mixedBatchItems).map((item) => item.review_id),
  ["translate-0", "translate-1", "translate-2"],
);
assert.deepEqual(
  compatibleReviewBatchItems(mixedBatchItems, "ai.retranscribe").map((item) => item.review_id),
  ["retranscribe-1"],
);
assert.equal(statusTone("Failed"), "danger");
assert.equal(statusTone("Running"), "running");
assert.equal(statusTone("extracting_subtitles"), "running");
assert.equal(statusTone("completed_waiting_extract"), "warn");
assert.equal(statusTone("terminal_failed"), "danger");
assert.equal(statusTone("replaced"), "muted");
assert.equal(statusTone("failed_candidate"), "muted");
assert.equal(statusTone("danger"), "danger");
assert.equal(statusTone("warn"), "warn");
assert.equal(eventSeverity({ source: "mikan", status: "created", severity: "queued" }), "queued");
assert.equal(eventMark({ source: "mikan", status: "created", severity: "queued" }), "•");
assert.equal(eventNeedsAttention({ status: "failed" }), true);
assert.equal(eventNeedsAttention({ status: "queued" }), false);
assert.equal(eventSucceeded({ status: "ok" }), true);
assert.equal(displayStatus("Queued"), "等待中");
assert.equal(nodeLabel("translate"), "翻譯");
assert.equal(formatPercent(52.4), "52%");
assert.equal(formatBytes(1048576), "1.0 MB");
assert.equal(formatDuration(3661), "1 小時 1 分");
assert.equal(formatCompactCount(6320), "6.3k");
assert.equal(reviewOperationLabel("submitting"), "送出中");
assert.equal(reviewOperationLabel("accepted"), "等待 Worker");
assert.equal(reviewOperationLabel("running"), "正在處理");
assert.equal(reviewOperationLabel("reconnecting"), "確認狀態中");
assert.equal(reviewOperationLabel("completed"), "已完成");
assert.equal(reviewOperationLabel("failed"), "處理失敗");
assert.equal(reviewOperationIsActive("running"), true);
assert.equal(reviewOperationIsActive("unknown"), true);
assert.equal(reviewOperationIsActive("completed"), false);
assert.equal(qualityIssueLabel("prompt_leak"), "翻譯混入模型指令");
assert.equal(qualityIssueLabel("asr_prompt_echo"), "轉錄提示混入日文字幕");
assert.equal(qualityIssueLabel("leading_gap"), "片頭開場可能漏轉");
assert.equal(qualityIssueLabel({ code: "residual_japanese_kana", message: "raw detail" }), "中文字幕仍有未翻譯日文");
assert.equal(qualityIssueLabel({ message: "Unexpected translated index: 1" }), "翻譯編號不正確");
assert.equal(
  friendlyTaskMessage({ status: "Running", message: "Translating batch 28/61" }),
  "正在翻譯字幕（28 / 61）",
);
assert.equal(
  friendlyTaskMessage({ status: "Failed", message: "Traceback: private path", problem: { description: "翻譯未完成，可安全重試。" } }),
  "翻譯未完成，可安全重試。",
);
assert.equal(
  friendlyTechnicalMessage("sqlite3.OperationalError: database is locked at /work/scanner_state.sqlite3"),
  "狀態資料庫暫時忙碌，系統會自動重試。",
);
assert.equal(
  friendlyTechnicalMessage("Traceback: https://secret.example/private"),
  "系統已保留診斷資料，可在進階資訊中查看識別碼。",
);
const originalConsoleError = console.error;
console.error = () => {};
try {
  assert.equal(
    friendlyApiError("重新提取字幕", { status: 409, message: "database busy" }),
    "重新提取字幕暫時忙碌，請稍後再試；原有狀態不會遺失。",
  );
  assert.equal(
    friendlyApiError("讀取審核詳情", { status: 404, message: "Not Found" }),
    "讀取審核詳情失敗：Worker 與 WebUI 版本不一致，請用「安全更新整個 Stack」同時更新兩個服務。",
  );
} finally {
  console.error = originalConsoleError;
}
const friendlyProblem = {
  problem: {
    description: "找到多個可能目標，系統已停止自動配對。",
    recommended_action: "選擇正確作品與季度。",
  },
};
assert.equal(problemDescription(friendlyProblem), "找到多個可能目標，系統已停止自動配對。");
assert.equal(problemRecommendedAction(friendlyProblem), "選擇正確作品與季度。");
const groupedReviews = groupReviewItems([
  { review_id: "review-old", kind: "target_ambiguity", updated_at: 1, diagnosis: { torrent_hash: "a".repeat(40) }, candidates: [] },
  { review_id: "review-new", kind: "target_ambiguity", updated_at: 2, diagnosis: { torrent_hash: "a".repeat(40) }, candidates: [{ path: "/anime/A/Season 1/A - S01E01.mkv" }] },
  { review_id: "review-quality", kind: "subtitle_quality", updated_at: 3, diagnosis: {}, candidates: [] },
]);
assert.equal(groupedReviews.length, 2);
assert.equal(groupedReviews[0].review_id, "review-new");
assert.equal(groupedReviews[0].duplicate_count, 2);
assert.equal(groupedReviews[1].review_id, "review-quality");
assert.equal(taskProgress({ status: "Running", node_id: "translate" }), null);
assert.equal(taskProgress({ status: "Running", node_id: "translate", message: "Translating batch 28/61" }), 28 / 61 * 100);
assert.equal(taskProgress({ status: "Success", node_id: "output" }), 100);
assert.equal(repairDisplayText("ä¸­æ–‡"), "中文");
assert.equal(downloadProgress({ progress: 0.42 }), 42);
assert.equal(mikanSourceLabel("nyaa"), "Nyaa");
assert.equal(mikanSourceLabel("animegarden:dmhy"), "Anime Garden");
assert.equal(mikanStatusLabel("extract_failed"), "字幕提取失敗");
assert.equal(mikanStatusLabel("terminal_failed"), "提取終止");
assert.equal(mikanStatusLabel("failed"), "提取失敗，可重試");
assert.equal(mikanStatusLabel("extracting_subtitles"), "提取字幕中");
assert.equal(mikanSubtitleStateLabel("official_ready"), "字幕已匯入");
assert.equal(mikanSubtitleStateLabel("official_extract_failed_replace"), "提取失敗，尋找替補");
assert.equal(nextActionLabel("extracting_subtitles"), "正在提取字幕");
assert.equal(nextActionLabel("extract_subtitles"), "提取字幕");
assert.equal(actionLabel("ai-refresh-queue-state"), "重新整理 AI 分類");
assert.equal(actionLabel("retry-all-failures"), "批次重試失敗");
assert.equal(actionLabel("ai-safe-retry-sweep"), "安全處理下一筆 AI 失敗");
assert.equal(actionLabel("series-sync"), "同步作品資訊");
assert.equal(actionLabel("database-maintenance"), "最佳化資料庫");
assert.equal(mikanSubtitle({ episodes: [1, 12] }), "第 01, 12 集");
assert.equal(mikanSubtitle({ episode: 3 }), "第 03 集");
assert.deepEqual(
  mikanAttentionSummary({
    mikanCounts: { extract_failed: 157, target_missing: 1 },
    extractCounts: { failed: 157, replaced: 11, terminal_failed: 2 },
    stateDb: { stalled: 4, zero_speed_downloading: 4 },
    queueCounts: { failed_retry: 3 },
  }),
  {
    total: 6,
    retryableTotal: 160,
    terminalExtractFailures: 2,
    targetMissing: 1,
    blockedDownloads: 4,
    aiRetryFailures: 3,
    retryableExtractFailures: 157,
    replacedExtractFailures: 11,
    sourceExtractFailures: 157,
    autoReplacementFailures: 157,
    replacementHistory: 11,
    stalled: 4,
    zeroSpeedDownloading: 4,
  },
);
assert.equal(mikanRowKey({ key: "3669:11", status: "downloading" }), "3669:11");
assert.equal(
  mikanRowKey({ key: "3669:11", status: "downloading" }),
  mikanRowKey({ key: "3669:11", status: "completed_waiting_extract" }),
);
assert.deepEqual(
  mikanPipelineSummary({
    mikanCounts: { extracting_subtitles: 0, completed_waiting_extract: 2, extract_failed: 4 },
    extractCounts: { running: 2, queued: 0, failed: 4, replaced: 209 },
  }),
  {
    queuedDownloads: 0,
    downloading: 0,
    extracting: 2,
    waitingExtract: 0,
    candidateRetry: 0,
    autoReplacing: 4,
    needsAttention: 0,
    imported: 0,
  },
);
assert.equal(subtitleQualityLabel({ status: "watchable" }), "可觀看");
assert.equal(subtitleQualityTone({ status: "check" }), "warn");
assert.match(subtitleQualitySummary({ status: "rerun", score: 20, issues: [{ message: "字幕太短" }] }), /建議重跑/);
assert.match(subtitleQualitySummary({ status: "check", score: 95, issues: [{ message: "安全省略", count: 2 }] }), /2 行/);
assert.deepEqual(
  newestCompletedFirst([
    { job_key: "old", finished_at: 100 },
    { job_key: "new", finished_at: 300 },
    { job_key: "middle", finished_at: 200 },
  ]).map((item) => item.job_key),
  ["new", "middle", "old"],
);

assert.match(appVue, /\/api\/dashboard\/summary/);
assert.match(appVue, /\/api\/dashboard\/tasks\?\$\{query\.toString\(\)\}/);
assert.match(appVue, /page_size: String\(taskQuery\.value\.pageSize\)/);
assert.match(appVue, /page_size: "20"/);
assert.match(appVue, /compact: "true"/);
assert.match(appVue, /\/api\/mikan\/downloads/);
assert.match(appVue, /URLSearchParams/);
assert.match(appVue, /status_filter/);
assert.match(appVue, /loadDownloads/);
assert.match(appVue, /loadTasks/);
assert.match(appVue, /supersede/);
assert.match(appVue, /\/api\/v2\/events\?limit=20/);
assert.match(appVue, /scheduleRefresh/);
assert.match(appVue, /scheduleRefresh\(false, actionable\)/);
assert.match(appVue, /changedEntities/);
assert.match(appVue, /refreshInFlight/);
assert.match(appVue, /showIndicator/);
assert.match(appVue, /preserveMikanRowOrder/);
assert.match(appVue, /preserveRowsByKey/);
assert.match(appVue, /preserveOrder: !forceAll/);
assert.match(appVue, /addEventListener\("state"/);
assert.doesNotMatch(appVue, /api\("\/api\/actions\/status"/);
assert.match(appVue, /activePanel/);
assert.match(appVue, /Promise\.allSettled/);
assert.match(appVue, /timeoutMs = 20000/);
assert.match(appVue, /upstreamSignal/);
assert.match(appVue, /refreshQueuedForceAll/);
assert.match(appVue, /global-operation/);
assert.match(appVue, /pending-paths/);
assert.match(appVue, /healthState/);
assert.match(appVue, /aiSchedulerNeedsAttention/);
assert.match(appVue, /AI 排程暫時讀不到工作清單/);
assert.match(appVue, /立即重試 AI 排程/);
assert.match(appVue, /system\.ai_scheduler_retry/);
assert.match(appVue, /runtimeFacts/);
assert.match(appVue, /runtime version and health/);
assert.match(appVue, /overviewPrimaryStats/);
assert.match(appVue, /overviewResourceStats/);
assert.match(appVue, /overviewDetailStats/);
assert.match(appVue, /\/api\/v2\/overview/);
assert.match(appVue, /overview\.ai_delivery_slo/);
assert.match(appVue, /overviewResources\.telemetry/);
assert.match(appVue, /resourceTelemetry/);
assert.match(appVue, /resourceStats/);
assert.match(appVue, /label: "CPU"/);
assert.match(appVue, /label: "RAM"/);
assert.match(appVue, /label: "GPU"/);
assert.match(appVue, /label: "VRAM"/);
assert.match(appVue, /value: cpuAvailable \? `\$\{cpuPercent\.toFixed\(1\)\}%` : "無法讀取"/);
assert.match(appVue, /nvidia_smi_timeout: "nvidia-smi 讀取逾時"/);
assert.match(appVue, /value === null \|\| value === undefined \|\| value === ""/);
assert.match(appVue, /tone: resourceMetricTone/);
assert.match(appVue, /resourceEnvelope\.value\.disk/);
assert.match(appVue, /resourceEnvelope\.value\.admission/);
assert.match(appVue, /label: "Disk"/);
assert.match(appVue, /label: "資源准入"/);
assert.match(appVue, /label: "執行路由"/);
assert.match(appVue, /effective\.batch_size/);
assert.match(appVue, /effective\.context_max_blocks/);
assert.match(appVue, /effective\.context_max_chars/);
assert.match(appVue, /effective\.concurrency/);
assert.match(appVue, /retry_after_seconds/);
assert.match(appVue, /resourceSnapshotIsStale/);
assert.match(appVue, /transport_stale/);
assert.match(appVue, /retainStaleResourceSnapshot/);
assert.match(appVue, /overview_field_missing/);
assert.match(appVue, /etaSummary\.remaining \?\? queueCounts\.queued/);
assert.match(appVue, /aiDeliverySloCard/);
assert.match(appVue, /const AI_DELIVERY_SLO_TARGET = 0\.9999;/);
assert.match(appVue, /const AI_DELIVERY_SLO_TARGET_LABEL = "99\.99%";/);
assert.match(appVue, /target: AI_DELIVERY_SLO_TARGET/);
assert.match(appVue, /confidence_lower_bound: null/);
assert.match(appVue, /confidence_target_met: null/);
assert.match(appVue, /coverage_active_queue_total: null/);
assert.match(appVue, /coverage_active_queue_tracked: null/);
assert.match(appVue, /coverage_active_queue_untracked: null/);
assert.match(appVue, /coverage_active_queue_complete: null/);
assert.match(appVue, /coverage_inventory_available: false/);
assert.match(appVue, /coverage_inventory_state: "unavailable"/);
assert.match(appVue, /coverage_inventory_total: null/);
assert.match(appVue, /coverage_inventory_tracked: null/);
assert.match(appVue, /coverage_inventory_untracked: null/);
assert.match(appVue, /coverage_inventory_legacy_grandfathered: null/);
assert.match(appVue, /coverage_inventory_complete: null/);
assert.match(appVue, /coverage_complete: null/);
assert.match(appVue, /publication_breakdown:/);
assert.match(appVue, /translated_chinese:/);
assert.match(appVue, /publication_kinds: \["adopted_zh_tw", "converted_zh_cn", "translated_trilingual"\]/);
assert.match(appVue, /required_output_language: "zh-TW"/);
assert.match(appVue, /source_language:/);
assert.match(appVue, /counts_as_traditional_chinese_success: false/);
assert.match(appVue, /invalid_success_evidence:/);
assert.match(appVue, /unclassified_misses:/);
assert.match(appVue, /AI 繁中字 30 天準時交付率（目標 \$\{AI_DELIVERY_SLO_TARGET_LABEL\}）/);
assert.match(appVue, /AI 繁中字準時交付累積證據/);
assert.match(appVue, /rolling_operational/);
assert.match(appVue, /cumulative_evidence/);
assert.match(appVue, /target_evidence_met/);
assert.match(appVue, /只證明可用繁中字 strict 準時交付/);
assert.match(appVue, /source-only／source_language 明確不算成功/);
assert.match(appVue, /const completedDelivery = computed/);
assert.match(appVue, /completed_delivery: overviewResult\.status === "fulfilled"/);
assert.match(appVue, /available: false/);
assert.match(appVue, /final_path: ""/);
assert.match(appVue, /error: "overview_unavailable"/);
assert.match(appVue, /label: "成品影片交付"/);
assert.match(appVue, /delivery\.available === true && state === "committed"/);
assert.match(appVue, /\$\{delivery\.final_path\}/);
assert.match(appVue, /未提供可用成品路徑/);
assert.match(appVue, /成品雜湊與 Worker 收據不符/);
assert.match(appVue, /Worker 台帳與成品收據不一致/);
assert.match(appVue, /completedDeliveryCard\.value/);
assert.match(appVue, /state === "coverage_incomplete"/);
assert.match(appVue, /const overallVerified = slo\.target_met === true/);
assert.match(appVue, /tone: overallVerified \? "success"/);
assert.match(appVue, /證據門檻已達，整體尚未驗證/);
assert.doesNotMatch(appVue, /aiDeliverySloLegacyCard/);
assert.doesNotMatch(appVue, /已證明/);
assert.equal(appVue.includes("/api/ai/control/"), false);
assert.match(appVue, /\/api\/v2\/commands/);
assert.match(appVue, /system\.ai_queue_pause/);
assert.match(appVue, /system\.retry_all_failures/);
assert.match(appVue, /mikan\.process_completed/);
assert.match(appVue, /mikan\.requeue_failed_extracts/);
assert.match(appVue, /commandActionBusy/);
assert.match(appVue, /ai\.retranslate_lines/);
assert.match(appVue, /waitForCommand/);
assert.match(appVue, /idempotencyKey/);
assert.equal(appVue.includes("/api/mikan/redownload/cancel"), false);
assert.match(appVue, /mikan\.cancel_redownload/);
assert.match(appVue, /mikan\.cancel_extract/);
assert.match(appVue, /mikan\.requeue_extract/);
assert.match(appVue, /mikan\.request_redownload_all/);
assert.match(appVue, /AI 處理速度/);
assert.match(appVue, /預估清空/);
assert.match(appVue, /安全停止/);
assert.match(appVue, /mikanAttentionSummary/);
assert.match(appVue, /site-header/);
assert.match(appVue, /mikanStateDb/);
assert.match(appVue, /state_db/);
assert.match(appVue, /TaskDashboard/);
assert.match(appVue, /MikanDownloads/);
assert.match(appVue, /ActionsPanel/);
assert.match(appVue, /SeriesMetadata/);
assert.match(appVue, /ReviewCenter/);
assert.match(appVue, /\/api\/series/);
assert.match(appVue, /\/api\/ai\/diagnostics/);
assert.doesNotMatch(appVue, /FlowEditor/);
assert.doesNotMatch(appVue, /\/api\/workflow/);
assert.match(taskDashboard, /queue-action/);
assert.match(taskDashboard, /subtitleQualitySummary/);
assert.match(taskDashboard, /qualityIssueIndexes/);
assert.match(taskDashboard, /quality-issue-list/);
assert.match(taskDashboard, /primaryActionFor/);
assert.match(taskDashboard, /secondaryActionsFor/);
assert.match(taskDashboard, /task-action-menu/);
assert.match(taskDashboard, /task-failure-state/);
assert.match(taskDashboard, /viewMode === 'active' && task\.status !== 'Failed'/);
assert.match(taskDashboard, /<summary>技術資料與操作<\/summary>/);
assert.match(taskDashboard, /class="queue-utilities"/);
assert.match(taskDashboard, /正常情況由系統自動排程，不需要操作/);
assert.match(taskDashboard, /task-card--\$\{statusTone\(task\.status\)\}/);
assert.match(taskDashboard, /task\.subtitle_quality\.status !== 'watchable'/);
assert.doesNotMatch(taskDashboard, /<span><b>\{\{ counts\.done \|\| 0 \}\}<\/b> 完成<\/span>/);
assert.match(taskDashboard, /<dt>檔案路徑<\/dt>/);
assert.doesNotMatch(taskDashboard, /<summary>更多操作<\/summary>/);
assert.match(taskDashboard, /只重翻這/);
assert.match(taskDashboard, /emit\("query"/);
assert.match(taskDashboard, /emit\(['"]page['"]/);
assert.match(taskDashboard, /pendingPathSet/);
assert.match(taskDashboard, /syncingQuery/);
assert.match(taskDashboard, /safe-retry/);
assert.match(taskDashboard, /安全處理 1 筆/);
assert.match(appVue, /manualAttentionCount/);
assert.match(appVue, /retryBacklogCount/);
assert.match(appVue, /retryableExtractCount/);
assert.match(appVue, /outcomes_7d/);
assert.match(appVue, /deduplicated_attention_total/);
assert.match(appVue, /extractJobs\.value\?\.retryable_count/);
assert.match(appVue, /system\.ai_failed_retry_sweep/);
assert.match(appVue, /@safe-retry="runAiFailedRetrySweep\('start'\)"/);
assert.match(appVue, /目前失敗待重試/);
assert.match(appVue, /已重新排隊/);
assert.doesNotMatch(appVue, /retryableExtractFailures\s*\+\s*attentionSummary\.value\.terminalExtractFailures/);
assert.match(appVue, /<strong>例外<\/strong>/);
assert.match(appVue, /overview-exception-strip/);
assert.match(appVue, /<strong>進階資訊<\/strong>/);
assert.match(appVue, /humanRequiredReviewCount/);
assert.match(appVue, /counts\.human_required/);
assert.match(appVue, /label: "需要你決定"/);
assert.doesNotMatch(appVue, /label: "AI／ASR 待確認"/);
assert.match(appVue, /activeTask\.value && waitingCount\.value > 0/);
assert.match(appVue, /aiPaused && !routineDeploymentPause/);
assert.doesNotMatch(appVue, /<h2>字幕來源<\/h2>/);
assert.doesNotMatch(appVue, /尚未列入審核/);
assert.match(taskDashboard, /只重翻譯/);
assert.match(taskDashboard, /重翻指定行/);
assert.match(taskDashboard, /AI 診斷/);
assert.match(taskDashboard, /重新轉錄/);
assert.match(taskDashboard, /ai-control/);
assert.match(taskDashboard, /一次只處理一筆安全候選/);
assert.match(taskDashboard, /暫停 AI 佇列/);
assert.doesNotMatch(appVue, /:busy="actionBusy \|\| tasksLoading"/);
assert.match(mikanDownloads, /nextActionLabel/);
assert.match(mikanDownloads, /statusOptions/);
assert.match(mikanDownloads, /listCounts/);
assert.match(mikanDownloads, /props\.downloads\.counts/);
assert.match(mikanDownloads, /failed_candidate/);
assert.match(mikanDownloads, /等待來源配對審核/);
assert.match(mikanDownloads, /尚未分類/);
assert.match(mikanDownloads, /來源群組/);
assert.match(mikanDownloads, /提取工作/);
assert.match(mikanDownloads, /個來源群組等待候選/);
assert.match(mikanDownloads, /stateDb/);
assert.match(mikanDownloads, /recent_completed/);
assert.match(mikanDownloads, /recent_attention/);
assert.match(mikanDownloads, /extractAttention/);
assert.match(mikanDownloads, /newestCompletedFirst/);
assert.match(mikanDownloads, /mikanSourceLabel/);
assert.match(mikanDownloads, /mikanSubtitleStateLabel/);
assert.match(mikanDownloads, /source-chip/);
assert.match(mikanDownloads, /emit\("query"/);
assert.match(mikanDownloads, /props\.query/);
assert.match(mikanDownloads, /syncingQuery/);
assert.match(mikanDownloads, /clearFilters/);
assert.match(mikanDownloads, /mikanRowKey\(row\)/);
assert.doesNotMatch(mikanDownloads, /\$\{row\.status\}-\$\{row\.key/);
assert.match(mikanDownloads, /source-stage-grid/);
assert.match(mikanDownloads, /terminal_failed/);
assert.match(seriesMetadata, /loading && items\.length === 0/);
assert.doesNotMatch(mikanDownloads, /matchStatus/);
assert.match(actionsPanel, /ai-refresh-queue-state/);
assert.match(actionsPanel, /ai-safe-retry-sweep/);
assert.match(actionsPanel, /terminal-error/);
assert.match(actionLabel("ai-refresh-queue-state"), /重新整理 AI 分類/);
assert.match(actionsPanel, /不啟動 AI/);
assert.match(actionsPanel, /mikan-redownload-all/);
assert.match(actionsPanel, /mikan-requeue-failed-extracts/);
assert.match(actionsPanel, /restart-worker/);
assert.match(actionsPanel, /backup-state/);
assert.match(seriesMetadata, /作品資訊與術語/);
assert.match(seriesMetadata, /作品專用術語庫/);
assert.match(seriesMetadata, /人工修正作品匹配/);
assert.match(seriesMetadata, /立即同步作品/);
assert.match(seriesMetadata, /完整資料/);
assert.match(seriesMetadata, /series-operation-status/);
assert.match(seriesMetadata, /aria-live="polite"/);
assert.match(seriesMetadata, /mutationBusy/);
assert.match(seriesMetadata, /operationButtonLabel/);
assert.match(seriesMetadata, /其他更新執行中/);
assert.match(seriesMetadata, /deleteConfirmation/);
assert.match(seriesMetadata, /輸入內容仍保留/);
assert.doesNotMatch(seriesMetadata, /emit\("glossary-upsert"[\s\S]{0,500}termSource\.value = ""/);
assert.match(reviewCenter, /candidate_path/);
assert.match(reviewCenter, /function candidateEpisode/);
assert.match(reviewCenter, /function candidateList/);
assert.match(reviewCenter, /const unique = new Map/);
assert.match(reviewCenter, /自動整理並重新比對/);
assert.match(reviewCenter, /target\.auto_rebuild_candidates/);
assert.match(reviewCenter, /手動指定作品與季度/);
assert.match(reviewCenter, /function targetActionLabel/);
assert.match(reviewCenter, /確認配對並重新下載來源/);
assert.match(reviewCenter, /確認配對並提取現有檔案/);
assert.match(reviewCenter, /function targetConfirmationSafety/);
assert.match(reviewCenter, /已保存的原始種子網址重新加入 qBittorrent/);
assert.match(reviewCenter, /duplicate_count/);
assert.match(reviewCenter, /review-workbench/);
assert.match(reviewCenter, /review-detail-panel/);
assert.match(reviewCenter, /review-meta-details review-diagnostics/);
assert.match(reviewCenter, /來源時間、封鎖狀態與技術診斷/);
assert.match(reviewCenter, /候選技術資料/);
assert.match(reviewCenter, /review-line-list/);
assert.match(reviewCenter, /<template v-else-if="selectedItem">\s*<div class="review-detail-content">/);
assert.match(
  reviewCenter,
  /<\/div>\s*<footer v-if="selectedItem\.state !== 'resolved'" class="review-detail-actions">/,
);
assert.match(styles, /@media \(min-width: 901px\) and \(max-height: 900px\)/);
assert.match(styles, /\.workspace:has\(> \.review-center\)\s*{\s*padding-bottom: 0;/);
assert.match(styles, /\.page-panel\.review-center\s*{\s*padding-bottom: 0\.5rem;/);
assert.match(styles, /\.review-workbench\s*{[\s\S]*?height: auto;/);
assert.match(styles, /\.review-inbox\s*{[\s\S]*?overflow: visible;/);
assert.match(styles, /@media \(min-width: 901px\)[\s\S]*?\.review-detail-panel\s*{[\s\S]*?position: sticky;[\s\S]*?overflow: hidden;/);
assert.match(styles, /grid-template-rows: minmax\(0, 1fr\) auto;/);
assert.match(styles, /\.review-detail-content\s*{[\s\S]*?min-height: 0;[\s\S]*?overflow-y: auto;/);
assert.match(styles, /\.review-detail-panel > \.review-detail-actions\s*{[\s\S]*?position: static;[\s\S]*?margin-top: 0;/);
assert.match(styles, /\.review-candidate-list\s*{\s*position: relative;\s*}/);
assert.match(reviewCenter, /mediaFileInfo/);
assert.match(reviewCenter, /\$\{subject\}建立時間/);
assert.match(reviewCenter, /建立時間不可用/);
assert.match(reviewCenter, /batch-resolve/);
assert.match(reviewCenter, /安全批次處理/);
assert.match(reviewCenter, /需要你決定/);
assert.match(reviewCenter, /等待自動處理/);
assert.match(reviewCenter, /stateCounts\.value\.automatic_safe/);
assert.match(reviewCenter, /stateCounts\.value\.human_required/);
assert.match(reviewCenter, /reviewScope\.value === "automatic"/);
assert.match(reviewCenter, /reviewScope\.value === "all"/);
assert.match(reviewCenter, /const batchMode = ref\(false\)/);
assert.match(reviewCenter, /v-if="batchMode" class="review-row-check"/);
assert.match(reviewCenter, /開啟批次選取/);
assert.match(reviewCenter, /target\.rebuild_candidates/);
assert.match(reviewCenter, /建立安全候選/);
assert.match(reviewCenter, /review-operation-status/);
assert.match(reviewCenter, /data-testid="review-operation-status"/);
assert.match(reviewCenter, /aria-live="polite"/);
assert.match(reviewCenter, /aria-label="搜尋例外項目"/);
assert.match(reviewCenter, /不需要返回清單確認/);
assert.doesNotMatch(reviewCenter, /處理狀態與結果會自動更新/);
assert.match(reviewCenter, /operationButtonLabel/);
assert.match(reviewCenter, /reviewActionLocked/);
assert.match(reviewCenter, /operationCommandId/);
assert.match(reviewCenter, /continueAfterOperation/);
assert.doesNotMatch(reviewCenter, /背景操作不會中斷/);
assert.match(reviewCenter, /function confirmDeleteReview/);
assert.match(reviewCenter, /review\.dismiss/);
assert.match(reviewCenter, /刪除這筆審核/);
assert.match(reviewCenter, /不會刪除影片、字幕或 qBittorrent torrent/);
assert.match(reviewCenter, /同一個原始來源不會再次出現/);
assert.match(reviewCenter, /review-delete-action/);
assert.match(reviewCenter, /review-secondary-actions/);
const stickyReviewActions = reviewCenter.match(/<footer[^>]+review-detail-actions[\s\S]*?<\/footer>/)?.[0] || "";
assert.doesNotMatch(stickyReviewActions, /review-delete-action/);
assert.match(reviewCenter, /reviewDeleteLocked/);
assert.match(reviewCenter, /reviewWasDismissed/);
assert.match(reviewCenter, /已忽略/);
assert.match(reviewCenter, /查看 AI 處理進度/);
assert.match(reviewCenter, /查看字幕提取狀態/);
assert.match(reviewCenter, /這不代表新字幕已完成/);
assert.match(reviewCenter, /已處理 <b>/);
assert.doesNotMatch(reviewCenter, /最後確認/);
assert.doesNotMatch(reviewCenter, /下一步：確認所選季度/);
assert.doesNotMatch(reviewCenter, /window\.confirm/);
assert.match(appVue, /searchReviewSeries/);
assert.match(appVue, /已找到唯一安全影片/);
assert.match(appVue, /auto-rebuild/);
assert.match(appVue, /mode === "dismiss"/);
assert.match(appVue, /正在從待辦移除這筆審核/);
assert.match(appVue, /這筆審核已從待辦移除/);
assert.match(appVue, /openReviewCount/);
assert.match(appVue, /view: "summary"/);
assert.match(appVue, /loadReviewDetail/);
assert.match(appVue, /review-items\/batch-resolve/);
assert.match(appVue, /resolveReviewBatch/);
assert.match(appVue, /reviewOperations/);
assert.match(appVue, /setReviewOperation/);
assert.match(appVue, /status: "submitting"/);
assert.match(appVue, /status: "reconnecting"/);
assert.match(appVue, /loadReviewDetail\(reviewId, \{ force: true \}\)/);
assert.match(appVue, /:operations-by-review="reviewOperations"/);
assert.match(appVue, /reviewCompletionMessage/);
assert.match(appVue, /問題字幕已排入重新翻譯/);
assert.match(appVue, /@open-work="openReviewWork"/);
assert.match(appVue, /seriesOperation/);
assert.match(appVue, /:operation="seriesOperation"/);
assert.match(appVue, /正在安全送出作品資料更新/);
assert.doesNotMatch(appVue, /delete nextDetails\[reviewId\]/);
assert.doesNotMatch(reviewCenter, /const first = \(item\.candidates/);
assert.doesNotMatch(reviewCenter, /inferredSeriesPath\(candidatePath\(candidate\)\)/);
assert.doesNotMatch(appVue, /review\.kind !== "target_ambiguity"[\s\S]*window\.confirm/);
assert.match(actionsPanel, /SQLite 健康狀態/);
assert.match(mikanDownloads, /提取進度/);
assert.match(mikanDownloads, /current_file_timestamp/);
assert.match(mikanDownloads, /\$\{subject\}建立時間/);
assert.match(mikanDownloads, /建立時間不可用/);
assert.match(mikanDownloads, /種子發佈/);
assert.match(mikanDownloads, /種子建立/);
assert.match(mikanDownloads, /加入 qB/);
assert.match(mikanDownloads, /下載完成/);
assert.match(mikanDownloads, /Worker 心跳正常/);
assert.match(mikanDownloads, /安全中斷/);
assert.match(mikanDownloads, /只重試這一筆/);
assert.match(taskDashboard, /字幕逐句檢查/);
assert.match(taskDashboard, /只重翻這一行/);
assert.match(taskDashboard, /pendingPathSet\.has\(task\.path\)[\s\S]*只重翻這一行/);
assert.match(taskDashboard, /這個項目目前不在進行中佇列/);
assert.match(taskDashboard, /查看最近完成/);
assert.match(taskDashboard, /清除搜尋/);
assert.match(taskDashboard, /friendlyTaskMessage/);
assert.match(taskDashboard, /qualityIssueLabel/);
assert.doesNotMatch(taskDashboard, /\{\{ task\.skip_reason \|\| task\.message \}\}/);
assert.match(mikanDownloads, /friendlyTechnicalMessage/);
assert.doesNotMatch(mikanDownloads, /\{\{ job\.last_error \}\}/);
assert.match(reviewCenter, /qualityIssueLabel/);
assert.match(reviewCenter, /itemDescription/);
assert.match(reviewCenter, /來源已不存在，這筆已自動結案/);
assert.match(reviewCenter, /qB 項目已移除，但下載檔仍在/);
assert.match(reviewCenter, /確認正確作品與季度後，Worker 會重新下載來源/);
assert.match(reviewCenter, /source_unavailable_pending/);
assert.match(reviewCenter, /sourceSafetyText/);
assert.match(reviewCenter, /operationSourceResumeMode/);
assert.match(reviewCenter, /原始來源已安全加入 qBittorrent/);
assert.match(appVue, /key: "reviews", label: "例外"/);
assert.match(appVue, /if \(document\.hidden\) \{[\s\S]*?stream\?\.close\(\);[\s\S]*?stream = null;/);
assert.match(appVue, /if \(stream\) return;/);
assert.match(appVue, /const nextStream = new EventSource/);
assert.match(appVue, /connectStream\(\);\s*scheduleRefresh\(true\);/);
assert.match(appVue, /\/api\/v2\/worker\/runtime-log\?tail=120/);
assert.match(appVue, /Worker 持續重啟，工作已停止/);
assert.match(
  reviewCenter,
  /if \(isDesktop \|\| detailOpen\.value\) emit\("select", selectedReviewId\.value\)/,
  "an open mobile review must fetch the replacement item's full detail",
);

console.log("frontend dashboard tests: ok");
