<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import {
  candidateEvidenceLabel,
  compatibleReviewBatchItems,
  fileName,
  formatDuration,
  formatTime,
  friendlyTechnicalMessage,
  qualityIssueLabel,
  reviewOperationIsActive,
  reviewOperationLabel,
} from "../dashboard.js";

const props = defineProps({
  payload: { type: Object, default: () => ({ items: [], total: 0, state_counts: {} }) },
  loading: { type: Boolean, default: false },
  busyIds: { type: Array, default: () => [] },
  operationsByReview: { type: Object, default: () => ({}) },
  nowSeconds: { type: Number, default: () => Date.now() / 1000 },
  recoveryByReview: { type: Object, default: () => ({}) },
  query: { type: Object, default: () => ({ state: "needs_action", kind: "", search: "", sort: "priority" }) },
  detailsByReview: { type: Object, default: () => ({}) },
  detailLoadingIds: { type: Array, default: () => [] },
});

const emit = defineEmits([
  "refresh",
  "resolve",
  "batch-resolve",
  "search-series",
  "query",
  "load-more",
  "select",
  "open-work",
]);

const selectedReviewId = ref("");
const detailOpen = ref(false);
const searchInput = ref(String(props.query.search || ""));
const selectedPaths = ref({});
const selectedLineIndexes = ref({});
const batchReviewIds = ref([]);
const recoveryOpen = ref({});
const recoveryQueries = ref({});
const selectedSeriesIds = ref({});
const selectedSeasons = ref({});
const confirmation = ref(null);
const confirmButton = ref(null);
let searchTimer = null;
let mediaQuery = null;
let isDesktop = true;
let returnFocus = null;

const items = computed(() => props.payload?.items || []);
const busySet = computed(() => new Set(props.busyIds || []));
const detailLoadingSet = computed(() => new Set(props.detailLoadingIds || []));
const selectedSummary = computed(() => (
  items.value.find((item) => String(item.review_id) === selectedReviewId.value) || null
));
const selectedItem = computed(() => (
  props.detailsByReview?.[selectedReviewId.value] || selectedSummary.value
));
const stateCounts = computed(() => props.payload?.state_counts || {});
const selectedBatchAction = computed(() => {
  const first = items.value.find((item) => batchReviewIds.value.includes(String(item.review_id)));
  return String(first?.recommended_action?.action || "safe.default");
});
const visibleCompatibleBatchItems = computed(() => compatibleReviewBatchItems(
  items.value.filter((item) => !busySet.value.has(String(item?.review_id || ""))),
  batchReviewIds.value.length ? selectedBatchAction.value : "",
));
const allVisibleBatchSelected = computed(() => {
  const selectable = visibleCompatibleBatchItems.value;
  return selectable.length > 0 && selectable.every((item) => batchReviewIds.value.includes(String(item.review_id)));
});

function kindLabel(kind) {
  return {
    target_ambiguity: "來源配對",
    subtitle_quality: "翻譯品質",
    asr_quality: "轉錄品質",
  }[kind] || "人工審核";
}

function reviewOperation(item) {
  const reviewId = String(item?.review_id || "");
  const local = props.operationsByReview?.[reviewId];
  if (local && String(local.status || "").toLowerCase() !== "idle") return local;
  const durable = item?.action_state;
  if (durable && String(durable.status || "").toLowerCase() !== "idle") return durable;
  return null;
}

function operationStatus(item) {
  return String(reviewOperation(item)?.status || "").toLowerCase();
}

function operationMode(item) {
  const operation = reviewOperation(item) || {};
  const explicit = String(operation.mode || "").toLowerCase();
  if (explicit) return explicit;
  const action = String(operation.action || "").toLowerCase();
  if (action.includes("dismiss")) return "dismiss";
  if (action.includes("auto_rebuild")) return "auto-rebuild";
  if (action.includes("rebuild")) return "rebuild";
  return "resolve";
}

function operationIsCandidateRefresh(item) {
  return ["auto-rebuild", "rebuild"].includes(operationMode(item));
}

function candidateRefreshNeedsConfirmation(item) {
  return operationIsCandidateRefresh(item) && item?.state !== "resolved" && item?.status !== "resolved";
}

function operationIsActive(item) {
  return reviewOperationIsActive(operationStatus(item));
}

function operationSourceResumeMode(item) {
  const operation = reviewOperation(item) || {};
  return String(
    operation?.result?.source_resume?.mode
    || item?.resolution?.source_resume?.mode
    || "",
  ).toLowerCase();
}

function reviewActionLocked(item) {
  const status = operationStatus(item);
  return busySet.value.has(String(item?.review_id || ""))
    || operationIsActive(item)
    || item?.state === "processing"
    || ["processing", "source_unavailable_pending", "source_gone"].includes(sourceLifecycle(item))
    || (status === "completed" && !candidateRefreshNeedsConfirmation(item));
}

function reviewDeleteLocked(item) {
  return busySet.value.has(String(item?.review_id || ""))
    || operationIsActive(item)
    || item?.state === "processing"
    || sourceLifecycle(item) === "processing";
}

function operationTitle(item) {
  const status = operationStatus(item);
  const dismissing = operationMode(item) === "dismiss";
  if (status === "submitting") return dismissing ? "正在送出刪除要求" : "正在安全送出";
  if (["accepted", "queued"].includes(status)) return dismissing ? "刪除要求已送出，等待 Worker" : "已送出，等待 Worker";
  if (status === "running") {
    if (dismissing) return "Worker 正在移除這筆待辦";
    return candidateRefreshNeedsConfirmation(item) ? "Worker 正在核對作品與季度" : "Worker 正在安排後續處理";
  }
  if (status === "reconnecting") return "正在重新確認狀態";
  if (status === "unknown") return "操作仍在背景執行";
  if (status === "completed" && dismissing) return "這筆審核已刪除";
  if (status === "completed" && candidateRefreshNeedsConfirmation(item)) return "候選資料已更新";
  if (
    status === "completed"
    && item?.kind === "target_ambiguity"
    && ["redownload_queued", "waiting_download"].includes(operationSourceResumeMode(item))
  ) return "配對已確認，來源正在下載";
  if (status === "completed" && item?.kind === "target_ambiguity") return "配對已確認，等待字幕提取";
  if (status === "completed" && item?.kind === "asr_quality") return "已加入重新轉錄佇列";
  if (status === "completed" && item?.kind === "subtitle_quality") return "已加入重新翻譯佇列";
  if (status === "completed") return "操作已交給 Worker";
  if (status === "failed") return "處理未完成";
  return "";
}

function operationElapsed(item) {
  const operation = reviewOperation(item) || {};
  const startedAt = Number(operation.started_at || operation.requested_at || 0);
  return startedAt > 0 ? formatDuration(Math.max(0, Number(props.nowSeconds || 0) - startedAt)) : "";
}

