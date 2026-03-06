import Prism from 'prismjs'
import 'prismjs/components/prism-python'

const lightTheme = `
  .token.comment, .token.prolog, .token.doctype, .token.cdata { color: #6a9955; }
  .token.punctuation { color: #393a34; }
  .token.namespace { opacity: .7; }
  .token.property, .token.tag, .token.boolean, .token.number, .token.constant, .token.symbol, .token.deleted { color: #d73a49; }
  .token.selector, .token.attr-name, .token.string, .token.char, .token.builtin, .token.inserted { color: #005cc5; }
  .token.operator, .token.entity, .token.url, .language-css .token.string, .style .token.string { color: #d73a49; }
  .token.atrule, .token.attr-value, .token.keyword { color: #d73a49; }
  .token.function, .token.class-name { color: #6f42c1; }
  .token.regex, .token.important, .token.variable { color: #e36209; }
`

const darkTheme = `
  .token.comment, .token.prolog, .token.doctype, .token.cdata { color: #8b949e; }
  .token.punctuation { color: #c9d1d9; }
  .token.property, .token.tag, .token.boolean, .token.number, .token.constant, .token.symbol, .token.deleted { color: #ff7b72; }
  .token.selector, .token.attr-name, .token.string, .token.char, .token.builtin, .token.inserted { color: #a5d6ff; }
  .token.operator, .token.entity, .token.url { color: #ff7b72; }
  .token.atrule, .token.attr-value, .token.keyword { color: #ff7b72; }
  .token.function, .token.class-name { color: #d2a8ff; }
  .token.regex, .token.important, .token.variable { color: #ffa657; }
`

export function highlightCode(code, language = 'python') {
  if (!code) return ''
  const grammar = Prism.languages[language] || Prism.languages.python
  return Prism.highlight(code, grammar, language)
}

export function getThemeStyles(isDark = false) {
  return isDark ? darkTheme : lightTheme
}

export function injectPrismStyles(isDark = false) {
  const styleId = 'prism-theme-styles'
  let styleEl = document.getElementById(styleId)
  
  if (!styleEl) {
    styleEl = document.createElement('style')
    styleEl.id = styleId
    document.head.appendChild(styleEl)
  }
  
  styleEl.textContent = getThemeStyles(isDark)
}
