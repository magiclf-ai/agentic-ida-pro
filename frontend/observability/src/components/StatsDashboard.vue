<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import VChart from 'vue-echarts'
import { Activity, Clock, CheckCircle, Layers, Hash, Cpu } from 'lucide-vue-next'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, PieChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent } from 'echarts/components'
import * as echarts from 'echarts'

use([CanvasRenderer, BarChart, PieChart, LineChart, GridComponent, TooltipComponent, LegendComponent, TitleComponent])

const props = defineProps({
  turns: { type: Array, default: () => [] },
  messages: { type: Array, default: () => [] },
  tools: { type: Array, default: () => [] },
  isDark: { type: Boolean, default: false }
})

const stats = computed(() => {
  const totalTokens = props.messages.reduce((sum, m) => sum + (m.token_count || 0), 0)
  const totalLatency = props.turns.reduce((sum, t) => sum + (t.latency_ms || 0), 0)
  const completedTurns = props.turns.filter(t => t.status === 'completed').length
  const successRate = props.turns.length > 0 ? (completedTurns / props.turns.length * 100).toFixed(1) : 0
  
  const toolStats = {}
  props.tools.forEach(t => {
    const name = t.tool_name || 'unknown'
    if (!toolStats[name]) {
      toolStats[name] = { count: 0, success: 0 }
    }
    toolStats[name].count++
    if (t.is_success) toolStats[name].success++
  })
  
  return {
    totalTokens,
    totalLatency,
    turnCount: props.turns.length,
    messageCount: props.messages.length,
    toolCount: props.tools.length,
    successRate,
    toolStats
  }
})

const tokenChartOption = computed(() => {
  const turnTokens = props.turns.map(turn => {
    const turnMessages = props.messages.filter(m => m.turn_id === turn.turn_id)
    return {
      turn: `Turn ${turn.turn_id}`,
      tokens: turnMessages.reduce((sum, m) => sum + (m.token_count || 0), 0)
    }
  })
  
  return {
    backgroundColor: 'transparent',
    title: {
      text: 'Token Usage per Turn',
      left: 'center',
      textStyle: {
        fontSize: 14,
        fontWeight: 500,
        color: props.isDark ? '#e5e7eb' : '#1f2937'
      }
    },
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'category',
      data: turnTokens.map(t => t.turn),
      axisLine: { lineStyle: { color: props.isDark ? '#374151' : '#e5e7eb' } },
      axisLabel: { color: props.isDark ? '#9ca3af' : '#6b7280' }
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: props.isDark ? '#374151' : '#e5e7eb' } },
      axisLabel: { color: props.isDark ? '#9ca3af' : '#6b7280' },
      splitLine: { lineStyle: { color: props.isDark ? '#374151' : '#f3f4f6' } }
    },
    series: [{
      type: 'bar',
      data: turnTokens.map(t => t.tokens),
      itemStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#3b82f6' },
          { offset: 1, color: '#8b5cf6' }
        ])
      },
      barWidth: '60%'
    }]
  }
})

const toolChartOption = computed(() => {
  const toolData = Object.entries(stats.value.toolStats).map(([name, data]) => ({
    name,
    value: data.count
  }))
  
  return {
    backgroundColor: 'transparent',
    title: {
      text: 'Tool Calls Distribution',
      left: 'center',
      textStyle: {
        fontSize: 14,
        fontWeight: 500,
        color: props.isDark ? '#e5e7eb' : '#1f2937'
      }
    },
    tooltip: { trigger: 'item' },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      avoidLabelOverlap: false,
      itemStyle: {
        borderRadius: 8,
        borderColor: props.isDark ? '#161b22' : '#ffffff',
        borderWidth: 2
      },
      label: { show: false },
      emphasis: {
        label: {
          show: true,
          fontSize: 14,
          fontWeight: 'bold',
          color: props.isDark ? '#e5e7eb' : '#1f2937'
        }
      },
      data: toolData.length > 0 ? toolData : [{ name: 'No data', value: 1 }],
      color: ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']
    }]
  }
})
</script>