function operationDescription(item) {
  const operation = reviewOperation(item) || {};
  const status = operationStatus(item);
  const dismissing = operationMode(item) === "dismiss";
  if (status === "submitting") return "請留在此頁片刻；送出成功後會自動顯示 Worker 狀態。";
  if (["accepted", "queued"].includes(status)) return "不需要返回清單確認；本頁會自動更新，也可以安全瀏覽其他頁面。";
  if (status === "running") {
    const elapsed = operationElapsed(item);
    return `${elapsed ? `已執行 ${elapsed}。` : ""}完成後本頁會自動顯示結果，請勿重複送出。`;
  }
  if (status === "reconnecting") return "暫時無法讀取最新進度；既有操作不會中斷，系統也不會重複送出。";
  if (status === "unknown") return "這個操作花費較久，WebUI 已保留操作識別碼且不會重複送出。";
  if (status === "completed" && dismissing) return "已從待辦移除並記錄為不再提醒；影片、字幕與 qBittorrent torrent 都沒有被刪除。";
  if (status === "completed" && candidateRefreshNeedsConfirmation(item)) return "候選影片已重新整理，請在下方確認正確作品與季度後再繼續。";
  if (
    status === "completed"
    && item?.kind === "target_ambiguity"
    && ["redownload_queued", "waiting_download"].includes(operationSourceResumeMode(item))
  ) return "原始來源已安全加入 qBittorrent；下載完成後會自動提取字幕，不需要再次操作。";
  if (status === "completed" && item?.kind === "target_ambiguity") return "Worker 已接受配對，字幕提取會在安全排程中執行；這不代表字幕已經匯入。";
  if (status === "completed") return "Worker 已接受這次修復並加入 AI 佇列；這不代表新字幕已完成。可前往 AI 字幕查看進度。";
  if (status === "failed") {
    return friendlyTechnicalMessage(operation.error, "Worker 沒有完成這次操作；請確認選擇後再重試，系統不會自動重送。");
  }
  return "";
}

function operationTone(item) {
  const status = operationStatus(item);
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "unknown") return "warn";
  return "running";
}

function operationButtonLabel(item) {
  const status = operationStatus(item);
  if (status === "submitting") return "正在送出…";
  if (["accepted", "queued"].includes(status)) return "等待 Worker…";
  if (status === "running") return "Worker 正在處理…";
  if (status === "reconnecting") return "正在確認狀態…";
  if (status === "unknown") return "仍在背景執行…";
  if (status === "completed" && operationMode(item) === "dismiss") return "已刪除待辦";
  if (status === "completed" && !candidateRefreshNeedsConfirmation(item)) return item?.kind === "target_ambiguity" ? "已確認配對" : "已排入修復";
  return actionLabel(item);
}

function operationCanReturnToInbox(item) {
  return operationStatus(item) === "completed" && !candidateRefreshNeedsConfirmation(item);
}

function reviewWasDismissed(item) {
  return Boolean(item?.dismissed || item?.resolution?.dismissed)
    || (operationStatus(item) === "completed" && operationMode(item) === "dismiss");
}

function handledStateLabel(item) {
  if (reviewWasDismissed(item)) return "已忽略";
  if (sourceLifecycle(item) === "source_gone") return "來源已失效，已結案";
  if (item?.kind === "target_ambiguity") return "配對已處理";
  if (["subtitle_quality", "asr_quality"].includes(String(item?.kind || ""))) return "已送修";
  return "已處理";
}

function openRelatedWork(item) {
  emit("open-work", {
    panel: item?.kind === "target_ambiguity" ? "downloads" : "queue",
    target: String(item?.diagnosis?.video || item?.target_key || item?.media_path || ""),
  });
}

function operationCommandId(item) {
  return String(reviewOperation(item)?.command_id || "");
}

function operationError(item) {
  return String(reviewOperation(item)?.error || "");
}

function stateLabel(item) {
  const commandStatus = operationStatus(item);
  if (item?.state === "resolved" || item?.status === "resolved") return handledStateLabel(item);
  if (commandStatus === "completed" && candidateRefreshNeedsConfirmation(item)) return "請確認候選";
  if (commandStatus === "completed") return handledStateLabel(item);
  const operationLabel = reviewOperationLabel(commandStatus);
  if (operationLabel) return operationLabel;
  if (item?.state === "processing") return "正在處理";
  return "等你處理";
}

function stateTone(item) {
  const commandStatus = operationStatus(item);
  if (item?.state === "resolved" || item?.status === "resolved") return "success";
  if (commandStatus === "completed" && candidateRefreshNeedsConfirmation(item)) return "warn";
  if (commandStatus === "completed") return "success";
  if (commandStatus === "failed") return "danger";
  if (reviewOperationIsActive(commandStatus) || item?.state === "processing") return "running";
  return item?.severity === "error" ? "danger" : "warn";
}

function mediaTitle(item) {
  return String(
    item?.media_title
    || item?.diagnosis?.series_title
    || item?.diagnosis?.torrent_name
    || fileName(item?.diagnosis?.video || item?.media_path || item?.target_key)
    || "未命名項目",
  );
}

function sourceLifecycle(item) {
  return String(
    item?.source_lifecycle
    || item?.diagnosis?.source_lifecycle
    || "",
  ).trim().toLowerCase();
}

function sourceLifecycleInfo(item) {
  if (item?.kind !== "target_ambiguity") return null;
  return {
    qbit_present: {
      tone: "success",
      title: "來源仍在 qBittorrent",
      text: "下載項目與來源仍可使用。確認正確作品與季度後，Worker 會直接重新提取字幕。",
    },
    source_files_present: {
      tone: "success",
      title: "qB 項目已移除，但下載檔仍在",
      text: "來源影片仍存在。確認正確作品與季度後，Worker 會使用現有檔案重新提取，不需要重新下載。",
    },
    redownload_available: {
      tone: "warn",
      title: "qB 項目已移除，但來源可以恢復",
      text: "原始種子網址仍有保存。確認正確作品與季度後，Worker 會重新下載來源，再自動提取字幕。",
    },
    processing: {
      tone: "running",
      title: "Worker 正在處理這筆來源",
      text: "操作已送出或字幕提取仍在執行；不需要重複按下按鈕，本頁會自動更新。",
    },
    source_unavailable_pending: {
      tone: "warn",
      title: "正在確認來源是否已完全移除",
      text: "qB、下載檔與可重新下載資料目前都找不到。Worker 會再次確認，確定無法恢復後自動移到「已處理」。",
    },
    source_gone: {
      tone: "neutral",
      title: "來源已不存在，這筆已自動結案",
      text: "qB 項目、下載檔與可恢復種子都不存在，因此沒有可以繼續提取的內容；稽核紀錄仍保留在「已處理」。",
    },
    unknown: {
      tone: "danger",
      title: "暫時無法確認來源狀態",
      text: "Worker 無法可靠讀取 qB 或來源儲存空間，因此不會自動結案，也不會猜測執行。",
    },
  }[sourceLifecycle(item)] || null;
}

function itemDescription(item) {
  const lifecycle = sourceLifecycleInfo(item);
  if (lifecycle) return lifecycle.text;
  return String(item?.description || item?.problem?.description || item?.summary || "需要確認後才能繼續處理。");
}

function itemMetric(item) {
  if (item?.kind === "target_ambiguity") {
    if (sourceLifecycle(item) === "source_gone") return "來源已移除";
    if (sourceLifecycle(item) === "redownload_available") return "可重新下載";
    if (sourceLifecycle(item) === "source_files_present") return "下載檔仍在";
    const count = Number(item?.candidate_count ?? candidateList(item).length);
    return count ? `${count} 個候選` : "尚無安全候選";
  }
  const indexes = availableIssueIndexes(item);
  if (indexes.length) return `${indexes.length} 行受影響`;
  const count = Number(item?.issue_count || issueLines(item).length);
  return count ? `${count} 個問題` : "需重新檢查";
}

function mediaFileInfo(item) {
  const value = item?.media_file || item?.diagnosis?.media_file;
  return value && Number(value.timestamp || 0) > 0 ? value : null;
}

