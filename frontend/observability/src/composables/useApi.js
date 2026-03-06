import { ref, computed } from 'vue'

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
  const turns = ref([])
  const messages = ref([])
  const turnTools = ref([])
  const executedToolCalls = ref([])
  const loading = ref(false)
  
  async function fetchAll() {
    if (!sessionId.value) return
    loading.value = true
    try {
      const [turnsRes, messagesRes, turnToolsRes, executedToolsRes] = await Promise.all([
        fetch(`${API_BASE}/api/turns?session_id=${sessionId.value}`),
        fetch(`${API_BASE}/api/messages?session_id=${sessionId.value}`),
        fetch(`${API_BASE}/api/turn_tools?session_id=${sessionId.value}`),
        fetch(`${API_BASE}/api/executed_tool_calls?session_id=${sessionId.value}`)
      ])
      const turnsData = await turnsRes.json()
      const messagesData = await messagesRes.json()
      const turnToolsData = await turnToolsRes.json()
      const executedToolsData = await executedToolsRes.json()
      turns.value = turnsData.turns || []
      messages.value = messagesData.messages || []
      turnTools.value = turnToolsData.turn_tools || []
      executedToolCalls.value = executedToolsData.executed_tool_calls || []
    } catch (e) {
      console.error('Failed to fetch session:', e)
    } finally {
      loading.value = false
    }
  }
  
  // Extract task summary from first human message
  function extractTaskSummary(content, maxLen = 60) {
    if (!content) return ''
    // Remove message ID prefix, markdown headers, code blocks, extra whitespace
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

  // Group messages by turn_id and attach to turns
  const turnsWithMessages = computed(() => {
    if (!turns.value.length) return []

    // Create a map of turn_id -> messages
    const messagesByTurn = {}
    messages.value.forEach(msg => {
      const turnId = msg.turn_id || 'unknown'
      if (!messagesByTurn[turnId]) {
        messagesByTurn[turnId] = []
      }
      messagesByTurn[turnId].push(msg)
    })

    // Sort messages within each turn by msg_index
    Object.keys(messagesByTurn).forEach(turnId => {
      messagesByTurn[turnId].sort((a, b) => (a.msg_index || 0) - (b.msg_index || 0))
    })

    // Create a map of turn_id -> tools
    const toolsByTurn = {}
    turnTools.value.forEach(tool => {
      const turnId = tool.turn_id || 'unknown'
      if (!toolsByTurn[turnId]) {
        toolsByTurn[turnId] = []
      }
      toolsByTurn[turnId].push(tool)
    })

    const executedToolsByTurn = {}
    executedToolCalls.value.forEach(tool => {
      const turnId = tool.turn_id || 'unknown'
      if (!executedToolsByTurn[turnId]) {
        executedToolsByTurn[turnId] = []
      }
      executedToolsByTurn[turnId].push(tool)
    })

    // Attach messages and tools to each turn, extract task summary
    return turns.value.map(turn => {
      const turnMessages = messagesByTurn[turn.turn_id] || []
      const firstHumanMsg = turnMessages.find(m => m.role === 'user')
      const taskSummary = firstHumanMsg ? extractTaskSummary(firstHumanMsg.content) : ''

      return {
        ...turn,
        messages: turnMessages,
        bound_tools: toolsByTurn[turn.turn_id] || [],
        executed_tool_calls: executedToolsByTurn[turn.turn_id] || [],
        task_summary: taskSummary
      }
    })
  })
  
  return {
    turns,
    messages,
    turnTools,
    executedToolCalls,
    turnsWithMessages,
    loading,
    fetchAll
  }
}
