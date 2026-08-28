export const taskNodeLabels = {
  input: "輸入檢查",
  transcribe: "語音轉錄",
  translate: "翻譯",
  output: "字幕輸出",
};

export const statusLabels = {
  Queued: "等待中",
  Running: "處理中",
  Success: "完成",
  Failed: "失敗",
  Paused: "已暫停",
  Skipped: "已略過",
};

export const rawStatusLabels = {
  queued: "等待中",
  running: "處理中",
  failed_retry: "失敗，可重試",
  review: "等待人工審核",
  paused: "已暫停",
  skipped: "已略過",
  done: "完成",
};

export const reviewOperationLabels = {
  submitting: "送出中",
  accepted: "等待 Worker",
  queued: "等待 Worker",
  running: "正在處理",
  reconnecting: "確認狀態中",
  unknown: "背景執行中",
  completed: "已完成",
  failed: "處理失敗",
};

export function reviewOperationLabel(status) {
  return reviewOperationLabels[String(status || "").toLowerCase()] || "";
}

export function reviewOperationIsActive(status) {
  return ["submitting", "accepted", "queued", "running", "reconnecting", "unknown"]
    .includes(String(status || "").toLowerCase());
}

export const mikanStatusLabels = {
  downloading: "下載中",
  queued: "等待下載",
  deferred: "延後處理",
  extracting_subtitles: "提取字幕中",
  completed_waiting_extract: "等待提取字幕",
  target_missing: "找不到對應影片",
  extract_failed: "字幕提取失敗",
  failed: "提取失敗，可重試",
  replaced: "已換替補來源",
  terminal_failed: "提取終止",
  review: "等待來源配對審核",
  failed_candidate: "先前候選不可用，等待重新搜尋",
  no_candidate_retry: "暫無候選，等待重試",
  completed: "字幕已匯入",
  success: "字幕已匯入",
  unknown: "未知狀態",
};

export const nextActionLabels = {
  wait_qbit_start: "等待 qBittorrent 開始",
  wait_qbit_progress: "等待下載進度",
  replace_when_stall_timeout: "卡住後更換來源",
  extracting_subtitles: "正在提取字幕",
  extract_subtitles: "提取字幕",
  wait_target_video: "等待媒體庫影片",
  find_replacement: "尋找替補來源",
  wait_retry_window: "等待重試時間",
  retry_candidate_search: "重新找候選",
  resolve_target_ambiguity: "確認來源配對",
  queue_when_qbit_available: "等待 qBittorrent 可用",
  done: "完成",
  inspect_state: "檢查狀態",
};

export const eventStageLabels = {
  input: "輸入檢查",
  queued: "已排隊",
  worker: "Worker",
  preflight: "前置檢查",
  language_detect: "語言偵測",
  language_skip: "語言略過",
  language_uncertain: "語言不確定",
  audio: "音訊提取",
  vocal_separation: "人聲分離",
  transcription: "語音轉錄",
  source_transcription: "原文轉錄",
  metadata_context: "作品資訊",
  translation: "翻譯",
  postprocess: "字幕整理",
  opencc: "繁簡轉換",
  ass_export: "輸出 ASS",
  source_ass_export: "輸出原文 ASS",
  quality_check: "品質檢查",
  cleanup: "清理暫存",
  complete: "完成",
  skipped: "略過",
  mikan: "字幕來源",
};

export function fileName(value) {
  return String(value || "").split(/[\\/]/).filter(Boolean).pop() || "unknown";
}

export function parentPath(value) {
  const parts = String(value || "").split(/[\\/]/).filter(Boolean);
  parts.pop();
  return parts.length ? `/${parts.join("/")}` : "";
}

