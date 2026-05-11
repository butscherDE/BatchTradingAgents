import React, { useState, useEffect } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api, TaskItem, fetchJson } from '../api/client'
import { useWebSocket } from '../api/websocket'
import { parseUtc, formatTime } from '../api/time'

export default function Tasks() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [expandedTask, setExpandedTask] = useState<string | null>(null)
  const [taskDetail, setTaskDetail] = useState<{ result?: unknown; error?: string; payload?: unknown } | null>(null)

  const [showQueueDetail, setShowQueueDetail] = useState(false)
  const [showRetryModal, setShowRetryModal] = useState(false)
  const [retrySince, setRetrySince] = useState(() => {
    const d = new Date()
    d.setHours(d.getHours() - 1)
    return d.toISOString().slice(0, 16)
  })

  const page = parseInt(searchParams.get('page') || '0')
  const pageSize = parseInt(searchParams.get('size') || '20')
  const statusFilter = searchParams.get('status') || ''
  const typeFilter = searchParams.get('type') || ''
  const tickerFilter = searchParams.get('ticker') || ''
  const modelFilter = searchParams.get('model') || ''

  const setFilter = (key: string, value: string) => {
    const params = new URLSearchParams(searchParams)
    if (value) params.set(key, value); else params.delete(key)
    if (key !== 'page') params.delete('page')
    setSearchParams(params, { replace: true })
  }
  const setPage = (p: number | ((prev: number) => number)) => {
    const params = new URLSearchParams(searchParams)
    const newPage = typeof p === 'function' ? p(page) : p
    if (newPage > 0) params.set('page', String(newPage)); else params.delete('page')
    setSearchParams(params, { replace: true })
  }

  const queryClient = useQueryClient()
  const { lastMessage, connected } = useWebSocket()

  const { data: stats } = useQuery({
    queryKey: ['taskStats'],
    queryFn: api.getTaskStats,
  })

  const { data: newsSources } = useQuery({
    queryKey: ['newsSourceStatus'],
    queryFn: api.getNewsSourceStatus,
    refetchInterval: 10000,
  })

  const queryParams = new URLSearchParams()
  queryParams.set('limit', String(pageSize))
  queryParams.set('offset', String(page * pageSize))
  if (statusFilter) queryParams.set('status', statusFilter)
  if (modelFilter) queryParams.set('model_tier', modelFilter)
  if (typeFilter) queryParams.set('task_type', typeFilter)
  if (tickerFilter) queryParams.set('ticker', tickerFilter)

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ['tasks', page, statusFilter, typeFilter, tickerFilter, modelFilter],
    queryFn: () => fetchJson<TaskItem[]>(`/api/tasks?${queryParams.toString()}`),
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
        <div className="stat-card" onClick={() => setShowQueueDetail(true)} style={{ cursor: 'pointer' }}>
          <h3>Queue</h3>
          <div className="value">{stats?.queue_depth ?? '—'}</div>
          <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 4 }}>click for detail</div>
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
          <h3>Workers</h3>
          <div style={{ fontSize: 12 }}>
            {stats?.providers?.map(p => (
              <div key={p.name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                <span>
                  <strong>{p.name}:</strong>{' '}
                  <span style={{ color: p.state === 'executing' ? 'var(--green)' : p.state === 'paused' ? 'var(--yellow)' : 'var(--text-dim)' }}>
                    {p.state ?? 'offline'}
                  </span>
                  {p.active_tasks > 0 && <span style={{ color: 'var(--text-dim)' }}> ({p.active_tasks}/{p.max_concurrent})</span>}
                </span>
                <button
                  onClick={async (e) => {
                    e.stopPropagation()
                    const action = p.state === 'paused' || p.state === 'pausing' ? 'resume' : 'pause'
                    await fetch(`/api/tasks/${action}?provider=${p.name}`, { method: 'POST' })
                    queryClient.invalidateQueries({ queryKey: ['taskStats'] })
                  }}
                  style={{ fontSize: 10, padding: '1px 5px', background: p.state === 'paused' ? 'var(--green)' : 'var(--yellow)', color: 'var(--bg)' }}
                >
                  {p.state === 'paused' || p.state === 'pausing' ? '▶' : '⏸'}
                </button>
              </div>
            )) ?? <span style={{ color: 'var(--text-dim)' }}>—</span>}
          </div>
        </div>
        <div className="stat-card">
          <h3>WebSocket</h3>
          <div className="value" style={{ fontSize: 14 }}>{connected ? '🟢 connected' : '🔴 disconnected'}</div>
        </div>
        <div className="stat-card">
          <h3>Alpaca Stream</h3>
          <div className="value" style={{ fontSize: 14 }}>
            {newsSources?.alpaca.status === 'connected' ? '🟢' : newsSources?.alpaca.status === 'unknown' ? '⚪' : '🔴'}{' '}
            {newsSources?.alpaca.status ?? 'unknown'}
          </div>
          {newsSources?.alpaca.error && (
            <div style={{ fontSize: 10, color: 'var(--red)', marginTop: 4 }}>{newsSources.alpaca.error}</div>
          )}
        </div>
        <div className="stat-card">
          <h3>yfinance Poller</h3>
          <div className="value" style={{ fontSize: 14 }}>
            {newsSources?.yfinance.status === 'running' ? '🟢' : newsSources?.yfinance.status === 'backing_off' ? '🟡' : '🔴'}{' '}
            {newsSources?.yfinance.status ?? 'stopped'}
          </div>
          <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 4 }}>
            {newsSources ? `${newsSources.yfinance.tickers_total} tickers · ${newsSources.yfinance.articles_found} found` : ''}
          </div>
          {newsSources?.yfinance.last_error && (
            <div style={{ fontSize: 10, color: 'var(--red)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 150 }}>
              {newsSources.yfinance.last_error}
            </div>
          )}
        </div>
      </div>

      <div className="filters">
        <select value={statusFilter} onChange={e => setFilter('status', e.target.value)}>
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <select value={typeFilter} onChange={e => setFilter('type', e.target.value)}>
          <option value="">All types</option>
          <option value="news_screen">news_screen</option>
          <option value="news_consolidate">news_consolidate</option>
          <option value="investigation">investigation</option>
          <option value="full_analysis">full_analysis</option>
          <option value="merge_and_allocate">merge_and_allocate</option>
          <option value="watchlist_discovery">watchlist_discovery</option>
          <option value="watchlist_prune">watchlist_prune</option>
        </select>
        <select value={modelFilter} onChange={e => setFilter('model', e.target.value)}>
          <option value="">All models</option>
          <option value="quick">quick</option>
          <option value="deep">deep</option>
        </select>
        <input
          placeholder="Filter ticker..."
          value={tickerFilter}
          onChange={e => setFilter('ticker', e.target.value)}
          style={{ width: 100 }}
        />
        <button
          onClick={() => setShowRetryModal(true)}
          style={{ background: 'var(--yellow)', color: 'var(--bg)' }}
        >
          Retry Failed
        </button>
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
              {tasks.map((t: TaskItem) => {
                let duration: string
                let elapsed: number
                if (t.started_at && t.completed_at) {
                  elapsed = Math.floor((parseUtc(t.completed_at).getTime() - parseUtc(t.started_at).getTime()) / 1000)
                } else if (t.started_at) {
                  elapsed = Math.floor((Date.now() - parseUtc(t.started_at).getTime()) / 1000)
                } else {
                  elapsed = -1
                }
                if (elapsed < 0) {
                  duration = '—'
                } else if (elapsed < 60) {
                  duration = `${elapsed}s`
                } else {
                  duration = `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`
                }
                return (
                  <React.Fragment key={t.id}>
                  <tr onClick={async () => {
                    if (expandedTask === t.task_id) {
                      setExpandedTask(null)
                      setTaskDetail(null)
                    } else {
                      setExpandedTask(t.task_id)
                      try {
                        const detail = await fetchJson<{ result?: unknown; error?: string; payload?: unknown }>(`/api/tasks/${t.task_id}`)
                        setTaskDetail(detail)
                      } catch { setTaskDetail(null) }
                    }
                  }} style={{ cursor: 'pointer' }}>
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
                          onClick={(e) => { e.stopPropagation(); cancelMutation.mutate(t.task_id) }}
                          style={{ background: 'var(--red)', fontSize: 11, padding: '2px 6px' }}
                        >
                          cancel
                        </button>
                      )}
                    </td>
                  </tr>
                  {expandedTask === t.task_id && taskDetail && (
                    <tr>
                      <td colSpan={8} style={{ padding: '12px 16px', background: 'var(--surface)' }}>
                        <div style={{ fontSize: 12 }}>
                          {!!taskDetail.result && (
                            <div style={{ marginBottom: 8 }}>
                              <strong style={{ color: 'var(--accent)' }}>Result:</strong>
                              <pre style={{ marginTop: 4, whiteSpace: 'pre-wrap', color: 'var(--text)', maxHeight: 300, overflow: 'auto' }}>
                                {JSON.stringify(taskDetail.result, null, 2)}
                              </pre>
                            </div>
                          )}
                          {taskDetail.error && (
                            <div style={{ marginBottom: 8 }}>
                              <strong style={{ color: 'var(--red)' }}>Error:</strong>
                              <pre style={{ marginTop: 4, whiteSpace: 'pre-wrap', color: 'var(--red)' }}>{taskDetail.error}</pre>
                            </div>
                          )}
                          {!!taskDetail.payload && (
                            <div>
                              <strong style={{ color: 'var(--text-dim)' }}>Payload:</strong>
                              <pre style={{ marginTop: 4, whiteSpace: 'pre-wrap', color: 'var(--text-dim)', maxHeight: 200, overflow: 'auto' }}>
                                {JSON.stringify(taskDetail.payload, null, 2)}
                              </pre>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                )
              })}
            </tbody>
          </table>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, fontSize: 13 }}>
            <span style={{ color: 'var(--text-dim)' }}>
              Page {page + 1} · Showing {tasks.length} tasks
            </span>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <select
                value={pageSize}
                onChange={e => setFilter('size', e.target.value === '50' ? '' : e.target.value)}
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

      {showRetryModal && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }} onClick={() => setShowRetryModal(false)}>
          <div style={{ background: 'var(--surface)', borderRadius: 8, padding: 24, minWidth: 340 }} onClick={e => e.stopPropagation()}>
            <h2 style={{ marginTop: 0 }}>Retry Failed Tasks</h2>
            <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>Re-queue all failed tasks since:</p>
            <input
              type="datetime-local"
              value={retrySince}
              onChange={e => setRetrySince(e.target.value)}
              style={{ width: '100%', fontSize: 14, padding: '6px 8px', marginBottom: 16 }}
            />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => setShowRetryModal(false)}>Cancel</button>
              <button
                onClick={async () => {
                  const since = new Date(retrySince).toISOString()
                  const resp = await fetch(`/api/tasks/retry-failed?since=${encodeURIComponent(since)}`, { method: 'POST' })
                  if (resp.ok) {
                    const data = await resp.json()
                    alert(`Retried ${data.retried} tasks`)
                    queryClient.invalidateQueries({ queryKey: ['tasks'] })
                    queryClient.invalidateQueries({ queryKey: ['taskStats'] })
                  } else {
                    alert('Failed to retry tasks')
                  }
                  setShowRetryModal(false)
                }}
                style={{ background: 'var(--green)', color: 'var(--bg)' }}
              >
                Retry
              </button>
            </div>
          </div>
        </div>
      )}

      {showQueueDetail && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }} onClick={() => setShowQueueDetail(false)}>
          <div style={{ background: 'var(--surface)', borderRadius: 8, padding: 24, minWidth: 500, maxWidth: '90vw' }} onClick={e => e.stopPropagation()}>
            <h2 style={{ marginTop: 0 }}>Queue Detail — Total: {stats?.queue_depth ?? 0}</h2>
            <table>
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>State</th>
                  <th>Queued</th>
                  <th>Quick</th>
                  <th>Deep</th>
                  <th>Active</th>
                  <th>Limit</th>
                </tr>
              </thead>
              <tbody>
                {stats?.providers?.map(p => (
                  <tr key={p.name}>
                    <td><strong>{p.name}</strong></td>
                    <td><span className={`badge badge-${p.state === 'executing' ? 'running' : p.state === 'idle' ? 'completed' : p.state ?? 'unknown'}`}>{p.state ?? 'offline'}</span></td>
                    <td>{p.queue_depth}</td>
                    <td>{p.quick_queued}</td>
                    <td>{p.deep_queued}</td>
                    <td>{p.active_tasks}/{p.max_concurrent}</td>
                    <td>{p.max_queue < 0 ? '∞' : p.max_queue}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button onClick={() => setShowQueueDetail(false)} style={{ marginTop: 16 }}>Close</button>
          </div>
        </div>
      )}
    </div>
  )
}
