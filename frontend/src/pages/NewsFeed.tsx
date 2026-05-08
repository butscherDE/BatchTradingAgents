import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, NewsArticle } from '../api/client'
import { useWebSocket } from '../api/websocket'
import { useEffect } from 'react'

export default function NewsFeed() {
  const [statusFilter, setStatusFilter] = useState('')
  const [symbolFilter, setSymbolFilter] = useState('')
  const queryClient = useQueryClient()
  const { lastMessage } = useWebSocket()

  const { data: articles = [], isLoading } = useQuery({
    queryKey: ['news', statusFilter, symbolFilter],
    queryFn: () => api.getNews({
      limit: '200',
      status: statusFilter || undefined,
      symbol: symbolFilter || undefined,
    }),
  })

  useEffect(() => {
    if (lastMessage?.type === 'news_added' || lastMessage?.type === 'news_status_changed') {
      queryClient.invalidateQueries({ queryKey: ['news'] })
    }
  }, [lastMessage, queryClient])

  const exportState = async (id: number) => {
    const state = await api.getNewsState(id)
    const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `news_state_${id}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div>
      <h1>News Feed</h1>
      <div className="filters">
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="quick_no_action">Quick - No Action</option>
          <option value="escalated">Escalated</option>
          <option value="deep_no_action">Deep - No Action</option>
          <option value="report_generated">Report Generated</option>
        </select>
        <input
          placeholder="Filter by symbol..."
          value={symbolFilter}
          onChange={e => setSymbolFilter(e.target.value)}
        />
      </div>

      {isLoading ? <p>Loading...</p> : (
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Headline</th>
              <th>Symbols</th>
              <th>Status</th>
              <th>Score</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {articles.map((a: NewsArticle) => (
              <tr key={a.id}>
                <td style={{ whiteSpace: 'nowrap', fontSize: '12px' }}>
                  {new Date(a.received_at).toLocaleTimeString()}
                </td>
                <td>{a.headline}</td>
                <td>{a.symbols.join(', ')}</td>
                <td><span className={`badge badge-${a.status}`}>{a.status}</span></td>
                <td>{(a.quick_result as any)?.score?.toFixed(2) ?? '—'}</td>
                <td>
                  <button onClick={() => exportState(a.id)} style={{ fontSize: '11px', padding: '3px 6px' }}>
                    dump
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
