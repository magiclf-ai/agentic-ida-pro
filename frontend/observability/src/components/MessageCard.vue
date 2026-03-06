<script setup>
import { ref, computed } from 'vue'
import { ChevronDown, ChevronUp, Bot, User, Terminal, Clock, Copy, Check } from 'lucide-vue-next'
import MarkdownRenderer from './MarkdownRenderer.vue'
import { formatTimestamp } from '../utils/formatters.js'

const props = defineProps({
  message: { type: Object, required: true },
  isDark: { type: Boolean, default: false }
})

const expanded = ref(true)
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

const roleConfig = {
  assistant: { icon: Bot, label: 'AI', color: '#10b981' },
  user: { icon: User, label: 'Human', color: '#3b82f6' },
  tool: { icon: Terminal, label: 'Tool', color: '#f59e0b' },
  system: { icon: Terminal, label: 'System', color: '#8b5cf6' }
}

const config = roleConfig[props.message.role] || roleConfig.assistant

const hasContent = computed(() => {
  return props.message.content && props.message.content.trim().length > 0
})

const hasToolCalls = computed(() => {
  return props.message.tool_calls && props.message.tool_calls.length > 0
})
</script>

<template>
  <div class="message-card" :class="{ 'is-dark': isDark, [message.role]: true }">
    <!-- Header -->
    <div class="message-header" @click="expanded = !expanded">
      <div class="header-left">
        <div class="role-badge" :style="{ backgroundColor: config.color + '15', color: config.color }">
          <component :is="config.icon" class="role-icon" />
          <span class="role-label">{{ config.label }}</span>
        </div>
        <span v-if="message.name && message.name !== config.label.toLowerCase()" class="name-tag">
          {{ message.name }}
        </span>
        <span v-if="hasToolCalls" class="tool-indicator">
          {{ message.tool_calls.length }} tool calls
        </span>
        <span v-if="message.role === 'tool' && message.tool_call_id" class="tool-call-id-tag">
          → {{ message.tool_call_id }}
        </span>
      </div>
      <div class="header-right">
        <button
          v-if="hasContent"
          class="copy-btn"
          :class="{ 'copied': copied }"
          @click.stop="copyContent"
          title="复制消息内容"
        >
          <component :is="copied ? Check : Copy" class="copy-icon" />
        </button>
        <span v-if="message.created_at" class="timestamp">
          <Clock class="timestamp-icon" />
          {{ formatTimestamp(message.created_at, 'HH:mm:ss') }}
        </span>
        <component :is="expanded ? ChevronUp : ChevronDown" class="expand-icon" />
      </div>
    </div>
    
    <!-- Body -->
    <div v-if="expanded" class="message-body">
      <!-- Content Section (for all roles) -->
      <div v-if="hasContent" class="content-section">
        <MarkdownRenderer :content="message.content" :is-dark="isDark" />
      </div>
      
      <!-- Tool Calls Section (for assistant) -->
      <div v-if="hasToolCalls" class="tool-calls-section">
        <div class="tool-calls-header" @click.stop="toolCallsExpanded = !toolCallsExpanded">
          <span class="section-title">
            <Terminal class="section-icon" />
            Tool Calls
          </span>
          <component :is="toolCallsExpanded ? ChevronUp : ChevronDown" class="section-expand-icon" />
        </div>
        
        <div v-if="toolCallsExpanded" class="tool-calls-list">
          <div v-for="tc in message.tool_calls" :key="tc.id" class="tool-call-item">
            <div class="tool-call-header">
              <span class="tool-name">{{ tc.name }}</span>
              <span class="tool-call-id">{{ tc.id }}</span>
            </div>
            <div class="tool-call-body">
              <pre class="tool-args">{{ JSON.stringify(tc.args, null, 2) }}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.message-card {
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  overflow: hidden;
  transition: all 0.2s ease;
}

.message-card.is-dark {
  background: #161b22;
  border-color: #30363d;
}

.message-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
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
  gap: 8px;
}

.role-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
}

.role-icon {
  width: 14px;
  height: 14px;
}

.name-tag {
  padding: 2px 8px;
  font-size: 11px;
  color: #6b7280;
  background: #f3f4f6;
  border-radius: 4px;
}

.is-dark .name-tag {
  background: #21262d;
  color: #9ca3af;
}

.tool-indicator {
  padding: 2px 8px;
  font-size: 11px;
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.1);
  border-radius: 4px;
}

.is-dark .tool-indicator {
  background: rgba(245, 158, 11, 0.15);
}

.tool-call-id-tag {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: #6b7280;
  background: rgba(0, 0, 0, 0.05);
  padding: 2px 8px;
  border-radius: 4px;
}

.is-dark .tool-call-id-tag {
  color: #9ca3af;
  background: rgba(255, 255, 255, 0.05);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.timestamp {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #9ca3af;
}

.timestamp-icon {
  width: 12px;
  height: 12px;
}

.expand-icon {
  width: 16px;
  height: 16px;
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

.message-body {
  border-top: 1px solid #e5e7eb;
}

.is-dark .message-body {
  border-top-color: #30363d;
}

.content-section {
  padding: 16px;
}

.tool-calls-section {
  border-top: 1px solid #e5e7eb;
  background: #f9fafb;
}

.is-dark .tool-calls-section {
  border-top-color: #30363d;
  background: #0d1117;
}

.tool-calls-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease;
}

.tool-calls-header:hover {
  background: rgba(0, 0, 0, 0.03);
}

.is-dark .tool-calls-header:hover {
  background: rgba(255, 255, 255, 0.03);
}

.section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.section-icon {
  width: 14px;
  height: 14px;
  color: #f59e0b;
}

.section-expand-icon {
  width: 14px;
  height: 14px;
  color: #9ca3af;
}

.tool-calls-list {
  padding: 0 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tool-call-item {
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-left: 3px solid #f59e0b;
  border-radius: 8px;
  overflow: hidden;
}

.is-dark .tool-call-item {
  background: #161b22;
  border-color: #30363d;
}

.tool-call-header {
  padding: 10px 12px;
  background: rgba(245, 158, 11, 0.08);
  border-bottom: 1px solid #e5e7eb;
}

.is-dark .tool-call-header {
  background: rgba(245, 158, 11, 0.1);
  border-bottom-color: #30363d;
}

.tool-name {
  font-weight: 600;
  font-size: 13px;
  color: #f59e0b;
}

.tool-call-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: #9ca3af;
  background: rgba(0, 0, 0, 0.05);
  padding: 2px 6px;
  border-radius: 4px;
}

.is-dark .tool-call-id {
  color: #6b7280;
  background: rgba(255, 255, 255, 0.05);
}

.tool-call-body {
  padding: 12px;
}

.tool-args {
  margin: 0;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 11px;
  line-height: 1.6;
  color: #4b5563;
  white-space: pre-wrap;
  word-break: break-word;
}

.is-dark .tool-args {
  color: #9ca3af;
}
</style>
