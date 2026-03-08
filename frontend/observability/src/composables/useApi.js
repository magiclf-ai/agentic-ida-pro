import { ref, shallowRef, computed } from 'vue'

const API_BASE = ''

export function useSessions() {
  const sessions = ref([])
  const loading = ref(false)

  async function fetchSessions() {
    loading.value = true
    try {
      const res = await fetch(`${API_BASE}/api/sessions`)
      const data = await res.json()
      sessions.value = data.sessions || []
    } catch (e) {
      console.error('Failed to fetch sessions:', e)
    } finally {
      loading.value = false
    }
  }

  return { sessions, loading, fetchSessions }
}

export function useSessionDetail(sessionId) {
  // Use shallowRef for large arrays to avoid deep reactivity overhead
  const turns = shallowRef([])
  const messages = shallowRef([])
  const turnTools = shallowRef([])
  const executedToolCalls = shallowRef([])

  // Separate loading state for initial vs incremental
  const initialLoading = ref(false)
  const updating = ref(false)

  // Track expansion states
  const turnExpansionStates = ref(new Map())

  // Stable reference cache for computed result
  const cachedResult = ref([])
  let lastTurnsRef = null
  let lastMessagesRef = null
  let lastTurnToolsRef = null
  let lastExecutedToolsRef = null

  async function fetchAll(options = {}) {
    if (!sessionId.value) return
    const { incremental = true } = options

    // Use updating for incremental, initialLoading for first fetch
    if (incremental && turns.value.length > 0) {
      updating.value = true
    } else {
      initialLoading.value = true
    }

    try {
      const [turnsRes, messagesRes, turnToolsRes, executedToolsRes] = await Promise.all([
        fetch(`${API_BASE}/api/turns?session_id=${sessionId.value}`),
        fetch(`${API_BASE}/api/messages?session_id=${sessionId.value}`),
        fetch(`${API_BASE}/api/turn_tools?session_id=${sessionId.value}`),
        fetch(`${API_BASE}/api/executed_tool_calls?session_id=${sessionId.value}`)
      ])

      const [turnsData, messagesData, turnToolsData, executedToolsData] = await Promise.all([
        turnsRes.json(),
        messagesRes.json(),
        turnToolsRes.json(),
        executedToolsRes.json()
      ])

      const newTurns = turnsData.turns || []
      const newMessages = messagesData.messages || []
      const newTurnTools = turnToolsData.turn_tools || []
      const newExecutedTools = executedToolsData.executed_tool_calls || []

      if (incremental && turns.value.length > 0) {
        // Smart merge: preserve existing objects, only add/update changed ones
        const mergedTurns = smartMerge(turns.value, newTurns, 'turn_id')
        const mergedMessages = smartMerge(messages.value, newMessages, 'id')

        turns.value = mergedTurns
        messages.value = mergedMessages
        turnTools.value = newTurnTools
        executedToolCalls.value = newExecutedTools
      } else {
        turns.value = newTurns
        messages.value = newMessages
        turnTools.value = newTurnTools
        executedToolCalls.value = newExecutedTools
      }
    } catch (e) {
      console.error('Failed to fetch session:', e)
    } finally {
      initialLoading.value = false
      updating.value = false
    }
  }

  // Smart merge that preserves object references for unchanged items
  function smartMerge(existing, incoming, keyField) {
    const existingMap = new Map(existing.map(item => [item[keyField], item]))
    const result = []

    for (const item of incoming) {
      const key = item[keyField]
      const existingItem = existingMap.get(key)

      if (existingItem && !hasChanged(existingItem, item)) {
        // Keep existing reference
        result.push(existingItem)
      } else {
        // New or changed item
        result.push(item)
      }
    }

    if (result.length === existing.length && result.every((item, idx) => item === existing[idx])) {
      return existing
    }
    return result
  }

  // Shallow comparison for changes
  function hasChanged(a, b) {
    const keys = Object.keys(b)
    for (const key of keys) {
      if (a[key] !== b[key]) return true
    }
    return false
  }

  // Extract task summary from first human message
  function extractTaskSummary(content, maxLen = 60) {
    if (!content) return ''
    let summary = content
      .replace(/^\s*消息ID:\s*Message_\d+\s*/i, '')
      .replace(/^#+\s*/gm, '')
      .replace(/```[\s\S]*?```/g, '[code]')
      .replace(/\s+/g, ' ')
      .trim()
    if (summary.length > maxLen) {
      summary = summary.slice(0, maxLen) + '...'
    }
    return summary
  }

  // Optimized computed that preserves references
  const turnsWithMessages = computed(() => {
    if (!turns.value.length) return []

    // Skip recomputation only when all source collections are the same references.
    if (
      turns.value === lastTurnsRef &&
      messages.value === lastMessagesRef &&
      turnTools.value === lastTurnToolsRef &&
      executedToolCalls.value === lastExecutedToolsRef &&
      cachedResult.value.length > 0
    ) {
      return cachedResult.value
    }

    // Build lookup maps once
    const messagesByTurn = {}
    for (const msg of messages.value) {
      const tid = msg.turn_id || 'unknown'
      if (!messagesByTurn[tid]) messagesByTurn[tid] = []
      messagesByTurn[tid].push(msg)
    }

    // Sort messages
    for (const tid in messagesByTurn) {
      messagesByTurn[tid].sort((a, b) => (a.msg_index || 0) - (b.msg_index || 0))
    }

    const toolsByTurn = {}
    for (const tool of turnTools.value) {
      const tid = tool.turn_id || 'unknown'
      if (!toolsByTurn[tid]) toolsByTurn[tid] = []
      toolsByTurn[tid].push(tool)
    }

    const executedByTurn = {}
    for (const tool of executedToolCalls.value) {
      const tid = tool.turn_id || 'unknown'
      if (!executedByTurn[tid]) executedByTurn[tid] = []
      executedByTurn[tid].push(tool)
    }

    // Build result, reusing cached turn objects when possible
    const result = []
    const oldCache = new Map(cachedResult.value.map(t => [t.turn_id, t]))

    for (const turn of turns.value) {
      const turnId = turn.turn_id
      const turnMessages = messagesByTurn[turnId] || []
      const firstHuman = turnMessages.find(m => m.role === 'user')

      const newTurnData = {
        ...turn,
        messages: turnMessages,
        bound_tools: toolsByTurn[turnId] || [],
        executed_tool_calls: executedByTurn[turnId] || [],
        task_summary: firstHuman ? extractTaskSummary(firstHuman.content) : ''
      }

      // Check if we can reuse cached turn object
      const cached = oldCache.get(turnId)
      if (cached && isSameTurn(cached, newTurnData)) {
        result.push(cached)
      } else {
        result.push(newTurnData)
      }
    }

    cachedResult.value = result
    lastTurnsRef = turns.value
    lastMessagesRef = messages.value
    lastTurnToolsRef = turnTools.value
    lastExecutedToolsRef = executedToolCalls.value
    return result
  })

  function isSameTurn(a, b) {
    if (a.status !== b.status) return false
    if (a.messages.length !== b.messages.length) return false
    for (let i = 0; i < a.messages.length; i++) {
      if (a.messages[i].id !== b.messages[i].id) return false
    }
    return true
  }

  function setTurnExpanded(turnId, expanded) {
    turnExpansionStates.value.set(turnId, expanded)
  }

  function isTurnExpanded(turnId) {
    return turnExpansionStates.value.get(turnId)
  }

  function clearCache() {
    cachedResult.value = []
    lastTurnsRef = null
    lastMessagesRef = null
    lastTurnToolsRef = null
    lastExecutedToolsRef = null
  }

  return {
    turns,
    messages,
    turnTools,
    executedToolCalls,
    turnsWithMessages,
    initialLoading,
    updating,
    fetchAll,
    setTurnExpanded,
    isTurnExpanded,
    clearCache
  }
}
