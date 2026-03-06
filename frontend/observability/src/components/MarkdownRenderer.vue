<script setup>
import { computed, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import Prism from 'prismjs'

const props = defineProps({
  content: { type: String, default: '' },
  isDark: { type: Boolean, default: false }
})

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  breaks: true,
  highlight: (code, lang) => {
    if (lang && Prism.languages[lang]) {
      try {
        return Prism.highlight(code, Prism.languages[lang], lang)
      } catch (e) {
        return escapeHtml(code)
      }
    }
    return escapeHtml(code)
  }
})

// Escape HTML special characters
function escapeHtml(text) {
  const map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;'
  }
  return text.replace(/[&<>"']/g, m => map[m])
}

// Post-process rendered HTML for task list styling
const renderedContent = computed(() => {
  if (!props.content) return ''
  let html = md.render(props.content)
  
  // Style task list checkboxes
  html = html.replace(
    /<input type="checkbox"([^>]*)>/g,
    '<span class="task-checkbox$1"></span>'
  )
  
  // Style checked/unchecked states
  html = html.replace(
    /class="task-checkbox checked"/g,
    'class="task-checkbox checked"'
  )
  
  return html
})

// Highlight code blocks after render
const contentRef = ref(null)
watch(() => props.content, () => {
  if (contentRef.value) {
    setTimeout(() => {
      contentRef.value.querySelectorAll('pre code').forEach((block) => {
        Prism.highlightElement(block)
      })
    }, 0)
  }
}, { immediate: true })
</script>

<template>
  <div ref="contentRef" class="markdown-body" :class="{ 'is-dark': isDark }" v-html="renderedContent"></div>
</template>

<style scoped>
.markdown-body {
  font-size: 13px;
  line-height: 1.7;
  color: #374151;
}

.markdown-body.is-dark {
  color: #c9d1d9;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4),
.markdown-body :deep(h5),
.markdown-body :deep(h6) {
  margin-top: 16px;
  margin-bottom: 12px;
  font-weight: 600;
  line-height: 1.4;
  color: #1f2937;
}

.markdown-body.is-dark :deep(h1),
.markdown-body.is-dark :deep(h2),
.markdown-body.is-dark :deep(h3),
.markdown-body.is-dark :deep(h4),
.markdown-body.is-dark :deep(h5),
.markdown-body.is-dark :deep(h6) {
  color: #e5e7eb;
}

.markdown-body :deep(h1) { font-size: 1.5em; border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; }
.markdown-body :deep(h2) { font-size: 1.3em; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }
.markdown-body :deep(h3) { font-size: 1.1em; }
.markdown-body :deep(h4) { font-size: 1em; }
.markdown-body :deep(h5) { font-size: 0.95em; }
.markdown-body :deep(h6) { font-size: 0.9em; color: #6b7280; }

.markdown-body.is-dark :deep(h1),
.markdown-body.is-dark :deep(h2) {
  border-bottom-color: #30363d;
}

.markdown-body.is-dark :deep(h6) {
  color: #9ca3af;
}

.markdown-body :deep(p) {
  margin-bottom: 12px;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin-bottom: 12px;
  padding-left: 24px;
}

.markdown-body :deep(li) {
  margin-bottom: 4px;
}

.markdown-body :deep(li.task-list-item) {
  list-style: none;
  position: relative;
  padding-left: 24px;
}

.markdown-body :deep(.task-checkbox) {
  position: absolute;
  left: 0;
  top: 3px;
  width: 14px;
  height: 14px;
  border: 2px solid #d1d5db;
  border-radius: 3px;
  display: inline-block;
}

.markdown-body :deep(.task-checkbox.checked) {
  background: #10b981;
  border-color: #10b981;
}

.markdown-body :deep(.task-checkbox.checked)::after {
  content: '';
  position: absolute;
  left: 3px;
  top: 0;
  width: 4px;
  height: 8px;
  border: solid white;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

.markdown-body.is-dark :deep(.task-checkbox) {
  border-color: #6b7280;
}

.markdown-body :deep(code) {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 0.9em;
  padding: 2px 6px;
  background: #f3f4f6;
  border-radius: 4px;
  color: #ef4444;
}

.markdown-body.is-dark :deep(code) {
  background: #21262d;
  color: #f87171;
}

.markdown-body :deep(pre) {
  margin-bottom: 16px;
  padding: 12px;
  background: #f6f8fa;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow-x: auto;
}

.markdown-body.is-dark :deep(pre) {
  background: #0d1117;
  border-color: #30363d;
}

.markdown-body :deep(pre code) {
  padding: 0;
  background: transparent;
  border-radius: 0;
  color: inherit;
  font-size: 12px;
  line-height: 1.6;
}

.markdown-body :deep(blockquote) {
  margin-bottom: 12px;
  padding: 8px 12px;
  border-left: 4px solid #e5e7eb;
  background: #f9fafb;
  color: #6b7280;
}

.markdown-body.is-dark :deep(blockquote) {
  border-left-color: #30363d;
  background: #161b22;
  color: #9ca3af;
}

.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid #e5e7eb;
  margin: 16px 0;
}

.markdown-body.is-dark :deep(hr) {
  border-top-color: #30363d;
}

.markdown-body :deep(table) {
  width: 100%;
  margin-bottom: 16px;
  border-collapse: collapse;
  font-size: 12px;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  padding: 8px 12px;
  border: 1px solid #e5e7eb;
  text-align: left;
}

.markdown-body :deep(th) {
  background: #f9fafb;
  font-weight: 600;
}

.markdown-body.is-dark :deep(th),
.markdown-body.is-dark :deep(td) {
  border-color: #30363d;
}

.markdown-body.is-dark :deep(th) {
  background: #161b22;
}

.markdown-body :deep(a) {
  color: #3b82f6;
  text-decoration: none;
}

.markdown-body :deep(a:hover) {
  text-decoration: underline;
}

.markdown-body.is-dark :deep(a) {
  color: #60a5fa;
}

/* Syntax highlighting colors for light theme */
.markdown-body :deep(.token.keyword) { color: #c678dd; }
.markdown-body :deep(.token.string) { color: #98c379; }
.markdown-body :deep(.token.number) { color: #d19a66; }
.markdown-body :deep(.token.comment) { color: #5c6370; font-style: italic; }
.markdown-body :deep(.token.function) { color: #61afef; }
.markdown-body :deep(.token.operator) { color: #56b6c2; }
.markdown-body :deep(.token.punctuation) { color: #abb2bf; }

/* Syntax highlighting colors for dark theme */
.markdown-body.is-dark :deep(.token.keyword) { color: #ff7b72; }
.markdown-body.is-dark :deep(.token.string) { color: #a5d6ff; }
.markdown-body.is-dark :deep(.token.number) { color: #79c0ff; }
.markdown-body.is-dark :deep(.token.comment) { color: #8b949e; font-style: italic; }
.markdown-body.is-dark :deep(.token.function) { color: #d2a8ff; }
.markdown-body.is-dark :deep(.token.operator) { color: #56d364; }
.markdown-body.is-dark :deep(.token.punctuation) { color: #c9d1d9; }
</style>
