<script setup>
import { ref, computed } from 'vue'
import { ChevronDown, ChevronUp, Clock, MessageSquare, CheckCircle, XCircle, Loader2, Layers, Copy, Check, Wrench } from 'lucide-vue-next'
import MessageCard from './MessageCard.vue'
import { formatTimestamp, formatDuration } from '../utils/formatters.js'

const props = defineProps({
  turn: { type: Object, required: true },
  index: { type: Number, default: 0 },
  isDark: { type: Boolean, default: false },
  defaultExpanded: { type: Boolean, default: false }
})

const expanded = ref(props.defaultExpanded)
const copied = ref(false)
const toolsExpanded = ref(false)
const executedToolsExpanded = ref(false)

const roleLabels = {
  assistant: '🤖 AI',
  user: '👤 Human',
  tool: '🔧 Tool',
  system: '⚙️ System'
}

const copyTurnMessages = async () => {
  if (!props.turn.messages?.length) return

  const lines = []
  const agentName = props.turn.agent_name || props.turn.agent_id || 'main'
  const task = props.turn.task_summary || props.turn.phase || ''
  lines.push(`=== ${agentName}${task ? ` | ${task}` : ''} ===`)
  lines.push('')
  
  for (const msg of props.turn.messages) {
    const roleLabel = roleLabels[msg.role] || roleLabels.assistant
    lines.push(`--- ${roleLabel} ---`)
    if (msg.content) {
      lines.push(msg.content)
    }
    if (msg.tool_calls?.length) {
      lines.push('')
      lines.push('[Tool Calls]')
      for (const tc of msg.tool_calls) {
        lines.push(`  - ${tc.name}: ${JSON.stringify(tc.args)}`)
      }
    }
    lines.push('')
  }
  
  const text = lines.join('\n')
  try {
    await navigator.clipboard.writeText(text)
    copied.value = true
    setTimeout(() => copied.value = false, 2000)
  } catch (err) {
    console.error('Failed to copy:', err)
  }
}

const statusConfig = {
  completed: { icon: CheckCircle, color: '#10b981', label: 'completed' },
  error: { icon: XCircle, color: '#ef4444', label: 'error' },
  running: { icon: Loader2, color: '#f59e0b', label: 'running' },
  pending: { icon: Loader2, color: '#f59e0b', label: 'pending' }
}

const config = computed(() => statusConfig[props.turn.status] || statusConfig.pending)

const messageCount = computed(() => props.turn.messages?.length || 0)

const latencyMs = computed(() => {
  if (props.turn.latency_s) return Math.round(props.turn.latency_s * 1000)
  return null
})

const tokenSummary = computed(() => {
  const input = props.turn.input_tokens
  const output = props.turn.output_tokens
  if (input || output) {
    return `${input || 0}→${output || 0}`
  }
  return null
})

const boundTools = computed(() => {
  return props.turn.bound_tools || []
})

const executedToolCalls = computed(() => {
  return props.turn.executed_tool_calls || []
})

const executedToolErrorCount = computed(() => {
  return executedToolCalls.value.filter(t => t.is_error).length
})
</script>

