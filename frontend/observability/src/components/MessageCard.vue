<script setup>
import { ref, computed, watch } from 'vue'
import { ChevronDown, ChevronUp, Bot, User, Terminal, Clock, Copy, Check, Wrench, ArrowLeftRight } from 'lucide-vue-next'
import MarkdownRenderer from './MarkdownRenderer.vue'
import { formatTimestamp } from '../utils/formatters.js'

const props = defineProps({
  message: { type: Object, required: true },
  isDark: { type: Boolean, default: false },
  defaultExpanded: { type: Boolean, default: true },
  // 用于关联 tool_call 和其结果
  allMessages: { type: Array, default: () => [] },
  messageIndex: { type: Number, default: -1 }
})

const expanded = ref(props.defaultExpanded)
const toolCallsExpanded = ref(true)
const copied = ref(false)

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

// Semi Design inspired colors
const roleConfig = {
  assistant: {
    icon: Bot,
    label: 'AI',
    color: '#6b5ce7',
    bg: 'rgba(107, 92, 231, 0.08)',
    border: 'rgba(107, 92, 231, 0.2)'
  },
  user: {
    icon: User,
    label: 'User',
    color: '#3b82f6',
    bg: 'rgba(59, 130, 246, 0.08)',
    border: 'rgba(59, 130, 246, 0.2)'
  },
  tool: {
    icon: Terminal,
    label: 'Tool',
    color: '#10b981',
    bg: 'rgba(16, 185, 129, 0.08)',
    border: 'rgba(16, 185, 129, 0.2)'
  },
  system: {
    icon: Terminal,
    label: 'System',
    color: '#f59e0b',
    bg: 'rgba(245, 158, 11, 0.08)',
    border: 'rgba(245, 158, 11, 0.2)'
  }
}

const config = computed(() => roleConfig[props.message.role] || roleConfig.assistant)

const hasContent = computed(() => props.message.content?.trim().length > 0)
const hasToolCalls = computed(() => props.message.tool_calls?.length > 0)
const isToolCallOnly = computed(() => !hasContent.value && hasToolCalls.value)

// 找到当前 tool 消息对应的 tool_call
const linkedToolCall = computed(() => {
  if (props.message.role !== 'tool' || !props.message.tool_call_id) return null
  // 在当前消息之前查找对应的 assistant message 中的 tool_call
  for (let i = props.messageIndex - 1; i >= 0; i--) {
    const msg = props.allMessages[i]
    if (msg.role === 'assistant' && msg.tool_calls?.length > 0) {
      const found = msg.tool_calls.find(tc => tc.id === props.message.tool_call_id)
      if (found) return found
    }
  }
  return null
})