function fileTimeText(fileInfo, subject = "影片", clarification = "") {
  if (!fileInfo || Number(fileInfo.timestamp || 0) <= 0) return "";
  const label = String(fileInfo.kind || "").toLowerCase() === "created"
    ? `${subject}建立時間`
    : `${subject}修改時間（建立時間不可用）`;
  const note = String(clarification || "").trim();
  return `${label}${note ? `，${note}` : ""}：${formatTime(fileInfo.timestamp)}`;
}

function formatTimelineTime(row) {
  if (!row?.available) return String(row?.unavailable || "時間未保存");
  if (row.precision === "date") {
    const date = new Date(Number(row.timestamp) * 1000).toLocaleDateString("zh-TW", {
      timeZone: "Asia/Taipei",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    return `${date}（來源網址僅保存日期）`;
  }
  return formatTime(row.timestamp);
}

function sourceTimeline(item) {
  const diagnosis = item?.diagnosis || {};
  const rows = [
    {
      key: "published",
      label: "種子發佈",
      timestamp: Number(diagnosis.source_published_at || 0),
      precision: String(diagnosis.source_published_precision || ""),
      unavailable: "來源網站未提供",
    },
    {
      key: "created",
      label: "種子建立",
      timestamp: Number(diagnosis.torrent_created_at || 0),
      unavailable: "qB 未提供或 torrent 已移除",
    },
    {
      key: "added",
      label: "加入 qB",
      timestamp: Number(diagnosis.torrent_added_at || 0),
      unavailable: "舊紀錄未保存",
    },
    {
      key: "completed",
      label: "下載完成",
      timestamp: Number(diagnosis.torrent_completed_at || 0),
      unavailable: "舊紀錄未保存",
    },
    {
      key: "review",
      label: "進入審核",
      timestamp: Number(item?.created_at || 0),
      unavailable: "時間未知",
    },
  ];
  return rows.map((row) => ({
    ...row,
    available: Number.isFinite(row.timestamp) && row.timestamp > 0,
  }));
}

function sourceSafetyText(item) {
  return {
    source_files_present: "不會刪除現有下載檔；確認季度後會直接重新提取。",
    redownload_available: "不會猜測匯入；確認季度後才會重新下載來源並提取。",
    processing: "操作已交給 Worker；請勿重複送出，本頁會自動更新。",
    source_unavailable_pending: "系統正在做第二次確認；確定無法恢復後才會自動結案。",
    source_gone: "沒有可處理的來源，因此未匯入字幕；這筆只保留稽核紀錄。",
    unknown: "來源狀態不明時保持封鎖，不匯入字幕也不自動結案。",
  }[sourceLifecycle(item)] || "不匯入字幕、不刪除 torrent，也不啟動替代下載。";
}

function candidateDateGuidance(item) {
  const diagnosis = item?.diagnosis || {};
  if (diagnosis.candidate_date_conflict) {
    const sourceYear = Number(diagnosis.source_release_year || 0);
    const candidateYears = (diagnosis.candidate_file_years || []).map(Number).filter(Boolean);
    const count = Number(diagnosis.date_rejected_candidate_count || 0);
    return {
      tone: "danger",
      title: "候選年份明顯不相符，已禁止選擇",
      text: sourceYear && candidateYears.length
        ? `來源是 ${sourceYear} 年，但候選影片只有 ${candidateYears.join("、")} 年。已排除 ${count || candidateYears.length} 個舊季度，請重新整理作品資料。`
        : "來源日期與所有候選影片相差太久，請重新整理作品資料後再比對。",
    };
  }
  const recommended = (item?.candidates || []).find((candidate) => candidate?.date_recommended);
  if (!recommended) return null;
  const days = Number(recommended.source_date_distance_days || 0);
  return {
    tone: "success",
    title: `日期最接近：${candidateLabel(recommended)}`,
    text: days < 1
      ? "候選影片日期與種子發佈日相同；系統已先選取，仍需你確認後才會匯入。"
      : `候選影片日期與種子發佈相差約 ${Math.round(days)} 天；系統已先選取，仍需你確認後才會匯入。`,
  };
}

function candidateDateText(candidate) {
  const days = Number(candidate?.source_date_distance_days);
  if (!Number.isFinite(days) || days < 0) return "";
  return days < 1 ? "與種子發佈同一天" : `與種子發佈相差約 ${Math.round(days)} 天`;
}

function candidatePath(candidate) {
  return String(candidate?.path || candidate?.series_path || "").trim();
}

function candidateList(item) {
  const unique = new Map();
  for (const candidate of item?.candidates || []) {
    if (candidate?.selectable === false) continue;
    const path = candidatePath(candidate);
    if (!path) continue;
    const previous = unique.get(path);
    if (!previous || Number(candidate?.score || 0) > Number(previous?.score || 0)) unique.set(path, candidate);
  }
  return [...unique.values()];
}

function candidateSeriesPath(value) {
  const normalized = String(value || "").replaceAll("\\", "/").replace(/\/+$/, "");
  const match = normalized.match(/^(.*)\/(?:Season\s+\d+|Specials)\/[^/]+$/i);
  return match?.[1] || normalized;
}

function candidateSeason(candidate) {
  const explicit = Number(candidate?.season);
  if (Number.isInteger(explicit) && explicit >= 0) return explicit;
  const match = candidatePath(candidate).replaceAll("\\", "/").match(/\/Season\s+(\d+)\//i);
  return match ? Number(match[1]) : 0;
}

function candidateEpisode(candidate) {
  const match = fileName(candidatePath(candidate)).match(/S(\d{1,3})E(\d{1,4})/i);
  return match ? `S${match[1].padStart(2, "0")}E${match[2].padStart(2, "0")}` : "";
}

function candidateLabel(candidate) {
  const season = candidateSeason(candidate);
  const seasonLabel = season === 0 ? "特別篇" : `第 ${season} 季`;
  return candidateEpisode(candidate) ? `${seasonLabel} · ${candidateEpisode(candidate)}` : seasonLabel;
}

function selectedPath(item) {
  const explicit = String(selectedPaths.value[item?.review_id] || "");
  const candidates = candidateList(item);
  if (explicit && candidates.some((candidate) => candidatePath(candidate) === explicit)) return explicit;
  const recommended = candidates.filter((candidate) => candidate?.date_recommended);
  if (recommended.length === 1) return candidatePath(recommended[0]);
  return candidates.length === 1 ? candidatePath(candidates[0]) : "";
}

function setSelectedPath(item, path) {
  selectedPaths.value = { ...selectedPaths.value, [item.review_id]: String(path || "") };
}

function selectedCandidate(item) {
  return candidateList(item).find((candidate) => candidatePath(candidate) === selectedPath(item)) || null;
}

function sourceId(item) {
  const recovery = item?.diagnosis?.recovery && typeof item.diagnosis.recovery === "object"
    ? item.diagnosis.recovery
    : {};
  const direct = selectedCandidate(item)?.source_id || recovery.source_id;
  if (direct) return String(direct);
  const diagnosed = [...new Set((item?.diagnosis?.bangumi_ids || []).map(String).filter(Boolean))];
  return diagnosed.length === 1 ? diagnosed[0] : "";
}

function issueLines(item) {
  const reports = Array.isArray(item?.diagnosis?.reports) ? item.diagnosis.reports : [];
  const unique = new Map();
  for (const report of reports) {
    for (const issue of report?.issues || []) {
      const indexes = [...new Set((issue?.indexes || []).map(Number).filter((value) => Number.isInteger(value) && value > 0))];
      const key = `${String(issue?.code || issue?.message || "unknown")}:${indexes.join(",")}`;
      if (!unique.has(key)) unique.set(key, { ...issue, indexes });
    }
  }
  return [...unique.values()];
}

function linePreviews(item) {
  return Array.isArray(item?.diagnosis?.line_previews) ? item.diagnosis.line_previews : [];
}

function availableIssueIndexes(item) {
  const explicit = item?.recommended_action?.indexes || item?.affected_indexes || [];
  const indexes = new Set(explicit.map(Number).filter((value) => Number.isInteger(value) && value > 0));
  for (const issue of issueLines(item)) {
    for (const value of issue.indexes || []) {
      const index = Number(value);
      if (Number.isInteger(index) && index > 0) indexes.add(index);
    }
  }
  return [...indexes].sort((left, right) => left - right);
}

function selectedLines(item) {
  const reviewId = String(item?.review_id || "");
  const explicit = selectedLineIndexes.value[reviewId];
  return explicit ? [...explicit] : availableIssueIndexes(item);
}

function qualityRequiresRetranscription(item) {
  if (item?.kind === "asr_quality") return true;
  if (String(item?.recommended_action?.action || "") === "ai.retranscribe") return true;
  return issueLines(item).some((issue) => [
    "asr_prompt_echo",
    "hallucination_text",
    "asr_low_confidence",
    "leading_gap",
  ].includes(String(issue?.code || "").trim().toLowerCase().replace(/-/g, "_")));
}

function toggleLine(item, index) {
  const reviewId = String(item.review_id);
  const current = new Set(selectedLines(item));
  if (current.has(index)) current.delete(index);
  else current.add(index);
  selectedLineIndexes.value = { ...selectedLineIndexes.value, [reviewId]: [...current].sort((a, b) => a - b) };
}

function setAllLines(item, checked) {
  selectedLineIndexes.value = {
    ...selectedLineIndexes.value,
    [item.review_id]: checked ? availableIssueIndexes(item) : [],
  };
}

function actionLabel(item) {
  return String(item?.recommended_action?.label || (
    item?.kind === "asr_quality" ? "重新辨識問題片段" : "使用日文快取重新翻譯"
  ));
}

function targetActionLabel(item) {
  return {
    redownload_available: "確認配對並重新下載來源",
    source_files_present: "確認配對並提取現有檔案",
    qbit_present: "確認配對並提取字幕",
  }[sourceLifecycle(item)] || "確認配對並重新提取";
}

function targetConfirmationSafety(item) {
  return {
    redownload_available: "會使用已保存的原始種子網址重新加入 qBittorrent；下載完成後自動提取字幕。不會刪除影片，也不會搜尋或啟動其他替代來源。",
    source_files_present: "會直接從保留的來源檔案提取字幕；不會重新下載、不會刪除影片或 torrent，也不會啟動替代來源。",
    qbit_present: "會使用 qBittorrent 中現有的來源提取字幕；不會刪除影片或 torrent，也不會啟動替代來源。",
  }[sourceLifecycle(item)] || "Worker 會先重新確認原始來源，再安全提取字幕；不會刪除影片或 torrent，也不會啟動替代來源。";
}

function canSelectBatch(item) {
  if (!item?.batch_eligible || item?.state !== "needs_action" || busySet.value.has(String(item.review_id))) return false;
  if (!batchReviewIds.value.length) return true;
  return String(item?.recommended_action?.action || "safe.default") === selectedBatchAction.value;
}

function toggleBatch(item) {
  const reviewId = String(item.review_id);
  if (!batchReviewIds.value.includes(reviewId) && !canSelectBatch(item)) return;
  batchReviewIds.value = batchReviewIds.value.includes(reviewId)
    ? batchReviewIds.value.filter((value) => value !== reviewId)
    : [...batchReviewIds.value, reviewId];
}

function toggleAllVisible() {
  const selectableIds = visibleCompatibleBatchItems.value.map((item) => String(item.review_id));
  if (allVisibleBatchSelected.value) {
    batchReviewIds.value = batchReviewIds.value.filter((value) => !selectableIds.includes(value));
  } else {
    batchReviewIds.value = [...new Set([...batchReviewIds.value, ...selectableIds])];
  }
}

function openDetail(item) {
  selectedReviewId.value = String(item.review_id || "");
  window.localStorage.setItem("review-selected-id", selectedReviewId.value);
  detailOpen.value = true;
  emit("select", selectedReviewId.value);
  const indexes = availableIssueIndexes(item);
  if (indexes.length && !selectedLineIndexes.value[selectedReviewId.value]) {
    selectedLineIndexes.value = { ...selectedLineIndexes.value, [selectedReviewId.value]: indexes };
  }
}

function closeDetail() {
  detailOpen.value = false;
  if (!items.value.some((item) => String(item.review_id) === selectedReviewId.value)) {
    const next = items.value[0];
    selectedReviewId.value = String(next?.review_id || "");
    window.localStorage.setItem("review-selected-id", selectedReviewId.value);
  }
  returnFocus?.focus?.();
}

function continueAfterOperation() {
  const currentId = selectedReviewId.value;
  const next = items.value.find((item) => String(item.review_id) !== currentId) || null;
  if (!isDesktop) {
    detailOpen.value = false;
    selectedReviewId.value = String(next?.review_id || "");
    window.localStorage.setItem("review-selected-id", selectedReviewId.value);
    return;
  }
  if (next) {
    openDetail(next);
    return;
  }
  selectedReviewId.value = "";
  detailOpen.value = false;
  window.localStorage.removeItem("review-selected-id");
}

function emitQuery(change) {
  batchReviewIds.value = [];
  emit("query", { ...props.query, ...change });
}

function setState(state) {
  if (state !== props.query.state) emitQuery({ state });
}

function setKind(kind) {
  if (kind !== props.query.kind) emitQuery({ kind });
}

function setSort(sort) {
  if (sort !== props.query.sort) emitQuery({ sort });
}

function openConfirmation(payload, event) {
  returnFocus = event?.currentTarget || document.activeElement;
  confirmation.value = payload;
  nextTick(() => confirmButton.value?.focus());
}

function confirmTarget(item, event) {
  const candidate = selectedCandidate(item);
  const path = selectedPath(item);
  if (!candidate || !path || !sourceId(item)) return;
  openConfirmation({
    mode: "single",
    title: `確認配對到${candidateLabel(candidate)}`,
    description: fileName(path),
    safety: targetConfirmationSafety(item),
    review: item,
    body: {
      candidate_path: path,
      source_id: sourceId(item),
      series_id: String(candidate.series_id || ""),
    },
  }, event);
}

function confirmQuality(item, event) {
  const indexes = selectedLines(item);
  const recommended = String(item?.recommended_action?.action || "");
  let body;
  if (qualityRequiresRetranscription(item)) {
    body = {
      action: "ai.retranscribe",
      target: String(item?.diagnosis?.video || item?.target_key || ""),
    };
  } else if (item.kind === "subtitle_quality" && indexes.length) {
    body = {
      action: "ai.retranslate_lines",
      target: String(item?.diagnosis?.video || item?.target_key || ""),
      indexes,
    };
  } else {
    body = {
      action: recommended || (item.kind === "asr_quality" ? "ai.retranscribe" : "ai.retranslate"),
      target: String(item?.diagnosis?.video || item?.target_key || ""),
    };
  }
  openConfirmation({
    mode: "single",
    title: qualityRequiresRetranscription(item)
      ? "重新轉錄並修復日文字幕"
      : (indexes.length ? `修復 ${indexes.length} 行字幕` : actionLabel(item)),
    description: mediaTitle(item),
    safety: "目前失敗輸出會先封存；只有重新通過品質檢查才會發布，不會覆蓋既有良好字幕。",
    review: item,
    body,
  }, event);
}

function confirmDeleteReview(item, event) {
  if (item?.kind !== "target_ambiguity" || reviewDeleteLocked(item)) return;
  openConfirmation({
    mode: "single",
    title: "刪除這筆審核？",
    description: mediaTitle(item),
    safety: "只會從待辦移除並記錄為不再提醒；不會刪除影片、字幕或 qBittorrent torrent。同一個原始來源不會再次出現，新的來源仍可正常建立審核。",
    confirmLabel: "確認刪除",
    danger: true,
    review: item,
    body: { action: "review.dismiss" },
  }, event);
}

function confirmBatch(event) {
  if (!batchReviewIds.value.length) return;
  openConfirmation({
    mode: "batch",
    title: `安全處理 ${batchReviewIds.value.length} 個項目`,
    description: "只會執行系統已判定可逆且高信心的修復；仍有歧義的項目不會執行。",
    safety: "影片與 torrent 永遠不會被刪除，未通過品質檢查的字幕也不會發布。",
    reviewIds: [...batchReviewIds.value],
    action: selectedBatchAction.value,
  }, event);
}

function submitConfirmation() {
  const pending = confirmation.value;
  confirmation.value = null;
  if (!pending) return;
  if (pending.mode === "batch") {
    emit("batch-resolve", { reviewIds: pending.reviewIds, action: pending.action });
    batchReviewIds.value = [];
    return;
  }
  emit("resolve", { review: pending.review, body: pending.body });
}

function autoRecoverTarget(item) {
  emit("resolve", { review: item, body: { action: "target.auto_rebuild_candidates" } });
}

function releaseSeriesTitle(item) {
  let value = String(item?.diagnosis?.torrent_name || item?.summary || "").trim();
  value = value.replace(/^(?:\[[^\]]+\]\s*)+/, "").split("[")[0].trim();
  value = value.replace(/\b(?:season|s)\s*\d+\s*$/i, "").replace(/\s+\d+\s*$/, "").trim();
  return value || String(item?.diagnosis?.series_title || "").trim();
}

function suggestedSeason(item) {
  const explicit = Number(item?.diagnosis?.season ?? item?.diagnosis?.recovery?.season);
  if (Number.isInteger(explicit) && explicit >= 0 && explicit <= 99) return explicit;
  const value = String(item?.diagnosis?.torrent_name || item?.summary || "").replace(/^(?:\[[^\]]+\]\s*)+/, "").split("[")[0].trim();
  const match = value.match(/\b(?:season|s)\s*(\d{1,2})\s*$/i) || value.match(/\s+(\d{1,2})\s*$/);
  return match ? Number(match[1]) : 1;
}

function recoveryState(item) {
  return props.recoveryByReview?.[item?.review_id] || { items: [], loading: false, error: "" };
}

function recoveryQuery(item) {
  return String(recoveryQueries.value[item.review_id] ?? releaseSeriesTitle(item));
}

function toggleRecovery(item) {
  const reviewId = String(item.review_id);
  const opening = !recoveryOpen.value[reviewId];
  recoveryOpen.value = { ...recoveryOpen.value, [reviewId]: opening };
  if (!opening) return;
  if (!(reviewId in selectedSeasons.value)) {
    selectedSeasons.value = { ...selectedSeasons.value, [reviewId]: suggestedSeason(item) };
  }
  const query = recoveryQuery(item).trim();
  if (query && !(recoveryState(item).items || []).length) emit("search-series", { review: item, query });
}

function searchRecovery(item) {
  const query = recoveryQuery(item).trim();
  if (!query) return;
  selectedSeriesIds.value = { ...selectedSeriesIds.value, [item.review_id]: "" };
  emit("search-series", { review: item, query });
}

function rebuildTargetCandidates(item) {
  const seriesId = String(selectedSeriesIds.value[item.review_id] || "");
  const season = Number(selectedSeasons.value[item.review_id]);
  if (!seriesId || !Number.isInteger(season) || season < 0 || season > 99) return;
  emit("resolve", {
    review: item,
    body: { action: "target.rebuild_candidates", series_id: seriesId, season },
  });
}

function handleKeydown(event) {
  if (event.key !== "Escape") return;
  if (confirmation.value) confirmation.value = null;
  else if (detailOpen.value && !isDesktop) closeDetail();
}

function updateViewport() {
  isDesktop = !mediaQuery?.matches;
  if (isDesktop && selectedReviewId.value) detailOpen.value = true;
}

watch(() => props.query.search, (value) => {
  const normalized = String(value || "");
  if (normalized !== searchInput.value) searchInput.value = normalized;
});

watch(searchInput, (value) => {
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    if (String(props.query.search || "") !== value) emitQuery({ search: value.trim() });
  }, 300);
});