<template>
  <div class="turn-card" :class="{ 'is-dark': isDark }">
    <!-- Header -->
    <div class="turn-header" @click="expanded = !expanded">
      <div class="header-left">
        <span class="agent-name" :title="turn.agent_name || turn.agent_id || 'main'">
          {{ turn.agent_name || turn.agent_id || 'main' }}
        </span>

        <div class="turn-info">
          <div class="turn-title">
            <span v-if="turn.task_summary" class="task-summary" :title="turn.task_summary">
              {{ turn.task_summary }}
            </span>
            <span v-else-if="turn.phase" class="phase-badge">
              {{ turn.phase }}
            </span>
            <span class="status-badge" :style="{ color: config.color, backgroundColor: config.color + '15' }">
              <component :is="config.icon" class="status-icon" :class="{ spin: turn.status === 'running' || turn.status === 'pending' }" />
              {{ config.label }}
            </span>
          </div>
        </div>
      </div>
      
      <div class="header-right">
        <button
          v-if="messageCount > 0"
          class="copy-btn"
          :class="{ 'copied': copied }"
          @click.stop="copyTurnMessages"
          title="复制该回合所有消息"
        >
          <component :is="copied ? Check : Copy" class="copy-icon" />
        </button>
        
        <span v-if="latencyMs" class="meta-item">
          <Clock class="meta-icon" />
          {{ formatDuration(latencyMs) }}
        </span>
        
        <span v-if="tokenSummary" class="meta-item">
          <Layers class="meta-icon" />
          {{ tokenSummary }}
        </span>
        
        <span class="meta-item">
          <MessageSquare class="meta-icon" />
          {{ messageCount }}
        </span>
        
        <span class="timestamp">
          {{ formatTimestamp(turn.started_at, 'HH:mm:ss') }}
        </span>
        
        <component :is="expanded ? ChevronUp : ChevronDown" class="expand-icon" />
      </div>
    </div>
    
    <!-- Body (Messages) -->
    <div v-if="expanded" class="turn-body">
      <!-- Bound Tools Section -->
      <div v-if="boundTools.length" class="bound-tools-section">
        <div class="bound-tools-header" @click.stop="toolsExpanded = !toolsExpanded">
          <span class="section-title">
            <Wrench class="section-icon" />
            Bound Tools ({{ boundTools.length }})
          </span>
          <component :is="toolsExpanded ? ChevronUp : ChevronDown" class="section-expand-icon" />
        </div>
        <div v-if="toolsExpanded" class="bound-tools-list">
          <span v-for="tool in boundTools" :key="tool.id" class="tool-badge" :title="tool.tool_description">
            {{ tool.tool_name }}
          </span>
        </div>
      </div>

      <!-- Executed Tool Calls Section -->
      <div v-if="executedToolCalls.length" class="executed-tools-section">
        <div class="executed-tools-header" @click.stop="executedToolsExpanded = !executedToolsExpanded">
          <span class="section-title executed">
            <Wrench class="section-icon" />
            Executed Calls ({{ executedToolCalls.length }})
            <span v-if="executedToolErrorCount" class="error-count">{{ executedToolErrorCount }} errors</span>
          </span>
          <component :is="executedToolsExpanded ? ChevronUp : ChevronDown" class="section-expand-icon" />
        </div>
        <div v-if="executedToolsExpanded" class="executed-tools-list">
          <div
            v-for="toolCall in executedToolCalls"
            :key="toolCall.id"
            class="executed-tool-row"
            :class="{ 'is-error': toolCall.is_error }"
            :title="toolCall.result_preview"
          >
            <span class="executed-tool-name">{{ toolCall.tool_name }}</span>
            <span v-if="toolCall.mutation_effective === true" class="exec-chip mutation">mutation</span>
            <span v-if="toolCall.mutation_effective === false" class="exec-chip">no-op</span>
            <span class="exec-chip">{{ toolCall.duration_ms }}ms</span>
            <span v-if="toolCall.is_error" class="exec-chip error">error</span>
          </div>
        </div>
      </div>
      
      <div class="messages-container">
        <MessageCard
          v-for="msg in turn.messages"
          :key="msg.id"
          :message="msg"
          :is-dark="isDark"
        />
        <div v-if="!turn.messages.length" class="no-messages">
          No messages in this turn
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.turn-card {
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  overflow: hidden;
  transition: all 0.2s ease;
}

.turn-card.is-dark {
  background: #161b22;
  border-color: #30363d;
}

.turn-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease;
}

.turn-header:hover {
  background: rgba(0, 0, 0, 0.02);
}

