import { useState, useEffect } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { fetchJson, AccountSummary, api } from '../api/client'
import { useWebSocket } from '../api/websocket'
import { formatTime } from '../api/time'

interface ProposalSummary {
  id: number
  account_id: string
  strategy: string
  status: string
  tickers: string[]
  created_at: string
  decided_at: string | null
  superseded_by: number | null
}

interface ProposalDetail {
  id: number
  account_id: string
  strategy: string
  status: string
  merge_report: string
  tickers: string[]
  ticker_data: { ticker: string; decision: string; reasoning: string }[]
  allocation: { symbol: string; action: string; current_pct?: number; target_pct?: number; pct?: number; current_value?: number; target_value?: number; current_qty?: number; price?: number | null }[] | null
  allocation_reasoning: string | null
  cash_pct: number | null
  portfolio_value: number | null
  cash_after: number | null
  proposed_orders: { ticker: string; side: string; qty?: number; notional?: number }[] | null
  superseded_by: number | null
  created_at: string
  decided_at: string | null
  execution_results: { ticker: string; side?: string; order_id?: string; status?: string; error?: string }[] | null
}

export default function Proposals() {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [supersededWarning, setSupersededWarning] = useState<number | null>(null)
  const queryClient = useQueryClient()
  const { lastMessage } = useWebSocket()

  const { data: proposals = [] } = useQuery({
    queryKey: ['proposals'],
    queryFn: () => fetchJson<ProposalSummary[]>('/api/proposals?limit=20'),
  })

  const { data: detail } = useQuery({
    queryKey: ['proposal', selectedId],
    queryFn: () => fetchJson<ProposalDetail>(`/api/proposals/${selectedId}`),
    enabled: !!selectedId,
  })

  useEffect(() => {
    if (lastMessage?.type === 'proposal_created') {
      queryClient.invalidateQueries({ queryKey: ['proposals'] })
      const superseded = (lastMessage.data as any).superseded_ids as number[]
      if (selectedId && superseded?.includes(selectedId)) {
        setSupersededWarning((lastMessage.data as any).proposal_id)
      }
    }
    if (lastMessage?.type === 'proposal_approved') {
      queryClient.invalidateQueries({ queryKey: ['proposals'] })
      queryClient.invalidateQueries({ queryKey: ['proposal', selectedId] })
    }
  }, [lastMessage, queryClient, selectedId])

  const approveMutation = useMutation({
    mutationFn: (id: number) =>
      fetch(`/api/proposals/${id}/approve`, { method: 'POST' }).then(r => {
        if (!r.ok) return r.json().then(d => { throw new Error(d.detail) })
        return r.json()
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['proposals'] })
      queryClient.invalidateQueries({ queryKey: ['proposal', selectedId] })
    },
  })

  const rejectMutation = useMutation({
    mutationFn: (id: number) =>
      fetch(`/api/proposals/${id}/reject`, { method: 'POST' }).then(r => {
        if (!r.ok) return r.json().then(d => { throw new Error(d.detail) })
        return r.json()
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['proposals'] })
      queryClient.invalidateQueries({ queryKey: ['proposal', selectedId] })
    },
  })

  const pending = proposals.filter(p => p.status === 'pending')
  const history = proposals.filter(p => p.status !== 'pending')

  const { data: accounts = [] } = useQuery({
    queryKey: ['accounts'],
    queryFn: api.getAccounts,
  })

  const [mergeChecks, setMergeChecks] = useState(1)
  const [allocChecks, setAllocChecks] = useState(1)
  const [selectedAccount, setSelectedAccount] = useState('')
  const [selectedProvider, setSelectedProvider] = useState('')

  const { data: taskStats } = useQuery({
    queryKey: ['taskStats'],
    queryFn: api.getTaskStats,
  })

  const triggerMutation = useMutation({
    mutationFn: () => {
      const params = new URLSearchParams({ account_id: selectedAccount })
      if (mergeChecks !== 1) params.set('merge_checks', String(mergeChecks))
      if (allocChecks !== 1) params.set('allocation_checks', String(allocChecks))
      if (selectedProvider) params.set('provider', selectedProvider)
      return fetch(`/api/proposals/trigger?${params.toString()}`, { method: 'POST' }).then(async r => {
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed') }
        return r.json()
      })
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      alert(`Merge+Allocate submitted for ${data.tickers_count} tickers`)
    },
  })

  return (
    <div>
      <h1>Trade Proposals</h1>

      <div className="stat-card" style={{ marginBottom: 24, padding: '16px 20px' }}>
        <h3 style={{ fontSize: 12, color: 'var(--accent)', marginBottom: 12, textTransform: 'uppercase' }}>New Merge & Allocation</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 300 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Account</span>
            <select value={selectedAccount} onChange={e => setSelectedAccount(e.target.value)} style={{ width: 160, fontSize: 12 }}>
              <option value="">Select account...</option>
              {accounts.map((a: AccountSummary) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Merge checks</span>
            <select value={mergeChecks} onChange={e => setMergeChecks(Number(e.target.value))} style={{ width: 160, fontSize: 12 }}>
              <option value={0}>0</option>
              <option value={1}>1</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
            </select>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Alloc checks</span>
            <select value={allocChecks} onChange={e => setAllocChecks(Number(e.target.value))} style={{ width: 160, fontSize: 12 }}>
              <option value={0}>0</option>
              <option value={1}>1</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
            </select>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Provider</span>
            <select value={selectedProvider} onChange={e => setSelectedProvider(e.target.value)} style={{ width: 160, fontSize: 12 }}>
              <option value="">Auto (priority)</option>
              {taskStats?.providers?.map(p => (
                <option key={p.name} value={p.name}>{p.name}</option>
              ))}
            </select>
          </div>
          <button
            onClick={() => triggerMutation.mutate()}
            disabled={triggerMutation.isPending || !selectedAccount}
            style={{ marginTop: 4, alignSelf: 'flex-start' }}
          >
            {triggerMutation.isPending ? 'Submitting...' : 'Run'}
          </button>
        </div>
        {triggerMutation.isError && (
          <p style={{ color: 'var(--red)', fontSize: 13, marginTop: 8 }}>
            {(triggerMutation.error as Error).message}
          </p>
        )}
      </div>

      {pending.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <h2 style={{ fontSize: 14, color: 'var(--yellow)', marginBottom: 8 }}>Pending Approval</h2>
          {pending.map(p => (
            <div
              key={p.id}
              className="stat-card"
              style={{ marginBottom: 8, cursor: 'pointer', borderColor: selectedId === p.id ? 'var(--accent)' : undefined }}
              onClick={() => { setSelectedId(p.id); setSupersededWarning(null) }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <strong>{p.account_id}</strong>
                  <span className="badge badge-escalated" style={{ marginLeft: 8 }}>{p.strategy}</span>
                </div>
                <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                  {formatTime(p.created_at)}
                </span>
              </div>
              <div style={{ marginTop: 6, fontSize: 13 }}>{p.tickers.join(', ')}</div>
            </div>
          ))}
        </div>
      )}

      {pending.length === 0 && (
        <p style={{ color: 'var(--text-dim)', marginBottom: 24 }}>No pending proposals. Proposals appear here after merge+allocation completes.</p>
      )}

      {detail && (
        <div className="stat-card" style={{ marginBottom: 24 }}>
          {supersededWarning && (
            <div style={{ background: 'var(--red)', color: '#fff', padding: '8px 12px', borderRadius: 4, marginBottom: 12 }}>
              This plan has been superseded by a newer one.
              <button
                onClick={() => { setSelectedId(supersededWarning); setSupersededWarning(null) }}
                style={{ marginLeft: 8, background: '#fff', color: 'var(--red)' }}
              >
                View new plan
              </button>
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div>
              <strong>{detail.account_id}</strong>
              <span className="badge badge-escalated" style={{ marginLeft: 8 }}>{detail.strategy}</span>
              <span className={`badge badge-${detail.status}`} style={{ marginLeft: 8 }}>{detail.status}</span>
            </div>
            {detail.status === 'pending' && !supersededWarning && (
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  onClick={() => approveMutation.mutate(detail.id)}
                  disabled={approveMutation.isPending}
                  style={{ background: 'var(--green)' }}
                >
                  {approveMutation.isPending ? 'Submitting...' : 'Approve & Execute'}
                </button>
                <button
                  onClick={() => rejectMutation.mutate(detail.id)}
                  disabled={rejectMutation.isPending}
                  style={{ background: 'var(--red)' }}
                >
                  Reject
                </button>
              </div>
            )}
          </div>

          {approveMutation.isError && (
            <div style={{ color: 'var(--red)', marginBottom: 8 }}>
              {(approveMutation.error as Error).message}
            </div>
          )}

          {detail.allocation && detail.allocation.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <h3 style={{ fontSize: 12, color: 'var(--accent)', marginBottom: 6 }}>ALLOCATION PLAN</h3>
              {detail.portfolio_value && (
                <div style={{ display: 'flex', gap: 24, marginBottom: 8, fontSize: 13 }}>
                  <span>Portfolio: <strong>${detail.portfolio_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong></span>
                  {detail.cash_after != null && <span>Cash after: <strong>${detail.cash_after.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong></span>}
                </div>
              )}
              {detail.allocation_reasoning && (
                <p style={{ fontSize: 13, color: 'var(--text-dim)', marginBottom: 8 }}>{detail.allocation_reasoning}</p>
              )}
              <table>
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Action</th>
                    <th>Before %</th>
                    <th>After %</th>
                    <th>Before $</th>
                    <th>After $</th>
                    <th>Price</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.allocation.filter(a => a.symbol.toUpperCase() !== 'CASH').map((a, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 600 }}>{a.symbol}</td>
                      <td className={a.action === 'buy' ? 'positive' : a.action === 'sell' ? 'negative' : ''}>{a.action}</td>
                      <td>{a.current_pct != null ? `${a.current_pct.toFixed(1)}%` : '—'}</td>
                      <td style={{ fontWeight: 600 }}>{(a.target_pct ?? a.pct ?? 0).toFixed(1)}%</td>
                      <td>{a.current_value != null ? `$${a.current_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}</td>
                      <td style={{ fontWeight: 600 }}>{a.target_value != null ? `$${a.target_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}</td>
                      <td>{a.price ? `$${a.price.toFixed(2)}` : '—'}</td>
                    </tr>
                  ))}
                  {detail.cash_pct != null && (
                    <tr style={{ borderTop: '2px solid var(--border)', background: 'var(--border)' }}>
                      <td style={{ fontWeight: 600, fontStyle: 'italic', color: 'var(--yellow)' }}>Cash Reserve</td>
                      <td style={{ color: 'var(--text-dim)' }}>—</td>
                      <td>{detail.portfolio_value && detail.cash_after != null && detail.allocation
                        ? ((detail.portfolio_value - detail.allocation.reduce((s, a) => s + (a.current_value || 0), 0)) / detail.portfolio_value * 100).toFixed(1) + '%'
                        : '—'}</td>
                      <td style={{ fontWeight: 600 }}>{detail.cash_pct.toFixed(1)}%</td>
                      <td>{detail.portfolio_value && detail.allocation
                        ? '$' + (detail.portfolio_value - detail.allocation.reduce((s, a) => s + (a.current_value || 0), 0)).toLocaleString(undefined, { maximumFractionDigits: 0 })
                        : '—'}</td>
                      <td style={{ fontWeight: 600 }}>{detail.cash_after != null ? '$' + detail.cash_after.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'}</td>
                      <td>—</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}

          {detail.proposed_orders && detail.proposed_orders.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <h3 style={{ fontSize: 12, color: 'var(--accent)', marginBottom: 6 }}>ORDERS TO EXECUTE</h3>
              <table>
                <thead>
                  <tr><th>Ticker</th><th>Side</th><th>Qty</th></tr>
                </thead>
                <tbody>
                  {detail.proposed_orders.map((o, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 600 }}>{o.ticker}</td>
                      <td className={o.side === 'buy' ? 'positive' : 'negative'}>{o.side.toUpperCase()}</td>
                      <td>{o.qty ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {detail.execution_results && detail.execution_results.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <h3 style={{ fontSize: 12, color: 'var(--green)', marginBottom: 6 }}>EXECUTION RESULTS</h3>
              <table>
                <thead>
                  <tr><th>Ticker</th><th>Side</th><th>Order ID</th><th>Status</th><th>Error</th></tr>
                </thead>
                <tbody>
                  {detail.execution_results.map((r, i) => (
                    <tr key={i}>
                      <td>{r.ticker}</td>
                      <td>{r.side}</td>
                      <td style={{ fontSize: 11 }}>{r.order_id ?? '—'}</td>
                      <td>{r.status ?? '—'}</td>
                      <td className="negative">{r.error ?? ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {detail.ticker_data.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <h3 style={{ fontSize: 12, color: 'var(--accent)', marginBottom: 6 }}>PER-TICKER DECISIONS</h3>
              <table>
                <thead>
                  <tr><th>Ticker</th><th>Decision</th><th>Reasoning</th></tr>
                </thead>
                <tbody>
                  {detail.ticker_data.map(t => (
                    <tr key={t.ticker}>
                      <td style={{ fontWeight: 600 }}>{t.ticker}</td>
                      <td>{t.decision}</td>
                      <td style={{ fontSize: 12, maxWidth: 400 }}>{t.reasoning.slice(0, 200)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <details style={{ marginTop: 12 }}>
            <summary style={{ cursor: 'pointer', color: 'var(--text-dim)', fontSize: 13 }}>
              Full CIO Merge Report
            </summary>
            <pre style={{ marginTop: 8, whiteSpace: 'pre-wrap', fontSize: 12, lineHeight: 1.5, color: 'var(--text)' }}>
              {detail.merge_report}
            </pre>
          </details>
        </div>
      )}

      {history.length > 0 && (
        <div>
          <h2 style={{ fontSize: 14, color: 'var(--text-dim)', marginBottom: 8 }}>History</h2>
          <table>
            <thead>
              <tr><th>Time</th><th>Account</th><th>Strategy</th><th>Tickers</th><th>Status</th></tr>
            </thead>
            <tbody>
              {history.map(p => (
                <tr key={p.id} onClick={() => setSelectedId(p.id)} style={{ cursor: 'pointer' }}>
                  <td style={{ fontSize: 12 }}>{new Date(p.created_at).toLocaleTimeString()}</td>
                  <td>{p.account_id}</td>
                  <td>{p.strategy}</td>
                  <td>{p.tickers.join(', ')}</td>
                  <td><span className={`badge badge-${p.status}`}>{p.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