watch(items, (nextItems) => {
  const ids = new Set(nextItems.map((item) => String(item.review_id)));
  batchReviewIds.value = batchReviewIds.value.filter((value) => ids.has(value));
  if (selectedReviewId.value && ids.has(selectedReviewId.value)) return;
  if (selectedReviewId.value && detailOpen.value && reviewOperation(selectedItem.value)) return;
  const saved = window.localStorage.getItem("review-selected-id") || "";
  const first = nextItems.find((item) => String(item.review_id) === saved) || nextItems[0];
  selectedReviewId.value = String(first?.review_id || "");
  if (first) {
    if (isDesktop) detailOpen.value = true;
    // On mobile the detail drawer can already be open when polling, searching,
    // or resolving an item replaces the visible row. Load the newly selected
    // review as well; otherwise the compact list summary is rendered as if it
    // were complete detail data (missing candidates and source timestamps).
    if (isDesktop || detailOpen.value) emit("select", selectedReviewId.value);
  }
}, { immediate: true });

onMounted(() => {
  mediaQuery = window.matchMedia("(max-width: 900px)");
  updateViewport();
  mediaQuery.addEventListener?.("change", updateViewport);
  window.addEventListener("keydown", handleKeydown);
});

onUnmounted(() => {
  if (searchTimer) window.clearTimeout(searchTimer);
  mediaQuery?.removeEventListener?.("change", updateViewport);
  window.removeEventListener("keydown", handleKeydown);
});
</script>

