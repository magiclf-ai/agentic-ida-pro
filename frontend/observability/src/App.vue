<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick, provide } from 'vue'
import {
  Sun, Moon, Download, RefreshCw, LayoutGrid,
  ChevronDown, ChevronUp, Pin, Maximize2, Minimize2,
  Activity, Loader2, X, Bot, User, Wrench, Clock,
  Layers, MessageSquare, CheckCircle, XCircle
} from 'lucide-vue-next'
import SessionsPanel from './components/SessionsPanel.vue'
import MessageCard from './components/MessageCard.vue'
import { useSessions, useSessionDetail } from './composables/useApi.js'
import { formatTimestamp, formatDuration } from './utils/formatters.js'

const { sessions, loading: sessionsLoading, fetchSessions } = useSessions()
const selectedSessionId = ref('')
const isDark = ref(false)
const autoRefresh = ref(true)
const autoScroll = ref(false)
let refreshInterval = null
const messagesPaneRef = ref(null)

// Modal state
const selectedTurn = ref(null)
const showModal = ref(false)
const modalMessagesExpanded = ref(true)
// 用于触发 MessageCard 重新渲染的 key
const messageCardsKey = ref(0)

const {
  turnsWithMessages,
  initialLoading,
  updating,
  fetchAll,
  clearCache
} = useSessionDetail(selectedSessionId)

// Reverse turns for display (newest first)
const reversedTurns = computed(() => {
  const turns = turnsWithMessages.value
  if (!turns.length) return []
  return [...turns].reverse()
})

// Track if user has manually scrolled
const userScrolled = ref(false)
const isNearBottom = ref(true)

function selectSession(id) {
  selectedSessionId.value = id
  userScrolled.value = false
  isNearBottom.value = true
}

function toggleTheme() {
  isDark.value = !isDark.value
  document.documentElement.setAttribute('data-theme', isDark.value ? 'dark' : 'light')
}

function refreshNow() {
  fetchSessions()
  if (selectedSessionId.value) {
    fetchAll({ incremental: true })
  }
}

function scrollToBottom(smooth = true) {
  nextTick(() => {
    if (messagesPaneRef.value) {
      messagesPaneRef.value.scrollTo({
        top: messagesPaneRef.value.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto'
      })
    }
  })
}

function checkScrollPosition() {
  if (!messagesPaneRef.value) return
  const { scrollTop, scrollHeight, clientHeight } = messagesPaneRef.value
  isNearBottom.value = scrollHeight - scrollTop - clientHeight < 100
}

function onScroll() {
  userScrolled.value = true
  checkScrollPosition()
}

function toggleAutoScroll() {
  autoScroll.value = !autoScroll.value
  if (autoScroll.value) {
    scrollToBottom()
  }
}

function toggleAutoRefresh() {
  autoRefresh.value = !autoRefresh.value
  if (autoRefresh.value) {
    startAutoRefresh()
  } else {
    stopAutoRefresh()
  }
}

function startAutoRefresh() {
  if (refreshInterval) clearInterval(refreshInterval)
  refreshInterval = setInterval(() => {
    refreshNow()
  }, 5000)
}

function stopAutoRefresh() {
  if (refreshInterval) {
    clearInterval(refreshInterval)
    refreshInterval = null
  }
}

