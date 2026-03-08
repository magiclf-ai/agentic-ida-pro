<script setup>
import { ref, computed, inject, watch } from 'vue'
import { ChevronDown, ChevronUp, Bot, User, Terminal, Clock, Copy, Check, Wrench } from 'lucide-vue-next'
import MarkdownRenderer from './MarkdownRenderer.vue'
import { formatTimestamp } from '../utils/formatters.js'

const props = defineProps({
  message: { type: Object, required: true },
  isDark: { type: Boolean, default: false },
  defaultExpanded: { type: Boolean, default: true }
})

const expanded = ref(props.defaultExpanded)
const toolCallsExpanded = ref(true)
const copied = ref(false)

// Listen for collapse all signal from parent
const collapseAllSignal = inject('collapseAllSignal', ref(0))
watch(collapseAllSignal, () => {
  expanded.value = false
})

// Sync with parent defaultExpanded (only when it changes meaningfully)
watch(() => props.defaultExpanded, (newVal, oldVal) => {
  if (newVal !== oldVal) {
    expanded.value = newVal
  }
})

const copyContent = async () => {
  if (!props.message.content) return
  try {
    await navigator.clipboard.writeText(props.message.content)
    copied.value = true
    setTimeout(() => copied.value = false, 2000)
  } catch (err) {
    console.error('Failed to copy:', err)
  }
}

const roleConfig = {
  assistant: {
    icon: Bot,
    label: 'AI',
    color: '#10b981',
    bg: 'rgba(16, 185, 129, 0.08)',
    border: 'rgba(16, 185, 129, 0.2)'
  },
  user: {
    icon: User,
    label: 'Human',
    color: '#3b82f6',
    bg: 'rgba(59, 130, 246, 0.08)',
    border: 'rgba(59, 130, 246, 0.2)'
  },
  tool: {
    icon: Terminal,
    label: 'Tool',
    color: '#f59e0b',
    bg: 'rgba(245, 158, 11, 0.08)',
    border: 'rgba(245, 158, 11, 0.2)'
  },
  system: {
    icon: Terminal,
    label: 'System',
    color: '#8b5cf6',
    bg: 'rgba(139, 92, 246, 0.08)',
    border: 'rgba(139, 92, 246, 0.2)'
  }
}

const config = computed(() => roleConfig[props.message.role] || roleConfig.assistant)

const hasContent = computed(() => props.message.content?.trim().length > 0)
const hasToolCalls = computed(() => props.message.tool_calls?.length > 0)
const isToolCallOnly = computed(() => !hasContent.value && hasToolCalls.value)
</script>

