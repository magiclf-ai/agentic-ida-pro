<script setup>
import { computed } from 'vue'
import { Search, RefreshCw, Activity, Clock, Hash, FileCode } from 'lucide-vue-next'
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

// Extract filename from binary_name or path
function getBinaryName(session) {
  if (session.binary_name) return session.binary_name
  // Try to extract from first user message if it contains file path
  if (session.goal) {
    const match = session.goal.match(/[\\/]([^\\/]+\.(exe|dll|so|dylib|elf|bin|i64|idb))/i)
    if (match) return match[1]
  }
  return ''
}

const filteredSessions = computed(() => {
  if (!props.searchQuery) return props.sessions
  const query = props.searchQuery.toLowerCase()
  return props.sessions.filter(s =>
    (s.session_id && s.session_id.toLowerCase().includes(query)) ||
    (s.goal && s.goal.toLowerCase().includes(query)) ||
    (s.binary_name && s.binary_name.toLowerCase().includes(query))
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
    <!-- Panel Header -->
    <div class="panel-header">
      <div class="panel-title-wrapper">
        <div class="panel-icon">
          <Activity class="title-icon" />
        </div>
        <h2 class="panel-title">
          Sessions
          <span class="count">{{ sessions.length }}</span>
        </h2>
      </div>
      <button class="refresh-btn" @click="$emit('refresh')" title="刷新">
        <RefreshCw class="icon" />
      </button>
    </div>

    <!-- Search Box -->
    <div class="search-box">
      <Search class="search-icon" />
      <input
        type="text"
        :value="searchQuery"
        @input="$emit('update:searchQuery', $event.target.value)"
        placeholder="搜索会话..."
        class="search-input"
      />
    </div>

    <!-- Sessions List -->
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

        <div v-if="session.goal || getBinaryName(session)" class="session-secondary">
          <span v-if="getBinaryName(session)" class="binary-name">
            <FileCode class="binary-icon" />
            {{ getBinaryName(session) }}
          </span>
          <span v-else class="session-id-text">{{ formatSessionId(session.session_id) }}</span>
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
        <Activity class="empty-icon" />
        <p>暂无会话</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Premium Sessions Panel */
.sessions-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-primary, #ffffff);
  font-family: 'Plus Jakarta Sans', 'Inter', sans-serif;
}

.sessions-panel.is-dark {
  background: var(--bg-primary, #0f172a);
}

/* Panel Header */
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 24px 24px 20px;
}

.panel-title-wrapper {
  display: flex;
  align-items: center;
  gap: 14px;
}

.panel-icon {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #6366f1, #ec4899);
  border-radius: 12px;
  box-shadow: 0 4px 14px rgba(99, 102, 241, 0.35);
  animation: float 3s ease-in-out infinite;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-3px); }
}

.title-icon {
  width: 20px;
  height: 20px;
  color: white;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary, #0f172a);
  letter-spacing: -0.5px;
}