function exportSession() {
  if (!selectedSessionId.value) return
  const data = {
    session_id: selectedSessionId.value,
    turns: turnsWithMessages.value,
    exported_at: new Date().toISOString()
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `session-${selectedSessionId.value.slice(0, 8)}.json`
  a.click()
  URL.revokeObjectURL(url)
}

// Modal functions
function openTurnModal(turn) {
  selectedTurn.value = turn
  showModal.value = true
}

function closeModal() {
  showModal.value = false
  selectedTurn.value = null
}

function onRowDoubleClick(turn) {
  openTurnModal(turn)
}

function expandAllModalMessages() {
  modalMessagesExpanded.value = true
  messageCardsKey.value++ // 强制重新渲染
}

function collapseAllModalMessages() {
  modalMessagesExpanded.value = false
  messageCardsKey.value++ // 强制重新渲染
}

// Helper functions for table
function getStatusConfig(status) {
  const configs = {
    completed: { icon: CheckCircle, color: '#10b981', bg: 'rgba(16, 185, 129, 0.1)' },
    error: { icon: XCircle, color: '#ef4444', bg: 'rgba(239, 68, 68, 0.1)' },
    running: { icon: Loader2, color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.1)' },
    pending: { icon: Loader2, color: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.1)' }
  }
  return configs[status] || configs.pending
}

function getLatencyMs(turn) {
  if (turn.latency_s) return Math.round(turn.latency_s * 1000)
  return null
}

function getTokenSummary(turn) {
  const input = turn.input_tokens
  const output = turn.output_tokens
  if (input || output) {
    return `${input || 0}→${output || 0}`
  }
  return '-'
}

function getMessageCount(turn) {
  return turn.messages?.length || 0
}

function formatRole(role) {
  const labels = {
    assistant: 'AI',
    user: 'User',
    tool: 'Tool',
    system: 'System'
  }
  return labels[role] || role
}

function getRoleIcon(role) {
  const icons = {
    assistant: Bot,
    user: User,
    tool: Wrench,
    system: Activity
  }
  return icons[role] || Bot
}

function formatContent(content, maxLen = 150) {
  if (!content) return ''
  let text = content
    .replace(/```[\s\S]*?```/g, '[code]')
    .replace(/\s+/g, ' ')
    .trim()
  if (text.length > maxLen) {
    text = text.slice(0, maxLen) + '...'
  }
  return text
}

// Watch for new turns and auto-scroll if enabled
watch(() => turnsWithMessages.value.length, (newLen, oldLen) => {
  if (newLen > oldLen && autoScroll.value && isNearBottom.value) {
    scrollToBottom(true)
  }
})

// Watch for session changes
watch(selectedSessionId, (newId, oldId) => {
  if (newId !== oldId) {
    clearCache()
    fetchAll({ incremental: false })
  }
})

onMounted(() => {
  fetchSessions()
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  isDark.value = prefersDark
  document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light')

  if (autoRefresh.value) {
    startAutoRefresh()
  }
})

onUnmounted(() => {
  stopAutoRefresh()
})
</script>

<template>
  <div class="app" :class="{ 'is-dark': isDark }">
    <!-- Header -->
    <header class="app-header">
      <div class="header-left">
        <div class="logo-wrapper">
          <LayoutGrid class="logo" />
        </div>
        <h1 class="app-title">Agent Observability</h1>
        <div v-if="updating" class="updating-indicator">
          <Loader2 class="spinner" size="14" />
          <span>Updating...</span>
        </div>
      </div>

      <div class="header-actions">
        <!-- Session Actions -->
        <template v-if="selectedSessionId">
          <div class="action-group">
            <button class="action-btn" @click="scrollToBottom" title="滚动到底部">
              <Pin size="16" />
            </button>
            <button
              class="action-btn"
              :class="{ active: autoScroll }"
              @click="toggleAutoScroll"
              title="自动滚动"
            >
              <Activity size="16" />
            </button>
          </div>

          <div class="divider"></div>
        </template>

        <!-- Global Actions -->
        <div class="action-group">
          <button class="action-btn" @click="refreshNow" title="立即刷新">
            <RefreshCw size="16" />
          </button>
          <button
            class="action-btn"
            :class="{ active: autoRefresh }"
            @click="toggleAutoRefresh"
            title="自动刷新"
          >
            <span class="auto-badge">A</span>
          </button>
          <button
            class="action-btn"
            @click="exportSession"
            :disabled="!selectedSessionId"
            title="导出会话"
          >
            <Download size="16" />
          </button>
          <button class="action-btn theme-btn" @click="toggleTheme" title="切换主题">
            <Sun v-if="isDark" size="16" />
            <Moon v-else size="16" />
          </button>
        </div>
      </div>
    </header>

    <!-- Main Content -->
    <main class="app-main">
      <!-- Sessions Sidebar -->
      <aside class="sessions-sidebar">
        <SessionsPanel
          :sessions="sessions"
          :selected-id="selectedSessionId"
          @select="selectSession"
        />
      </aside>

      <!-- Messages Area -->
      <div
        ref="messagesPaneRef"
        class="messages-area"
        @scroll="onScroll"
      >
        <!-- Empty State -->
        <div v-if="!selectedSessionId" class="empty-state">
          <div class="empty-icon">
            <LayoutGrid size="48" />
          </div>
          <h3>选择一个会话</h3>
          <p>从左侧列表选择一个会话来查看详细内容</p>
        </div>

        <!-- Initial Loading -->
        <div v-else-if="initialLoading" class="loading-state">
          <div class="skeleton-table">
            <div class="skeleton-row header">
              <div class="skeleton-cell" v-for="i in 7" :key="i"></div>
            </div>
            <div class="skeleton-row" v-for="i in 5" :key="i">
              <div class="skeleton-cell" v-for="j in 7" :key="j"></div>
            </div>
          </div>
        </div>

        <!-- No Data -->
        <div v-else-if="!turnsWithMessages.length" class="empty-state">
          <div class="empty-icon">
            <Activity size="48" />
          </div>
          <h3>暂无数据</h3>
          <p>该会话暂时没有交互记录</p>
        </div>

        <!-- Turns Table -->
        <div v-else class="turns-table-container">
          <table class="turns-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Agent</th>
                <th>Task</th>
                <th>Status</th>
                <th>Messages</th>
                <th>Tokens</th>
                <th>Latency</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(turn, index) in reversedTurns"
                :key="turn.turn_id"
                @dblclick="onRowDoubleClick(turn)"
                class="turn-row"
                :class="{ 'status-completed': turn.status === 'completed', 'status-error': turn.status === 'error', 'status-running': turn.status === 'running' }"
              >
                <td class="col-index">{{ reversedTurns.length - index }}</td>
                <td class="col-agent">
                  <div class="agent-cell">
                    <span class="agent-icon-small">{{ turn.agent_name?.[0] || 'A' }}</span>
                    <span class="agent-name-text">{{ turn.agent_name || turn.agent_id || 'main' }}</span>
                  </div>
                </td>
                <td class="col-task" :title="turn.task_summary">
                  {{ turn.task_summary || turn.phase || '-' }}
                </td>
                <td class="col-status">
                  <div class="status-cell" :style="{ color: getStatusConfig(turn.status).color }">
                    <component
                      :is="getStatusConfig(turn.status).icon"
                      size="12"
                      :class="{ spin: turn.status === 'running' || turn.status === 'pending' }"
                    />
                    <span>{{ turn.status }}</span>
                  </div>
                </td>
                <td class="col-messages">
                  <div class="messages-cell">
                    <MessageSquare size="12" />
                    {{ getMessageCount(turn) }}
                  </div>
                </td>
                <td class="col-tokens">
                  <div v-if="turn.input_tokens || turn.output_tokens" class="tokens-cell">
                    <Layers size="12" />
                    {{ getTokenSummary(turn) }}
                  </div>
                  <span v-else>-</span>
                </td>
                <td class="col-latency">
                  <div v-if="getLatencyMs(turn)" class="latency-cell">
                    <Clock size="12" />
                    {{ formatDuration(getLatencyMs(turn)) }}
                  </div>
                  <span v-else>-</span>
                </td>
                <td class="col-time">
                  {{ formatTimestamp(turn.started_at, 'MM-DD HH:mm:ss') }}
                </td>
              </tr>
            </tbody>
          </table>

          <!-- Scroll to bottom button -->
          <Transition name="fade">
            <button
              v-if="!isNearBottom && reversedTurns.length > 3"
              class="scroll-to-bottom-btn"
              @click="scrollToBottom"
            >
              <ChevronDown size="20" />
            </button>
          </Transition>
        </div>
      </div>
    </main>

    <!-- Turn Detail Modal -->
    <Transition name="modal">
      <div v-if="showModal && selectedTurn" class="modal-overlay" @click.self="closeModal">
        <div class="modal-container" :class="{ 'is-dark': isDark }">
          <!-- Modal Header -->
          <div class="modal-header">
            <div class="modal-title">
              <span class="modal-agent">{{ selectedTurn.agent_name || selectedTurn.agent_id || 'main' }}</span>
              <span class="modal-divider">|</span>
              <span class="modal-task">{{ selectedTurn.task_summary || selectedTurn.phase || 'No task' }}</span>
            </div>
            <div class="modal-meta">
              <div class="status-cell" :style="{ color: getStatusConfig(selectedTurn.status).color }">
                <component
                  :is="getStatusConfig(selectedTurn.status).icon"
                  size="14"
                  :class="{ spin: selectedTurn.status === 'running' || selectedTurn.status === 'pending' }"
                />
                <span>{{ selectedTurn.status }}</span>
              </div>
              <span class="modal-time">{{ formatTimestamp(selectedTurn.started_at, 'YYYY-MM-DD HH:mm:ss') }}</span>
            </div>
            <button class="modal-close" @click="closeModal">
              <X size="20" />
            </button>
          </div>

          <!-- Modal Body -->
          <div class="modal-body">
            <!-- Messages Header with Expand/Collapse -->
            <div class="modal-messages-header">
              <span class="messages-count">{{ selectedTurn.messages?.length || 0 }} messages</span>
              <div class="messages-actions">
                <button class="text-btn" @click="collapseAllModalMessages">
                  <Minimize2 size="12" />
                  折叠全部
                </button>
                <button class="text-btn" @click="expandAllModalMessages">
                  <Maximize2 size="12" />
                  展开全部
                </button>
              </div>
            </div>

            <!-- Messages -->
            <div class="messages-container">
              <MessageCard
                v-for="(msg, index) in selectedTurn.messages"
                :key="msg.id + '-' + messageCardsKey"
                :message="msg"
                :is-dark="isDark"
                :default-expanded="modalMessagesExpanded"
                :all-messages="selectedTurn.messages"
                :message-index="index"
              />
            </div>

            <!-- Tool Calls Results -->
            <div v-if="selectedTurn.executed_tool_calls?.length" class="executed-tools-section">
              <h4 class="section-title">
                <Wrench size="14" />
                Executed Tools ({{ selectedTurn.executed_tool_calls.length }})
              </h4>
              <div class="executed-tools-list">
                <div
                  v-for="tool in selectedTurn.executed_tool_calls"
                  :key="tool.id"
                  class="executed-tool-item"
                  :class="{ 'is-error': tool.is_error }"
                >
                  <div class="tool-header">
                    <span class="tool-name">{{ tool.tool_name }}</span>
                    <span class="tool-duration">{{ tool.duration_ms }}ms</span>
                  </div>
                  <div v-if="tool.result_preview" class="tool-result">{{ tool.result_preview }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style>
/* Modern Premium Theme with Enhanced Animations */
:root {
  /* Primary Colors - Vibrant gradient */
  --semi-primary: #6366f1;
  --semi-primary-light: #818cf8;
  --semi-primary-dark: #4f46e5;
  --semi-secondary: #ec4899;
  --semi-accent: #06b6d4;
  
  /* Background Colors - Warmer tones */
  --bg-primary: #ffffff;
  --bg-secondary: #f8fafc;
  --bg-tertiary: #f1f5f9;
  --bg-elevated: #ffffff;
  --bg-subtle: #f8fafc;
  
  /* Border Colors - Softer */
  --border-color: #e2e8f0;
  --border-light: #f1f5f9;
  --border-subtle: rgba(226, 232, 240, 0.6);
  
  /* Text Colors - Better contrast */
  --text-primary: #0f172a;
  --text-secondary: #475569;
  --text-muted: #94a3b8;
  --text-placeholder: #cbd5e1;
  
  /* Accent Colors */
  --accent-primary: #6366f1;
  --accent-hover: #4f46e5;
  --accent-light: rgba(99, 102, 241, 0.1);
  --accent-gradient: linear-gradient(135deg, #6366f1 0%, #ec4899 100%);
  
  /* Status Colors - More vibrant */
  --success: #10b981;
  --success-light: rgba(16, 185, 129, 0.12);
  --warning: #f59e0b;
  --warning-light: rgba(245, 158, 11, 0.12);
  --error: #ef4444;
  --error-light: rgba(239, 68, 68, 0.12);
  --info: #3b82f6;
  --info-light: rgba(59, 130, 246, 0.12);
  
  /* Shadows - Multi-layered depth */
  --shadow-xs: 0 1px 2px 0 rgba(0, 0, 0, 0.03);
  --shadow-sm: 0 1px 3px 0 rgba(0, 0, 0, 0.04), 0 1px 2px -1px rgba(0, 0, 0, 0.04);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.06), 0 4px 6px -4px rgba(0, 0, 0, 0.04);
  --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.08), 0 8px 10px -6px rgba(0, 0, 0, 0.04);
  --shadow-glow: 0 0 20px rgba(99, 102, 241, 0.15);
  
  /* Border Radius - Pill and rounded */
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-xl: 24px;
  --radius-full: 9999px;
  
  /* Transitions */
  --transition-fast: 150ms cubic-bezier(0.4, 0, 0.2, 1);
  --transition-base: 250ms cubic-bezier(0.4, 0, 0.2, 1);
  --transition-slow: 350ms cubic-bezier(0.4, 0, 0.2, 1);
  --transition-bounce: 500ms cubic-bezier(0.34, 1.56, 0.64, 1);
}

[data-theme="dark"] {
  --bg-primary: #0f172a;
  --bg-secondary: #1e293b;
  --bg-tertiary: #334155;
  --bg-elevated: #1e293b;
  --bg-subtle: #0f172a;
  
  --border-color: #334155;
  --border-light: #1e293b;
  --border-subtle: rgba(51, 65, 85, 0.6);
  
  --text-primary: #f8fafc;
  --text-secondary: #cbd5e1;
  --text-muted: #64748b;
  --text-placeholder: #475569;
  
  --accent-primary: #818cf8;
  --accent-hover: #a5b4fc;
  --accent-light: rgba(129, 140, 248, 0.15);
  
  --shadow-xs: 0 1px 2px 0 rgba(0, 0, 0, 0.3);
  --shadow-sm: 0 1px 3px 0 rgba(0, 0, 0, 0.3);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.4);
  --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
  --shadow-glow: 0 0 20px rgba(129, 140, 248, 0.2);
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html {
  scroll-behavior: smooth;
}

html, body {
  height: 100%;
  font-family: 'Plus Jakarta Sans', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg-secondary);
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  letter-spacing: -0.01em;
}