<template>
  <section class="page-panel review-center" aria-labelledby="review-center-title">
    <header class="review-page-header">
      <div class="review-page-title">
        <h1 id="review-center-title">例外處理</h1>
        <p>只有無法安全自動決定的項目才會出現在這裡</p>
      </div>
      <button type="button" class="review-refresh" :disabled="loading" @click="emit('refresh')">
        {{ loading ? "更新中…" : "重新整理" }}
      </button>
    </header>

    <nav class="review-state-tabs" aria-label="審核狀態">
      <button type="button" :class="{ active: query.state === 'needs_action' }" @click="setState('needs_action')">
        等你處理 <b>{{ stateCounts.needs_action || 0 }}</b>
      </button>
      <button type="button" :class="{ active: query.state === 'processing' }" @click="setState('processing')">
        處理中 <b>{{ stateCounts.processing || 0 }}</b>
      </button>
      <button type="button" :class="{ active: query.state === 'resolved' }" @click="setState('resolved')">
        已處理 <b>{{ stateCounts.resolved || 0 }}</b>
      </button>
    </nav>

    <div class="review-toolbar">
      <label class="review-search">
        <span aria-hidden="true">⌕</span>
        <input
          v-model="searchInput"
          type="search"
          aria-label="搜尋人工審核"
          placeholder="搜尋作品、檔名或問題"
          autocomplete="off"
        />
      </label>
      <details class="review-filter-details">
        <summary>
          <span>篩選與排序</span>
          <b v-if="query.kind || query.sort !== 'priority'">已套用</b>
        </summary>
        <div class="review-filter-fields">
          <label>
            <span>類型</span>
            <select :value="query.kind" @change="setKind($event.target.value)">
              <option value="">全部類型</option>
              <option value="target_ambiguity">來源配對</option>
              <option value="subtitle_quality">翻譯品質</option>
              <option value="asr_quality">轉錄品質</option>
            </select>
          </label>
          <label>
            <span>排序</span>
            <select :value="query.sort" @change="setSort($event.target.value)">
              <option value="priority">優先處理</option>
              <option value="latest">最新發現</option>
              <option value="oldest">等待最久</option>
            </select>
          </label>
        </div>
      </details>
    </div>

    <div v-if="batchReviewIds.length" class="review-batch-bar" role="status">
      <span>已選 {{ batchReviewIds.length }} 個安全項目</span>
      <button type="button" @click="batchReviewIds = []">取消選取</button>
      <button type="button" class="primary-review-action" @click="confirmBatch($event)">安全批次處理</button>
    </div>

    <div class="review-workbench">
      <section class="review-inbox" aria-label="審核待辦清單">
        <div v-if="items.length" class="review-inbox-heading">
          <label>
            <input
              type="checkbox"
              :checked="allVisibleBatchSelected"
              :disabled="!items.some(canSelectBatch)"
              aria-label="選取目前頁面可安全批次處理的項目"
              @change="toggleAllVisible"
            />
          </label>
          <span>顯示 {{ items.length }} / {{ payload.total || items.length }}</span>
        </div>

        <div v-if="loading && !items.length" class="review-list-skeleton" aria-label="正在載入">
          <i v-for="index in 6" :key="index"></i>
        </div>
        <div v-else-if="!items.length" class="review-empty-state">
          <span>✓</span>
          <strong>{{ query.state === 'resolved' ? '沒有已處理紀錄' : '這個清單已處理完畢' }}</strong>
          <p>變更篩選條件，或等待 Worker 產生新的審核項目。</p>
        </div>

        <div v-else class="review-inbox-list" role="list">
          <article
            v-for="item in items"
            :key="item.review_id"
            :class="['review-row', { selected: selectedReviewId === item.review_id }]"
            role="listitem"
          >
            <label class="review-row-check" @click.stop>
              <input
                type="checkbox"
                :checked="batchReviewIds.includes(String(item.review_id))"
                :disabled="!canSelectBatch(item) && !batchReviewIds.includes(String(item.review_id))"
                :aria-label="`選取 ${mediaTitle(item)}`"
                @change="toggleBatch(item)"
              />
            </label>
            <button type="button" class="review-row-main" @click="openDetail(item)">
              <span :class="['review-status-dot', stateTone(item)]" aria-hidden="true"></span>
              <span class="review-row-copy">
                <span class="review-row-topline">
                  <span :class="['review-kind-chip', item.kind]">{{ kindLabel(item.kind) }}</span>
                </span>
                <strong>{{ mediaTitle(item) }}</strong>
                <span v-if="item.kind !== 'target_ambiguity' && operationStatus(item) !== 'failed'">{{ itemDescription(item) }}</span>
                <span class="review-row-meta">
                  <b>{{ stateLabel(item) }}</b>
                  <i>{{ itemMetric(item) }}</i>
                  <i v-if="item.duplicate_count > 1">合併 {{ item.duplicate_count }} 筆</i>
                </span>
              </span>
              <span class="review-row-arrow" aria-hidden="true">›</span>
            </button>
          </article>
        </div>

        <button
          v-if="payload.next_cursor"
          type="button"
          class="review-load-more"
          :disabled="loading"
          @click="emit('load-more')"
        >
          {{ loading ? "載入中…" : "載入更多" }}
        </button>
      </section>

      <aside :class="['review-detail-panel', { open: detailOpen }]" aria-label="審核詳情">
        <div v-if="detailLoadingSet.has(selectedReviewId) && !detailsByReview[selectedReviewId]" class="review-detail-skeleton">
          <i></i><i></i><i></i><i></i>
        </div>
        <template v-else-if="selectedItem">
          <div class="review-detail-content">
            <header class="review-detail-header">
            <button type="button" class="review-detail-back" aria-label="返回審核清單" @click="closeDetail">‹</button>
            <div class="review-poster" aria-hidden="true">
              <img v-if="selectedItem.artwork_url" :src="selectedItem.artwork_url" alt="" />
              <span v-else>字</span>
            </div>
            <div>
              <span :class="['review-kind-chip', selectedItem.kind]">{{ kindLabel(selectedItem.kind) }}</span>
              <h2>{{ mediaTitle(selectedItem) }}</h2>
              <p v-if="selectedItem.kind !== 'target_ambiguity' && !reviewOperation(selectedItem)">{{ itemDescription(selectedItem) }}</p>
            </div>
            <span :class="['review-detail-state', stateTone(selectedItem)]">{{ stateLabel(selectedItem) }}</span>
          </header>

          <details class="review-meta-details review-diagnostics">
            <summary>
              <span>詳細資料</span>
              <small title="來源時間、封鎖狀態與技術診斷">需要時再查看</small>
            </summary>
            <div class="review-meta-details-body">
          <section v-if="selectedItem.kind === 'target_ambiguity'" class="review-source-timeline">
            <header>
              <span class="section-label">來源時間</span>
              <h3>這個字幕種子是什麼時候出現的</h3>
            </header>
            <dl>
              <template v-for="row in sourceTimeline(selectedItem)" :key="row.key">
                <dt>{{ row.label }}</dt>
                <dd :class="{ unavailable: !row.available }">
                  {{ formatTimelineTime(row) }}
                </dd>
              </template>
            </dl>
            <p>來源發佈、torrent 建立、加入 qB 與下載完成是四種不同時間；下方候選卡則是本地影片檔案時間。</p>
            <aside
              v-if="candidateDateGuidance(selectedItem)"
              :class="['review-date-guidance', candidateDateGuidance(selectedItem).tone]"
              role="status"
            >
              <strong>{{ candidateDateGuidance(selectedItem).title }}</strong>
              <span>{{ candidateDateGuidance(selectedItem).text }}</span>
            </aside>
          </section>

          <section
            v-if="sourceLifecycleInfo(selectedItem)"
            :class="['review-source-lifecycle', sourceLifecycleInfo(selectedItem).tone]"
            role="status"
            aria-live="polite"
          >
            <span class="review-source-lifecycle-mark" aria-hidden="true">
              {{ sourceLifecycle(selectedItem) === 'source_gone' ? '✓' : sourceLifecycle(selectedItem) === 'processing' ? '↻' : 'i' }}
            </span>
            <div>
              <strong>{{ sourceLifecycleInfo(selectedItem).title }}</strong>
              <p>{{ sourceLifecycleInfo(selectedItem).text }}</p>
            </div>
          </section>

          <section class="review-safety-note">
            <strong>{{ selectedItem.kind === 'target_ambiguity' ? '目前保持封鎖' : '不合格字幕未發布' }}</strong>
            <span v-if="selectedItem.kind === 'target_ambiguity'">{{ sourceSafetyText(selectedItem) }}</span>
            <span v-else>既有良好字幕不會被覆蓋，修復結果必須重新通過品質檢查。</span>
            <small v-if="mediaFileInfo(selectedItem)" class="file-time-note">
              {{ fileTimeText(mediaFileInfo(selectedItem)) }}
            </small>
          </section>

          <section
            v-if="selectedItem.kind === 'target_ambiguity' && candidateList(selectedItem).length"
            class="review-candidate-technical"
          >
            <h3>候選技術資料</h3>
            <article v-for="candidate in candidateList(selectedItem)" :key="`technical-${candidatePath(candidate)}`">
              <strong>{{ candidateLabel(candidate) }} · {{ fileName(candidatePath(candidate)) }}</strong>
              <small>{{ candidateSeriesPath(candidatePath(candidate)) }}</small>
              <small v-if="candidate.file_info?.timestamp">
                {{ fileTimeText(candidate.file_info, '本地影片', '不是種子發佈時間') }}
              </small>
              <small v-if="candidateDateText(candidate)">{{ candidateDateText(candidate) }}</small>
              <small v-if="candidate.reasons?.length">
                {{ [...new Set(candidate.reasons.map(candidateEvidenceLabel))].join(' · ') }}
              </small>
            </article>
          </section>

          <dl class="review-technical-grid">
            <dt>更新時間</dt><dd>{{ formatTime(selectedItem.updated_at) }}</dd>
            <dt>審核 ID</dt><dd>{{ selectedItem.review_id }}</dd>
            <dt>目標</dt><dd>{{ selectedItem.diagnosis?.video || selectedItem.target_key || selectedItem.media_path || '—' }}</dd>
            <dt v-if="operationCommandId(selectedItem)">操作 ID</dt><dd v-if="operationCommandId(selectedItem)">{{ operationCommandId(selectedItem) }}</dd>
            <dt v-if="operationError(selectedItem)">上次錯誤</dt><dd v-if="operationError(selectedItem)">{{ friendlyTechnicalMessage(operationError(selectedItem)) }}</dd>
          </dl>
            </div>
          </details>

          <section
            v-if="reviewOperation(selectedItem)"
            :class="['review-operation-status', operationTone(selectedItem)]"
            data-testid="review-operation-status"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <span v-if="operationIsActive(selectedItem)" class="operation-spinner" aria-hidden="true"></span>
            <span v-else class="review-operation-mark" aria-hidden="true">
              {{ operationStatus(selectedItem) === 'completed' ? '✓' : '!' }}
            </span>
            <div>
              <strong>{{ operationTitle(selectedItem) }}</strong>
              <p>{{ operationDescription(selectedItem) }}</p>
              <small v-if="operationIsActive(selectedItem)">可以安全離開此頁，背景操作不會中斷。</small>
              <small v-else-if="operationStatus(selectedItem) === 'failed'">修正選擇後可直接在本頁重試。</small>
            </div>
            <div v-if="operationCanReturnToInbox(selectedItem)" class="review-operation-actions">
              <button v-if="operationMode(selectedItem) !== 'dismiss'" type="button" class="primary-review-action" @click="openRelatedWork(selectedItem)">
                {{ selectedItem.kind === 'target_ambiguity' ? '查看字幕提取狀態' : '查看 AI 處理進度' }}
              </button>
              <button type="button" @click="continueAfterOperation">返回待辦清單</button>
            </div>
          </section>

          <template v-if="selectedItem.kind === 'target_ambiguity'">
            <section class="review-detail-section">
              <header>
                <div>
                  <span class="section-label">候選影片</span>
                  <h3>{{ candidateList(selectedItem).length ? '選擇正確作品與季度' : '尚未找到安全候選' }}</h3>
                </div>
                <b>{{ candidateList(selectedItem).length }}</b>
              </header>

              <fieldset v-if="candidateList(selectedItem).length" class="review-candidate-list">
                <legend class="sr-only">候選影片</legend>
                <label
                  v-for="candidate in candidateList(selectedItem)"
                  :key="candidatePath(candidate)"
                  :class="{ selected: selectedPath(selectedItem) === candidatePath(candidate) }"
                >
                  <input
                    type="radio"
                    :name="`candidate-${selectedItem.review_id}`"
                    :value="candidatePath(candidate)"
                    :checked="selectedPath(selectedItem) === candidatePath(candidate)"
                    @change="setSelectedPath(selectedItem, candidatePath(candidate))"
                  />
                  <span class="review-candidate-copy">
                    <span>
                      <strong>{{ candidateLabel(candidate) }}</strong>
                      <b v-if="candidate.date_recommended" class="date-match-badge">日期最接近</b>
                      <b v-if="candidate.confidence">{{ Math.round(Number(candidate.confidence) * 100) }}%</b>
                    </span>
                    <span>{{ fileName(candidatePath(candidate)) }}</span>
                  </span>
                </label>
              </fieldset>

              <div v-else class="review-no-candidate">
                <span class="review-no-candidate-icon">?</span>
                <strong>{{ selectedItem.diagnosis?.candidate_date_conflict ? '現有候選年份不相符' : '先整理作品資料，再重新比對' }}</strong>
                <p v-if="selectedItem.diagnosis?.candidate_date_conflict">這些候選來自明顯不同年份，系統已禁止選擇，避免把字幕放進舊季度。</p>
                <p v-else>Worker 只會在作品、季度與實際集數能唯一確認時建立候選，不接受任意路徑猜測。</p>
                <button
                  type="button"
                  class="primary-review-action"
                  :disabled="reviewActionLocked(selectedItem)"
                  @click="autoRecoverTarget(selectedItem)"
                >
                  {{ reviewActionLocked(selectedItem) ? operationButtonLabel(selectedItem) : '自動整理並重新比對' }}
                </button>
                <small v-if="recoveryState(selectedItem).error" class="recovery-error">
                  {{ friendlyTechnicalMessage(recoveryState(selectedItem).error, '自動比對尚未完成，請使用進階選擇。') }}
                </small>
              </div>

              <details class="review-advanced" :open="recoveryOpen[selectedItem.review_id]">
                <summary @click.prevent="toggleRecovery(selectedItem)">進階：手動指定作品與季度</summary>
                <form class="review-recovery-search" @submit.prevent="searchRecovery(selectedItem)">
                  <label>
                    <span>作品名稱</span>
                    <input
                      type="search"
                      :value="recoveryQuery(selectedItem)"
                      placeholder="例如 Bofuri"
                      @input="recoveryQueries = { ...recoveryQueries, [selectedItem.review_id]: $event.target.value }"
                    />
                  </label>
                  <button type="submit" :disabled="recoveryState(selectedItem).loading || !recoveryQuery(selectedItem).trim()">
                    {{ recoveryState(selectedItem).loading ? '搜尋中…' : '搜尋作品' }}
                  </button>
                </form>
                <fieldset v-if="recoveryState(selectedItem).items?.length" class="review-series-results">
                  <legend>搜尋結果</legend>
                  <label v-for="profile in recoveryState(selectedItem).items" :key="profile.series_id">
                    <input
                      type="radio"
                      :name="`series-${selectedItem.review_id}`"
                      :value="profile.series_id"
                      :checked="selectedSeriesIds[selectedItem.review_id] === profile.series_id"
                      :disabled="!profile.mikan_bangumi_id"
                      @change="selectedSeriesIds = { ...selectedSeriesIds, [selectedItem.review_id]: profile.series_id }"
                    />
                    <span>
                      <strong>{{ profile.canonical_title || fileName(profile.local_path) }}</strong>
                      <small>{{ fileName(profile.local_path) }}</small>
                      <small v-if="!profile.mikan_bangumi_id">尚無來源識別碼，不能安全套用</small>
                    </span>
                  </label>
                </fieldset>
                <div v-if="selectedSeriesIds[selectedItem.review_id]" class="review-recovery-submit">
                  <label>
                    <span>季度</span>
                    <select
                      :value="selectedSeasons[selectedItem.review_id]"
                      @change="selectedSeasons = { ...selectedSeasons, [selectedItem.review_id]: Number($event.target.value) }"
                    >
                      <option :value="0">特別篇</option>
                      <option v-for="season in 20" :key="season" :value="season">第 {{ season }} 季</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    class="primary-review-action"
                    :disabled="reviewActionLocked(selectedItem)"
                    @click="rebuildTargetCandidates(selectedItem)"
                  >
                    {{ reviewActionLocked(selectedItem) ? operationButtonLabel(selectedItem) : '建立安全候選' }}
                  </button>
                </div>
              </details>
            </section>
          </template>

          <template v-else>
            <section class="review-detail-section">
              <header>
                <div>
                  <span class="section-label">問題摘要</span>
                  <h3>{{ issueLines(selectedItem).length ? `${issueLines(selectedItem).length} 個品質問題` : '需要重新檢查字幕' }}</h3>
                </div>
                <b>{{ availableIssueIndexes(selectedItem).length }} 行</b>
              </header>
              <ul v-if="issueLines(selectedItem).length" class="review-issue-list">
                <li v-for="(issue, index) in issueLines(selectedItem)" :key="`${issue.code}-${index}`">
                  <span>!</span>
                  <div>
                    <strong>{{ qualityIssueLabel(issue) }}</strong>
                    <small v-if="issue.indexes?.length">字幕行 {{ issue.indexes.join('、') }}</small>
                  </div>
                </li>
              </ul>
            </section>

            <section
              v-if="linePreviews(selectedItem).length && !qualityRequiresRetranscription(selectedItem)"
              class="review-detail-section review-lines-section"
            >
              <header>
                <div>
                  <span class="section-label">字幕前後對照</span>
                  <h3>只修復有問題的行</h3>
                </div>
                <label class="review-select-all-lines">
                  <input
                    type="checkbox"
                    :checked="selectedLines(selectedItem).length === availableIssueIndexes(selectedItem).length"
                    @change="setAllLines(selectedItem, $event.target.checked)"
                  />
                  全選
                </label>
              </header>
              <div class="review-line-list">
                <label v-for="line in linePreviews(selectedItem)" :key="line.index" class="review-line-row">
                  <input
                    type="checkbox"
                    :checked="selectedLines(selectedItem).includes(Number(line.index))"
                    @change="toggleLine(selectedItem, Number(line.index))"
                  />
                  <b>#{{ line.index }}</b>
                  <span class="review-line-time">{{ line.timing || '時間未知' }}</span>
                  <span class="review-line-source"><small>日文原文</small>{{ line.source_ja || '—' }}</span>
                  <span class="review-line-output"><small>目前中文</small>{{ line.output_zh || '—' }}</span>
                  <span class="review-line-problem">
                    {{ (line.issue_codes || []).map((code) => qualityIssueLabel({ code })).join('、') }}
                  </span>
                </label>
              </div>
            </section>
          </template>

          <details
            v-if="selectedItem.kind === 'target_ambiguity' && selectedItem.state !== 'resolved'"
            class="review-secondary-actions"
          >
            <summary>更多操作</summary>
            <div class="review-secondary-action-body">
              <div>
                <strong>不再處理這筆待辦</strong>
                <p>只會移除這筆人工審核並記住不要再次提醒；影片、字幕與 qBittorrent torrent 都不會被刪除。</p>
              </div>
              <button
                type="button"
                class="review-delete-action danger"
                :disabled="reviewDeleteLocked(selectedItem)"
                @click="confirmDeleteReview(selectedItem, $event)"
              >
                {{ operationMode(selectedItem) === 'dismiss' && reviewDeleteLocked(selectedItem) ? operationButtonLabel(selectedItem) : '刪除這筆審核' }}
              </button>
            </div>
          </details>

          </div>
          <footer v-if="selectedItem.state !== 'resolved'" class="review-detail-actions">
            <span v-if="operationIsActive(selectedItem)">不必離開此頁，處理狀態與結果會自動更新。</span>
            <span v-else-if="operationCanReturnToInbox(selectedItem)">後續工作已排入佇列，可直接查看處理進度。</span>
            <div class="review-detail-action-buttons">
              <button
                v-if="selectedItem.kind === 'target_ambiguity' && candidateList(selectedItem).length"
                type="button"
                class="primary-review-action"
                :disabled="reviewActionLocked(selectedItem) || !selectedPath(selectedItem) || !sourceId(selectedItem)"
                @click="confirmTarget(selectedItem, $event)"
              >
                {{ reviewActionLocked(selectedItem) ? operationButtonLabel(selectedItem) : (selectedPath(selectedItem) ? targetActionLabel(selectedItem) : '請先選擇候選影片') }}
              </button>
              <button
                v-else-if="selectedItem.kind !== 'target_ambiguity'"
                type="button"
                class="primary-review-action"
                :disabled="reviewActionLocked(selectedItem) || (!qualityRequiresRetranscription(selectedItem) && availableIssueIndexes(selectedItem).length > 0 && !selectedLines(selectedItem).length)"
                @click="confirmQuality(selectedItem, $event)"
              >
                {{ reviewActionLocked(selectedItem) ? operationButtonLabel(selectedItem) : actionLabel(selectedItem) }}
              </button>
            </div>
          </footer>
        </template>
        <div v-else class="review-detail-placeholder">
          <span>✓</span>
          <strong>選擇一個審核項目</strong>
          <p>左側保留快速待辦清單，完整候選、字幕對照與修復操作會顯示在這裡。</p>
        </div>
      </aside>
    </div>

    <div v-if="confirmation" class="review-confirm-backdrop" @click.self="confirmation = null">
      <section class="review-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="review-confirm-title">
        <span :class="['review-confirm-icon', { danger: confirmation.danger }]">{{ confirmation.danger ? '!' : '✓' }}</span>
        <h2 id="review-confirm-title">{{ confirmation.title }}</h2>
        <p>{{ confirmation.description }}</p>
        <div :class="['review-confirm-safety', { danger: confirmation.danger }]">
          <strong>{{ confirmation.danger ? '刪除範圍' : '安全範圍' }}</strong>
          <span>{{ confirmation.safety }}</span>
        </div>
        <footer>
          <button type="button" @click="confirmation = null">取消</button>
          <button ref="confirmButton" type="button" :class="['primary-review-action', { danger: confirmation.danger }]" @click="submitConfirmation">
            {{ confirmation.confirmLabel || '確認並送出' }}
          </button>
        </footer>
      </section>
    </div>
  </section>
</template>
