<script setup>
import { ref, computed, provide, watch, nextTick } from 'vue'
import {
  ChevronDown, ChevronUp, Clock, MessageSquare,
  CheckCircle, XCircle, Loader2, Layers, Copy, Check,
  Wrench, Minimize2, Maximize2, Bot, User, Terminal
} from 'lucide-vue-next'
import MessageCard from './MessageCard.vue'
import { formatTimestamp, formatDuration } from '../utils/formatters.js'

const props = defineProps({
  turn: { type: Object, required: true },
  index: { type: Number, default: 0 },
  isDark: { type: Boolean, default: false },
  defaultExpanded: { type: Boolean, default: false },
  isUpdating: { type: Boolean, default: false }
})

const emit = defineEmits(['expanded-change'])

// Local state - only sync with props on mount or turn change
const expanded = ref(props.defaultExpanded)
const copied = ref(false)
const toolsExpanded = ref(false)
const executedToolsExpanded = ref(false)
const messagesExpanded = ref(true)

// Only sync from parent when turn_id actually changes (not on every refresh)
watch(() => props.turn.turn_id, (newId, oldId) => {
  if (newId !== oldId && oldId !== undefined) {
    expanded.value = props.defaultExpanded
  }
})

// Notify parent of expansion changes
const notifyExpanded = (val) => {
  emit('expanded-change', val)
}

const toggleExpanded = () => {
  expanded.value = !expanded.value
  notifyExpanded(expanded.value)
}

// Signal for collapsing all messages
const collapseAllSignal = ref(0)
provide('collapseAllSignal', collapseAllSignal)

function collapseAllMessages() {
  messagesExpanded.value = false
  collapseAllSignal.value++
}

function expandAllMessages() {
  messagesExpanded.value = true
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
    if (msg.content) lines.push(msg.content)
    if (msg.tool_calls?.length) {
      lines.push('')
      lines.push('[Tool Calls]')
      for (const tc of msg.tool_calls) {
        lines.push(`  - ${tc.name}: ${JSON.stringify(tc.args)}`)
      }
    }
    lines.push('')
  }

  try {
    await navigator.clipboard.writeText(lines.join('\n'))
    copied.value = true
    setTimeout(() => copied.value = false, 2000)
  } catch (err) {
    console.error('Failed to copy:', err)
  }
}

const roleLabels = {
  assistant: '🤖 AI',
  user: '👤 Human',
  tool: '🔧 Tool',
  system: '⚙️ System'
}

const statusConfig = {
  completed: { icon: CheckCircle, color: '#10b981', bg: 'rgba(16, 185, 129, 0.1)' },
  error: { icon: XCircle, color: '#ef4444', bg: 'rgba(239, 68, 68, 0.1)' },
  running: { icon: Loader2, color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.1)' },
  pending: { icon: Loader2, color: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.1)' }
}

const config = computed(() => statusConfig[props.turn.status] || statusConfig.pending)

const messageCount = computed(() => props.turn.messages?.length || 0)

const hasNewMessages = computed(() => {
  // Check if there are messages with recent timestamps
  const lastMessage = props.turn.messages?.[props.turn.messages.length - 1]
  if (!lastMessage?.created_at) return false
  const msgTime = new Date(lastMessage.created_at).getTime()
  return Date.now() - msgTime < 5000 // Within last 5 seconds
})

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

const boundTools = computed(() => props.turn.bound_tools || [])
const executedToolCalls = computed(() => props.turn.executed_tool_calls || [])

// Count AI messages with tool calls
const aiMessagesWithToolCalls = computed(() => {
  return props.turn.messages?.filter(m =>
    m.role === 'assistant' && m.tool_calls?.length > 0
  ).length || 0
})

const executedToolErrorCount = computed(() =>
  executedToolCalls.value.filter(t => t.is_error).length
)

const formatToolSchema = (schema) => {
  if (!schema) return ''
  if (typeof schema === 'string') return schema
  try {
    return JSON.stringify(schema, null, 2)
  } catch (e) {
    return String(schema)
  }
}
</script>

