import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const packageJson = JSON.parse(read("package.json"));
const dockerfile = read("Dockerfile");
const viteConfig = read("vite.config.js");
const index = read("index.html");
const app = read("src/App.vue");
const dashboard = read("src/dashboard.js");
const taskDashboard = read("src/components/TaskDashboard.vue");
const mikanDownloads = read("src/components/MikanDownloads.vue");
const actionsPanel = read("src/components/ActionsPanel.vue");
const seriesMetadata = read("src/components/SeriesMetadata.vue");
const eventsTimeline = read("src/components/EventsTimeline.vue");
const reviewCenter = read("src/components/ReviewCenter.vue");
const css = read("src/styles.css");
const replacementCharacter = String.fromCharCode(0xfffd);
const mojibakePatterns = [
  "摮",
  "蝑",
  "銝",
  "頛",
  "霈",
  "憭",
  "閬",
  "撟",
  "蝚",
  "隞",
  "鞈",
  "?",
  "?",
];

for (const [name, source] of [
  ["index.html", index],
  ["src/App.vue", app],
  ["src/dashboard.js", dashboard],
  ["src/components/TaskDashboard.vue", taskDashboard],
  ["src/components/MikanDownloads.vue", mikanDownloads],
  ["src/components/ActionsPanel.vue", actionsPanel],
  ["src/components/SeriesMetadata.vue", seriesMetadata],
  ["src/components/EventsTimeline.vue", eventsTimeline],
  ["src/components/ReviewCenter.vue", reviewCenter],
  ["src/styles.css", css],
]) {
  assert.equal(source.includes(replacementCharacter), false, `${name} contains replacement characters`);
}

for (const [name, source] of [
  ["src/App.vue", app],
  ["src/dashboard.js", dashboard],
  ["src/components/TaskDashboard.vue", taskDashboard],
  ["src/components/MikanDownloads.vue", mikanDownloads],
  ["src/components/ActionsPanel.vue", actionsPanel],
  ["src/components/SeriesMetadata.vue", seriesMetadata],
  ["src/components/EventsTimeline.vue", eventsTimeline],
  ["src/components/ReviewCenter.vue", reviewCenter],
]) {
  for (const pattern of mojibakePatterns) {
    assert.equal(source.includes(pattern), false, `${name} still contains mojibake pattern ${pattern}`);
  }
}

assert.equal(packageJson.type, "module");
assert.equal(packageJson.dependencies.vue.startsWith("^3."), true);
assert.equal(packageJson.dependencies["@vue-flow/core"], undefined);
assert.equal(packageJson.dependencies["@vue-flow/background"], undefined);
assert.equal(packageJson.dependencies["@vue-flow/controls"], undefined);
assert.ok(packageJson.devDependencies.vite);
assert.ok(packageJson.devDependencies["@vitejs/plugin-vue"]);

