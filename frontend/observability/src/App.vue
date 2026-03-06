<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { Sun, Moon, Download, RefreshCw, LayoutGrid } from 'lucide-vue-next'
import SessionsPanel from './components/SessionsPanel.vue'
import TurnCard from './components/TurnCard.vue'
import { useSessions, useSessionDetail } from './composables/useApi.js'

const { sessions, loading: sessionsLoading, fetchSessions } = useSessions()
const selectedSessionId = ref('')
const isDark = ref(false)
const autoRefresh = ref(false)
let refreshInterval = null

const { turnsWithMessages, loading, fetchAll } = useSessionDetail(selectedSessionId)

const reversedTurns = computed(() => {
  return [...turnsWithMessages.value].reverse()
})

function selectSession(id) {
  selectedSessionId.value = id
}

function toggleTheme() {
  isDark.value = !isDark.value
  document.documentElement.setAttribute('data-theme', isDark.value ? 'dark' : 'light')
}

function refreshNow() {
  fetchSessions()
  if (selectedSessionId.value) fetchAll()
}

function toggleAutoRefresh() {
  autoRefresh.value = !autoRefresh.value
  if (autoRefresh.value) {
    refreshInterval = setInterval(() => {
      refreshNow()
    }, 5000)
  } else {
    clearInterval(refreshInterval)
  }
}

function exportSession() {
  if (!selectedSessionId.value) return
  const data = {
    session_id: selectedSessionId.value,
    turns: turns.value,
    messages: messages.value,
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

onMounted(() => {
  fetchSessions()
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  isDark.value = prefersDark
  document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light')
})

onUnmounted(() => {
  if (refreshInterval) clearInterval(refreshInterval)
})

watch(selectedSessionId, fetchAll)
</script>

<template>
  <div class="app" :class="{ 'is-dark': isDark }">
    <header class="app-header">
      <div class="header-left">
        <LayoutGrid class="logo" />
        <h1 class="app-title">Agent Observability</h1>
      </div>
      <div class="header-actions">
        <button class="action-btn" @click="refreshNow">
          <RefreshCw class="icon" />
        </button>
        <button class="action-btn" :class="{ active: autoRefresh }" @click="toggleAutoRefresh" title="自动刷新">
          <span class="auto-refresh-icon">A</span>
        </button>
        <button class="action-btn" @click="exportSession" :disabled="!selectedSessionId">
          <Download class="icon" />
        </button>
        <button class="action-btn" @click="toggleTheme">
          <Sun v-if="isDark" class="icon" />
          <Moon v-else class="icon" />
        </button>
      </div>
    </header>
    
    <main class="app-main">
      <SessionsPanel
        :sessions="sessions"
        :selected-id="selectedSessionId"
        @select="selectSession"
        class="sessions-pane"
      />
      
      <div class="messages-pane">
        <div v-if="!selectedSessionId" class="empty-state">
          <p>Select a session to view turns</p>
        </div>
        <div v-else-if="loading" class="loading">Loading...</div>
        <div v-else-if="!turnsWithMessages.length" class="empty-state">
          <p>No turns yet</p>
        </div>
        <div v-else class="turns-list">
          <TurnCard
            v-for="(turn, index) in reversedTurns"
            :key="turn.turn_id"
            :turn="turn"
            :index="reversedTurns.length - 1 - index"
            :is-dark="isDark"
            :default-expanded="index === 0"
          />
        </div>
      </div>
    </main>
  </div>
</template>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }

html, body {
  height: 100%;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #f9fafb;
  color: #1f2937;
}

.app.is-dark {
  background: #0d1117;
  color: #e5e7eb;
}

.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  background: #ffffff;
  border-bottom: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.app.is-dark .app-header {
  background: #161b22;
  border-bottom-color: #30363d;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo {
  width: 28px;
  height: 28px;
  color: #3b82f6;
}

.app-title {
  font-size: 18px;
  font-weight: 600;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #6b7280;
  cursor: pointer;
}

.action-btn:hover {
  background: #f3f4f6;
  color: #374151;
}

.action-btn.active {
  background: #eff6ff;
  color: #3b82f6;
}

.app.is-dark .action-btn {
  color: #9ca3af;
}

.app.is-dark .action-btn:hover {
  background: #21262d;
  color: #e5e7eb;
}

.icon {
  width: 18px;
  height: 18px;
}

.icon.spinning {
  animation: spin 1s linear infinite;
}

.auto-refresh-icon {
  font-size: 12px;
  font-weight: 600;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.app-main {
  display: grid;
  grid-template-columns: 300px 1fr;
  flex: 1;
  overflow: hidden;
}

.sessions-pane {
  border-right: 1px solid #e5e7eb;
}

.app.is-dark .sessions-pane {
  border-right-color: #30363d;
}

.messages-pane {
  overflow-y: auto;
  padding: 16px;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #9ca3af;
}

.loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #6b7280;
}

.turns-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 900px;
  margin: 0 auto;
}
</style>