<template>
  <div
    class="turn-card"
    :class="{
      'is-dark': isDark,
      'is-expanded': expanded,
      'has-new': hasNewMessages && isUpdating,
      [`status-${turn.status}`]: true
    }"
  >
    <!-- Header -->
    <div class="turn-header" @click="toggleExpanded">
      <div class="header-main">
        <!-- Agent Badge -->
        <div class="agent-badge" :title="turn.agent_name || turn.agent_id">
          <span class="agent-icon">{{ turn.agent_name?.[0] || 'A' }}</span>
          <span class="agent-name">{{ turn.agent_name || turn.agent_id || 'main' }}</span>
        </div>

        <!-- Task Info -->
        <div class="task-info">
          <span v-if="turn.task_summary" class="task-title" :title="turn.task_summary">
            {{ turn.task_summary }}
          </span>
          <span v-else-if="turn.phase" class="phase-tag">
            {{ turn.phase }}
          </span>
        </div>

        <!-- Status Indicator -->
        <div
          class="status-pill"
          :style="{
            color: config.color,
            background: config.bg
          }"
        >
          <component
            :is="config.icon"
            class="status-icon"
            :class="{ spin: turn.status === 'running' || turn.status === 'pending' }"
            size="12"
          />
          <span>{{ turn.status }}</span>
        </div>
      </div>

      <div class="header-actions">
        <!-- Quick Actions (visible on hover/expanded) -->
        <div class="quick-actions" :class="{ visible: expanded }">
          <button
            v-if="messageCount > 0"
            class="icon-btn"
            @click.stop="messagesExpanded ? collapseAllMessages() : expandAllMessages()"
            :title="messagesExpanded ? '折叠所有消息' : '展开所有消息'"
          >
            <component :is="messagesExpanded ? Minimize2 : Maximize2" size="14" />
          </button>

          <button
            v-if="messageCount > 0"
            class="icon-btn"
            :class="{ copied }"
            @click.stop="copyTurnMessages"
            title="复制内容"
          >
            <component :is="copied ? Check : Copy" size="14" />
          </button>
        </div>

        <!-- Meta Info -->
        <div class="meta-info">
          <span v-if="latencyMs" class="meta-item">
            <Clock size="12" />
            {{ formatDuration(latencyMs) }}
          </span>

          <span v-if="tokenSummary" class="meta-item">
            <Layers size="12" />
            {{ tokenSummary }}
          </span>

          <span class="meta-item">
            <MessageSquare size="12" />
            {{ messageCount }}
          </span>

          <span class="timestamp">
            {{ formatTimestamp(turn.started_at, 'HH:mm:ss') }}
          </span>
        </div>

        <!-- Expand Toggle -->
        <div class="expand-toggle">
          <component :is="expanded ? ChevronUp : ChevronDown" size="18" />
        </div>
      </div>
    </div>

    <!-- Body -->
    <Transition name="expand">
      <div v-if="expanded" class="turn-body">
        <!-- Bound Tools Section -->
        <div v-if="boundTools.length" class="section tools-section">
          <div class="section-header" @click.stop="toolsExpanded = !toolsExpanded">
            <div class="section-title">
              <Wrench size="14" class="section-icon" />
              <span>Bound Tools ({{ boundTools.length }})</span>
            </div>
            <component :is="toolsExpanded ? ChevronUp : ChevronDown" size="14" />
          </div>
          <Transition name="expand">
            <div v-if="toolsExpanded" class="section-content">
              <div v-for="tool in boundTools" :key="tool.id" class="tool-card">
                <div class="tool-name">{{ tool.tool_name }}</div>
                <div v-if="tool.tool_description" class="tool-desc">
                  {{ tool.tool_description }}
                </div>
                <pre v-if="tool.tool_schema" class="tool-schema">{{ formatToolSchema(tool.tool_schema) }}</pre>
              </div>
            </div>
          </Transition>
        </div>

        <!-- Executed Tool Calls Section -->
        <div v-if="executedToolCalls.length" class="section executed-section">
          <div class="section-header" @click.stop="executedToolsExpanded = !executedToolsExpanded">
            <div class="section-title">
              <Terminal size="14" class="section-icon" />
              <span>Executed ({{ executedToolCalls.length }})</span>
              <span v-if="executedToolErrorCount" class="error-badge">
                {{ executedToolErrorCount }} errors
              </span>
            </div>
            <component :is="executedToolsExpanded ? ChevronUp : ChevronDown" size="14" />
          </div>
          <Transition name="expand">
            <div v-if="executedToolsExpanded" class="section-content">
              <div class="executed-grid">
                <div
                  v-for="toolCall in executedToolCalls"
                  :key="toolCall.id"
                  class="executed-chip"
                  :class="{ 'is-error': toolCall.is_error, 'is-mutation': toolCall.mutation_effective }"
                  :title="toolCall.result_preview"
                >
                  <span class="chip-name">{{ toolCall.tool_name }}</span>
                  <span class="chip-meta">{{ toolCall.duration_ms }}ms</span>
                </div>
              </div>
            </div>
          </Transition>
        </div>

        <!-- Messages -->
        <div class="messages-section">
          <div class="messages-header" v-if="turn.messages.length > 1">
            <span class="messages-count">{{ messageCount }} messages</span>
            <div class="messages-actions">
              <button class="text-btn" @click="collapseAllMessages">
                <Minimize2 size="12" />
                折叠全部
              </button>
              <button class="text-btn" @click="expandAllMessages">
                <Maximize2 size="12" />
                展开全部
              </button>
            </div>
          </div>

          <div class="messages-list">
            <MessageCard
              v-for="msg in turn.messages"
              :key="msg.id"
              :message="msg"
              :is-dark="isDark"
              :default-expanded="messagesExpanded"
            />
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.turn-card {
  background: var(--bg-primary, #ffffff);
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 12px;
  overflow: hidden;
  transition: all 0.2s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

.turn-card.is-dark {
  background: #1e293b;
  border-color: #334155;
}

.turn-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}

.turn-card.is-expanded {
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
}

.turn-card.has-new {
  animation: pulse-border 2s ease-in-out;
}

@keyframes pulse-border {
  0%, 100% { border-color: var(--border-color, #e2e8f0); }
  50% { border-color: #3b82f6; }
}

/* Header */
.turn-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  cursor: pointer;
  user-select: none;
  background: linear-gradient(180deg, rgba(255,255,255,0.02) 0%, rgba(255,255,255,0) 100%);
  transition: background 0.15s ease;
}

.turn-header:hover {
  background: rgba(59, 130, 246, 0.03);
}

.is-dark .turn-header:hover {
  background: rgba(59, 130, 246, 0.05);
}

.header-main {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
}

/* Agent Badge */
.agent-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: var(--bg-secondary, #f8fafc);
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary, #64748b);
  flex-shrink: 0;
}

.is-dark .agent-badge {
  background: #334155;
  color: #94a3b8;
}

.agent-icon {
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  color: white;
  border-radius: 50%;
  font-size: 10px;
  font-weight: 600;
}

.agent-name {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Task Info */
.task-info {
  flex: 1;
  min-width: 0;
}

.task-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary, #1e293b);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
  max-width: 300px;
}

.is-dark .task-title {
  color: #f1f5f9;
}

.phase-tag {
  display: inline-block;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  color: #3b82f6;
  background: rgba(59, 130, 246, 0.1);
  border-radius: 4px;
}

.is-dark .phase-tag {
  color: #60a5fa;
  background: rgba(59, 130, 246, 0.15);
}

/* Status Pill */
.status-pill {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  flex-shrink: 0;
}

.status-icon.spin {
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

.quick-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.quick-actions.visible,
.turn-header:hover .quick-actions {
  opacity: 1;
}

.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted, #94a3b8);
  cursor: pointer;
  transition: all 0.15s ease;
}

.icon-btn:hover {
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--text-primary, #1e293b);
}

.is-dark .icon-btn:hover {
  background: #475569;
  color: #f1f5f9;
}

.icon-btn.copied {
  color: #10b981;
}

/* Meta Info */
.meta-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-muted, #94a3b8);
}

.timestamp {
  font-size: 12px;
  color: var(--text-muted, #94a3b8);
  font-family: 'JetBrains Mono', monospace;
  padding: 2px 8px;
  background: var(--bg-secondary, #f8fafc);
  border-radius: 4px;
}

.is-dark .timestamp {
  background: #334155;
}

.expand-toggle {
  color: var(--text-muted, #94a3b8);
  transition: transform 0.2s ease;
}

.is-expanded .expand-toggle {
  transform: rotate(180deg);
}

/* Body */
.turn-body {
  border-top: 1px solid var(--border-color, #e2e8f0);
  background: var(--bg-secondary, #f8fafc);
}

.is-dark .turn-body {
  border-top-color: #334155;
  background: #0f172a;
}

/* Sections */
.section {
  border-bottom: 1px solid var(--border-color, #e2e8f0);
}

.is-dark .section {
  border-bottom-color: #334155;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
  color: var(--text-secondary, #64748b);
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  transition: background 0.15s ease;
}

.section-header:hover {
  background: rgba(59, 130, 246, 0.03);
}

.is-dark .section-header:hover {
  background: rgba(59, 130, 246, 0.05);
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.section-icon {
  color: #3b82f6;
}

.tools-section .section-icon {
  color: #8b5cf6;
}

.executed-section .section-icon {
  color: #10b981;
}

.error-badge {
  margin-left: 4px;
  padding: 2px 6px;
  background: #ef4444;
  color: white;
  border-radius: 4px;
  font-size: 10px;
}

.section-content {
  padding: 0 16px 16px;
}

/* Tool Cards */
.tool-card {
  padding: 12px;
  background: var(--bg-primary, #ffffff);
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 8px;
  margin-bottom: 8px;
}

.is-dark .tool-card {
  background: #1e293b;
  border-color: #334155;
}

.tool-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  font-weight: 600;
  color: #3b82f6;
  margin-bottom: 4px;
}

.tool-desc {
  font-size: 12px;
  color: var(--text-secondary, #64748b);
  margin-bottom: 8px;
}

.tool-schema {
  margin: 0;
  padding: 10px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  background: var(--bg-secondary, #f8fafc);
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 6px;
  overflow-x: auto;
  max-height: 200px;
  color: var(--text-secondary, #64748b);
}

.is-dark .tool-schema {
  background: #0f172a;
  border-color: #334155;
  color: #94a3b8;
}

/* Executed Grid */
.executed-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.executed-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.2);
  border-radius: 6px;
  font-size: 11px;
  color: #059669;
}

.is-dark .executed-chip {
  background: rgba(16, 185, 129, 0.15);
  border-color: rgba(16, 185, 129, 0.3);
  color: #34d399;
}

.executed-chip.is-error {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.2);
  color: #dc2626;
}

.is-dark .executed-chip.is-error {
  background: rgba(239, 68, 68, 0.15);
  border-color: rgba(239, 68, 68, 0.3);
  color: #f87171;
}

.executed-chip.is-mutation {
  background: rgba(59, 130, 246, 0.1);
  border-color: rgba(59, 130, 246, 0.2);
  color: #2563eb;
}

.chip-name {
  font-weight: 600;
}

.chip-meta {
  opacity: 0.7;
  font-size: 10px;
}

/* Messages Section */
.messages-section {
  padding: 16px;
}

.messages-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--border-color, #e2e8f0);
}

.is-dark .messages-header {
  border-bottom-color: #334155;
}

.messages-count {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary, #64748b);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.messages-actions {
  display: flex;
  gap: 8px;
}

.text-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted, #94a3b8);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.text-btn:hover {
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--text-primary, #1e293b);
}

.is-dark .text-btn:hover {
  background: #334155;
  color: #f1f5f9;
}

.messages-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* Transitions */
.expand-enter-active,
.expand-leave-active {
  transition: all 0.25s ease;
  max-height: 2000px;
  opacity: 1;
  overflow: hidden;
}

.expand-enter-from,
.expand-leave-to {
  max-height: 0;
  opacity: 0;
}
</style>