assert.match(dockerfile, /FROM node:22-bookworm-slim AS frontend/);
assert.match(dockerfile, /npm ci/);
assert.match(dockerfile, /COPY Dockerfile \/frontend\/Dockerfile/);
assert.match(dockerfile, /npm run build/);
assert.match(dockerfile, /COPY --from=frontend \/frontend\/dist \/app\/static/);
assert.match(viteConfig, /base: "\/static\/"/);
assert.match(viteConfig, /VITE_DEV_BACKEND/);
assert.match(viteConfig, /"\/api": \{ target: devBackend/);
assert.match(index, /<div id="app"><\/div>/);
assert.match(index, /type="module" src="\/src\/main\.js"/);

assert.match(app, /\/api\/dashboard\/summary/);
assert.match(app, /\/api\/dashboard\/tasks/);
assert.match(app, /mode: taskQuery\.value\.mode/);
assert.match(app, /page_size: String\(taskQuery\.value\.pageSize\)/);
assert.match(app, /compact: "true"/);
assert.match(app, /scheduleRefresh/);
assert.match(app, /scheduleRefresh\(false, actionable\)/);
assert.match(app, /changedEntities/);
assert.match(app, /refreshInFlight/);
assert.match(app, /preserveMikanRowOrder/);
assert.match(app, /preserveRowsByKey/);
assert.match(app, /\/api\/v2\/events\?limit=20/);
assert.match(app, /friendlyApiError/);
assert.match(app, /preserveOrder: !forceAll/);
assert.match(app, /addEventListener\("state"/);
assert.doesNotMatch(app, /api\("\/api\/actions\/status"/);
assert.match(app, /activePanel/);
assert.match(app, /Promise\.allSettled/);
assert.match(app, /overview-status-bar/);
assert.match(app, /aiSchedulerNeedsAttention/);
assert.match(app, /system\.ai_scheduler_retry/);
assert.match(app, /立即重試 AI 排程/);
assert.match(app, /runtime-facts/);
assert.match(app, /runtimeFacts/);
assert.match(app, /recommendations/);
assert.match(app, /copyDiagnostics/);
assert.match(app, /診斷摘要已複製/);
assert.match(app, /overviewPrimaryStats/);
assert.match(app, /overviewResourceStats/);
assert.match(app, /overviewDetailStats/);
assert.match(app, /resourceTelemetry/);
assert.match(app, /resourceStats/);
assert.match(app, /overviewResources\.telemetry/);
assert.match(app, /label: "CPU"/);
assert.match(app, /label: "RAM"/);
assert.match(app, /label: "GPU"/);
assert.match(app, /label: "VRAM"/);
assert.match(app, /label: "Disk"/);
assert.match(app, /label: "資源准入"/);
assert.match(app, /label: "執行路由"/);
assert.match(app, /監控資料正在讀取/);
assert.match(app, /nvidia_smi_missing/);
assert.match(app, /resourceMetricNumber/);
assert.match(app, /resourceSnapshotIsStale/);
assert.match(app, /retainStaleResourceSnapshot/);
assert.match(app, /overview_field_missing/);
assert.match(app, /effective\.batch_size/);
assert.match(app, /retry_after_seconds/);
assert.match(app, /\.\.\.resourceStats\.value/);
assert.equal(app.includes("/api/ai/control/"), false);
assert.match(app, /\/api\/v2\/commands/);
assert.match(app, /\/api\/v2\/series\//);
assert.match(app, /series\.glossary_upsert/);
assert.equal(app.includes("/api/series/glossary"), false);
assert.match(app, /system\.ai_queue_pause/);
assert.match(app, /system\.retry_all_failures/);
assert.match(app, /mikan\.process_completed/);
assert.match(app, /mikan\.requeue_failed_extracts/);
assert.match(app, /ai\.retranslate_lines/);
assert.match(app, /waitForCommand/);
assert.equal(app.includes("/api/mikan/redownload/cancel"), false);
assert.match(app, /mikan\.cancel_redownload/);
assert.match(app, /mikan\.cancel_extract/);
assert.match(app, /mikan\.requeue_extract/);
assert.match(app, /mikan\.request_redownload_all/);
assert.match(app, /control-deck/);
assert.match(app, /mikanPipelineSummary/);
assert.match(app, /site-header/);
assert.match(app, /mikanStateDb/);
assert.match(app, /status_filter/);
assert.match(app, /downloadsLoading/);
assert.match(app, /downloadController/);
assert.match(app, /taskController/);
assert.match(app, /timeoutMs = 20000/);
assert.match(app, /upstreamSignal/);
assert.match(app, /refreshQueuedForceAll/);
assert.match(app, /global-operation/);
assert.match(app, /pending-paths/);
assert.match(app, /AI_DELIVERY_SLO_TARGET = 0\.9999/);
assert.match(app, /AI_DELIVERY_SLO_TARGET_LABEL = "99\.99%"/);
assert.match(app, /confidence_lower_bound: null/);
assert.match(app, /confidence_target_met: null/);
assert.match(app, /coverage_active_queue_total: null/);
assert.match(app, /coverage_active_queue_tracked: null/);
assert.match(app, /coverage_active_queue_untracked: null/);
assert.match(app, /coverage_active_queue_complete: null/);
assert.match(app, /coverage_inventory_available: false/);
assert.match(app, /coverage_inventory_state: "unavailable"/);
assert.match(app, /coverage_inventory_total: null/);
assert.match(app, /coverage_inventory_tracked: null/);
assert.match(app, /coverage_inventory_untracked: null/);
assert.match(app, /coverage_inventory_legacy_grandfathered: null/);
assert.match(app, /coverage_inventory_complete: null/);
assert.match(app, /coverage_complete: null/);
assert.match(app, /publication_breakdown:/);
assert.match(app, /translated_chinese:/);
assert.match(app, /publication_kinds: \["adopted_zh_tw", "converted_zh_cn", "translated_trilingual"\]/);
assert.match(app, /required_output_language: "zh-TW"/);
assert.match(app, /source_language:/);
assert.match(app, /counts_as_traditional_chinese_success: false/);
assert.match(app, /AI 繁中字 30 天準時交付率/);
assert.match(app, /source-only／source_language 明確不算成功/);
assert.match(app, /const completedDelivery = computed/);
assert.match(app, /label: "成品影片交付"/);
assert.match(app, /delivery\.available === true && state === "committed"/);
assert.match(app, /\$\{delivery\.final_path\}/);
assert.match(app, /未提供可用成品路徑/);
assert.match(app, /成品雜湊與 Worker 收據不符/);
assert.doesNotMatch(app, /completed-delivery-button/);
assert.match(app, /state === "coverage_incomplete"/);
assert.match(app, /const overallVerified = slo\.target_met === true/);
assert.match(app, /tone: overallVerified \? "success"/);
assert.match(app, /證據門檻已達，整體尚未驗證/);
assert.doesNotMatch(app, /aiDeliverySloLegacyCard/);
assert.doesNotMatch(app, /已證明/);
assert.doesNotMatch(app, /FlowEditor/);
assert.doesNotMatch(app, /VueFlow/);

assert.match(taskDashboard, /subtitle_quality/);
assert.match(taskDashboard, /primaryActionFor/);
assert.match(taskDashboard, /task-action-menu/);
assert.match(taskDashboard, /emit\("query"/);
assert.match(taskDashboard, /emit\(['"]page['"]/);
assert.match(taskDashboard, /pendingPathSet/);
assert.match(taskDashboard, /syncingQuery/);
assert.match(taskDashboard, /safe-retry/);
assert.match(taskDashboard, /安全處理 1 筆/);
assert.match(app, /manualAttentionCount/);
assert.match(app, /retryBacklogCount/);
assert.match(app, /retryableExtractCount/);
assert.match(app, /extractJobs\.value\?\.retryable_count/);
assert.match(app, /system\.ai_failed_retry_sweep/);
assert.match(app, /@safe-retry="runAiFailedRetrySweep\('start'\)"/);
assert.match(app, /<strong>例外<\/strong>/);
assert.match(app, /overview-exception-strip/);
assert.match(app, /<strong>進階資訊<\/strong>/);
assert.match(app, /counts\.human_required/);
assert.match(app, /key: "reviews", label: "例外"/);
assert.match(app, /if \(document\.hidden\) \{[\s\S]*?stream\?\.close\(\);[\s\S]*?stream = null;/);
assert.match(app, /if \(stream\) return;/);
assert.match(app, /const nextStream = new EventSource/);
assert.match(app, /aiPaused && !routineDeploymentPause/);
assert.doesNotMatch(app, /<h2>字幕來源<\/h2>/);
assert.doesNotMatch(app, /尚未列入審核/);
assert.match(taskDashboard, /ai-control/);
assert.match(taskDashboard, /一次只處理一筆安全候選/);
assert.doesNotMatch(app, /:busy="actionBusy \|\| tasksLoading"/);
assert.match(mikanDownloads, /nextActionLabel/);
assert.match(mikanDownloads, /mikanSubtitleStateLabel/);
assert.match(mikanDownloads, /recent_completed/);
assert.match(mikanDownloads, /recent_attention/);
assert.match(mikanDownloads, /requestedPage/);
assert.match(mikanDownloads, /emit\("query"/);
assert.match(mikanDownloads, /props\.query/);
assert.match(mikanDownloads, /syncingQuery/);
assert.match(mikanDownloads, /clearFilters/);
assert.match(mikanDownloads, /mikanRowKey\(row\)/);
assert.doesNotMatch(mikanDownloads, /\$\{row\.status\}-\$\{row\.key/);
assert.match(mikanDownloads, /source-stage-grid/);
assert.match(mikanDownloads, /個來源群組等待候選/);
assert.match(mikanDownloads, /terminal_failed/);
assert.match(actionsPanel, /系統工具/);
assert.match(actionsPanel, /ai-refresh-queue-state/);
assert.match(actionsPanel, /ai-safe-retry-sweep/);
assert.match(actionsPanel, /terminal-error/);
assert.match(actionsPanel, /不啟動 AI/);
assert.match(eventsTimeline, /eventStageLabel/);
assert.match(eventsTimeline, /event\.description/);
assert.match(eventsTimeline, /occurrence_count/);
assert.match(reviewCenter, /qualityIssueLabel/);
assert.match(reviewCenter, /review-workbench/);
assert.match(reviewCenter, /review-detail-panel/);
assert.match(reviewCenter, /review-meta-details review-diagnostics/);
assert.match(reviewCenter, /來源時間、封鎖狀態與技術診斷/);
assert.match(reviewCenter, /review-confirm-dialog/);
assert.match(reviewCenter, /mediaFileInfo/);
assert.match(reviewCenter, /candidate\.file_info/);
assert.match(reviewCenter, /sourceTimeline/);
assert.match(reviewCenter, /種子發佈/);
assert.match(reviewCenter, /種子建立/);
assert.match(reviewCenter, /加入 qB/);
assert.match(reviewCenter, /下載完成/);
assert.match(reviewCenter, /不是種子發佈時間/);
assert.match(reviewCenter, /candidateDateGuidance/);
assert.match(reviewCenter, /候選年份明顯不相符/);
assert.match(reviewCenter, /日期最接近/);
assert.match(reviewCenter, /安全批次處理/);
assert.match(reviewCenter, /stateCounts\.value\.automatic_safe/);
assert.match(reviewCenter, /stateCounts\.value\.human_required/);
assert.match(reviewCenter, /reviewScope\.value === "automatic"/);
assert.match(reviewCenter, /v-if="batchMode" class="review-row-check"/);
assert.match(reviewCenter, /review-operation-status/);
assert.match(reviewCenter, /data-testid="review-operation-status"/);
assert.match(reviewCenter, /aria-live="polite"/);
assert.match(reviewCenter, /aria-label="搜尋例外項目"/);
assert.match(reviewCenter, /operationButtonLabel/);
assert.match(reviewCenter, /不需要返回清單確認/);
assert.doesNotMatch(reviewCenter, /背景操作不會中斷/);
assert.match(reviewCenter, /confirmDeleteReview/);
assert.match(reviewCenter, /review\.dismiss/);
assert.match(reviewCenter, /刪除這筆審核/);
assert.match(reviewCenter, /review-secondary-actions/);
const stickyReviewActions = reviewCenter.match(/<footer[^>]+review-detail-actions[\s\S]*?<\/footer>/)?.[0] || "";
assert.doesNotMatch(stickyReviewActions, /review-delete-action/);
assert.match(reviewCenter, /同一個原始來源不會再次出現/);
assert.match(reviewCenter, /reviewWasDismissed/);
assert.match(reviewCenter, /已忽略/);
assert.match(reviewCenter, /查看 AI 處理進度/);
assert.match(reviewCenter, /這不代表新字幕已完成/);
assert.match(app, /view: "summary"/);
assert.match(app, /review-items\/batch-resolve/);
assert.match(app, /reviewOperations/);
assert.match(app, /setReviewOperation/);
assert.match(app, /:operations-by-review="reviewOperations"/);
assert.match(app, /reviewCompletionMessage/);
assert.match(app, /@open-work="openReviewWork"/);
assert.match(app, /seriesOperation/);
assert.match(app, /:operation="seriesOperation"/);
assert.doesNotMatch(app, /delete nextDetails\[reviewId\]/);
assert.match(seriesMetadata, /series-operation-status/);
assert.match(seriesMetadata, /mutationBusy/);
assert.match(seriesMetadata, /deleteConfirmation/);
assert.match(taskDashboard, /friendlyTaskMessage/);
assert.match(mikanDownloads, /friendlyTechnicalMessage/);
assert.match(mikanDownloads, /current_file_timestamp/);
assert.match(mikanDownloads, /source_published_at/);
assert.match(mikanDownloads, /torrent_added_at/);
assert.match(mikanDownloads, /torrent_completed_at/);
assert.match(dashboard, /mikanStatusLabels/);
assert.match(dashboard, /mikanPipelineSummary/);
assert.match(dashboard, /subtitleQualitySummary/);
assert.match(dashboard, /重新整理 AI 分類/);
assert.match(css, /\.download-list/);
assert.match(css, /\.download-card/);
assert.match(css, /\.task-card/);
assert.match(css, /\.overview-status-bar/);
assert.match(css, /\.runtime-facts/);
assert.match(css, /\.recommendation-panel/);
assert.match(css, /\.copy-diagnostics-button/);
assert.match(css, /\.site-header/);
assert.match(css, /\.global-operation/);
assert.match(css, /\.source-stage-grid/);
assert.match(css, /\.retry-failed-button/);
assert.match(css, /\.control-deck/);
assert.match(css, /\.queue-control-button/);
assert.match(css, /\.queue-utilities/);
assert.match(css, /\.ai-page\s*\{[^}]*background:\s*transparent/);
assert.match(css, /\.task-card--running\s*\{[^}]*var\(--blue\)/);
assert.match(css, /\.task-action-menu/);
assert.match(css, /\.task-failure-state/);
assert.match(css, /\.review-meta-details/);
assert.match(css, /\.review-workbench/);
assert.match(css, /\.review-detail-panel/);
assert.match(css, /\.review-source-timeline/);
assert.match(css, /\.review-date-guidance/);
assert.match(css, /\.review-line-row/);
assert.match(css, /\.review-confirm-dialog/);
assert.match(css, /\.review-operation-status/);
assert.match(css, /\.review-delete-action/);
assert.match(css, /\.review-confirm-safety\.danger/);
assert.match(css, /\.series-operation-status/);
assert.match(css, /content-visibility: auto/);
const liveCardContentVisibilitySelectors = [...css.matchAll(/([^{}]+)\{[^{}]*content-visibility:\s*auto;[^{}]*\}/g)]
  .map((match) => match[1]);
assert.ok(liveCardContentVisibilitySelectors.every((selector) => !selector.includes(".download-card")));
assert.ok(liveCardContentVisibilitySelectors.every((selector) => !selector.includes(".task-card")));
assert.match(css, /\.current-task-card\s*\{[^}]*overflow-anchor:\s*none/);
assert.match(css, /\.refresh-button\s*\{[^}]*min-width:/);
assert.match(css, /button:focus-visible/);
assert.match(app, /aria-label="手機主要選單"/);
assert.match(seriesMetadata, /series_id/);
assert.match(seriesMetadata, /loading && items\.length === 0/);
assert.doesNotMatch(css, /@media \(max-width: 800px\)/);
assert.match(css, /@media \(max-width: 900px\)/);
assert.match(css, /--workspace-max:\s*1680px/);
assert.match(css, /--site-header-offset:\s*76px/);
assert.match(css, /--sticky-content-offset:\s*calc\(var\(--site-header-offset\) \+ var\(--operation-bar-offset\)/);
assert.match(css, /html,\s*body,\s*#app\s*\{\s*max-width:\s*100%;\s*overflow-x:\s*clip/);
assert.doesNotMatch(css, /html,\s*body,\s*#app\s*\{[^}]*overflow-x:\s*hidden/);
assert.match(css, /\.global-operation\s*\{[^}]*top:\s*var\(--site-header-offset\)/);
assert.match(css, /\.review-batch-bar\s*\{[^}]*top:\s*var\(--sticky-content-offset\)/);
assert.match(css, /\.list-toolbar\s*\{[^}]*top:\s*var\(--sticky-content-offset\)/);
assert.match(css, /@media \(max-width: 1180px\)\s*\{\s*:root\s*\{\s*--site-header-offset:\s*136px/);
assert.match(css, /@media \(max-width: 900px\)\s*\{\s*:root\s*\{\s*--site-header-offset:\s*62px/);
assert.match(css, /@media \(max-width: 900px\)[\s\S]*?\.recommendation-panel\s*\{\s*grid-template-columns:\s*1fr/);
assert.match(css, /@media \(max-width: 520px\)[\s\S]*?\.overview-stats\s*\{\s*grid-template-columns:\s*1fr/);

const compactDesktopCss = css.slice(
  css.indexOf("@media (max-width: 1320px)"),
  css.indexOf("@media (max-width: 900px)", css.indexOf("@media (max-width: 1320px)")),
);
assert.doesNotMatch(compactDesktopCss, /\.overview-stats\s*\{\s*grid-template-columns:\s*repeat\(2/);
assert.doesNotMatch(compactDesktopCss, /\.control-deck\s*\{\s*grid-template-columns:\s*1fr/);
assert.match(css, /\.control-deck\s*>\s*:last-child:nth-child\(odd\)\s*\{\s*grid-column:\s*1\s*\/\s*-1/);
assert.match(css, /\.home-grid\s*>\s*:last-child:nth-child\(odd\)\s*\{\s*grid-column:\s*1\s*\/\s*-1/);
assert.match(css, /\.failure-root-list\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(10\.5rem,\s*1fr\)\)/);
assert.match(css, /\.home-grid\s*>\s*\.recent-card:last-child:nth-child\(odd\)\s+\.recent-list\s*\{[^}]*grid-template-columns:\s*repeat\(3/);

const reviewWorkbenchCss = css.match(/\.review-workbench\s*\{[^}]*\}/)?.[0] || "";
assert.match(reviewWorkbenchCss, /height:\s*auto/);
assert.match(reviewWorkbenchCss, /min-height:\s*0/);
assert.match(reviewWorkbenchCss, /max-height:\s*none/);
assert.match(reviewWorkbenchCss, /overflow:\s*visible/);
assert.doesNotMatch(reviewWorkbenchCss, /min-height:\s*620px|max-height:\s*calc\(100vh/);
assert.match(css, /\.review-inbox\s*\{[^}]*overflow:\s*visible/);
assert.match(css, /@media \(min-width: 901px\)[\s\S]*?\.review-detail-panel\s*\{[^}]*position:\s*sticky;[^}]*overflow:\s*hidden/);
assert.match(css, /\.review-detail-content\s*\{[^}]*width:\s*min\(100%,\s*70rem\)/);
assert.match(css, /\.review-detail-panel\s+\.review-line-list\s*\{[^}]*max-height:\s*none;[^}]*overflow:\s*visible/);
assert.match(css, /\.task-card\s+\.review-line-list\s*\{[^}]*max-height:\s*34rem;[^}]*overflow:\s*auto/);
assert.doesNotMatch(css, /var\(--danger\)/);

console.log("static dashboard contract tests: ok");