.sessions-panel.is-dark .panel-title {
  color: var(--text-primary, #f8fafc);
}

.count {
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-secondary, #475569);
  background: var(--bg-tertiary, #f1f5f9);
  border-radius: 20px;
  font-family: 'JetBrains Mono', monospace;
}

.sessions-panel.is-dark .count {
  color: var(--text-secondary, #cbd5e1);
  background: var(--bg-tertiary, #334155);
}

.refresh-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 12px;
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--text-secondary, #475569);
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.refresh-btn:hover {
  background: linear-gradient(135deg, #6366f1, #ec4899);
  color: white;
  transform: translateY(-3px) rotate(180deg);
  box-shadow: 0 6px 20px rgba(99, 102, 241, 0.4);
}

.refresh-btn:active {
  transform: translateY(-1px) rotate(180deg);
}

.icon {
  width: 18px;
  height: 18px;
}

/* Search Box */
.search-box {
  position: relative;
  padding: 0 24px 20px;
}

.search-icon {
  position: absolute;
  left: 38px;
  top: 50%;
  transform: translateY(-50%);
  width: 18px;
  height: 18px;
  color: var(--text-muted, #94a3b8);
  pointer-events: none;
  transition: all 0.3s ease;
}

.search-input:focus + .search-icon,
.search-box:focus-within .search-icon {
  color: #6366f1;
}

.search-input {
  width: 100%;
  padding: 14px 18px 14px 50px;
  font-size: 15px;
  font-weight: 500;
  color: var(--text-primary, #0f172a);
  background: var(--bg-secondary, #f8fafc);
  border: 2px solid var(--border-color, #e2e8f0);
  border-radius: 14px;
  outline: none;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  font-family: 'Plus Jakarta Sans', sans-serif;
}

.search-input::placeholder {
  color: var(--text-muted, #94a3b8);
  font-weight: 400;
}

.search-input:focus {
  background: var(--bg-primary, #ffffff);
  border-color: #6366f1;
  box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.12);
}

.sessions-panel.is-dark .search-input {
  color: var(--text-primary, #f8fafc);
  background: var(--bg-secondary, #1e293b);
  border-color: var(--border-color, #334155);
}

.sessions-panel.is-dark .search-input:focus {
  background: var(--bg-primary, #0f172a);
  border-color: #818cf8;
  box-shadow: 0 0 0 4px rgba(129, 140, 248, 0.15);
}

/* Sessions List */
.sessions-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 20px 20px;
}

.session-item {
  padding: 18px;
  margin-bottom: 12px;
  background: var(--bg-primary, #ffffff);
  border: 1px solid var(--border-light, #f1f5f9);
  border-radius: 16px;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.02);
  animation: slideInLeft 0.4s ease-out backwards;
}

.session-item:nth-child(1) { animation-delay: 0.02s; }
.session-item:nth-child(2) { animation-delay: 0.04s; }
.session-item:nth-child(3) { animation-delay: 0.06s; }
.session-item:nth-child(4) { animation-delay: 0.08s; }
.session-item:nth-child(5) { animation-delay: 0.10s; }

@keyframes slideInLeft {
  from {
    opacity: 0;
    transform: translateX(-20px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.session-item:hover {
  border-color: rgba(99, 102, 241, 0.3);
  box-shadow: 0 8px 24px rgba(99, 102, 241, 0.12);
  transform: translateY(-3px);
}

.session-item.active {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.08), rgba(236, 72, 153, 0.05));
  border-color: #6366f1;
  box-shadow: 0 8px 28px rgba(99, 102, 241, 0.18);
}

.sessions-panel.is-dark .session-item {
  background: var(--bg-elevated, #1e293b);
  border-color: var(--border-color, #334155);
}

.sessions-panel.is-dark .session-item:hover {
  border-color: rgba(129, 140, 248, 0.4);
  background: var(--bg-secondary, #1e293b);
}

.sessions-panel.is-dark .session-item.active {
  background: linear-gradient(135deg, rgba(129, 140, 248, 0.12), rgba(236, 72, 153, 0.08));
  border-color: #818cf8;
}

.session-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 10px;
  gap: 12px;
}

.session-goal-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary, #0f172a);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  flex: 1;
}

.sessions-panel.is-dark .session-goal-title {
  color: var(--text-primary, #f8fafc);
}

.session-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 14px;
  font-weight: 700;
  color: #6366f1;
  flex: 1;
}

.sessions-panel.is-dark .session-id {
  color: #818cf8;
}

.session-secondary {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: var(--text-muted, #94a3b8);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
}

.session-id-text {
  font-size: 12px;
  color: var(--text-muted, #94a3b8);
}

.binary-name {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #6366f1;
  font-weight: 600;
  padding: 6px 12px;
  background: rgba(99, 102, 241, 0.1);
  border-radius: 8px;
  font-size: 12px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sessions-panel.is-dark .binary-name {
  color: #818cf8;
  background: rgba(129, 140, 248, 0.15);
}

.binary-icon {
  width: 14px;
  height: 14px;
}

.status-badge {
  padding: 5px 12px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  border-radius: 20px;
  white-space: nowrap;
  flex-shrink: 0;
}

.status-badge.success {
  color: #10b981;
  background: rgba(16, 185, 129, 0.12);
}

.status-badge.warning {
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.12);
}

.status-badge.error {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.12);
}

.status-badge.default {
  color: var(--text-secondary, #475569);
  background: var(--bg-tertiary, #f1f5f9);
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

.sessions-panel.is-dark .status-badge.default {
  background: var(--bg-tertiary, #334155);
}

.session-meta {
  display: flex;
  gap: 20px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-muted, #94a3b8);
}

.meta-icon {
  width: 14px;
  height: 14px;
}

.empty-state {
  padding: 80px 20px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.empty-icon {
  width: 56px;
  height: 56px;
  color: var(--text-muted, #94a3b8);
  opacity: 0.4;
}

.empty-state p {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-muted, #94a3b8);
}

/* Scrollbar Styling */
.sessions-list::-webkit-scrollbar {
  width: 8px;
}

.sessions-list::-webkit-scrollbar-track {
  background: transparent;
  margin: 8px 0;
}

.sessions-list::-webkit-scrollbar-thumb {
  background: var(--border-color, #e2e8f0);
  border-radius: 4px;
  border: 2px solid transparent;
  background-clip: padding-box;
}

.sessions-list::-webkit-scrollbar-thumb:hover {
  background: var(--text-muted, #94a3b8);
}

.sessions-panel.is-dark .sessions-list::-webkit-scrollbar-thumb {
  background: var(--border-color, #334155);
}

.sessions-panel.is-dark .sessions-list::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary, #64748b);
}
</style>
