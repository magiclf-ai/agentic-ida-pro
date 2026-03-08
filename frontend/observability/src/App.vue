<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import {
  Sun, Moon, Download, RefreshCw, LayoutGrid,
  ChevronDown, ChevronUp, Pin, Maximize2, Minimize2,
  Activity, Loader2
} from 'lucide-vue-next'
import SessionsPanel from './components/SessionsPanel.vue'
import TurnCard from './components/TurnCard.vue'
import { useSessions, useSessionDetail } from './composables/useApi.js'

const { sessions, loading: sessionsLoading, fetchSessions } = useSessions()
const selectedSessionId = ref('')
const isDark = ref(false)
const autoRefresh = ref(true)
const autoScroll = ref(false)
let refreshInterval = null
const messagesPaneRef = ref(null)

const {
  turnsWithMessages,
  initialLoading,
  updating,
  fetchAll,
  setTurnExpanded,
  isTurnExpanded,
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

function onTurnExpanded(turnId, expanded) {
  setTurnExpanded(turnId, expanded)
}

function expandAllTurns() {
  reversedTurns.value.forEach(turn => {
    setTurnExpanded(turn.turn_id, true)
  })
}

function collapseAllTurns() {
  reversedTurns.value.forEach(turn => {
    setTurnExpanded(turn.turn_id, false)
  })
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
            <button class="action-btn" @click="expandAllTurns" title="展开所有">
              <Maximize2 size="16" />
            </button>
            <button class="action-btn" @click="collapseAllTurns" title="折叠所有">
              <Minimize2 size="16" />
            </button>
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
          <div class="skeleton-container">
            <div v-for="i in 3" :key="i" class="skeleton-turn">
              <div class="skeleton-header">
                <div class="skeleton-badge"></div>
                <div class="skeleton-title"></div>
              </div>
              <div class="skeleton-body">
                <div class="skeleton-line"></div>
                <div class="skeleton-line short"></div>
              </div>
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

        <!-- Turns List -->
        <div v-else class="turns-container">
          <TransitionGroup name="turn-list">
            <TurnCard
              v-for="(turn, index) in reversedTurns"
              :key="turn.turn_id"
              :turn="turn"
              :index="reversedTurns.length - 1 - index"
              :is-dark="isDark"
              :default-expanded="isTurnExpanded(turn.turn_id) ?? (index === 0)"
              :is-updating="updating"
              @expanded-change="onTurnExpanded(turn.turn_id, $event)"
            />
          </TransitionGroup>

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
  </div>
</template>

<style>
:root {
  --bg-primary: #ffffff;
  --bg-secondary: #f8fafc;
  --bg-tertiary: #f1f5f9;
  --border-color: #e2e8f0;
  --text-primary: #1e293b;
  --text-secondary: #64748b;
  --text-muted: #94a3b8;
  --accent-primary: #3b82f6;
  --accent-hover: #2563eb;
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1);
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
}

[data-theme="dark"] {
  --bg-primary: #0f172a;
  --bg-secondary: #1e293b;
  --bg-tertiary: #334155;
  --border-color: #334155;
  --text-primary: #f1f5f9;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --accent-primary: #60a5fa;
  --accent-hover: #3b82f6;
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.3);
  --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.4);
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body {
  height: 100%;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg-secondary);
  color: var(--text-primary);
}

.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg-secondary);
  color: var(--text-primary);
}

/* Header */
.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
  box-shadow: var(--shadow-sm);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  background: linear-gradient(135deg, var(--accent-primary), #8b5cf6);
  border-radius: var(--radius-sm);
}

.logo {
  width: 20px;
  height: 20px;
  color: white;
}

.app-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.updating-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: var(--bg-tertiary);
  border-radius: 20px;
  font-size: 12px;
  color: var(--text-secondary);
  margin-left: 8px;
}

.spinner {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Header Actions */
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.action-group {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
}

.action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s ease;
}

.action-btn:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.action-btn.active {
  background: var(--accent-primary);
  color: white;
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.auto-badge {
  font-size: 11px;
  font-weight: 600;
}

.divider {
  width: 1px;
  height: 24px;
  background: var(--border-color);
}

/* Main Layout */
.app-main {
  display: grid;
  grid-template-columns: 280px 1fr;
  flex: 1;
  overflow: hidden;
}

.sessions-sidebar {
  border-right: 1px solid var(--border-color);
  background: var(--bg-primary);
  overflow: hidden;
}

.messages-area {
  overflow-y: auto;
  padding: 20px;
  position: relative;
}

/* Empty State */
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
  margin-bottom: 16px;
  opacity: 0.5;
}

.empty-state h3 {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.empty-state p {
  font-size: 14px;
  color: var(--text-secondary);
}

/* Skeleton Loading */
.loading-state {
  padding: 20px 0;
}

.skeleton-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 900px;
  margin: 0 auto;
}

.skeleton-turn {
  background: var(--bg-primary);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color);
  overflow: hidden;
}

.skeleton-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid var(--border-color);
}

.skeleton-badge {
  width: 60px;
  height: 24px;
  background: linear-gradient(90deg, var(--bg-tertiary) 25%, var(--bg-secondary) 50%, var(--bg-tertiary) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  border-radius: 4px;
}

.skeleton-title {
  flex: 1;
  height: 16px;
  background: linear-gradient(90deg, var(--bg-tertiary) 25%, var(--bg-secondary) 50%, var(--bg-tertiary) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  border-radius: 4px;
}

.skeleton-body {
  padding: 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.skeleton-line {
  height: 12px;
  background: linear-gradient(90deg, var(--bg-tertiary) 25%, var(--bg-secondary) 50%, var(--bg-tertiary) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  border-radius: 4px;
}

.skeleton-line.short {
  width: 60%;
}

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* Turns Container */
.turns-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 900px;
  margin: 0 auto;
  padding-bottom: 40px;
}

/* Transition Animations */
.turn-list-enter-active,
.turn-list-leave-active {
  transition: all 0.3s ease;
}

.turn-list-enter-from {
  opacity: 0;
  transform: translateY(-20px);
}

.turn-list-leave-to {
  opacity: 0;
  transform: translateY(20px);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* Scroll to bottom button */
.scroll-to-bottom-btn {
  position: fixed;
  bottom: 30px;
  right: 40px;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--accent-primary);
  color: white;
  border: none;
  box-shadow: var(--shadow-md);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  z-index: 100;
}

.scroll-to-bottom-btn:hover {
  background: var(--accent-hover);
  transform: translateY(-2px);
}

/* Responsive */
@media (max-width: 768px) {
  .app-main {
    grid-template-columns: 1fr;
  }

  .sessions-sidebar {
    display: none;
  }

  .header-actions {
    gap: 8px;
  }

  .action-group {
    padding: 2px;
  }

  .action-btn {
    width: 28px;
    height: 28px;
  }
}
</style>