.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg-secondary);
  color: var(--text-primary);
  animation: fadeIn 0.5s ease-out;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Header - Premium Glassmorphism */
.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 28px;
  background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%);
  flex-shrink: 0;
  box-shadow: var(--shadow-lg);
  position: relative;
  overflow: hidden;
}

.app-header::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(45deg, 
    rgba(255,255,255,0.1) 0%, 
    rgba(255,255,255,0) 50%,
    rgba(255,255,255,0.05) 100%);
  pointer-events: none;
}

.app-header::after {
  content: '';
  position: absolute;
  top: -50%;
  left: -50%;
  width: 200%;
  height: 200%;
  background: radial-gradient(circle, rgba(255,255,255,0.1) 0%, transparent 70%);
  animation: shimmer 8s ease-in-out infinite;
  pointer-events: none;
}

@keyframes shimmer {
  0%, 100% { transform: translate(0, 0) rotate(0deg); }
  50% { transform: translate(10%, 10%) rotate(180deg); }
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
  position: relative;
  z-index: 1;
}

.logo-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(20px) saturate(180%);
  border-radius: var(--radius-md);
  border: 1px solid rgba(255, 255, 255, 0.25);
  box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1), inset 0 1px 0 rgba(255,255,255,0.2);
  transition: all var(--transition-base);
}

