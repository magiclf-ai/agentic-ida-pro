<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { highlightCode, injectPrismStyles } from '../utils/codeHighlight.js'
import { Copy, Check, Maximize2, Minimize2 } from 'lucide-vue-next'

const props = defineProps({
  code: { type: String, default: '' },
  language: { type: String, default: 'python' },
  isDark: { type: Boolean, default: false },
  maxHeight: { type: String, default: '600px' },
  showLineNumbers: { type: Boolean, default: true }
})

const copied = ref(false)
const isFullscreen = ref(false)
const codeContainer = ref(null)

const highlightedCode = computed(() => {
  return highlightCode(props.code, props.language)
})

const lines = computed(() => {
  return props.code?.split('\n') || []
})

function copyCode() {
  navigator.clipboard.writeText(props.code)
  copied.value = true
  setTimeout(() => copied.value = false, 2000)
}

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
}

onMounted(() => {
  injectPrismStyles(props.isDark)
})

watch(() => props.isDark, (newVal) => {
  injectPrismStyles(newVal)
})
</script>

<template>
  <div 
    class="code-viewer" 
    :class="{ 'is-fullscreen': isFullscreen, 'is-dark': isDark }"
    ref="codeContainer"
  >
    <div class="code-header">
      <span class="code-lang">{{ language }}</span>
      <div class="code-actions">
        <button class="action-btn" @click="copyCode" :title="copied ? 'Copied!' : 'Copy'">
          <Check v-if="copied" class="icon" />
          <Copy v-else class="icon" />
        </button>
        <button class="action-btn" @click="toggleFullscreen" :title="isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'">
          <Minimize2 v-if="isFullscreen" class="icon" />
          <Maximize2 v-else class="icon" />
        </button>
      </div>
    </div>
    <div class="code-content" :style="{ maxHeight: isFullscreen ? 'calc(100vh - 60px)' : maxHeight }">
      <div v-if="showLineNumbers" class="line-numbers">
        <div v-for="(_, i) in lines" :key="i" class="line-number">{{ i + 1 }}</div>
      </div>
      <pre class="code-block"><code v-html="highlightedCode"></code></pre>
    </div>
  </div>
</template>

<style scoped>
.code-viewer {
  border-radius: 8px;
  overflow: hidden;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
}

.code-viewer.is-dark {
  background: #1e1e1e;
  border-color: #374151;
}

.code-viewer.is-fullscreen {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 9999;
  border-radius: 0;
}

.code-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: rgba(0, 0, 0, 0.03);
  border-bottom: 1px solid #e5e7eb;
}

.code-viewer.is-dark .code-header {
  background: rgba(255, 255, 255, 0.05);
  border-bottom-color: #374151;
}

.code-lang {
  font-size: 11px;
  font-weight: 500;
  color: #6b7280;
  text-transform: uppercase;
}

.code-viewer.is-dark .code-lang {
  color: #9ca3af;
}

.code-actions {
  display: flex;
  gap: 4px;
}

.action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.2s;
}

.action-btn:hover {
  background: rgba(0, 0, 0, 0.06);
  color: #374151;
}

.code-viewer.is-dark .action-btn {
  color: #9ca3af;
}

.code-viewer.is-dark .action-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #e5e7eb;
}

.icon {
  width: 14px;
  height: 14px;
}

.code-content {
  display: flex;
  overflow: auto;
}

.line-numbers {
  flex-shrink: 0;
  padding: 12px 8px;
  background: rgba(0, 0, 0, 0.02);
  border-right: 1px solid #e5e7eb;
  text-align: right;
  user-select: none;
}

.code-viewer.is-dark .line-numbers {
  background: rgba(255, 255, 255, 0.02);
  border-right-color: #374151;
}

.line-number {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #9ca3af;
}

.code-block {
  flex: 1;
  margin: 0;
  padding: 12px 16px;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #1f2937;
  background: transparent;
  overflow-x: auto;
}

.code-viewer.is-dark .code-block {
  color: #e5e7eb;
}

.code-block code {
  font-family: inherit;
}
</style>
