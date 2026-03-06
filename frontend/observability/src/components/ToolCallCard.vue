<script setup>
import { ref, computed } from 'vue'
import { ChevronDown, ChevronUp, Terminal, CheckCircle, XCircle, Clock, Copy, Check } from 'lucide-vue-next'
import { formatTimestamp, formatDuration } from '../utils/formatters.js'

const props = defineProps({
  tool: { type: Object, required: true },
  isDark: { type: Boolean, default: false },
  showResult: { type: Boolean, default: true }
})

const expanded = ref(true)
const copied = ref(false)

const copyToolData = async () => {
  const lines = []
  if (props.tool.args_md) {
    lines.push('## Arguments')
    lines.push(props.tool.args_md)
  }
  if (props.tool.result_md) {
    lines.push('')
    lines.push('## Result')
    lines.push(props.tool.result_md)
  }
  const text = lines.join('\n')
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    copied.value = true
    setTimeout(() => copied.value = false, 2000)
  } catch (err) {
    console.error('Failed to copy:', err)
  }
}

const statusIcon = computed(() => {
  if (props.tool.is_success) return CheckCircle
  if (props.tool.is_error) return XCircle
  return Clock
})

const statusColor = computed(() => {
  if (props.tool.is_success) return '#10b981'
  if (props.tool.is_error) return '#ef4444'
  return '#f59e0b'
})

const latencyMs = computed(() => {
  if (props.tool.latency_ms) return props.tool.latency_ms
  if (props.tool.latency_s) return Math.round(props.tool.latency_s * 1000)
  return null
})
</script>

<template>
  <div 
    class="tool-card" 
    :class="{ 
      'is-dark': isDark, 
      'is-expanded': expanded,
      'is-success': tool.is_success,
      'is-error': tool.is_error
    }"
  >
    <div class="tool-header" @click="expanded = !expanded">
      <div class="header-left">
        <div class="tool-icon-wrapper" :style="{ backgroundColor: statusColor + '15', color: statusColor }">
          <component :is="statusIcon" class="tool-icon" />
        </div>
        <div class="tool-info">
          <span class="tool-name">{{ tool.tool_name || 'unknown' }}</span>
          <span class="tool-meta">
            <span v-if="latencyMs" class="meta-latency">{{ formatDuration(latencyMs) }}</span>
            <span v-if="tool.mutation_effective === true" class="meta-mutation effective">MUTATION</span>
            <span v-else-if="tool.mutation_effective === false" class="meta-mutation">no effect</span>
          </span>
        </div>
      </div>
      
      <div class="header-right">
        <button
          class="copy-btn"
          :class="{ 'copied': copied }"
          @click.stop="copyToolData"
          title="复制工具调用数据"
        >
          <component :is="copied ? Check : Copy" class="copy-icon" />
        </button>
        <span class="tool-time">{{ formatTimestamp(tool.created_at, 'HH:mm:ss') }}</span>
        <component 
          :is="expanded ? ChevronUp : ChevronDown" 
          class="expand-icon"
        />
      </div>
    </div>
    
    <div v-if="expanded" class="tool-body">
      <!-- Args Markdown -->
      <div v-if="tool.args_md" class="tool-section">
        <span class="section-label">Arguments</span>
        <div class="markdown-content">
          <pre class="md-block">{{ tool.args_md }}</pre>
        </div>
      </div>
      
      <!-- Result Markdown -->
      <div v-if="showResult && tool.result_md" class="tool-section">
        <span class="section-label">Result</span>
        <div class="markdown-content">
          <pre class="md-block">{{ tool.result_md }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tool-card {
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  transition: all 0.2s;
}

.tool-card.is-dark {
  background: #161b22;
  border-color: #30363d;
}

.tool-card.is-success {
  border-left: 3px solid #10b981;
}

.tool-card.is-error {
  border-left: 3px solid #ef4444;
}

.tool-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px;
  cursor: pointer;
  user-select: none;
}

.tool-header:hover {
  background: rgba(0, 0, 0, 0.02);
}

.tool-card.is-dark .tool-header:hover {
  background: rgba(255, 255, 255, 0.02);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.tool-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
}

.tool-icon {
  width: 16px;
  height: 16px;
}

.tool-info {
  display: flex;
  flex-direction: column;
}

.tool-name {
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
}

.tool-card.is-dark .tool-name {
  color: #e5e7eb;
}

.tool-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}

.meta-latency {
  color: #9ca3af;
}

.meta-mutation {
  padding: 1px 4px;
  font-size: 9px;
  font-weight: 600;
  color: #6b7280;
  background: #f3f4f6;
  border-radius: 3px;
}

.meta-mutation.effective {
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
}

.tool-card.is-dark .meta-mutation {
  background: #21262d;
  color: #9ca3af;
}

.tool-card.is-dark .meta-mutation.effective {
  background: rgba(16, 185, 129, 0.15);
  color: #10b981;
}

.tool-time {
  font-size: 11px;
  color: #9ca3af;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
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

.tool-card.is-dark .copy-btn:hover {
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

.tool-card.is-dark .copy-icon {
  color: #9ca3af;
}

.tool-card.is-dark .copy-btn:hover .copy-icon {
  color: #e5e7eb;
}

.tool-body {
  padding: 12px;
  border-top: 1px solid #e5e7eb;
}

.tool-card.is-dark .tool-body {
  border-top-color: #30363d;
}

.tool-section {
  margin-bottom: 16px;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.section-label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  color: #6b7280;
  margin-bottom: 8px;
}

.tool-card.is-dark .section-label {
  color: #9ca3af;
}

.markdown-content {
  font-size: 12px;
  line-height: 1.5;
}

.md-block {
  margin: 0;
  padding: 12px;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #374151;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  overflow-y: auto;
}

.tool-card.is-dark .md-block {
  color: #c9d1d9;
  background: #0d1117;
  border-color: #30363d;
}

/* Syntax highlighting simulation within markdown */
.md-block :deep(.keyword) {
  color: #c678dd;
}

.md-block :deep(.string) {
  color: #98c379;
}

.md-block :deep(.number) {
  color: #d19a66;
}

.md-block :deep(.comment) {
  color: #5c6370;
}
</style>