// 找到当前 assistant 消息中 tool_calls 对应的 tool 结果
const linkedToolResults = computed(() => {
  if (props.message.role !== 'assistant' || !props.message.tool_calls?.length) return []
  const results = []
  // 在当前消息之后查找对应的 tool 结果
  for (let i = props.messageIndex + 1; i < props.allMessages.length; i++) {
    const msg = props.allMessages[i]
    if (msg.role === 'tool' && msg.tool_call_id) {
      const toolCall = props.message.tool_calls.find(tc => tc.id === msg.tool_call_id)
      if (toolCall) {
        results.push({
          toolCall,
          toolResult: msg
        })
      }
    }
  }
  return results
})
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

        <!-- Tool 返回结果时显示关联的 tool call 信息 -->
        <template v-if="message.role === 'tool'">
          <span v-if="linkedToolCall" class="tool-return-name" title="对应的 Tool Call">
            <ArrowLeftRight size="10" />
            {{ linkedToolCall.name }}
          </span>
          <span v-else-if="message.name" class="tool-return-name" title="返回结果的 Tool 名称">
            <Wrench size="10" />
            {{ message.name }}
          </span>
          <span class="tool-call-id-badge" title="Tool Call ID">
            {{ message.tool_call_id?.slice(-8) || 'unknown' }}
          </span>
        </template>

        <!-- AI Tool Call 时显示调用的 tool 名称列表及结果状态 -->
        <span v-if="hasToolCalls" class="tool-calls-names" title="调用的 Tools">
          <Wrench size="10" />
          {{ message.tool_calls.map(tc => tc.name).join(', ') }}
          <span v-if="linkedToolResults.length > 0" class="result-status">
            ({{ linkedToolResults.length }}/{{ message.tool_calls.length }} 已返回)
          </span>
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

        <!-- Tool 返回结果时显示对应的 Tool Call -->
        <div v-if="message.role === 'tool' && linkedToolCall" class="linked-tool-call-section">
          <div class="linked-header">
            <ArrowLeftRight size="12" />
            <span>对应的 Tool Call</span>
            <code class="full-id">{{ message.tool_call_id }}</code>
          </div>
          <div class="linked-tool-call-card">
            <div class="tool-call-header">
              <span class="tool-name">{{ linkedToolCall.name }}</span>
              <code class="tool-id">{{ linkedToolCall.id }}</code>
            </div>
            <pre class="tool-args">{{ JSON.stringify(linkedToolCall.args, null, 2) }}</pre>
          </div>
        </div>

        <!-- AI Tool Call 显示调用的 Tools 及其结果 -->
        <div v-if="hasToolCalls" class="tool-calls-section">
          <div class="tool-calls-header" @click.stop="toolCallsExpanded = !toolCallsExpanded">
            <span class="tool-calls-title">
              <Wrench size="12" />
              Tool Calls ({{ message.tool_calls.length }})
              <span v-if="linkedToolResults.length > 0" class="result-count">
                · {{ linkedToolResults.length }} 个已返回结果
              </span>
            </span>
            <component :is="toolCallsExpanded ? ChevronUp : ChevronDown" size="14" />
          </div>

          <Transition name="expand">
            <div v-if="toolCallsExpanded" class="tool-calls-list">
              <div v-for="tc in message.tool_calls" :key="tc.id" class="tool-call-card" :class="{ 'has-result': linkedToolResults.find(r => r.toolCall.id === tc.id) }">
                <!-- Tool Call Info -->
                <div class="tool-call-header">
                  <span class="tool-name">{{ tc.name }}</span>
                  <code class="tool-id">{{ tc.id }}</code>
                </div>
                <pre class="tool-args">{{ JSON.stringify(tc.args, null, 2) }}</pre>
                
                <!-- Linked Tool Result -->
                <div v-if="linkedToolResults.find(r => r.toolCall.id === tc.id)" class="tool-result-section">
                  <div class="result-header">
                    <ArrowLeftRight size="10" />
                    <span>返回结果</span>
                  </div>
                  <div class="result-content">
                    <MarkdownRenderer 
                      :content="linkedToolResults.find(r => r.toolCall.id === tc.id).toolResult.content" 
                      :is-dark="isDark" 
                    />
                  </div>
                </div>
              </div>
            </div>
          </Transition>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
