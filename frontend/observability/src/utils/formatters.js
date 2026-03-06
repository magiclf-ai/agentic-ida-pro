import dayjs from 'dayjs'

export function formatTimestamp(ts, format = 'YYYY-MM-DD HH:mm:ss') {
  if (!ts) return '-'
  return dayjs(ts).format(format)
}

export function formatRelativeTime(ts) {
  if (!ts) return '-'
  const diff = dayjs().diff(dayjs(ts), 'second')
  
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

export function formatDuration(ms) {
  if (!ms && ms !== 0) return '-'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

export function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return '-'
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

export function truncate(str, length = 50) {
  if (!str) return ''
  if (str.length <= length) return str
  return str.slice(0, length) + '...'
}

export function formatSessionId(id) {
  if (!id) return '-'
  if (id.length <= 16) return id
  return id.slice(0, 8) + '...' + id.slice(-8)
}

export function getStatusType(status) {
  const map = {
    'completed': 'success',
    'in_progress': 'warning',
    'failed': 'error',
    'pending': 'default'
  }
  return map[status] || 'default'
}

export function getDirectionIcon(direction) {
  const map = {
    'request': 'arrow-up',
    'response': 'arrow-down',
    'tool_call': 'tool',
    'tool_result': 'checkmark'
  }
  return map[direction] || 'circle'
}

export function getDirectionColor(direction) {
  const map = {
    'request': '#3B82F6',
    'response': '#10B981',
    'tool_call': '#F59E0B',
    'tool_result': '#8B5CF6'
  }
  return map[direction] || '#6B7280'
}

/**
 * Parse tool result markdown format into structured data
 * Expected format:
 * - status: ok/error
 * - latency_s: 0.001
 * - mutation_effective: true/false/unknown
 * - args: ```json {...} ```
 * - output: ```text ... ```
 */
export function parseToolResultMd(resultMd) {
  if (!resultMd) return null
  
  const text = String(resultMd)
  const result = {
    status: null,
    latency_s: null,
    mutation_effective: null,
    args: null,
    output: null,
    raw: text
  }
  
  // Extract simple key-value pairs (- key: value)
  const kvPattern = /^-\s*(\w+):\s*(.+?)$/gm
  let match
  while ((match = kvPattern.exec(text)) !== null) {
    const [, key, value] = match
    if (key === 'status') result.status = value.trim()
    else if (key === 'latency_s') result.latency_s = parseFloat(value.trim())
    else if (key === 'mutation_effective') {
      const v = value.trim().toLowerCase()
      result.mutation_effective = v === 'true' ? true : v === 'false' ? false : null
    }
  }
  
  // Extract JSON args
  const argsMatch = text.match(/- args:\s*```json\s*\n([\s\S]*?)\n```/)
  if (argsMatch) {
    try {
      result.args = JSON.parse(argsMatch[1].trim())
    } catch (e) {
      result.args = argsMatch[1].trim()
    }
  }
  
  // Extract text output
  const outputMatch = text.match(/- output:\s*```text\s*\n([\s\S]*?)(?:\n```|$)/)
  if (outputMatch) {
    result.output = outputMatch[1].trim()
  }
  
  return result
}

/**
 * Parse tool arguments markdown format
 */
export function parseToolArgsMd(argsMd) {
  if (!argsMd) return null
  
  const text = String(argsMd).trim()
  
  // Try to find JSON code block
  const jsonMatch = text.match(/```json\s*\n([\s\S]*?)\n```/)
  if (jsonMatch) {
    try {
      return JSON.parse(jsonMatch[1].trim())
    } catch (e) {
      return jsonMatch[1].trim()
    }
  }
  
  // Try to parse entire text as JSON
  try {
    return JSON.parse(text)
  } catch (e) {
    return text
  }
}