.logo-wrapper:hover {
  transform: translateY(-2px) scale(1.05);
  box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15), inset 0 1px 0 rgba(255,255,255,0.3);
}

.logo {
  width: 24px;
  height: 24px;
  color: white;
  filter: drop-shadow(0 2px 4px rgba(0,0,0,0.1));
}

.app-title {
  font-size: 20px;
  font-weight: 700;
  color: white;
  letter-spacing: -0.5px;
  text-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.updating-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: rgba(255, 255, 255, 0.12);
  backdrop-filter: blur(20px) saturate(180%);
  border-radius: var(--radius-full);
  font-size: 13px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.95);
  margin-left: 12px;
  border: 1px solid rgba(255, 255, 255, 0.2);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
  animation: pulse-subtle 2s ease-in-out infinite;
}

@keyframes pulse-subtle {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.8; }
}

.spinner {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Header Actions - Glass Buttons */
.header-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  position: relative;
  z-index: 1;
}

.action-group {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  background: rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(20px);
  border-radius: var(--radius-lg);
  border: 1px solid rgba(255, 255, 255, 0.12);
}

.action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: rgba(255, 255, 255, 0.85);
  cursor: pointer;
  transition: all var(--transition-base);
  position: relative;
  overflow: hidden;
}

.action-btn::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(255,255,255,0.2), rgba(255,255,255,0));
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.action-btn:hover {
  background: rgba(255, 255, 255, 0.15);
  color: white;
  transform: translateY(-2px);
}

