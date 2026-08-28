<script setup>
import { computed, onUnmounted, ref, watch } from "vue";
import { friendlyTechnicalMessage } from "../dashboard.js";

const props = defineProps({
  payload: { type: Object, default: () => ({ items: [], total: 0, page: 1, page_count: 0 }) },
  detail: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  detailLoading: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  operation: { type: Object, default: () => ({ busy: false, status: "idle" }) },
  query: { type: Object, default: () => ({ page: 1, pageSize: 30, search: "" }) },
});

const emit = defineEmits([
  "query",
  "page",
  "select",
  "lock",
  "match",
  "glossary-upsert",
  "glossary-delete",
  "sync",
]);

const search = ref(props.query.search || "");
const provider = ref("anilist");
const providerId = ref("");
const manualTitle = ref("");
const termSource = ref("");
const termTarget = ref("");
const termType = ref("name");
const deleteConfirmation = ref("");
let searchTimer = null;

const items = computed(() => props.payload.items || []);
const profile = computed(() => props.detail?.profile || null);
const glossary = computed(() => props.detail?.glossary || []);
const page = computed(() => Math.max(1, Number(props.payload.page || props.query.page || 1)));
const pageCount = computed(() => Math.max(1, Number(props.payload.page_count || 1)));
const mutationBusy = computed(() => Boolean(props.busy || props.operation?.busy));
const operationVisible = computed(() => {
  const status = String(props.operation?.status || "").toLowerCase();
  if (["", "idle"].includes(status)) return false;
  const target = String(props.operation?.target || "");
  return Boolean(props.operation?.busy || !profile.value || !target || target === String(profile.value.local_path || ""));
});

function operationTone() {
  const status = String(props.operation?.status || "").toLowerCase();
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  return "running";
}

function operationTitle() {
  const status = String(props.operation?.status || "").toLowerCase();
  if (status === "submitting") return "正在安全送出";
  if (status === "running") return "Worker 正在更新作品資料";
  if (status === "completed") return "作品資料已更新";
  if (status === "failed") return "作品資料未變更";
  return "";
}

function operationDescription() {
  if (String(props.operation?.status || "").toLowerCase() === "failed") {
    return friendlyTechnicalMessage(props.operation?.error, "Worker 沒有完成這次更新；輸入內容仍保留，可修正後重試。");
  }
  return String(props.operation?.message || "背景操作不會因切換頁面中斷。");
}

function operationButtonLabel(action, idleLabel) {
  if (!mutationBusy.value) return idleLabel;
  if (String(props.operation?.action || "") !== action) return "其他更新執行中…";
  return String(props.operation?.status || "") === "submitting" ? "正在送出…" : "Worker 處理中…";
}

watch(() => props.query.search, (value) => {
  if ((value || "") !== search.value) search.value = value || "";
});

watch(search, (value) => {
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => emit("query", { search: value.trim(), page: 1 }), 350);
});

watch(profile, (value) => {
  provider.value = value?.provider || "anilist";
  providerId.value = value?.provider_id || "";
  manualTitle.value = value?.canonical_title || "";
  termSource.value = "";
  termTarget.value = "";
  deleteConfirmation.value = "";
}, { immediate: true });

onUnmounted(() => {
  if (searchTimer) window.clearTimeout(searchTimer);
});

function titleFor(item) {
  return item?.canonical_title || item?.titles?.[0] || item?.local_path?.split(/[\\/]/).pop() || "未命名作品";
}

function confidenceLabel(value) {
  const number = Number(value || 0);
  return number > 0 ? `${Math.round(number * 100)}%` : "尚未比對";
}

function itemLabel(value) {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return String(value || "");
  return value.name || value.full || value.native || value.title || JSON.stringify(value);
}

function submitMatch() {
  if (!profile.value || !providerId.value.trim() || !manualTitle.value.trim()) return;
  emit("match", {
    path: profile.value.local_path,
    series_id: profile.value.series_id,
    provider: provider.value.trim() || "anilist",
    provider_id: providerId.value.trim(),
    title: manualTitle.value.trim(),
  });
}

