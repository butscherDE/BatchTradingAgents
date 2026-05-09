import { useState, useEffect } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { api, TaskItem, TaskStats, fetchJson } from '../api/client'
import { useWebSocket } from '../api/websocket'
import { parseUtc, formatTime } from '../api/time'

export default function Tasks() {
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(50)
  const [statusFilter, setStatusFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [tickerFilter, setTickerFilter] = useState('')
  const [modelFilter, setModelFilter] = useState('')

  const queryClient = useQueryClient()
  const { lastMessage, connected } = useWebSocket()

  const { data: stats } = useQuery({
    queryKey: ['taskStats'],
    queryFn: api.getTaskStats,
  })

  const queryParams = new URLSearchParams()
  queryParams.set('limit', String(pageSize))
  queryParams.set('offset', String(page * pageSize))
  if (statusFilter) queryParams.set('status', statusFilter)
  if (modelFilter) queryParams.set('model_tier', modelFilter)

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ['tasks', page, statusFilter, typeFilter, tickerFilter, modelFilter],
    queryFn: () => fetchJson<TaskItem[]>(`/api/tasks?${queryParams.toString()}`),
  })

  // Client-side filters for type and ticker (not in API yet)
  const filtered = tasks.filter(t => {
    if (typeFilter && t.task_type !== typeFilter) return false
    if (tickerFilter && !(t.ticker || '').toLowerCase().includes(tickerFilter.toLowerCase())) return false
    return true
  })

  const cancelMutation = useMutation({
    mutationFn: (taskId: string) =>
      fetch(`/api/tasks/${taskId}/cancel`, { method: 'POST' }).then(async r => {
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed') }
        return r.json()
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['taskStats'] })
    },
  })

  const cancelAllMutation = useMutation({
    mutationFn: () =>
      fetch('/api/tasks/cancel-all', { method: 'POST' }).then(async r => {
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed') }
        return r.json()
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['taskStats'] })
    },
  })

  useEffect(() => {
    if (lastMessage?.type === 'task_update') {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['taskStats'] })
    }
  }, [lastMessage, queryClient])

  return (
    <div>
      <h1>Task Manager</h1>

      <div className="stat-grid">
        <div className="stat-card">
          <h3>Quick Queue</h3>
          <div className="value">{stats?.queue_depth_quick ?? '—'}</div>
        </div>
        <div className="stat-card">
          <h3>Deep Queue</h3>
          <div className="value">{stats?.queue_depth_deep ?? '—'}</div>
        </div>
        <div className="stat-card">
          <h3>Completed</h3>
          <div className="value positive">{stats?.total_completed ?? 0}</div>
        </div>
        <div className="stat-card">
          <h3>Failed</h3>
          <div className="value negative">{stats?.total_failed ?? 0}</div>
        </div>
        <div className="stat-card">
          <h3>Worker State</h3>
          <div className="value" style={{ fontSize: 16 }}>{stats?.worker_state ?? 'unknown'}</div>
        </div>
        <div className="stat-card">
          <h3>Current Model</h3>
          <div className="value" style={{ fontSize: 14 }}>{stats?.current_model ?? 'none'}</div>
        </div>
        <div className="stat-card">
          <h3>Model Switches</h3>
          <div className="value">{stats?.model_switches ?? 0}</div>
        </div>
        <div className="stat-card">
          <h3>WebSocket</h3>
          <div className="value" style={{ fontSize: 14 }}>{connected ? '🟢 connected' : '🔴 disconnected'}</div>
        </div>
      </div>

      <div className="filters">
        <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(0) }}>
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <select value={typeFilter} onChange={e => { setTypeFilter(e.target.value); setPage(0) }}>
          <option value="">All types</option>
          <option value="news_screen">news_screen</option>
          <option value="investigation">investigation</option>
          <option value="full_analysis">full_analysis</option>
          <option value="merge_and_allocate">merge_and_allocate</option>
          <option value="watchlist_discovery">watchlist_discovery</option>
        </select>
        <select value={modelFilter} onChange={e => { setModelFilter(e.target.value); setPage(0) }}>
          <option value="">All models</option>
          <option value="quick">quick</option>
          <option value="deep">deep</option>
        </select>
        <input
          placeholder="Filter ticker..."
          value={tickerFilter}
          onChange={e => { setTickerFilter(e.target.value); setPage(0) }}
          style={{ width: 100 }}
        />
        <button
          onClick={() => { if (confirm('Cancel all queued tasks?')) cancelAllMutation.mutate() }}
          disabled={cancelAllMutation.isPending}
          style={{ background: 'var(--red)', marginLeft: 'auto' }}
        >
          Cancel All Queued
        </button>
      </div>

      {isLoading ? <p>Loading...</p> : (
        <>
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Type</th>
                <th>Ticker</th>
                <th>Model</th>
                <th>Status</th>
                <th>Duration</th>
                <th>Error</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t: TaskItem) => {
                let duration: string
                if (t.started_at && t.completed_at) {
                  duration = ((parseUtc(t.completed_at).getTime() - parseUtc(t.started_at).getTime()) / 1000).toFixed(1) + 's'
                } else if (t.started_at) {
                  const elapsed = Math.floor((Date.now() - parseUtc(t.started_at).getTime()) / 1000)
                  const mins = Math.floor(elapsed / 60)
                  const secs = elapsed % 60
                  duration = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`
                } else {
                  duration = '—'
                }
                return (
                  <tr key={t.id}>
                    <td style={{ whiteSpace: 'nowrap', fontSize: '12px' }}>
                      {formatTime(t.created_at)}
                    </td>
                    <td>{t.task_type}</td>
                    <td>{t.ticker ?? '—'}</td>
                    <td>{t.model_tier}</td>
                    <td><span className={`badge badge-${t.status}`}>{t.status}</span></td>
                    <td>{duration}</td>
                    <td style={{ color: 'var(--red)', fontSize: 12, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {t.error ?? ''}
                    </td>
                    <td>
                      {(t.status === 'queued' || t.status === 'running') && (
                        <button
                          onClick={() => cancelMutation.mutate(t.task_id)}
                          style={{ background: 'var(--red)', fontSize: 11, padding: '2px 6px' }}
                        >
                          cancel
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, fontSize: 13 }}>
            <span style={{ color: 'var(--text-dim)' }}>
              Page {page + 1} · Showing {filtered.length} tasks
            </span>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <select
                value={pageSize}
                onChange={e => { setPageSize(Number(e.target.value)); setPage(0) }}
                style={{ fontSize: 13 }}
              >
                <option value={10}>10 / page</option>
                <option value={20}>20 / page</option>
                <option value={50}>50 / page</option>
                <option value={100}>100 / page</option>
              </select>
              <button
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                style={{ opacity: page === 0 ? 0.3 : 1 }}
              >
                ← Prev
              </button>
              <button
                onClick={() => setPage(p => p + 1)}
                disabled={tasks.length < pageSize}
                style={{ opacity: tasks.length < pageSize ? 0.3 : 1 }}
              >
                Next →
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
