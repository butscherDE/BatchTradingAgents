import { useQuery } from '@tanstack/react-query'
import { api, AccountSummary } from '../api/client'
import { Link } from 'react-router-dom'

export default function Accounts() {
  const { data: accounts = [], isLoading } = useQuery({
    queryKey: ['accounts'],
    queryFn: api.getAccounts,
  })

  if (isLoading) return <p>Loading accounts...</p>

  return (
    <div>
      <h1>Accounts</h1>
      <div className="stat-grid">
        {accounts.map((a: AccountSummary) => (
          <Link to={`/holdings/${a.id}`} key={a.id} style={{ textDecoration: 'none' }}>
            <div className="stat-card">
              <h3>{a.name} {a.is_paper ? '(paper)' : '(live)'}</h3>
              <div className="value">${a.portfolio_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
              <div style={{ marginTop: 8, fontSize: 12 }}>
                <span>Strategy: {a.strategy}</span>
                <span style={{ marginLeft: 12 }}>Watchlist: {a.watchlist}</span>
              </div>
              <div style={{ marginTop: 4, fontSize: 13 }}>
                <span className={a.day_pl >= 0 ? 'positive' : 'negative'}>
                  Day: {a.day_pl >= 0 ? '+' : ''}{a.day_pl.toFixed(2)} ({a.day_pl_pct.toFixed(2)}%)
                </span>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