.action-btn:hover::before {
  opacity: 1;
}

.action-btn:active {
  transform: translateY(0);
}

.action-btn.active {
  background: rgba(255, 255, 255, 0.25);
  color: white;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15), inset 0 1px 0 rgba(255,255,255,0.2);
}

.action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  transform: none;
}

.auto-badge {
  font-size: 12px;
  font-weight: 800;
  font-family: 'JetBrains Mono', monospace;
}

.divider {
  width: 1px;
  height: 28px;
  background: rgba(255, 255, 255, 0.15);
}

/* Main Layout */
.app-main {
  display: grid;
  grid-template-columns: 320px 1fr;
  flex: 1;
  overflow: hidden;
  background: var(--bg-secondary);
}

.sessions-sidebar {
  background: var(--bg-primary);
  overflow: hidden;
  border-right: 1px solid var(--border-color);
}

.messages-area {
  overflow-y: auto;
  padding: 32px 40px;
  position: relative;
  background: var(--bg-secondary);
}

/* Empty State - Semi Design Style */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
  color: var(--text-secondary);
}

.empty-icon {
  color: var(--text-muted);
  margin-bottom: 20px;
  opacity: 0.6;
}

.empty-state h3 {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 10px;
}

.empty-state p {
  font-size: 14px;
  color: var(--text-secondary);
}

/* Skeleton Loading - Semi Style */
.loading-state {
  padding: 0;
}

.skeleton-table {
  width: 100%;
  background: var(--bg-primary);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
  border: 1px solid var(--border-light);
}

.skeleton-row {
  display: flex;
  border-bottom: 1px solid var(--border-light);
}

