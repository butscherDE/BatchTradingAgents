import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, TaskItem, TaskStats } from '../api/client'
import { useWebSocket } from '../api/websocket'

export default function Tasks() {
  const queryClient = useQueryClient()
  const { lastMessage, connected } = useWebSocket()

  const { data: stats } = useQuery({
    queryKey: ['taskStats'],
    queryFn: api.getTaskStats,
  })

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.getTasks({ limit: '100' }),
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

      {isLoading ? <p>Loading...</p> : (
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
            </tr>
          </thead>
          <tbody>
            {tasks.map((t: TaskItem) => {
              const duration = t.started_at && t.completed_at
                ? ((new Date(t.completed_at).getTime() - new Date(t.started_at).getTime()) / 1000).toFixed(1) + 's'
                : t.started_at
                  ? 'running...'
                  : '—'
              return (
                <tr key={t.id}>
                  <td style={{ whiteSpace: 'nowrap', fontSize: '12px' }}>
                    {new Date(t.created_at).toLocaleTimeString()}
                  </td>
                  <td>{t.task_type}</td>
                  <td>{t.ticker ?? '—'}</td>
                  <td>{t.model_tier}</td>
                  <td><span className={`badge badge-${t.status}`}>{t.status}</span></td>
                  <td>{duration}</td>
                  <td style={{ color: 'var(--red)', fontSize: 12, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {t.error ?? ''}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