function submitGlossary() {
  if (!profile.value || !termSource.value.trim()) return;
  emit("glossary-upsert", {
    path: profile.value.local_path,
    series_id: profile.value.series_id,
    source_text: termSource.value.trim(),
    target_text: termTarget.value.trim(),
    term_type: termType.value,
  });
}

function confirmGlossaryDelete(term) {
  if (!profile.value || mutationBusy.value) return;
  emit("glossary-delete", {
    path: profile.value.local_path,
    series_id: profile.value.series_id,
    source_text: term.source_text,
  });
  deleteConfirmation.value = "";
}
</script>

<template>
  <section class="page-panel series-page">
    <header class="page-heading">
      <div>
        <span class="section-label">作品知識庫</span>
        <h1>作品資訊與術語</h1>
        <p>集中管理 AniList／Mikan 對應、角色與專有名詞。鎖定後，自動掃描不會覆蓋人工校正。</p>
      </div>
      <div class="series-coverage-chips">
        <span class="status-chip success">{{ payload.total || 0 }} 部作品</span>
        <span class="status-chip">AniList {{ payload.coverage?.anilist || 0 }}</span>
        <span class="status-chip">完整資料 {{ payload.coverage?.enriched || 0 }}</span>
        <span class="status-chip">術語 {{ payload.coverage?.glossary_terms || 0 }}</span>
      </div>
    </header>

    <section
      v-if="operationVisible"
      :class="['series-operation-status', operationTone()]"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span v-if="mutationBusy" class="operation-spinner" aria-hidden="true"></span>
      <span v-else class="series-operation-mark" aria-hidden="true">{{ operation.status === 'completed' ? '✓' : '!' }}</span>
      <div><strong>{{ operationTitle() }}</strong><small>{{ operationDescription() }}</small></div>
    </section>

    <div class="list-toolbar series-toolbar">
      <label class="search-field">
        <span>搜尋</span>
        <input v-model="search" type="search" placeholder="作品名稱或資料夾路徑" />
      </label>
    </div>

    <div class="series-workspace">
      <section class="series-list" aria-label="作品清單">
        <div v-if="loading && items.length === 0" class="empty-state">正在載入作品資訊…</div>
        <div v-else-if="items.length === 0" class="empty-state">
          <span>尚無作品資訊；可從既有 Mikan 與 AI 索引立即回填，不需要重新讀取影片。</span>
          <button type="button" class="primary" :disabled="mutationBusy" @click="emit('sync')">
            {{ mutationBusy ? '背景操作執行中…' : '立即同步作品' }}
          </button>
        </div>
        <button
          v-for="item in items"
          :key="item.series_id || item.local_path"
          type="button"
          :class="['series-list-item', { active: profile?.local_path === item.local_path }]"
          @click="emit('select', item.series_id || item.local_path)"
        >
          <span>
            <strong>{{ titleFor(item) }}</strong>
            <small>{{ item.local_path }}</small>
          </span>
          <span class="series-list-meta">
            <i v-if="item.locked" title="已鎖定">鎖</i>
            <b>{{ confidenceLabel(item.match_confidence) }}</b>
          </span>
        </button>

        <footer class="pagination compact-pagination">
          <button type="button" :disabled="loading || page <= 1" @click="emit('page', page - 1)">上一頁</button>
          <span>{{ page }} / {{ pageCount }}</span>
          <button type="button" :disabled="loading || page >= pageCount" @click="emit('page', page + 1)">下一頁</button>
        </footer>
      </section>

      <section class="series-detail">
        <div v-if="detailLoading" class="empty-state">正在讀取詳細資料…</div>
        <div v-else-if="!profile" class="empty-state">從左側選擇作品，即可查看與修正匹配資料。</div>
        <template v-else>
          <div class="series-detail-heading">
            <div>
              <span class="section-label">{{ profile.provider || "本機" }} · {{ profile.provider_id || "未匹配" }}</span>
              <h2>{{ titleFor(profile) }}</h2>
              <p>{{ profile.local_path }}</p>
            </div>
            <button
              type="button"
              :class="{ primary: !profile.locked }"
              :disabled="mutationBusy"
              @click="emit('lock', { path: profile.local_path, series_id: profile.series_id, locked: !profile.locked })"
            >
              {{ operationButtonLabel('series.lock', profile.locked ? '解除鎖定' : '鎖定人工資料') }}
            </button>
          </div>

          <div class="series-facts">
            <article><span>匹配信心</span><strong>{{ confidenceLabel(profile.match_confidence) }}</strong></article>
            <article><span>匹配來源</span><strong>{{ profile.match_source || "未知" }}</strong></article>
            <article><span>年份</span><strong>{{ profile.premiered_year || "—" }}</strong></article>
            <article><span>季別</span><strong>{{ profile.season_number || "—" }}</strong></article>
          </div>

          <details class="series-editor">
            <summary>人工修正作品匹配</summary>
            <form class="series-form" @submit.prevent="submitMatch">
              <label><span>來源</span><input v-model="provider" placeholder="anilist" /></label>
              <label><span>來源 ID</span><input v-model="providerId" required placeholder="AniList ID" /></label>
              <label class="wide"><span>正式名稱</span><input v-model="manualTitle" required /></label>
              <button type="submit" class="primary" :disabled="mutationBusy">
                {{ operationButtonLabel('series.match', '儲存並鎖定') }}
              </button>
            </form>
          </details>

          <section v-if="profile.synopsis || profile.titles?.length || profile.aliases?.length" class="metadata-copy">
            <h3>背景資訊</h3>
            <p v-if="profile.synopsis">{{ profile.synopsis }}</p>
            <div v-if="profile.titles?.length" class="tag-list">
              <span v-for="value in profile.titles" :key="itemLabel(value)">{{ itemLabel(value) }}</span>
            </div>
            <div v-if="profile.aliases?.length" class="tag-list muted-tags">
              <span v-for="value in profile.aliases" :key="itemLabel(value)">{{ itemLabel(value) }}</span>
            </div>
          </section>

          <section v-if="profile.characters?.length || profile.staff?.length" class="series-people-grid">
            <article v-if="profile.characters?.length">
              <h3>角色</h3>
              <div class="tag-list"><span v-for="value in profile.characters" :key="itemLabel(value)">{{ itemLabel(value) }}</span></div>
            </article>
            <article v-if="profile.staff?.length">
              <h3>製作人員</h3>
              <div class="tag-list"><span v-for="value in profile.staff" :key="itemLabel(value)">{{ itemLabel(value) }}</span></div>
            </article>
          </section>

          <section class="glossary-panel">
            <div class="card-heading">
              <div><span class="section-label">日文 → 中文</span><h3>作品專用術語庫</h3></div>
              <span>{{ glossary.length }} 筆</span>
            </div>
            <form class="glossary-form" @submit.prevent="submitGlossary">
              <input v-model="termSource" required placeholder="日文原詞" />
              <input v-model="termTarget" placeholder="指定中文；留空代表保持原文" />
              <select v-model="termType" aria-label="術語類型">
                <option value="name">人名</option>
                <option value="place">地名</option>
                <option value="term">專有名詞</option>
                <option value="phrase">固定譯法</option>
              </select>
              <button type="submit" class="primary" :disabled="mutationBusy">
                {{ operationButtonLabel('series.glossary_upsert', '新增／更新') }}
              </button>
            </form>
            <div v-if="glossary.length" class="glossary-list">
              <div v-for="term in glossary" :key="term.source_text">
                <span><strong>{{ term.source_text }}</strong><i>→</i><b>{{ term.target_text || "保持原文" }}</b></span>
                <small>{{ term.term_type }} · {{ term.source }}</small>
                <div class="glossary-delete-actions">
                  <button
                    v-if="deleteConfirmation !== term.source_text"
                    type="button"
                    class="quiet"
                    :disabled="mutationBusy"
                    @click="deleteConfirmation = term.source_text"
                  >刪除</button>
                  <template v-else>
                    <small>確定刪除？</small>
                    <button type="button" class="quiet" @click="deleteConfirmation = ''">取消</button>
                    <button type="button" class="danger" :disabled="mutationBusy" @click="confirmGlossaryDelete(term)">確認刪除</button>
                  </template>
                </div>
              </div>
            </div>
            <div v-else class="empty-inline">尚未設定人工術語。</div>
          </section>
        </template>
      </section>
    </div>
  </section>
</template>
