import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, NewsArticle } from '../api/client'
import { useWebSocket } from '../api/websocket'
import { useEffect } from 'react'

function DetailModal({ article, onClose }: { article: NewsArticle; onClose: () => void }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{article.headline}</h2>
          <button onClick={onClose} style={{ background: 'var(--border)' }}>✕</button>
        </div>

        <div className="modal-body">
          <div className="detail-section">
            <h3>Info</h3>
            <table className="detail-table">
              <tbody>
                <tr><td>Symbols</td><td>{article.symbols.join(', ') || '—'}</td></tr>
                <tr><td>Source</td><td>{article.source || '—'}</td></tr>
                <tr><td>Status</td><td><span className={`badge badge-${article.status}`}>{article.status}</span></td></tr>
                <tr><td>Published</td><td>{article.published_at ? new Date(article.published_at).toLocaleString() : '—'}</td></tr>
                <tr><td>Received</td><td>{new Date(article.received_at).toLocaleString()}</td></tr>
              </tbody>
            </table>
          </div>

          {article.summary && (
            <div className="detail-section">
              <h3>Summary</h3>
              <p>{article.summary}</p>
            </div>
          )}

          {article.quick_result && (
            <div className="detail-section">
              <h3>Quick Screen Result</h3>
              <table className="detail-table">
                <tbody>
                  <tr><td>Score</td><td className={(article.quick_result as any).score >= 0.6 ? 'positive' : ''}>{(article.quick_result as any).score?.toFixed(3)}</td></tr>
                  <tr><td>Reasoning</td><td>{(article.quick_result as any).reasoning || '—'}</td></tr>
                  <tr><td>Affected Ticker</td><td>{(article.quick_result as any).affected_ticker || '—'}</td></tr>
                </tbody>
              </table>
            </div>
          )}

          {article.escalation_reason && (
            <div className="detail-section">
              <h3>Escalation Reason</h3>
              <p>{article.escalation_reason}</p>
            </div>
          )}

          {article.deep_result && (
            <div className="detail-section">
              <h3>Deep Investigation Result</h3>
              <table className="detail-table">
                <tbody>
                  <tr><td>Verdict</td><td><strong>{(article.deep_result as any).verdict}</strong></td></tr>
                  <tr><td>Direction</td><td className={
                    (article.deep_result as any).direction === 'sell' ? 'negative' :
                    (article.deep_result as any).direction === 'buy' ? 'positive' : ''
                  }>{(article.deep_result as any).direction || '—'}</td></tr>
                  <tr><td>Reasoning</td><td>{(article.deep_result as any).reasoning || '—'}</td></tr>
                  <tr><td>Regenerate Report</td><td>{(article.deep_result as any).should_regenerate_report ? 'Yes' : 'No'}</td></tr>
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function NewsFeed() {
  const [statusFilter, setStatusFilter] = useState('')
  const [symbolFilter, setSymbolFilter] = useState('')
  const [selectedArticle, setSelectedArticle] = useState<NewsArticle | null>(null)
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
            </tr>
          </thead>
          <tbody>
            {articles.map((a: NewsArticle) => (
              <tr key={a.id} onClick={() => setSelectedArticle(a)} style={{ cursor: 'pointer' }}>
                <td style={{ whiteSpace: 'nowrap', fontSize: '12px' }}>
                  {new Date(a.received_at).toLocaleTimeString()}
                </td>
                <td>{a.headline}</td>
                <td>{a.symbols.join(', ')}</td>
                <td><span className={`badge badge-${a.status}`}>{a.status}</span></td>
                <td>{(a.quick_result as any)?.score?.toFixed(2) ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selectedArticle && (
        <DetailModal article={selectedArticle} onClose={() => setSelectedArticle(null)} />
      )}
    </div>
  )
}
