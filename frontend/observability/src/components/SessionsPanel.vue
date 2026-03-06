<script setup>
import { computed } from 'vue'
import { Search, RefreshCw, Activity, Clock, Hash } from 'lucide-vue-next'
import { formatRelativeTime, formatSessionId, getStatusType } from '../utils/formatters.js'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  selectedId: { type: String, default: '' },
  searchQuery: { type: String, default: '' },
  isDark: { type: Boolean, default: false }
})

const emit = defineEmits(['select', 'refresh', 'update:searchQuery'])

// Remove message ID prefix from content
function cleanMessageId(content) {
  if (!content) return ''
  return content.replace(/^\s*消息ID:\s*Message_\d+\s*/i, '')
}

const filteredSessions = computed(() => {
  if (!props.searchQuery) return props.sessions
  const query = props.searchQuery.toLowerCase()
  return props.sessions.filter(s =>
    (s.session_id && s.session_id.toLowerCase().includes(query)) ||
    (s.goal && s.goal.toLowerCase().includes(query))
  )
})

const sortedSessions = computed(() => {
  return [...filteredSessions.value].sort((a, b) => {
    return new Date(b.updated_at || b.created_at) - new Date(a.updated_at || a.created_at)
  })
})
</script>

<template>
  <div class="sessions-panel" :class="{ 'is-dark': isDark }">
    <div class="panel-header">
      <h2 class="panel-title">
        <Activity class="title-icon" />
        Sessions
        <span class="count">{{ sessions.length }}</span>
      </h2>
      <button class="refresh-btn" @click="$emit('refresh')">
        <RefreshCw class="icon" />
      </button>
    </div>
    
    <div class="search-box">
      <Search class="search-icon" />
      <input
        type="text"
        :value="searchQuery"
        @input="$emit('update:searchQuery', $event.target.value)"
        placeholder="Search sessions..."
        class="search-input"
      />
    </div>
    
    <div class="sessions-list">
      <div
        v-for="session in sortedSessions"
        :key="session.session_id"
        class="session-item"
        :class="{ active: session.session_id === selectedId }"
        @click="$emit('select', session.session_id)"
      >
        <div class="session-header">
          <span v-if="session.goal" class="session-goal-title">{{ cleanMessageId(session.goal) }}</span>
          <span v-else class="session-id">{{ formatSessionId(session.session_id) }}</span>
          <span
            class="status-badge"
            :class="getStatusType(session.status)"
          >
            {{ session.status || 'unknown' }}
          </span>
        </div>

        <div v-if="session.goal" class="session-id-secondary">
          {{ formatSessionId(session.session_id) }}
        </div>
        
        <div class="session-meta">
          <span class="meta-item">
            <Clock class="meta-icon" />
            {{ formatRelativeTime(session.updated_at || session.created_at) }}
          </span>
          <span v-if="session.turn_count !== undefined" class="meta-item">
            <Hash class="meta-icon" />
            {{ session.turn_count }} turns
          </span>
        </div>
      </div>
      
      <div v-if="sortedSessions.length === 0" class="empty-state">
        No sessions found
      </div>
    </div>
  </div>
</template>

<style scoped>
.sessions-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #ffffff;
  border-right: 1px solid #e5e7eb;
}

.sessions-panel.is-dark {
  background: #0d1117;
  border-right-color: #30363d;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  border-bottom: 1px solid #e5e7eb;
}

.sessions-panel.is-dark .panel-header {
  border-bottom-color: #30363d;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
}

.sessions-panel.is-dark .panel-title {
  color: #e5e7eb;
}

.title-icon {
  width: 18px;
  height: 18px;
  color: #3b82f6;
}

.count {
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 500;
  color: #6b7280;
  background: #f3f4f6;
  border-radius: 12px;
}

.sessions-panel.is-dark .count {
  color: #9ca3af;
  background: #21262d;
}

.refresh-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.2s;
}

.refresh-btn:hover {
  background: #f3f4f6;
  color: #374151;
}

.sessions-panel.is-dark .refresh-btn:hover {
  background: #21262d;
  color: #e5e7eb;
}

.icon {
  width: 16px;
  height: 16px;
}

.search-box {
  position: relative;
  padding: 12px 16px;
  border-bottom: 1px solid #e5e7eb;
}

.sessions-panel.is-dark .search-box {
  border-bottom-color: #30363d;
}

.search-icon {
  position: absolute;
  left: 24px;
  top: 50%;
  transform: translateY(-50%);
  width: 16px;
  height: 16px;
  color: #9ca3af;
}

.search-input {
  width: 100%;
  padding: 8px 12px 8px 36px;
  font-size: 13px;
  color: #1f2937;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  outline: none;
  transition: all 0.2s;
}

.search-input::placeholder {
  color: #9ca3af;
}

.search-input:focus {
  background: #ffffff;
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
}

.sessions-panel.is-dark .search-input {
  color: #e5e7eb;
  background: #21262d;
  border-color: #30363d;
}

.sessions-panel.is-dark .search-input:focus {
  background: #161b22;
  border-color: #3b82f6;
}

.sessions-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.session-item {
  padding: 12px;
  margin-bottom: 8px;
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.session-item:hover {
  border-color: #3b82f6;
  box-shadow: 0 2px 8px rgba(59, 130, 246, 0.1);
}

.session-item.active {
  background: #eff6ff;
  border-color: #3b82f6;
}

.sessions-panel.is-dark .session-item {
  background: #161b22;
  border-color: #30363d;
}

.sessions-panel.is-dark .session-item:hover {
  border-color: #3b82f6;
  background: #1c2128;
}

.sessions-panel.is-dark .session-item.active {
  background: rgba(59, 130, 246, 0.1);
  border-color: #3b82f6;
}

.session-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.session-goal-title {
  font-size: 13px;
  font-weight: 500;
  color: #1f2937;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
  flex: 1;
  margin-right: 8px;
}

.sessions-panel.is-dark .session-goal-title {
  color: #e5e7eb;
}

.session-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 500;
  color: #3b82f6;
}

.session-id-secondary {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #9ca3af;
  margin-bottom: 4px;
}

.sessions-panel.is-dark .session-id-secondary {
  color: #6b7280;
}

.status-badge {
  padding: 2px 6px;
  font-size: 10px;
  font-weight: 500;
  text-transform: uppercase;
  border-radius: 4px;
}

.status-badge.success {
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
}

.status-badge.warning {
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.1);
}

.status-badge.error {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.1);
}

.status-badge.default {
  color: #6b7280;
  background: rgba(107, 114, 128, 0.1);
}

.sessions-panel.is-dark .status-badge.success {
  background: rgba(16, 185, 129, 0.15);
}

.sessions-panel.is-dark .status-badge.warning {
  background: rgba(245, 158, 11, 0.15);
}

.sessions-panel.is-dark .status-badge.error {
  background: rgba(239, 68, 68, 0.15);
}

.session-goal {
  font-size: 12px;
  color: #4b5563;
  line-height: 1.4;
  margin-bottom: 8px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.sessions-panel.is-dark .session-goal {
  color: #9ca3af;
}

.session-meta {
  display: flex;
  gap: 12px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #9ca3af;
}

.meta-icon {
  width: 12px;
  height: 12px;
}

.empty-state {
  padding: 40px 16px;
  text-align: center;
  font-size: 13px;
  color: #9ca3af;
}
</style>