export function statusTone(status) {
  const value = String(status || "").toLowerCase();
  if (["success", "completed", "done", "ok", "watchable"].includes(value)) return "success";
  if (["replaced", "failed_candidate"].includes(value)) return "muted";
  if (["danger", "error", "failed", "failed_retry", "extract_failed", "terminal_failed", "rerun"].includes(value) || value.includes("fail")) return "danger";
  if (["running", "downloading", "extracting_subtitles"].includes(value)) return "running";
  if (["paused", "skipped", "unknown"].includes(value)) return "muted";
  if (["warn", "warning", "review", "completed_waiting_extract", "target_missing", "no_candidate_retry", "deferred", "stalleddl", "check"].includes(value)) return "warn";
  return "queued";
}

export function taskProgress(task) {
  const rawExplicit = task?.progress;
  const explicit = Number(rawExplicit);
  if (rawExplicit !== null && rawExplicit !== undefined && rawExplicit !== "" && Number.isFinite(explicit)) {
    return Math.max(0, Math.min(100, explicit <= 1 && explicit !== 0 ? explicit * 100 : explicit));
  }
  if (["Success", "Failed", "Skipped"].includes(task?.status)) return 100;
  const batch = String(task?.message || "").match(/\b(?:translating|translated)?\s*batch\s+(\d+)\s*\/\s*(\d+)\b/i);
  if (batch && Number(batch[2]) > 0) return Math.max(0, Math.min(100, Number(batch[1]) * 100 / Number(batch[2])));
  if (task?.status === "Running") return null;
  return 0;
}

export function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "0%";
  return `${Math.round(Math.max(0, Math.min(100, numeric)))}%`;
}

export function formatBytes(bytes) {
  const numeric = Number(bytes || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = numeric;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatCompactCount(value) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) return "0";
  if (numeric < 1000) return String(Math.round(numeric));
  if (numeric < 1_000_000) {
    const compact = numeric / 1000;
    return `${compact >= 100 ? Math.round(compact) : compact.toFixed(compact >= 10 ? 0 : 1).replace(/\.0$/, "")}k`;
  }
  const compact = numeric / 1_000_000;
  return `${compact >= 100 ? Math.round(compact) : compact.toFixed(compact >= 10 ? 0 : 1).replace(/\.0$/, "")}m`;
}

function reviewItemPriority(item = {}) {
  const candidates = Number(item.candidates?.length || 0);
  const identities = Number(item.diagnosis?.bangumi_ids?.length || 0);
  const rawUpdated = item.updated_at;
  const updated = Number.isFinite(Number(rawUpdated))
    ? Number(rawUpdated)
    : (Date.parse(String(rawUpdated || "")) / 1000 || 0);
  return candidates * 1e12 + identities * 1e9 + updated;
}

export function groupReviewItems(items = []) {
  const groups = new Map();
  items.forEach((item, index) => {
    const torrentHash = String(item?.diagnosis?.torrent_hash || "").trim().toLowerCase();
    const duplicateKey = item?.kind === "target_ambiguity" && /^[0-9a-f]{40}$/.test(torrentHash)
      ? `torrent:${torrentHash}`
      : `review:${item?.review_id || index}`;
    const group = groups.get(duplicateKey) || { firstIndex: index, entries: [] };
    group.entries.push(item);
    groups.set(duplicateKey, group);
  });
  return [...groups.values()]
    .sort((left, right) => left.firstIndex - right.firstIndex)
    .map((group) => {
      const representative = [...group.entries].sort((left, right) => (
        reviewItemPriority(right) - reviewItemPriority(left)
      ))[0];
      return { ...representative, duplicate_count: group.entries.length };
    });
}

export function compatibleReviewBatchItems(items = [], selectedAction = "") {
  const eligible = items.filter((item) => item?.batch_eligible && item?.state === "needs_action");
  const requested = String(selectedAction || "").trim();
  if (requested) {
    return eligible.filter((item) => String(item?.recommended_action?.action || "safe.default") === requested);
  }

  const groups = new Map();
  eligible.forEach((item, index) => {
    const action = String(item?.recommended_action?.action || "safe.default");
    const group = groups.get(action) || { action, firstIndex: index, items: [] };
    group.items.push(item);
    groups.set(action, group);
  });
  const preferred = [...groups.values()].sort((left, right) => (
    right.items.length - left.items.length || left.firstIndex - right.firstIndex
  ))[0];
  return preferred?.items || [];
}