.skeleton-row:last-child {
  border-bottom: none;
}

.skeleton-row.header {
  background: var(--bg-tertiary);
}

.skeleton-cell {
  flex: 1;
  height: 52px;
  margin: 10px;
  background: linear-gradient(90deg, var(--bg-tertiary) 25%, var(--bg-secondary) 50%, var(--bg-tertiary) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  border-radius: var(--radius-sm);
}

.skeleton-row.header .skeleton-cell {
  height: 20px;
}

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* Turns Table - Premium Floating Style */
.turns-table-container {
  max-width: 1400px;
  margin: 0 auto;
  padding-bottom: 80px;
}

.turns-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0 8px;
  background: transparent;
  font-size: 14px;
}

.turns-table thead {
  display: table-header-group;
}

.turns-table th {
  padding: 16px 20px;
  text-align: left;
  font-weight: 600;
  color: var(--text-secondary);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1px;
  white-space: nowrap;
  border: none;
}

.turns-table tbody tr {
  background: var(--bg-primary);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-base);
  animation: slideInUp 0.4s ease-out backwards;
}

.turns-table tbody tr:nth-child(1) { animation-delay: 0.02s; }
.turns-table tbody tr:nth-child(2) { animation-delay: 0.04s; }
.turns-table tbody tr:nth-child(3) { animation-delay: 0.06s; }
.turns-table tbody tr:nth-child(4) { animation-delay: 0.08s; }
.turns-table tbody tr:nth-child(5) { animation-delay: 0.10s; }

@keyframes slideInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.turns-table td {
  padding: 20px;
  color: var(--text-primary);
  border: none;
  transition: all var(--transition-fast);
}

.turns-table td:first-child {
  border-radius: var(--radius-lg) 0 0 var(--radius-lg);
}

.turns-table td:last-child {
  border-radius: 0 var(--radius-lg) var(--radius-lg) 0;
}

.turn-row {
  cursor: pointer;
}

.turn-row:hover {
  background: var(--bg-primary);
  transform: translateY(-3px) scale(1.005);
  box-shadow: var(--shadow-lg), var(--shadow-glow);
}

.is-dark .turn-row:hover {
  background: var(--bg-elevated);
}

.turn-row.status-completed td:first-child {
  position: relative;
}

.turn-row.status-completed td:first-child::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 4px;
  height: 24px;
  background: var(--success);
  border-radius: 0 4px 4px 0;
  animation: statusPulse 2s ease-in-out infinite;
}

.turn-row.status-error td:first-child::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 4px;
  height: 24px;
  background: var(--error);
  border-radius: 0 4px 4px 0;
}

.turn-row.status-running td:first-child::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 4px;
  height: 24px;
  background: var(--warning);
  border-radius: 0 4px 4px 0;
  animation: statusPulse 1.5s ease-in-out infinite;
}

@keyframes statusPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.col-index {
  width: 60px;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
  font-size: 14px;
  font-weight: 600;
}

.col-agent {
  width: 160px;
}

.agent-cell {
  display: flex;
  align-items: center;
  gap: 12px;
}

.agent-icon-small {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--accent-gradient);
  color: white;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
  box-shadow: var(--shadow-md);
  animation: float 3s ease-in-out infinite;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-3px); }
}

.agent-name-text {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  color: var(--text-primary);
  font-size: 14px;
}

.col-task {
  max-width: 400px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
  font-weight: 500;
}

.col-status {
  width: 120px;
}

.status-cell {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-radius: var(--radius-full);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  transition: all var(--transition-fast);
}

.col-messages, .col-tokens, .col-latency {
  width: 110px;
}

.messages-cell, .tokens-cell, .latency-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--text-secondary);
  font-size: 14px;
  font-weight: 500;
}

.col-time {
  width: 160px;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  font-weight: 500;
}

/* Transition Animations */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* Scroll to bottom button - Premium Floating */
.scroll-to-bottom-btn {
  position: fixed;
  bottom: 40px;
  right: 48px;
  width: 56px;
  height: 56px;
  border-radius: var(--radius-full);
  background: var(--accent-gradient);
  color: white;
  border: none;
  box-shadow: var(--shadow-xl), 0 0 0 4px rgba(99, 102, 241, 0.15);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--transition-bounce);
  z-index: 100;
  animation: bounceIn 0.5s ease-out;
}

@keyframes bounceIn {
  0% { opacity: 0; transform: scale(0.5); }
  60% { transform: scale(1.1); }
  100% { opacity: 1; transform: scale(1); }
}

.scroll-to-bottom-btn:hover {
  transform: translateY(-4px) scale(1.1);
  box-shadow: var(--shadow-xl), 0 0 30px rgba(99, 102, 241, 0.3);
}

