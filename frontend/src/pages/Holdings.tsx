import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, Holding, AccountSummary } from '../api/client'

export default function Holdings() {
  const { accountId } = useParams()

  const { data: accounts = [] } = useQuery({
    queryKey: ['accounts'],
    queryFn: api.getAccounts,
    enabled: !accountId,
  })

  const selectedId = accountId || accounts[0]?.id

  const { data, isLoading } = useQuery({
    queryKey: ['holdings', selectedId],
    queryFn: () => api.getHoldings(selectedId!),
    enabled: !!selectedId,
  })

  if (!selectedId) return <p>No accounts configured</p>
  if (isLoading) return <p>Loading holdings...</p>
  if (!data) return <p>No data</p>

  const { account, holdings } = data

  return (
    <div>
      <h1>Holdings — {account.name}</h1>
      <div className="stat-grid">
        <div className="stat-card">
          <h3>Portfolio Value</h3>
          <div className="value">${account.portfolio_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
        </div>
        <div className="stat-card">
          <h3>Cash</h3>
          <div className="value">${account.cash.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
        </div>
        <div className="stat-card">
          <h3>Day P/L</h3>
          <div className={`value ${account.day_pl >= 0 ? 'positive' : 'negative'}`}>
            {account.day_pl >= 0 ? '+' : ''}${account.day_pl.toFixed(2)}
          </div>
        </div>
        <div className="stat-card">
          <h3>Day P/L %</h3>
          <div className={`value ${account.day_pl_pct >= 0 ? 'positive' : 'negative'}`}>
            {account.day_pl_pct >= 0 ? '+' : ''}{account.day_pl_pct.toFixed(2)}%
          </div>
        </div>
      </div>

      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Qty</th>
            <th>Avg Entry</th>
            <th>Current</th>
            <th>Total P/L</th>
            <th>Total %</th>
            <th>Day P/L</th>
            <th>Day %</th>
            <th>Value</th>
            <th>% Portfolio</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((h: Holding) => (
            <tr key={h.symbol}>
              <td style={{ fontWeight: 600 }}>{h.symbol}</td>
              <td>{h.qty}</td>
              <td>${h.avg_entry_price.toFixed(2)}</td>
              <td>${h.current_price.toFixed(2)}</td>
              <td className={h.total_pl >= 0 ? 'positive' : 'negative'}>
                {h.total_pl >= 0 ? '+' : ''}${h.total_pl.toFixed(2)}
              </td>
              <td className={h.total_pl_pct >= 0 ? 'positive' : 'negative'}>
                {h.total_pl_pct >= 0 ? '+' : ''}{h.total_pl_pct.toFixed(2)}%
              </td>
              <td className={h.day_pl >= 0 ? 'positive' : 'negative'}>
                {h.day_pl >= 0 ? '+' : ''}${h.day_pl.toFixed(2)}
              </td>
              <td className={h.day_pl_pct >= 0 ? 'positive' : 'negative'}>
                {h.day_pl_pct >= 0 ? '+' : ''}{h.day_pl_pct.toFixed(2)}%
              </td>
              <td>${h.market_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
              <td>{h.portfolio_pct.toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