export function formatSpeed(bytesPerSecond) {
  const numeric = Number(bytesPerSecond || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) return "0 B/s";
  return `${formatBytes(numeric)}/s`;
}

export function formatTime(timestamp) {
  const numeric = Number(timestamp || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) return "-";
  return new Date(numeric * 1000).toLocaleString("zh-TW", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function completionTime(item) {
  const value = item?.completed_at || item?.finished_at || item?.job?.finished_at || item?.updated_at || 0;
  const timestamp = Number(value);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

export function newestCompletedFirst(items = []) {
  return [...items].sort((left, right) => {
    const timeDifference = completionTime(right) - completionTime(left);
    if (timeDifference) return timeDifference;
    const leftKey = String(left?.path || left?.job_key || "");
    const rightKey = String(right?.path || right?.job_key || "");
    return leftKey.localeCompare(rightKey);
  });
}

export function formatDuration(seconds) {
  const numeric = Math.max(0, Number(seconds || 0));
  if (!Number.isFinite(numeric) || numeric <= 0) return "0 秒";
  const days = Math.floor(numeric / 86400);
  const hours = Math.floor((numeric % 86400) / 3600);
  const minutes = Math.floor((numeric % 3600) / 60);
  const secs = Math.floor(numeric % 60);
  if (days > 0) return `${days} 天 ${hours} 小時`;
  if (hours > 0) return `${hours} 小時 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分 ${secs} 秒`;
  return `${secs} 秒`;
}

export function nodeLabel(nodeId) {
  return taskNodeLabels[nodeId] || nodeId || "-";
}

export function displayStatus(status) {
  return statusLabels[status] || rawStatusLabels[status] || status || "-";
}

export function mikanStatusLabel(status) {
  return mikanStatusLabels[status] || status || "-";
}

export function mikanSourceLabel(source) {
  const value = String(source || "");
  const base = value.split(":", 1)[0].toLowerCase();
  return {
    mikan: "Mikan",
    nyaa: "Nyaa",
    dmhy: "DMHY",
    acgrip: "ACG.RIP",
    bangumimoe: "Bangumi.moe",
    kisssub: "KissSub",
    animegarden: "Anime Garden",
    "qbit-recovered": "qBittorrent",
  }[base] || value || "-";
}

export function mikanSubtitleStateLabel(state) {
  return {
    official_ready: "字幕已匯入",
    official_completed_unknown: "下載完成，待確認字幕",
    official_waiting_extract: "等待提取字幕",
    official_extracting: "正在提取字幕",
    official_downloading: "字幕來源下載中",
    official_extract_failed_replace: "提取失敗，尋找替補",
    official_target_missing: "找不到媒體庫對應影片",
    no_candidate_retry: "暫無候選，等待重試",
    official_deferred: "延後處理",
    unknown: "未知",
  }[state] || state || "";
}

export function nextActionLabel(value) {
  return nextActionLabels[value] || value || "";
}

export function repairDisplayText(value) {
  const text = String(value || "");
  if (!text || !/[ÃÂäåæçèéïð]/.test(text)) return text;
  const cp1252 = new Map([
    [0x20ac, 0x80], [0x201a, 0x82], [0x0192, 0x83], [0x201e, 0x84], [0x2026, 0x85],
    [0x2020, 0x86], [0x2021, 0x87], [0x02c6, 0x88], [0x2030, 0x89], [0x0160, 0x8a],
    [0x2039, 0x8b], [0x0152, 0x8c], [0x017d, 0x8e], [0x2018, 0x91], [0x2019, 0x92],
    [0x201c, 0x93], [0x201d, 0x94], [0x2022, 0x95], [0x2013, 0x96], [0x2014, 0x97],
    [0x02dc, 0x98], [0x2122, 0x99], [0x0161, 0x9a], [0x203a, 0x9b], [0x0153, 0x9c],
    [0x017e, 0x9e], [0x0178, 0x9f],
  ]);
  const bytes = [];
  for (const character of text) {
    const code = character.codePointAt(0);
    const byte = code <= 0xff ? code : cp1252.get(code);
    if (byte === undefined) return text;
    bytes.push(byte);
  }
  try {
    const decoded = new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(bytes));
    return /[\u3400-\u9fff\u3040-\u30ff]/.test(decoded) ? decoded : text;
  } catch {
    return text;
  }
}

export function mikanTitle(row) {
  return repairDisplayText(row?.title || row?.torrent_name || row?.last_qbit_name || row?.key || "unknown");
}

export function mikanRowKey(row = {}) {
  const explicitKey = row?.key || row?.job_key;
  if (explicitKey) return String(explicitKey);
  const episodes = Array.isArray(row?.episodes) ? row.episodes.join(",") : (row?.episode || "");
  return [
    "mikan",
    row?.last_qbit_hash || row?.torrent_url || row?.last_qbit_name || "",
    row?.bangumi_id || "",
    episodes,
    row?.title || row?.torrent_name || "unknown",
  ].map((value) => String(value)).join(":");
}

export function mikanSubtitle(row) {
  const episodes = Array.isArray(row?.episodes) && row.episodes.length
    ? `第 ${row.episodes.map((episode) => String(episode).padStart(2, "0")).join(", ")} 集`
    : row?.episode
      ? `第 ${String(row.episode).padStart(2, "0")} 集`
      : "";
  return episodes || "集數未知";
}

export function downloadProgress(row) {
  const progress = Number(row?.progress);
  if (!Number.isFinite(progress)) return null;
  return Math.max(0, Math.min(100, progress <= 1 ? progress * 100 : progress));
}

function countNumber(value) {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : 0;
}

export function mikanPipelineSummary({
  pipeline = {},
  mikanCounts = {},
  extractCounts = {},
} = {}) {
  if (pipeline && Object.keys(pipeline).length) {
    return {
      queuedDownloads: countNumber(pipeline.queued_downloads),
      downloading: countNumber(pipeline.downloading),
      extracting: countNumber(pipeline.extracting),
      waitingExtract: countNumber(pipeline.waiting_extract),
      candidateRetry: countNumber(pipeline.candidate_retry),
      autoReplacing: countNumber(pipeline.auto_replacing),
      needsAttention: countNumber(pipeline.needs_attention),
      imported: countNumber(pipeline.imported),
    };
  }
  const downloadExtracting = countNumber(mikanCounts.extracting_subtitles);
  const runningExtracts = countNumber(extractCounts.running);
  const unrepresentedRunning = Math.max(0, runningExtracts - downloadExtracting);
  return {
    queuedDownloads: countNumber(mikanCounts.queued) + countNumber(mikanCounts.deferred),
    downloading: countNumber(mikanCounts.downloading),
    extracting: Math.max(downloadExtracting, runningExtracts),
    waitingExtract: Math.max(
      countNumber(extractCounts.queued),
      Math.max(0, countNumber(mikanCounts.completed_waiting_extract) - unrepresentedRunning),
    ),
    candidateRetry: countNumber(mikanCounts.no_candidate_retry),
    autoReplacing: Math.max(countNumber(mikanCounts.extract_failed), countNumber(extractCounts.failed)),
    needsAttention: countNumber(mikanCounts.target_missing) + countNumber(extractCounts.terminal_failed),
    imported: Math.max(countNumber(mikanCounts.completed), countNumber(extractCounts.success)),
  };
}

export function mikanAttentionSummary({
  mikanCounts = {},
  extractCounts = {},
  stateDb = {},
  queueCounts = {},
} = {}) {
  const retryableExtractFailures = countNumber(extractCounts.failed);
  const replacedExtractFailures = countNumber(extractCounts.replaced);
  const sourceExtractFailures = countNumber(mikanCounts.extract_failed);
  const terminalExtractFailures = countNumber(extractCounts.terminal_failed);
  const targetMissing = countNumber(mikanCounts.target_missing);
  const stalled = countNumber(stateDb.stalled);
  const zeroSpeedDownloading = countNumber(stateDb.zero_speed_downloading);
  const blockedDownloads = Math.max(stalled, zeroSpeedDownloading);
  const aiRetryFailures = countNumber(queueCounts.failed_retry);

  return {
    // Zero-speed/stalled downloads are monitored and replaced automatically;
    // keep them visible, but do not claim the user must intervene.
    total: terminalExtractFailures + targetMissing + aiRetryFailures,
    retryableTotal: aiRetryFailures + retryableExtractFailures,
    terminalExtractFailures,
    targetMissing,
    blockedDownloads,
    aiRetryFailures,
    retryableExtractFailures,
    replacedExtractFailures,
    sourceExtractFailures,
    autoReplacementFailures: Math.max(retryableExtractFailures, sourceExtractFailures),
    replacementHistory: replacedExtractFailures,
    stalled,
    zeroSpeedDownloading,
  };
}

export function actionLabel(action) {
  return {
    "ai-safe-retry-sweep": "安全處理下一筆 AI 失敗",
    "retry-all-failures": "批次重試失敗",
    "ai-scheduler-retry": "立即重試 AI 排程",
    "mikan-process-completed": "處理已完成下載",
    "mikan-requeue-failed-extracts": "重排提取失敗",
    "mikan-redownload-all": "重新下載全部字幕來源",
    "mikan-reset-all": "重設字幕來源狀態",
    "ai-refresh-queue-state": "重新整理 AI 分類",
    "ai-requeue-failed": "重排 AI 失敗",
    "refresh-ass": "重新輸出 ASS",
    "cleanup-generated": "清理暫存檔",
    "backup-state": "備份系統狀態",
    "database-maintenance": "最佳化資料庫",
    "series-sync": "同步作品資訊",
    "restart-worker": "重啟 Worker",
  }[action] || action;
}

export function eventStageLabel(stage) {
  return eventStageLabels[stage] || stage || "處理中";
}

export function eventSeverity(event) {
  const explicit = String(event?.severity || "").toLowerCase();
  if (["success", "danger", "warn", "running", "muted", "queued"].includes(explicit)) return explicit;
  const status = String(event?.status || "").toLowerCase();
  if (["ok", "success", "completed", "done"].includes(status)) return "success";
  if (["failed", "failed_retry", "terminal_failed", "error"].includes(status) || status.includes("fail")) return "danger";
  if (["warn", "warning", "target_missing", "check"].includes(status)) return "warn";
  if (["running", "downloading", "extracting_subtitles"].includes(status)) return "running";
  if (["skipped", "paused", "replaced", "no_candidate_retry"].includes(status)) return "muted";
  return "queued";
}

export function eventNeedsAttention(event) {
  return ["danger", "warn"].includes(eventSeverity(event));
}

export function eventSucceeded(event) {
  return eventSeverity(event) === "success";
}

export function eventMark(event) {
  const severity = eventSeverity(event);
  if (severity === "success") return "✓";
  if (["danger", "warn"].includes(severity)) return "!";
  if (severity === "running") return "↻";
  return "•";
}

export function eventMessage(message) {
  const text = String(message || "").trim();
  return text || "沒有詳細訊息";
}

const qualityIssueLabels = {
  asr_prompt_echo: "轉錄提示混入日文字幕",
  prompt_leak: "翻譯混入模型指令",
  prompt_pollution: "翻譯混入模型指令",
  residual_japanese_kana: "中文字幕仍有未翻譯日文",
  residual_japanese: "中文字幕仍有未翻譯日文",
  residual_kana: "中文字幕仍有未翻譯日文",
  missing_indexes: "翻譯結果缺少字幕行",
  missing_index: "翻譯結果缺少字幕行",
  unexpected_indexes: "翻譯編號不正確",
  unexpected_index: "翻譯編號不正確",
  unreasonable_length: "翻譯內容異常過長",
  excessive_length: "翻譯內容異常過長",
  asr_low_confidence: "日文轉錄信心不足",
  low_confidence: "日文轉錄信心不足",
  leading_gap: "片頭開場可能漏轉",
  large_gap: "部分語音區段可能漏轉",
  long_gap: "部分語音區段可能漏轉",
  timeout: "翻譯服務逾時",
};

function normalizedIssueCode(issue) {
  if (typeof issue === "string") return issue.trim().toLowerCase().replace(/[\s-]+/g, "_");
  return String(issue?.code || issue?.kind || issue?.reason || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
}

export function qualityIssueLabel(issue) {
  const code = normalizedIssueCode(issue);
  if (qualityIssueLabels[code]) return qualityIssueLabels[code];
  const text = String(typeof issue === "string" ? issue : (issue?.message || issue?.detail || "")).toLowerCase();
  if (/asr.+prompt|transcription instruction|轉錄提示/.test(text)) return qualityIssueLabels.asr_prompt_echo;
  if (/prompt|instruction|system message/.test(text)) return qualityIssueLabels.prompt_leak;
  if (/kana|japanese|日文|假名/.test(text)) return qualityIssueLabels.residual_japanese_kana;
  if (/missing.+index|缺少.+編號|缺少.+字幕/.test(text)) return qualityIssueLabels.missing_indexes;
  if (/unexpected.+index|編號.+錯/.test(text)) return qualityIssueLabels.unexpected_indexes;
  if (/too long|unreasonably long|過長/.test(text)) return qualityIssueLabels.unreasonable_length;
  if (/low confidence|信心不足/.test(text)) return qualityIssueLabels.asr_low_confidence;
  if (/leading gap|first subtitle starts unusually late|片頭/.test(text)) return qualityIssueLabels.leading_gap;
  if (/long gap|空洞|漏轉/.test(text)) return qualityIssueLabels.long_gap;
  return "字幕品質需要檢查";
}

export function problemTitle(item, fallback = "狀態已更新") {
  return String(item?.problem?.title || fallback);
}

export function problemDescription(item, fallback = "系統已保留目前狀態。") {
  return String(item?.problem?.description || fallback);
}

export function problemSystemAction(item, fallback = "系統已保留工作與診斷資料。") {
  return String(item?.problem?.system_action || fallback);
}

export function problemRecommendedAction(item, fallback = "不需要操作。") {
  return String(item?.problem?.recommended_action || fallback);
}

export function friendlyTechnicalMessage(value, fallback = "系統已保留診斷資料，可在進階資訊中查看識別碼。") {
  const text = String(value || "").trim();
  if (!text) return fallback;
  const lowered = text.toLowerCase();
  if (/no[_ ]subtitle[_ ]streams|no usable chinese/.test(lowered)) return "來源內沒有可用中文字幕，系統會自動尋找替補。";
  if (/target[_ ]ambiguity|ambiguous.+target/.test(lowered)) return "找到多個可能影片，需要先確認作品與季度。";
  if (/target[_ ]missing|no matching target/.test(lowered)) return "找不到可安全配對的媒體庫影片。";
  if (/database is locked|database.+busy/.test(lowered)) return "狀態資料庫暫時忙碌，系統會自動重試。";
  if (/timeout|timed out/.test(lowered)) return "處理逾時，工作狀態已保留並可安全重試。";
  if (/prompt.+leak|model instruction/.test(lowered)) return "翻譯混入模型指令，受影響字幕不會發布。";
  if (/retranscription is required|requires retranscription|japanese asr diagnostic/.test(lowered)) {
    return "日文原文快取未通過檢查，需要重新轉錄後再翻譯。";
  }
  if (/residual.+kana|japanese kana/.test(lowered)) return "中文字幕仍有未翻譯日文，需要重翻問題行。";
  const containsTechnicalData = /traceback|sqlite|ffprobe|https?:\/\/|\/anime\/|\/work\/|\/qbit|\\anime\\|error:\s|exception/i.test(text);
  if (!containsTechnicalData && /[\u3400-\u9fff]/.test(text) && text.length <= 160) return text;
  return fallback;
}

export function friendlyTaskMessage(task = {}) {
  const message = String(task?.message || "").trim();
  const batch = message.match(/\b(?:translating|translated)?\s*batch\s+(\d+)\s*\/\s*(\d+)\b/i);
  if (batch) return `正在翻譯字幕（${batch[1]} / ${batch[2]}）`;
  if (/running asr|whisper|transcrib/i.test(message)) return "正在將日文語音轉成字幕。";
  if (/extracting audio|audio extraction/i.test(message)) return "正在提取影片音訊。";
  if (/language detect/i.test(message)) return "正在確認音訊語言。";
  if (/quality check/i.test(message)) return "正在檢查字幕品質。";
  const status = String(task?.raw_status || task?.job_status || task?.status || "").toLowerCase();
  if (status.includes("fail") || status === "review" || status === "skipped") {
    return problemDescription(task, "這個工作需要檢查，系統已保留進度與快取。");
  }
  if (task?.problem?.description) return problemDescription(task);
  return status === "queued" || status === "waiting" ? "已排入佇列，等待 Worker 處理。" : "Worker 正在處理這個字幕工作。";
}

export function friendlyApiError(context, error) {
  const action = String(context || "操作");
  const raw = String(error?.message || error?.detail || error || "");
  if (typeof console !== "undefined" && typeof console.error === "function") console.error(`${action} failed`, error);
  const lowered = raw.toLowerCase();
  const status = Number(error?.status || error?.statusCode || 0);
  if (status === 404 && /審核/.test(action)) {
    return `${action}失敗：Worker 與 WebUI 版本不一致，請用「安全更新整個 Stack」同時更新兩個服務。`;
  }
  if (status === 409 || /\b409\b|busy|already running|conflict/.test(lowered)) {
    return `${action}暫時忙碌，請稍後再試；原有狀態不會遺失。`;
  }
  if (/timeout|timed out|abort/.test(lowered)) {
    return `${action}逾時，系統狀態未遺失，請稍後重試。`;
  }
  if (/failed to fetch|network|connection|econn|無法連線/.test(lowered)) {
    return `無法連線 Worker，${action}尚未送出；請確認服務仍在執行。`;
  }
  return `${action}未完成，系統已保留原有狀態。`;
}

export function candidateEvidenceLabel(reason) {
  const code = String(reason || "").trim().toLowerCase();
  return {
    episode: "集數相符",
    title_contains: "作品名稱相符",
    title_exact: "作品名稱完全相符",
    subtitle_missing: "目標影片尚缺字幕",
    season: "季度相符",
    year: "年份相符",
    source_mapping: "已鎖定來源映射",
  }[code] || "作品資料比對";
}

export function subtitleQualityLabel(quality) {
  const status = String(quality?.status || "").toLowerCase();
  if (status === "watchable") return "可觀看";
  if (status === "check") return "需檢查";
  if (status === "rerun") return "建議重跑";
  if (status === "missing") return "缺少字幕";
  return status || "未知";
}

export function subtitleQualityTone(quality) {
  const status = String(quality?.status || "").toLowerCase();
  if (status === "watchable") return "success";
  if (status === "check") return "warn";
  if (status === "rerun" || status === "missing") return "danger";
  return "muted";
}

export function subtitleQualitySummary(quality) {
  if (!quality || typeof quality !== "object") return "";
  const label = subtitleQualityLabel(quality);
  const score = Number(quality.score);
  const parts = [`品質：${label}`];
  if (Number.isFinite(score)) parts.push(`分數 ${Math.round(score)}`);
  const issues = Array.isArray(quality.issues) ? quality.issues : [];
  if (issues.length) {
    const first = issues[0];
    const count = Number(typeof first === "string" ? 0 : first?.count);
    parts.push(`${qualityIssueLabel(first)}${Number.isFinite(count) && count > 0 ? `（${Math.round(count)} 行）` : ""}`);
  }
  return parts.join("；");
}