<template>
  <div
    class="message-card"
    :class="{ 'is-dark': isDark, [message.role]: true }"
    :style="{ borderLeftColor: config.color }"
  >
    <!-- Header -->
    <div class="message-header" @click="expanded = !expanded">
      <div class="header-left">
        <div class="role-badge" :style="{
          color: config.color,
          background: config.bg,
          borderColor: config.border
        }">
          <component :is="config.icon" size="14" />
          <span>{{ config.label }}</span>
        </div>

        <!-- Tool 返回结果时显示 tool 名称 -->
        <span v-if="message.role === 'tool' && message.name" class="tool-return-name" title="返回结果的 Tool 名称">
          <Wrench size="10" />
          {{ message.name }}
        </span>

        <!-- AI Tool Call 时显示调用的 tool 名称列表 -->
        <span v-if="hasToolCalls" class="tool-calls-names" title="调用的 Tools">
          <Wrench size="10" />
          {{ message.tool_calls.map(tc => tc.name).join(', ') }}
        </span>
      </div>

      <div class="header-right">
        <button
          v-if="hasContent"
          class="icon-btn"
          :class="{ copied }"
          @click.stop="copyContent"
          title="复制内容"
        >
          <component :is="copied ? Check : Copy" size="14" />
        </button>

        <span v-if="message.created_at" class="timestamp">
          <Clock size="11" />
          {{ formatTimestamp(message.created_at, 'HH:mm:ss') }}
        </span>

        <component :is="expanded ? ChevronUp : ChevronDown" class="expand-icon" size="16" />
      </div>
    </div>

    <!-- Body -->
    <Transition name="expand">
      <div v-if="expanded" class="message-body">
        <!-- Content -->
        <div v-if="hasContent" class="content-section">
          <MarkdownRenderer :content="message.content" :is-dark="isDark" />
        </div>

        <!-- Tool Call Only Hint -->
        <div v-else-if="isToolCallOnly" class="tool-call-hint">
          <Wrench size="14" />
          <span>AI 选择调用工具</span>
        </div>

        <!-- Tool Calls -->
        <div v-if="hasToolCalls" class="tool-calls-section">
          <div class="tool-calls-header" @click.stop="toolCallsExpanded = !toolCallsExpanded">
            <span class="tool-calls-title">
              <Wrench size="12" />
              Tool Calls ({{ message.tool_calls.length }})
            </span>
            <component :is="toolCallsExpanded ? ChevronUp : ChevronDown" size="14" />
          </div>

          <Transition name="expand">
            <div v-if="toolCallsExpanded" class="tool-calls-list">
              <div v-for="tc in message.tool_calls" :key="tc.id" class="tool-call-card">
                <div class="tool-call-header">
                  <span class="tool-name">{{ tc.name }}</span>
                  <code class="tool-id">{{ tc.id }}</code>
                </div>
                <pre class="tool-args">{{ JSON.stringify(tc.args, null, 2) }}</pre>
              </div>
            </div>
          </Transition>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.message-card {
  background: var(--bg-primary, #ffffff);
  border: 1px solid var(--border-color, #e2e8f0);
  border-left: 3px solid;
  border-radius: 10px;
  overflow: hidden;
  transition: all 0.2s ease;
}

.message-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.is-dark .message-card {
  background: #1e293b;
  border-color: #334155;
}

/* Header */
.message-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease;
}

.message-header:hover {
  background: rgba(0, 0, 0, 0.02);
}

.is-dark .message-header:hover {
  background: rgba(255, 255, 255, 0.02);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.role-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid;
}

.name-tag {
  padding: 2px 8px;
  font-size: 11px;
  color: var(--text-muted, #94a3b8);
  background: var(--bg-secondary, #f8fafc);
  border-radius: 4px;
}

.is-dark .name-tag {
  background: #334155;
  color: #94a3b8;
}

/* Tool 返回结果时显示的名称 */
.tool-return-name {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  font-size: 11px;
  font-weight: 600;
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.25);
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
}

/* AI Tool Call 时显示的 tool 名称列表 */
.tool-calls-names {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  font-size: 11px;
  font-weight: 500;
  color: #8b5cf6;
  background: rgba(139, 92, 246, 0.1);
  border: 1px solid rgba(139, 92, 246, 0.2);
  border-radius: 4px;
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-calls-names:hover {
  background: rgba(139, 92, 246, 0.15);
  max-width: none;
  white-space: normal;
  word-break: break-all;
}

/* Header Right */
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
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

.timestamp {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--text-muted, #94a3b8);
  font-family: 'JetBrains Mono', monospace;
  padding: 2px 8px;
  background: var(--bg-secondary, #f8fafc);
  border-radius: 4px;
}

.is-dark .timestamp {
  background: #334155;
}

.expand-icon {
  color: var(--text-muted, #94a3b8);
  transition: transform 0.2s ease;
}

/* Body */
.message-body {
  border-top: 1px solid var(--border-color, #e2e8f0);
}

.is-dark .message-body {
  border-top-color: #334155;
}

.content-section {
  padding: 14px;
}

/* Tool Call Only Hint */
.tool-call-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  font-size: 13px;
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.06);
  border-bottom: 1px dashed var(--border-color, #e2e8f0);
}

.is-dark .tool-call-hint {
  background: rgba(245, 158, 11, 0.1);
  border-bottom-color: #334155;
}

/* Tool Calls Section */
.tool-calls-section {
  border-top: 1px solid var(--border-color, #e2e8f0);
  background: rgba(245, 158, 11, 0.03);
}

.is-dark .tool-calls-section {
  border-top-color: #334155;
  background: rgba(245, 158, 11, 0.05);
}

.tool-calls-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease;
}

.tool-calls-header:hover {
  background: rgba(245, 158, 11, 0.06);
}

.tool-calls-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #f59e0b;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.tool-calls-list {
  padding: 0 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tool-call-card {
  background: var(--bg-primary, #ffffff);
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 8px;
  overflow: hidden;
}

.is-dark .tool-call-card {
  background: #1e293b;
  border-color: #334155;
}

.tool-call-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: rgba(245, 158, 11, 0.08);
  border-bottom: 1px solid var(--border-color, #e2e8f0);
}

.is-dark .tool-call-header {
  background: rgba(245, 158, 11, 0.12);
  border-bottom-color: #334155;
}

.tool-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 600;
  color: #f59e0b;
}

.tool-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--text-muted, #94a3b8);
  background: rgba(0, 0, 0, 0.05);
  padding: 2px 6px;
  border-radius: 4px;
}

.is-dark .tool-id {
  background: rgba(255, 255, 255, 0.1);
}

.tool-args {
  margin: 0;
  padding: 12px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-secondary, #64748b);
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.is-dark .tool-args {
  color: #94a3b8;
}

/* Transitions */
.expand-enter-active,
.expand-leave-active {
  transition: all 0.2s ease;
  max-height: 1500px;
  opacity: 1;
  overflow: hidden;
}

.expand-enter-from,
.expand-leave-to {
  max-height: 0;
  opacity: 0;
}
</style>