/* Semi Design Style Message Card */
.message-card {
  background: var(--bg-primary, #ffffff);
  border: 1px solid var(--border-light, #f3f4f6);
  border-left: 3px solid;
  border-radius: 14px;
  overflow: hidden;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
}

.message-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
  transform: translateY(-1px);
}

.is-dark .message-card {
  background: var(--bg-elevated, #1f2937);
  border-color: var(--border-color, #374151);
}

/* Header */
.message-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  cursor: pointer;
  user-select: none;
  transition: background 0.2s ease;
}

.message-header:hover {
  background: rgba(0, 0, 0, 0.015);
}

.is-dark .message-header:hover {
  background: rgba(255, 255, 255, 0.02);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.role-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* Tool 返回结果时显示的名称 */
.tool-return-name {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  font-size: 11px;
  font-weight: 600;
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.2);
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
}

/* AI Tool Call 时显示的 tool 名称列表 */
.tool-calls-names {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  font-size: 11px;
  font-weight: 500;
  color: #6b5ce7;
  background: rgba(107, 92, 231, 0.1);
  border: 1px solid rgba(107, 92, 231, 0.2);
  border-radius: 6px;
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-calls-names:hover {
  background: rgba(107, 92, 231, 0.15);
  max-width: none;
  white-space: normal;
  word-break: break-all;
}

/* Header Right */
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-muted, #9ca3af);
  cursor: pointer;
  transition: all 0.2s ease;
}

.icon-btn:hover {
  background: var(--bg-tertiary, #f3f4f6);
  color: var(--text-primary, #111827);
}

.is-dark .icon-btn:hover {
  background: var(--bg-tertiary, #374151);
  color: var(--text-primary, #f9fafb);
}

.icon-btn.copied {
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
}

.timestamp {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted, #9ca3af);
  font-family: 'JetBrains Mono', monospace;
  padding: 6px 10px;
  background: var(--bg-tertiary, #f3f4f6);
  border-radius: 6px;
}

.is-dark .timestamp {
  background: var(--bg-tertiary, #374151);
}

.expand-icon {
  color: var(--text-muted, #9ca3af);
  transition: transform 0.2s ease;
}

/* Body */
.message-body {
  border-top: 1px solid var(--border-light, #f3f4f6);
}

.is-dark .message-body {
  border-top-color: var(--border-color, #374151);
}

.content-section {
  padding: 18px;
}

/* Tool Call Only Hint */
.tool-call-hint {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 18px;
  font-size: 14px;
  color: #6b5ce7;
  background: rgba(107, 92, 231, 0.05);
  border-bottom: 1px dashed var(--border-light, #f3f4f6);
}

.is-dark .tool-call-hint {
  background: rgba(107, 92, 231, 0.08);
  border-bottom-color: var(--border-color, #374151);
}

/* Tool Call ID Badge */
.tool-call-id-badge {
  padding: 3px 8px;
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted, #9ca3af);
  background: var(--bg-tertiary, #f3f4f6);
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
}

.result-status {
  margin-left: 6px;
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 600;
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
  border-radius: 4px;
}

/* Linked Tool Call Section */
.linked-tool-call-section {
  padding: 18px;
  background: rgba(107, 92, 231, 0.03);
  border-top: 1px dashed var(--border-light, #f3f4f6);
}

.is-dark .linked-tool-call-section {
  background: rgba(107, 92, 231, 0.05);
  border-top-color: var(--border-color, #374151);
}

.linked-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 12px;
  font-weight: 600;
  color: #6b5ce7;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.linked-header .full-id {
  margin-left: auto;
  font-size: 10px;
  color: var(--text-muted, #9ca3af);
  background: var(--bg-tertiary, #f3f4f6);
  padding: 3px 8px;
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
}

.linked-tool-call-card {
  background: var(--bg-primary, #ffffff);
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
}

.is-dark .linked-tool-call-card {
  background: var(--bg-primary, #111827);
  border-color: var(--border-color, #374151);
}

/* Tool Calls Section */
.tool-calls-section {
  border-top: 1px solid var(--border-light, #f3f4f6);
  background: rgba(107, 92, 231, 0.02);
}

.is-dark .tool-calls-section {
  border-top-color: var(--border-color, #374151);
  background: rgba(107, 92, 231, 0.03);
}

.tool-calls-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  cursor: pointer;
  user-select: none;
  transition: background 0.2s ease;
}

.tool-calls-header:hover {
  background: rgba(107, 92, 231, 0.05);
}

.tool-calls-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #6b5ce7;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.tool-calls-list {
  padding: 0 18px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tool-call-card {
  background: var(--bg-primary, #ffffff);
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
}

.is-dark .tool-call-card {
  background: var(--bg-primary, #111827);
  border-color: var(--border-color, #374151);
}

.tool-call-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  background: rgba(107, 92, 231, 0.06);
  border-bottom: 1px solid var(--border-light, #f3f4f6);
}

.is-dark .tool-call-header {
  background: rgba(107, 92, 231, 0.1);
  border-bottom-color: var(--border-color, #374151);
}

.tool-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  font-weight: 600;
  color: #6b5ce7;
}

.tool-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--text-muted, #9ca3af);
  background: rgba(0, 0, 0, 0.05);
  padding: 4px 8px;
  border-radius: 4px;
}

.is-dark .tool-id {
  background: rgba(255, 255, 255, 0.1);
}

.tool-args {
  margin: 0;
  padding: 14px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-secondary, #6b7280);
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--bg-secondary, #f9fafb);
}

.is-dark .tool-args {
  color: var(--text-secondary, #d1d5db);
  background: var(--bg-secondary, #1f2937);
}

/* Tool Call with Result */
.tool-call-card.has-result {
  border-color: #10b981;
  box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.2);
}

.tool-call-card.has-result .tool-call-header {
  background: rgba(16, 185, 129, 0.08);
}

.result-count {
  margin-left: 8px;
  padding: 3px 10px;
  font-size: 11px;
  font-weight: 600;
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
  border-radius: 4px;
  text-transform: none;
}

.tool-result-section {
  border-top: 1px dashed var(--border-color, #e5e7eb);
  background: rgba(16, 185, 129, 0.03);
}

.is-dark .tool-result-section {
  border-top-color: var(--border-color, #374151);
  background: rgba(16, 185, 129, 0.05);
}

.result-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  font-size: 11px;
  font-weight: 600;
  color: #10b981;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.result-content {
  padding: 14px;
  border-top: 1px solid var(--border-light, #f3f4f6);
}

.is-dark .result-content {
  border-top-color: var(--border-color, #374151);
}

/* Transitions */
.expand-enter-active,
.expand-leave-active {
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
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