.scroll-to-bottom-btn:active {
  transform: translateY(-2px) scale(1.05);
}

/* Modal Styles - Premium Glass */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(15, 23, 42, 0.6);
  backdrop-filter: blur(20px) saturate(180%);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 32px;
  animation: fadeIn 0.3s ease-out;
}

.modal-container {
  background: var(--bg-primary);
  border-radius: var(--radius-xl);
  border: 1px solid var(--border-subtle);
  width: 100%;
  max-width: 1000px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-xl), 0 0 0 1px rgba(255,255,255,0.05);
  overflow: hidden;
  animation: modalSlideIn 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes modalSlideIn {
  from {
    opacity: 0;
    transform: translateY(30px) scale(0.96);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 24px 28px;
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.06), rgba(236, 72, 153, 0.04));
  border-bottom: 1px solid var(--border-light);
  gap: 20px;
}

.modal-title {
  display: flex;
  align-items: center;
  gap: 14px;
  flex: 1;
  min-width: 0;
}

.modal-agent {
  font-weight: 700;
  color: var(--accent-primary);
  font-size: 14px;
  padding: 8px 16px;
  background: var(--accent-light);
  border-radius: var(--radius-full);
  border: 1px solid rgba(99, 102, 241, 0.2);
  animation: glow 2s ease-in-out infinite;
}

@keyframes glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(99, 102, 241, 0.2); }
  50% { box-shadow: 0 0 20px 4px rgba(99, 102, 241, 0.1); }
}

.modal-divider {
  color: var(--text-muted);
  font-weight: 300;
}

.modal-task {
  color: var(--text-secondary);
  font-size: 15px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.modal-meta {
  display: flex;
  align-items: center;
  gap: 20px;
}

.modal-time {
  font-size: 13px;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
  font-weight: 500;
  padding: 8px 14px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-full);
  border: 1px solid var(--border-subtle);
}

.modal-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: var(--radius-full);
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition-bounce);
}

.modal-close:hover {
  background: var(--error-light);
  color: var(--error);
  transform: rotate(90deg) scale(1.1);
}

.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 28px;
  display: flex;
  flex-direction: column;
  gap: 24px;
  background: var(--bg-secondary);
}

.modal-messages-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 24px;
  background: var(--bg-primary);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border-subtle);
  transition: all var(--transition-base);
}

.modal-messages-header:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

.messages-count {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
  font-family: 'Plus Jakarta Sans', sans-serif;
}

.messages-actions {
  display: flex;
  gap: 12px;
}

.text-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 18px;
  border: none;
  border-radius: var(--radius-full);
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--transition-base);
}

.text-btn:hover {
  background: var(--accent-light);
  color: var(--accent-primary);
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.messages-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* Message Item Styles in Modal - Premium Cards */
.message-item {
  border-radius: var(--radius-xl);
  background: var(--bg-primary);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
  border: 1px solid var(--border-light);
  transition: all var(--transition-base);
  animation: messageSlideIn 0.4s ease-out backwards;
}

.message-item:hover {
  box-shadow: var(--shadow-lg);
  transform: translateY(-2px);
}

@keyframes messageSlideIn {
  from {
    opacity: 0;
    transform: translateX(-20px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.message-item.role-user {
  border-left: 4px solid #3b82f6;
}

.message-item.role-assistant {
  border-left: 4px solid #6366f1;
}

.message-item.role-tool {
  border-left: 4px solid #10b981;
}

.message-item.role-system {
  border-left: 4px solid #64748b;
}

.message-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  background: linear-gradient(135deg, var(--bg-tertiary), rgba(255,255,255,0.3));
  border-bottom: 1px solid var(--border-light);
}

.message-role {
  font-weight: 700;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 1px;
}

.message-item.role-user .message-role {
  color: #3b82f6;
}

.message-item.role-assistant .message-role {
  color: #6366f1;
}

.message-item.role-tool .message-role {
  color: #10b981;
}

.message-item.role-system .message-role {
  color: #64748b;
}

.message-time {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
  font-weight: 500;
  padding: 6px 12px;
  background: var(--bg-secondary);
  border-radius: var(--radius-full);
  border: 1px solid var(--border-subtle);
}

.message-content {
  padding: 20px;
}

.message-content pre {
  margin: 0;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: 'JetBrains Mono', monospace;
  font-size: 14px;
  line-height: 1.8;
  color: var(--text-primary);
}

.tool-calls {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px dashed var(--border-color);
}

.tool-calls-title {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-secondary);
  text-transform: uppercase;
  margin-bottom: 16px;
  letter-spacing: 1px;
}

.tool-call-item {
  background: linear-gradient(135deg, var(--bg-tertiary), rgba(255,255,255,0.5));
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  margin-bottom: 12px;
  border: 1px solid var(--border-light);
  transition: all var(--transition-base);
}

.tool-call-item:hover {
  transform: translateX(4px);
  box-shadow: var(--shadow-md);
  border-color: var(--accent-primary);
}

.tool-call-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 14px;
  font-weight: 700;
  color: var(--accent-primary);
  margin-bottom: 10px;
}

.tool-call-args {
  margin: 0;
  padding: 14px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--text-secondary);
  overflow-x: auto;
  font-family: 'JetBrains Mono', monospace;
  line-height: 1.6;
}