.is-dark .turn-header:hover {
  background: rgba(255, 255, 255, 0.02);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.agent-name {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  max-width: 200px;
  padding: 4px 10px;
  background: #f3f4f6;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 600;
  color: #374151;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
}

.is-dark .agent-name {
  background: #21262d;
  color: #e5e7eb;
}

.task-summary {
  font-size: 13px;
  font-weight: 500;
  color: #1f2937;
  max-width: 300px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.is-dark .task-summary {
  color: #e5e7eb;
}

.turn-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.turn-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.iteration-badge {
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  color: #6b7280;
  background: #f3f4f6;
  border-radius: 4px;
}

.is-dark .iteration-badge {
  background: #21262d;
  color: #9ca3af;
}

.phase-badge {
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  color: #3b82f6;
  background: rgba(59, 130, 246, 0.1);
  border-radius: 4px;
}

.is-dark .phase-badge {
  color: #60a5fa;
  background: rgba(59, 130, 246, 0.15);
}

.status-badge {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 4px;
  text-transform: uppercase;
}

.status-icon {
  width: 12px;
  height: 12px;
}

.status-icon.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #6b7280;
}

.is-dark .meta-item {
  color: #9ca3af;
}

.meta-icon {
  width: 14px;
  height: 14px;
}

.timestamp {
  font-size: 12px;
  color: #9ca3af;
  font-family: 'JetBrains Mono', monospace;
}

.expand-icon {
  width: 18px;
  height: 18px;
  color: #9ca3af;
}

.copy-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 4px;
  background: transparent;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.copy-btn:hover {
  background: rgba(0, 0, 0, 0.05);
}

.is-dark .copy-btn:hover {
  background: rgba(255, 255, 255, 0.05);
}

.copy-btn.copied {
  color: #10b981;
}

.copy-icon {
  width: 14px;
  height: 14px;
  color: #9ca3af;
}

.copy-btn:hover .copy-icon {
  color: #6b7280;
}

.copy-btn.copied .copy-icon {
  color: #10b981;
}

.is-dark .copy-icon {
  color: #9ca3af;
}

.is-dark .copy-btn:hover .copy-icon {
  color: #e5e7eb;
}

.turn-body {
  border-top: 1px solid #e5e7eb;
  background: #f9fafb;
}

.is-dark .turn-body {
  border-top-color: #30363d;
  background: #0d1117;
}

.messages-container {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.no-messages {
  text-align: center;
  padding: 32px;
  color: #9ca3af;
  font-size: 13px;
}

.bound-tools-section {
  border-bottom: 1px solid #e5e7eb;
  background: #f0f9ff;
}

.is-dark .bound-tools-section {
  border-bottom-color: #30363d;
  background: #0c1d2b;
}

.executed-tools-section {
  border-bottom: 1px solid #e5e7eb;
  background: #f8fafc;
}

.is-dark .executed-tools-section {
  border-bottom-color: #30363d;
  background: #161f2a;
}

.bound-tools-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease;
}

.bound-tools-header:hover {
  background: rgba(0, 0, 0, 0.03);
}

.is-dark .bound-tools-header:hover {
  background: rgba(255, 255, 255, 0.03);
}

.section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #0369a1;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.is-dark .section-title {
  color: #38bdf8;
}

.section-icon {
  width: 14px;
  height: 14px;
}

.section-expand-icon {
  width: 14px;
  height: 14px;
  color: #9ca3af;
}

.bound-tools-list {
  padding: 0 16px 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tool-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  font-size: 11px;
  font-weight: 500;
  color: #0369a1;
  background: rgba(3, 105, 161, 0.1);
  border-radius: 4px;
  border: 1px solid rgba(3, 105, 161, 0.2);
  cursor: help;
}

.is-dark .tool-badge {
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.15);
  border-color: rgba(56, 189, 248, 0.3);
}

.executed-tools-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease;
}

.executed-tools-header:hover {
  background: rgba(0, 0, 0, 0.03);
}

.is-dark .executed-tools-header:hover {
  background: rgba(255, 255, 255, 0.03);
}

.section-title.executed {
  color: #0f766e;
}

.is-dark .section-title.executed {
  color: #2dd4bf;
}

.error-count {
  margin-left: 6px;
  font-size: 10px;
  color: #ef4444;
  text-transform: none;
  letter-spacing: 0;
}

.executed-tools-list {
  padding: 0 16px 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.executed-tool-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border-radius: 6px;
  border: 1px solid rgba(15, 118, 110, 0.2);
  background: rgba(15, 118, 110, 0.08);
  color: #0f766e;
  font-size: 11px;
}

.executed-tool-row.is-error {
  border-color: rgba(239, 68, 68, 0.35);
  background: rgba(239, 68, 68, 0.08);
  color: #b91c1c;
}

.is-dark .executed-tool-row {
  border-color: rgba(45, 212, 191, 0.35);
  background: rgba(45, 212, 191, 0.15);
  color: #2dd4bf;
}

.is-dark .executed-tool-row.is-error {
  border-color: rgba(248, 113, 113, 0.45);
  background: rgba(248, 113, 113, 0.2);
  color: #fca5a5;
}

.executed-tool-name {
  font-weight: 600;
}

.exec-chip {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.08);
}

.is-dark .exec-chip {
  background: rgba(255, 255, 255, 0.12);
}

.exec-chip.mutation {
  color: #047857;
}

.exec-chip.error {
  color: #b91c1c;
}
</style>
