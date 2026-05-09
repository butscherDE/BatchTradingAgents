import { useState, useEffect } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { fetchJson, AccountSummary, api } from '../api/client'
import { useWebSocket } from '../api/websocket'
import { parseUtc } from '../api/time'
import TickerSearch from '../components/TickerSearch'

interface WatchlistTicker {
  id: number
  account_id: string
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
  const [selectedAccount, setSelectedAccount] = useState<string | null>(null)
  const [showInactive, setShowInactive] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const queryClient = useQueryClient()
  const { lastMessage } = useWebSocket()

  const { data: accounts = [] } = useQuery({
    queryKey: ['accounts'],
    queryFn: api.getAccounts,
  })

  const accountId = selectedAccount || accounts[0]?.id || ''

  const { data: tickers = [], isLoading } = useQuery({
    queryKey: ['watchlist', accountId, showInactive],
    queryFn: () => fetchJson<WatchlistTicker[]>(
      `/api/watchlist?account_id=${accountId}&active_only=${!showInactive}`
    ),
    enabled: !!accountId,
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
        body: JSON.stringify({ account_id: accountId, symbol }),
      }).then(async r => { if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed') } return r.json() }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const removeMutation = useMutation({
    mutationFn: (symbol: string) =>
      fetch(`/api/watchlist/${symbol}?account_id=${accountId}`, { method: 'DELETE' }).then(async r => {
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed') }
        return r.json()
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const analyzeMutation = useMutation({
    mutationFn: (symbols?: string[]) => {
      const params = new URLSearchParams({ account_id: accountId })
      if (symbols && symbols.length === 1) params.set('symbol', symbols[0]!)
      return fetch(`/api/watchlist/analyze?${params.toString()}`, { method: 'POST' }).then(async r => {
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed') }
        return r.json()
      })
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      setSelected(new Set())
      alert(`Submitted ${data.count} ticker(s) for full analysis`)
    },
  })

  const analyzeSelected = () => {
    if (selected.size === 0) return
    const symbols = Array.from(selected)
    Promise.all(symbols.map(s =>
      fetch(`/api/watchlist/analyze?account_id=${accountId}&symbol=${s}`, { method: 'POST' })
    )).then(() => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      setSelected(new Set())
      alert(`Submitted ${symbols.length} ticker(s) for full analysis`)
    })
  }

  const activeCount = tickers.filter(t => t.active).length

  return (
    <div>
      <h1>Watchlist</h1>

      {accounts.length > 0 && (
        <div className="stat-grid" style={{ marginBottom: 16 }}>
          {accounts.map((a: AccountSummary) => (
            <div
              key={a.id}
              className="stat-card"
              style={{
                cursor: 'pointer',
                borderColor: accountId === a.id ? 'var(--accent)' : undefined,
                borderWidth: accountId === a.id ? 2 : 1,
              }}
              onClick={() => setSelectedAccount(a.id)}
            >
              <h3>{a.name}</h3>
              <div style={{ fontSize: 13, color: 'var(--text-dim)', marginTop: 4 }}>
                Strategy: {a.strategy} · Watchlist: {a.watchlist}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="stat-grid" style={{ marginBottom: 16 }}>
        <div className="stat-card">
          <h3>Active Tickers</h3>
          <div className="value">{activeCount}</div>
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
        <TickerSearch
          onSelect={(symbol) => addMutation.mutate(symbol)}
          disabled={addMutation.isPending || !accountId}
        />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 16, fontSize: 13, color: 'var(--text-dim)' }}>
          <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} />
          Show removed
        </label>
        <button
          onClick={() => analyzeSelected()}
          disabled={selected.size === 0 || !accountId}
          style={{ marginLeft: 'auto', opacity: selected.size === 0 ? 0.4 : 1 }}
        >
          Analyze Selected ({selected.size})
        </button>
        <button
          onClick={() => { if (confirm(`Run full analysis for all ${activeCount} tickers?`)) analyzeMutation.mutate() }}
          disabled={analyzeMutation.isPending || !accountId}
        >
          {analyzeMutation.isPending ? 'Submitting...' : `Analyze All (${activeCount})`}
        </button>
      </div>

      {addMutation.isError && (
        <p style={{ color: 'var(--red)', marginBottom: 12, fontSize: 13 }}>
          {(addMutation.error as Error).message}
        </p>
      )}

      {!accountId && <p style={{ color: 'var(--text-dim)' }}>No accounts configured</p>}

      {accountId && isLoading ? <p>Loading...</p> : (
        <table>
          <thead>
            <tr>
              <th style={{ width: 30 }}>
                <input
                  type="checkbox"
                  checked={selected.size > 0 && selected.size === tickers.filter(t => t.active).length}
                  onChange={e => {
                    if (e.target.checked) {
                      setSelected(new Set(tickers.filter(t => t.active).map(t => t.symbol)))
                    } else {
                      setSelected(new Set())
                    }
                  }}
                />
              </th>
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
                <td>
                  {t.active && (
                    <input
                      type="checkbox"
                      checked={selected.has(t.symbol)}
                      onChange={e => {
                        const next = new Set(selected)
                        if (e.target.checked) next.add(t.symbol); else next.delete(t.symbol)
                        setSelected(next)
                      }}
                    />
                  )}
                </td>
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
                    <div style={{ display: 'flex', gap: 4 }}>
                      <button
                        onClick={() => analyzeMutation.mutate([t.symbol])}
                        style={{ fontSize: 11, padding: '3px 8px' }}
                      >
                        analyze
                      </button>
                      <button
                        onClick={() => removeMutation.mutate(t.symbol)}
                        style={{ background: 'var(--red)', fontSize: 11, padding: '3px 8px' }}
                      >
                        remove
                      </button>
                    </div>
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
