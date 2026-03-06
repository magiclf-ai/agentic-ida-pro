<script setup>
import { computed } from 'vue'
import { ArrowRight, MessageSquare, Wrench, CheckCircle, AlertCircle } from 'lucide-vue-next'
import { formatTimestamp, formatDuration } from '../utils/formatters.js'

const props = defineProps({
  turns: { type: Array, default: () => [] },
  messages: { type: Array, default: () => [] },
  tools: { type: Array, default: () => [] },
  selectedTurnId: { type: [Number, String], default: null },
  isDark: { type: Boolean, default: false }
})

const emit = defineEmits(['select'])

const timelineData = computed(() => {
  // Sort by database id to maintain chronological order
  const sortedTurns = [...props.turns].sort((a, b) => (a.id || 0) - (b.id || 0))
  
  return sortedTurns.map((turn, index) => {
    const turnMessages = props.messages.filter(m => m.turn_id === turn.turn_id)
    const turnTools = props.tools.filter(t => t.turn_id === turn.turn_id)
    
    const requestMsg = turnMessages.find(m => m.direction === 'request')
    const responseMsg = turnMessages.find(m => m.direction === 'response')
    
    // Generate display name: "Turn X phase" format
    const displayNumber = turn.iteration > 0 ? turn.iteration : (index + 1)
    const displayPhase = turn.phase || ''
    
    return {
      ...turn,
      displayNumber,
      displayPhase,
      messages: turnMessages,
      tools: turnTools,
      hasError: turn.status === 'failed' || turnMessages.some(m => m.is_error),
      hasTools: turnTools.length > 0,
      tokenCount: turnMessages.reduce((sum, m) => sum + (m.token_count || 0), 0)
    }
  })
})

function getTurnIcon(turn) {
  if (turn.hasError) return AlertCircle
  if (turn.hasTools) return Wrench
  if (turn.status === 'completed') return CheckCircle
  return MessageSquare
}

function getTurnColor(turn) {
  if (turn.hasError) return '#ef4444'
  if (turn.hasTools) return '#f59e0b'
  if (turn.status === 'completed') return '#10b981'
  return '#3b82f6'
}
</script>

<template>
  <div class="turns-timeline" :class="{ 'is-dark': isDark }">
    <h2 class="timeline-title">
      <ArrowRight class="title-icon" />
      Turn Timeline
    </h2>
    
    <div class="timeline-container">
      <div
        v-for="(turn, index) in timelineData"
        :key="turn.turn_id"
        class="timeline-item"
        :class="{
          active: String(turn.turn_id) === String(selectedTurnId),
          error: turn.hasError,
          completed: turn.status === 'completed'
        }"
        @click="$emit('select', turn.turn_id)"
      >
        <div class="timeline-connector" v-if="index > 0"></div>
        
        <div class="timeline-node" :style="{ color: getTurnColor(turn) }">
          <component :is="getTurnIcon(turn)" class="node-icon" />
        </div>
        
        <div class="timeline-content">
          <div class="turn-header">
            <span class="turn-id">
              Turn {{ turn.displayNumber }}
              <span v-if="turn.displayPhase" class="phase-tag">{{ turn.displayPhase }}</span>
            </span>
            <span class="turn-status" :class="turn.status">{{ turn.status }}</span>
          </div>
          
          <div class="turn-meta">
            <span class="meta-item" v-if="turn.token_count">
              {{ turn.token_count }} tokens
            </span>
            <span class="meta-item" v-if="turn.latency_ms">
              {{ formatDuration(turn.latency_ms) }}
            </span>
            <span class="meta-item" v-if="turn.tools?.length">
              {{ turn.tools.length }} tool{{ turn.tools.length > 1 ? 's' : '' }}
            </span>
          </div>
          
          <div class="turn-time">
            {{ formatTimestamp(turn.created_at, 'HH:mm:ss') }}
          </div>
        </div>
      </div>
      
      <div v-if="timelineData.length === 0" class="empty-timeline">
        No turns available
      </div>
    </div>
  </div>
</template>

<style scoped>
.turns-timeline {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #ffffff;
  border-right: 1px solid #e5e7eb;
}

.turns-timeline.is-dark {
  background: #0d1117;
  border-right-color: #30363d;
}

.timeline-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  padding: 16px;
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
  border-bottom: 1px solid #e5e7eb;
}

.turns-timeline.is-dark .timeline-title {
  color: #e5e7eb;
  border-bottom-color: #30363d;
}

.title-icon {
  width: 18px;
  height: 18px;
  color: #8b5cf6;
}

.timeline-container {
  flex: 1;
  overflow-y: auto;
  padding: 20px 16px;
}

.timeline-item {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  margin-bottom: 4px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.timeline-item:hover {
  background: #f9fafb;
}

.timeline-item.active {
  background: #eff6ff;
}

.turns-timeline.is-dark .timeline-item:hover {
  background: #161b22;
}

.turns-timeline.is-dark .timeline-item.active {
  background: rgba(59, 130, 246, 0.1);
}

.timeline-connector {
  position: absolute;
  top: -16px;
  left: 30px;
  width: 2px;
  height: 16px;
  background: #e5e7eb;
}

.turns-timeline.is-dark .timeline-connector {
  background: #30363d;
}

.timeline-item.completed .timeline-connector {
  background: #10b981;
}

.timeline-node {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: #ffffff;
  border: 2px solid currentColor;
  border-radius: 50%;
  flex-shrink: 0;
}

.turns-timeline.is-dark .timeline-node {
  background: #0d1117;
}

.node-icon {
  width: 16px;
  height: 16px;
}

.timeline-content {
  flex: 1;
  min-width: 0;
}

.turn-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.turn-id {
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
  display: flex;
  align-items: center;
  gap: 8px;
}

.turns-timeline.is-dark .turn-id {
  color: #e5e7eb;
}

.phase-tag {
  padding: 2px 6px;
  font-size: 10px;
  font-weight: 500;
  text-transform: uppercase;
  color: #8b5cf6;
  background: rgba(139, 92, 246, 0.1);
  border-radius: 4px;
}

.turns-timeline.is-dark .phase-tag {
  background: rgba(139, 92, 246, 0.15);
}

.turn-status {
  padding: 2px 6px;
  font-size: 10px;
  font-weight: 500;
  text-transform: uppercase;
  border-radius: 4px;
}

.turn-status.completed {
  color: #10b981;
  background: rgba(16, 185, 129, 0.1);
}

.turn-status.failed {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.1);
}

.turn-status.in_progress {
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.1);
}

.turns-timeline.is-dark .turn-status.completed {
  background: rgba(16, 185, 129, 0.15);
}

.turns-timeline.is-dark .turn-status.failed {
  background: rgba(239, 68, 68, 0.15);
}

.turns-timeline.is-dark .turn-status.in_progress {
  background: rgba(245, 158, 11, 0.15);
}

.turn-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 4px;
}

.meta-item {
  font-size: 11px;
  color: #6b7280;
}

.turns-timeline.is-dark .meta-item {
  color: #9ca3af;
}

.turn-time {
  font-size: 11px;
  color: #9ca3af;
}

.empty-timeline {
  padding: 40px 16px;
  text-align: center;
  font-size: 13px;
  color: #9ca3af;
}
</style>
