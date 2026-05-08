import { useState, useEffect } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { fetchJson } from '../api/client'
import { useWebSocket } from '../api/websocket'
import { parseUtc } from '../api/time'

interface WatchlistTicker {
  id: number
  symbol: string
  added_by: string
  added_at: string
  removed_at: string | null
  remove_reason: string | null
  active: boolean
}

interface WatchlistConfig {
  dynamic_discovery: boolean
  auto_prune: boolean
}

export default function Watchlist() {
  const [newSymbol, setNewSymbol] = useState('')
  const [showInactive, setShowInactive] = useState(false)
  const queryClient = useQueryClient()
  const { lastMessage } = useWebSocket()

  const { data: tickers = [], isLoading } = useQuery({
    queryKey: ['watchlist', showInactive],
    queryFn: () => fetchJson<WatchlistTicker[]>(`/api/watchlist?active_only=${!showInactive}`),
  })

  const { data: config } = useQuery({
    queryKey: ['watchlistConfig'],
    queryFn: () => fetchJson<WatchlistConfig>('/api/watchlist/config'),
  })

  useEffect(() => {
    if (lastMessage?.type === 'watchlist_changed') {
      queryClient.invalidateQueries({ queryKey: ['watchlist'] })
    }
  }, [lastMessage, queryClient])

  const addMutation = useMutation({
    mutationFn: (symbol: string) =>
      fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol }),
      }).then(r => { if (!r.ok) throw new Error('Failed'); return r.json() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['watchlist'] })
      setNewSymbol('')
    },
  })

  const removeMutation = useMutation({
    mutationFn: (symbol: string) =>
      fetch(`/api/watchlist/${symbol}`, { method: 'DELETE' }).then(r => {
        if (!r.ok) throw new Error('Failed'); return r.json()
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const activeTickers = tickers.filter(t => t.active)
  const inactiveTickers = tickers.filter(t => !t.active)

  return (
    <div>
      <h1>Watchlist</h1>

      <div className="stat-grid">
        <div className="stat-card">
          <h3>Active Tickers</h3>
          <div className="value">{activeTickers.length}</div>
        </div>
        <div className="stat-card">
          <h3>Dynamic Discovery</h3>
          <div className="value" style={{ fontSize: 16 }}>
            {config?.dynamic_discovery ? '🟢 enabled' : '⚪ disabled'}
          </div>
        </div>
        <div className="stat-card">
          <h3>Auto Prune</h3>
          <div className="value" style={{ fontSize: 16 }}>
            {config?.auto_prune ? '🟢 enabled' : '⚪ disabled'}
          </div>
        </div>
      </div>

      <div className="filters" style={{ marginBottom: 16 }}>
        <form onSubmit={e => { e.preventDefault(); if (newSymbol.trim()) addMutation.mutate(newSymbol.trim().toUpperCase()) }}>
          <input
            placeholder="Add ticker..."
            value={newSymbol}
            onChange={e => setNewSymbol(e.target.value)}
            style={{ width: 120 }}
          />
          <button type="submit" style={{ marginLeft: 8 }}>Add</button>
        </form>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 16, fontSize: 13, color: 'var(--text-dim)' }}>
          <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} />
          Show removed
        </label>
      </div>

      {isLoading ? <p>Loading...</p> : (
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Added By</th>
              <th>Added</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {tickers.map(t => (
              <tr key={t.id} style={{ opacity: t.active ? 1 : 0.5 }}>
                <td style={{ fontWeight: 600 }}>{t.symbol}</td>
                <td>
                  <span className={`badge ${t.added_by === 'auto_discovery' ? 'badge-escalated' : 'badge-completed'}`}>
                    {t.added_by}
                  </span>
                </td>
                <td style={{ fontSize: 12 }}>{parseUtc(t.added_at).toLocaleDateString()}</td>
                <td>
                  {t.active ? (
                    <span className="badge badge-completed">active</span>
                  ) : (
                    <span className="badge badge-failed" title={t.remove_reason || ''}>removed</span>
                  )}
                </td>
                <td>
                  {t.active && (
                    <button
                      onClick={() => removeMutation.mutate(t.symbol)}
                      style={{ background: 'var(--red)', fontSize: 11, padding: '3px 8px' }}
                    >
                      remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
