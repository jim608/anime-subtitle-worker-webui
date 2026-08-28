<script setup>
import { computed, ref } from "vue";
import {
  eventMark,
  eventMessage,
  eventNeedsAttention,
  eventSeverity,
  eventStageLabel,
  eventSucceeded,
  fileName,
  formatTime,
  parentPath,
  statusTone,
} from "../dashboard.js";

const props = defineProps({ events: { type: Object, default: () => ({ recent: [] }) } });
const filter = ref("all");
const visibleEvents = computed(() => {
  const rows = props.events.items || props.events.recent || [];
  if (filter.value === "attention") return rows.filter(eventNeedsAttention);
  if (filter.value === "success") return rows.filter(eventSucceeded);
  return rows;
});
</script>

<template>
  <section class="page-panel events-page">
    <header class="page-heading">
      <div>
        <span class="section-label">處理紀錄</span>
        <h1>處理紀錄</h1>
        <p>只顯示重要且看得懂的處理結果；重複事件會合併，技術細節不會塞滿畫面。</p>
      </div>
    </header>

    <div class="event-toolbar view-switch" aria-label="處理紀錄篩選">
      <button type="button" :class="{ active: filter === 'all' }" @click="filter = 'all'">全部 {{ (events.items || events.recent || []).length }}</button>
      <button type="button" :class="{ active: filter === 'attention' }" @click="filter = 'attention'">需注意</button>
      <button type="button" :class="{ active: filter === 'success' }" @click="filter = 'success'">完成</button>
    </div>

    <div class="timeline">
      <article v-if="!visibleEvents.length" class="empty-state">目前沒有符合條件的事件紀錄</article>
      <article v-for="event in visibleEvents" :key="event.id" class="event-card">
        <span :class="['event-mark', statusTone(eventSeverity(event))]">{{ eventMark(event) }}</span>
        <div>
          <div class="card-line">
            <strong>{{ event.title || eventStageLabel(event.stage) }}</strong>
            <small>{{ formatTime(event.occurred_at || event.created_at) }}</small>
          </div>
          <p v-if="event.entity || event.path">{{ event.entity || fileName(event.path) }}</p>
          <small>{{ event.description || eventMessage(event.message) }}</small>
          <small v-if="Number(event.occurrence_count || 1) > 1" class="event-occurrences">
            相同事件已合併 {{ event.occurrence_count }} 次
          </small>
          <small v-if="event.path" class="event-path">{{ parentPath(event.path) }}</small>
        </div>
      </article>
    </div>
  </section>
</template>
