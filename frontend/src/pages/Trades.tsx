import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api, Trade } from '../api/client'
import { formatDateTime } from '../api/time'
import { useWebSocket } from '../api/websocket'

const TERMINAL = new Set([
  'filled', 'canceled', 'cancelled', 'expired', 'rejected',
  'done_for_day', 'replaced', 'stopped', 'suspended',
])

function statusClass(status: string): string {
  const s = status.toLowerCase()
  if (s === 'filled') return 'positive'
  if (s === 'rejected' || s === 'canceled' || s === 'cancelled' || s === 'expired' || s === 'failed') return 'negative'
  return ''
}

function actionClass(action: string): string {
  const a = action.toLowerCase()
  if (a === 'buy') return 'positive'
  if (a.startsWith('sell')) return 'negative'
  return ''
}

function fmtNum(n: number | null, digits = 2): string {
  if (n == null) return '—'
  return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })
}

function fmtMoney(n: number | null): string {
  if (n == null) return '—'
  return '$' + n.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

export default function Trades() {
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const { lastMessage } = useWebSocket()

  const accountFilter = searchParams.get('account_id') || ''
  const tickerFilter = searchParams.get('ticker') || ''
  const statusFilter = searchParams.get('status') || ''
  const triggerFilter = searchParams.get('trigger') || ''

  const setFilter = (key: string, value: string) => {
    const params = new URLSearchParams(searchParams)
    if (value) params.set(key, value); else params.delete(key)
    setSearchParams(params, { replace: true })
  }

  const { data: accounts = [] } = useQuery({
    queryKey: ['accounts'],
    queryFn: api.getAccounts,
  })

  const { data: trades = [], isLoading } = useQuery({
    queryKey: ['trades', accountFilter, tickerFilter, statusFilter, triggerFilter],
    queryFn: () => api.getTrades({
      account_id: accountFilter || undefined,
      ticker: tickerFilter || undefined,
      status: statusFilter || undefined,
      trigger: triggerFilter || undefined,
      limit: '200',
    }),
    refetchInterval: 30000,
  })

  useEffect(() => {
    if (lastMessage?.type === 'proposal_approved') {
      queryClient.invalidateQueries({ queryKey: ['trades'] })
    }
  }, [lastMessage, queryClient])

  const inFlight = trades.filter(t => !TERMINAL.has(t.status.toLowerCase()))

  return (
    <div>
      <h1>Trades</h1>

      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <select value={accountFilter} onChange={e => setFilter('account_id', e.target.value)}>
          <option value="">All accounts</option>
          {accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>

        <input
          type="text"
          placeholder="Ticker"
          value={tickerFilter}
          onChange={e => setFilter('ticker', e.target.value.toUpperCase())}
          style={{ width: 100 }}
        />

        <select value={statusFilter} onChange={e => setFilter('status', e.target.value)}>
          <option value="">All statuses</option>
          <option value="submitted">submitted</option>
          <option value="accepted">accepted</option>
          <option value="new">new</option>
          <option value="partially_filled">partially_filled</option>
          <option value="filled">filled</option>
          <option value="canceled">canceled</option>
          <option value="rejected">rejected</option>
          <option value="failed">failed</option>
        </select>

        <select value={triggerFilter} onChange={e => setFilter('trigger', e.target.value)}>
          <option value="">All triggers</option>
          <option value="proposal">proposal</option>
          <option value="emergency_sell">emergency_sell</option>
          <option value="manual">manual</option>
        </select>

        <div style={{ marginLeft: 'auto', color: 'var(--text-dim)', fontSize: 13, alignSelf: 'center' }}>
          {trades.length} trade{trades.length === 1 ? '' : 's'}
          {inFlight.length > 0 && ` · ${inFlight.length} in-flight`}
        </div>
      </div>

      {isLoading && <p>Loading trades…</p>}
      {!isLoading && trades.length === 0 && (
        <p style={{ color: 'var(--text-dim)' }}>No trades match these filters.</p>
      )}

      {trades.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Submitted</th>
              <th>Account</th>
              <th>Ticker</th>
              <th>Action</th>
              <th>Qty</th>
              <th>Notional</th>
              <th>Status</th>
              <th>Filled Qty</th>
              <th>Filled Price</th>
              <th>Filled At</th>
              <th>Trigger</th>
              <th>Order ID</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t: Trade) => (
              <tr key={t.id} title={t.error || undefined}>
                <td style={{ whiteSpace: 'nowrap' }}>{formatDateTime(t.submitted_at)}</td>
                <td>{t.account_id}</td>
                <td style={{ fontWeight: 600 }}>{t.ticker}</td>
                <td className={actionClass(t.action)}>{t.action}</td>
                <td>{fmtNum(t.qty, 0)}</td>
                <td>{fmtMoney(t.notional)}</td>
                <td className={statusClass(t.status)}>{t.status}</td>
                <td>{fmtNum(t.filled_qty, 0)}</td>
                <td>{fmtMoney(t.filled_avg_price)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{t.filled_at ? formatDateTime(t.filled_at) : '—'}</td>
                <td>{t.trigger}</td>
                <td style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-dim)' }}>
                  {t.order_id ? t.order_id.slice(0, 8) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
