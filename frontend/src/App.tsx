import { Routes, Route, NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import NewsFeed from './pages/NewsFeed'
import Accounts from './pages/Accounts'
import Holdings from './pages/Holdings'
import Tasks from './pages/Tasks'
import Watchlist from './pages/Watchlist'
import Proposals from './pages/Proposals'
import Trades from './pages/Trades'
import Login from './pages/Login'
import { WebSocketProvider, useWebSocket } from './api/websocket'
import { fetchJson, NewsSourceStatus } from './api/client'
import { useState, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'

function Sidebar() {
  const queryClient = useQueryClient()
  const { lastMessage } = useWebSocket()

  const { data: proposals = [] } = useQuery({
    queryKey: ['pendingProposals'],
    queryFn: () => fetchJson<{ id: number }[]>('/api/proposals?status=pending'),
  })

  const { data: taskStats } = useQuery({
    queryKey: ['taskStats'],
    queryFn: () => fetchJson<{ worker_state: string | null }>('/api/tasks/stats'),
    refetchInterval: 5000,
  })

  const { data: newsSources } = useQuery({
    queryKey: ['newsSourceStatus'],
    queryFn: () => fetchJson<NewsSourceStatus>('/api/status/news-sources'),
    refetchInterval: 10000,
  })

  useEffect(() => {
    if (lastMessage?.type === 'proposal_created' || lastMessage?.type === 'proposal_approved') {
      queryClient.invalidateQueries({ queryKey: ['pendingProposals'] })
    }
    if (lastMessage?.type === 'task_update') {
      queryClient.invalidateQueries({ queryKey: ['taskStats'] })
    }
  }, [lastMessage, queryClient])

  const pendingCount = proposals.length
  const workerState = taskStats?.worker_state

  return (
    <nav className="sidebar">
      <h2>TradingAgents</h2>
      <NavLink to="/">News</NavLink>
      <NavLink to="/proposals" className="nav-with-badge">
        Proposals
        {pendingCount > 0 && <span className="nav-badge">{pendingCount}</span>}
      </NavLink>
      <NavLink to="/trades">Trades</NavLink>
      <NavLink to="/watchlist">Watchlist</NavLink>
      <NavLink to="/accounts">Accounts</NavLink>
      <NavLink to="/holdings">Holdings</NavLink>
      <NavLink to="/tasks" className="nav-with-badge">
        Tasks
        {workerState && (
          <span className={`nav-worker-state ${workerState === 'paused' || workerState === 'pausing' ? 'state-paused' : workerState === 'executing' ? 'state-running' : 'state-idle'}`}>
            {workerState === 'paused' ? '⏸' : workerState === 'pausing' ? '⏸' : workerState === 'executing' ? '⚡' : '●'}
          </span>
        )}
      </NavLink>
      {newsSources && (
        <div className="news-sources-status">
          <div className="news-source-row">
            <span className={`source-dot ${newsSources.alpaca.status === 'connected' ? 'dot-ok' : 'dot-err'}`} />
            <span className="source-label">Alpaca</span>
          </div>
          <div className="news-source-row">
            <span className={`source-dot ${newsSources.yfinance.status === 'running' ? 'dot-ok' : newsSources.yfinance.status === 'backing_off' ? 'dot-warn' : 'dot-err'}`} />
            <span className="source-label">yfinance</span>
            {newsSources.yfinance.consecutive_failures > 0 && (
              <span className="source-failures">{newsSources.yfinance.consecutive_failures}</span>
            )}
          </div>
        </div>
      )}
    </nav>
  )
}

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)

  useEffect(() => {
    fetch('/api/auth/check')
      .then(r => setAuthenticated(r.ok))
      .catch(() => setAuthenticated(true))  // If auth not configured (no middleware), allow through
  }, [])

  if (authenticated === null) return null  // Loading
  if (!authenticated) return <Login onLogin={() => setAuthenticated(true)} />

  return (
    <WebSocketProvider>
      <div className="app">
        <Sidebar />
        <main className="content">
          <Routes>
            <Route path="/" element={<NewsFeed />} />
            <Route path="/proposals" element={<Proposals />} />
            <Route path="/trades" element={<Trades />} />
            <Route path="/watchlist" element={<Watchlist />} />
            <Route path="/accounts" element={<Accounts />} />
            <Route path="/holdings" element={<Holdings />} />
            <Route path="/holdings/:accountId" element={<Holdings />} />
            <Route path="/tasks" element={<Tasks />} />
          </Routes>
        </main>
      </div>
    </WebSocketProvider>
  )
}
