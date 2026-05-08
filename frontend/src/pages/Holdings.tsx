import { useQuery } from '@tanstack/react-query'
import { useParams, useNavigate } from 'react-router-dom'
import { api, Holding, AccountSummary } from '../api/client'

export default function Holdings() {
  const { accountId } = useParams()
  const navigate = useNavigate()

  const { data: accounts = [] } = useQuery({
    queryKey: ['accounts'],
    queryFn: api.getAccounts,
  })

  const selectedId = accountId || accounts[0]?.id

  const { data, isLoading } = useQuery({
    queryKey: ['holdings', selectedId],
    queryFn: () => api.getHoldings(selectedId!),
    enabled: !!selectedId,
  })

  return (
    <div>
      <h1>Holdings</h1>

      {accounts.length > 0 && (
        <div className="stat-grid" style={{ marginBottom: 20 }}>
          {accounts.map((a: AccountSummary) => (
            <div
              key={a.id}
              className="stat-card"
              style={{
                cursor: 'pointer',
                borderColor: selectedId === a.id ? 'var(--accent)' : undefined,
                borderWidth: selectedId === a.id ? 2 : 1,
              }}
              onClick={() => navigate(`/holdings/${a.id}`)}
            >
              <h3>{a.name} {a.is_paper ? '(paper)' : ''}</h3>
              <div className="value">${a.portfolio_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
              <div style={{ marginTop: 6, fontSize: 13 }}>
                <span className={a.day_pl >= 0 ? 'positive' : 'negative'}>
                  {a.day_pl >= 0 ? '+' : ''}${a.day_pl.toFixed(2)} ({a.day_pl_pct >= 0 ? '+' : ''}{a.day_pl_pct.toFixed(2)}%)
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {!selectedId && <p style={{ color: 'var(--text-dim)' }}>No accounts configured</p>}
      {selectedId && isLoading && <p>Loading holdings...</p>}

      {data && (
        <>
          <div className="stat-grid" style={{ marginBottom: 16 }}>
            <div className="stat-card">
              <h3>Portfolio Value</h3>
              <div className="value">${data.account.portfolio_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
            </div>
            <div className="stat-card">
              <h3>Cash</h3>
              <div className="value">${data.account.cash.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
            </div>
            <div className="stat-card">
              <h3>Day P/L</h3>
              <div className={`value ${data.account.day_pl >= 0 ? 'positive' : 'negative'}`}>
                {data.account.day_pl >= 0 ? '+' : ''}${data.account.day_pl.toFixed(2)}
              </div>
            </div>
            <div className="stat-card">
              <h3>Day P/L %</h3>
              <div className={`value ${data.account.day_pl_pct >= 0 ? 'positive' : 'negative'}`}>
                {data.account.day_pl_pct >= 0 ? '+' : ''}{data.account.day_pl_pct.toFixed(2)}%
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
              {data.holdings.map((h: Holding) => (
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
        </>
      )}
    </div>
  )
}