<template>
  <div class="stats-dashboard" :class="{ 'is-dark': isDark }">
    <h2 class="dashboard-title">
      <Activity class="title-icon" />
      Session Statistics
    </h2>
    
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon-wrapper blue">
          <Hash class="stat-icon" />
        </div>
        <div class="stat-info">
          <span class="stat-value">{{ stats.turnCount }}</span>
          <span class="stat-label">Turns</span>
        </div>
      </div>
      
      <div class="stat-card">
        <div class="stat-icon-wrapper green">
          <CheckCircle class="stat-icon" />
        </div>
        <div class="stat-info">
          <span class="stat-value">{{ stats.successRate }}%</span>
          <span class="stat-label">Success Rate</span>
        </div>
      </div>
      
      <div class="stat-card">
        <div class="stat-icon-wrapper purple">
          <Layers class="stat-icon" />
        </div>
        <div class="stat-info">
          <span class="stat-value">{{ stats.totalTokens.toLocaleString() }}</span>
          <span class="stat-label">Total Tokens</span>
        </div>
      </div>
      
      <div class="stat-card">
        <div class="stat-icon-wrapper orange">
          <Clock class="stat-icon" />
        </div>
        <div class="stat-info">
          <span class="stat-value">{{ (stats.totalLatency / 1000).toFixed(1) }}s</span>
          <span class="stat-label">Total Time</span>
        </div>
      </div>
      
      <div class="stat-card">
        <div class="stat-icon-wrapper cyan">
          <Cpu class="stat-icon" />
        </div>
        <div class="stat-info">
          <span class="stat-value">{{ stats.toolCount }}</span>
          <span class="stat-label">Tool Calls</span>
        </div>
      </div>
      
      <div class="stat-card">
        <div class="stat-icon-wrapper pink">
          <Activity class="stat-icon" />
        </div>
        <div class="stat-info">
          <span class="stat-value">{{ stats.messageCount }}</span>
          <span class="stat-label">Messages</span>
        </div>
      </div>
    </div>
    
    <div class="charts-row">
      <div class="chart-card">
        <v-chart :option="tokenChartOption" style="width: 100%; height: 280px;" />
      </div>
      
      <div class="chart-card">
        <v-chart :option="toolChartOption" style="width: 100%; height: 280px;" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.stats-dashboard {
  padding: 20px;
  background: #ffffff;
}

.stats-dashboard.is-dark {
  background: #0d1117;
}

.dashboard-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 20px 0;
  font-size: 18px;
  font-weight: 600;
  color: #1f2937;
}

.stats-dashboard.is-dark .dashboard-title {
  color: #e5e7eb;
}

.title-icon {
  width: 20px;
  height: 20px;
  color: #3b82f6;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

@media (max-width: 1200px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  transition: all 0.2s;
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}

.stats-dashboard.is-dark .stat-card {
  background: #161b22;
  border-color: #30363d;
}

.stat-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 12px;
}

.stat-icon-wrapper.blue {
  background: rgba(59, 130, 246, 0.1);
  color: #3b82f6;
}

.stat-icon-wrapper.green {
  background: rgba(16, 185, 129, 0.1);
  color: #10b981;
}

.stat-icon-wrapper.purple {
  background: rgba(139, 92, 246, 0.1);
  color: #8b5cf6;
}

.stat-icon-wrapper.orange {
  background: rgba(245, 158, 11, 0.1);
  color: #f59e0b;
}

.stat-icon-wrapper.cyan {
  background: rgba(6, 182, 212, 0.1);
  color: #06b6d4;
}

.stat-icon-wrapper.pink {
  background: rgba(236, 72, 153, 0.1);
  color: #ec4899;
}

.stat-icon {
  width: 24px;
  height: 24px;
}

.stat-info {
  display: flex;
  flex-direction: column;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: #1f2937;
  line-height: 1;
}

.stats-dashboard.is-dark .stat-value {
  color: #e5e7eb;
}

.stat-label {
  font-size: 13px;
  color: #6b7280;
  margin-top: 4px;
}

.stats-dashboard.is-dark .stat-label {
  color: #9ca3af;
}

.charts-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

@media (max-width: 1200px) {
  .charts-row {
    grid-template-columns: 1fr;
  }
}

.chart-card {
  padding: 16px;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
}

.stats-dashboard.is-dark .chart-card {
  background: #161b22;
  border-color: #30363d;
}
</style>