.executed-tools-section {
  background: var(--bg-primary);
  border-radius: var(--radius-xl);
  padding: 24px;
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border-light);
  transition: all var(--transition-base);
}

.executed-tools-section:hover {
  box-shadow: var(--shadow-md);
}

.executed-tools-section .section-title {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border-light);
}

.executed-tools-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.executed-tool-item {
  padding: 18px 20px;
  background: linear-gradient(135deg, var(--success-light), rgba(255,255,255,0.3));
  border: 1px solid rgba(16, 185, 129, 0.2);
  border-radius: var(--radius-lg);
  transition: all var(--transition-base);
  animation: slideInRight 0.4s ease-out backwards;
}

@keyframes slideInRight {
  from {
    opacity: 0;
    transform: translateX(20px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.executed-tool-item:hover {
  transform: translateX(8px);
  box-shadow: var(--shadow-md);
}

.executed-tool-item.is-error {
  background: linear-gradient(135deg, var(--error-light), rgba(255,255,255,0.3));
  border-color: rgba(239, 68, 68, 0.2);
}

.tool-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.tool-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 14px;
  font-weight: 700;
  color: var(--success);
}

.executed-tool-item.is-error .tool-name {
  color: var(--error);
}

.tool-duration {
  font-size: 12px;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
  font-weight: 600;
  padding: 6px 12px;
  background: rgba(255, 255, 255, 0.6);
  border-radius: var(--radius-full);
  border: 1px solid var(--border-subtle);
}

.tool-result {
  font-size: 14px;
  color: var(--text-secondary);
  white-space: pre-wrap;
  line-height: 1.6;
}

/* Modal Transitions */
.modal-enter-active,
.modal-leave-active {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-from .modal-container,
.modal-leave-to .modal-container {
  transform: scale(0.96) translateY(20px);
  opacity: 0;
}

/* Responsive - Premium Mobile */
@media (max-width: 1200px) {
  .app-main {
    grid-template-columns: 280px 1fr;
  }
  
  .messages-area {
    padding: 24px 28px;
  }
}

@media (max-width: 992px) {
  .app-main {
    grid-template-columns: 260px 1fr;
  }
  
  .app-header {
    padding: 14px 20px;
  }
  
  .messages-area {
    padding: 20px 24px;
  }
  
  .turns-table td {
    padding: 16px;
  }
}

@media (max-width: 768px) {
  .app-main {
    grid-template-columns: 1fr;
  }

  .sessions-sidebar {
    display: none;
  }
  
  .app-header {
    padding: 12px 16px;
  }
  
  .app-title {
    font-size: 16px;
  }
  
  .logo-wrapper {
    width: 36px;
    height: 36px;
  }

  .header-actions {
    gap: 10px;
  }

  .action-group {
    padding: 6px;
  }

  .action-btn {
    width: 36px;
    height: 36px;
  }
  
  .messages-area {
    padding: 16px;
  }

  .turns-table {
    font-size: 13px;
    border-spacing: 0 6px;
  }

  .turns-table th,
  .turns-table td {
    padding: 14px 16px;
  }

  .col-task {
    max-width: 100px;
  }
  
  .agent-icon-small {
    width: 32px;
    height: 32px;
    font-size: 12px;
  }
  
  .scroll-to-bottom-btn {
    width: 48px;
    height: 48px;
    right: 24px;
    bottom: 24px;
  }
  
  .modal-overlay {
    padding: 16px;
  }
  
  .modal-container {
    border-radius: var(--radius-lg);
  }
  
  .modal-header {
    padding: 16px 20px;
  }
  
  .modal-body {
    padding: 20px;
  }

  .col-time {
    display: none;
  }
}

@media (max-width: 480px) {
  .app-title {
    display: none;
  }
  
  .turns-table {
    font-size: 12px;
  }
  
  .turns-table th,
  .turns-table td {
    padding: 12px;
  }
  
  .col-time,
  .col-latency {
    display: none;
  }
}
</style>
