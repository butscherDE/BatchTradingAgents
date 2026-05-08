import { Routes, Route, NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import NewsFeed from './pages/NewsFeed'
import Accounts from './pages/Accounts'
import Holdings from './pages/Holdings'
import Tasks from './pages/Tasks'
import Watchlist from './pages/Watchlist'
import Proposals from './pages/Proposals'
import { WebSocketProvider, useWebSocket } from './api/websocket'
import { fetchJson } from './api/client'
import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'

function Sidebar() {
  const queryClient = useQueryClient()
  const { lastMessage } = useWebSocket()

  const { data: proposals = [] } = useQuery({
    queryKey: ['pendingProposals'],
    queryFn: () => fetchJson<{ id: number }[]>('/api/proposals?status=pending'),
  })

  useEffect(() => {
    if (lastMessage?.type === 'proposal_created' || lastMessage?.type === 'proposal_approved') {
      queryClient.invalidateQueries({ queryKey: ['pendingProposals'] })
    }
  }, [lastMessage, queryClient])

  const pendingCount = proposals.length

  return (
    <nav className="sidebar">
      <h2>TradingAgents</h2>
      <NavLink to="/">News</NavLink>
      <NavLink to="/proposals" className="nav-with-badge">
        Trades
        {pendingCount > 0 && <span className="nav-badge">{pendingCount}</span>}
      </NavLink>
      <NavLink to="/watchlist">Watchlist</NavLink>
      <NavLink to="/accounts">Accounts</NavLink>
      <NavLink to="/holdings">Holdings</NavLink>
      <NavLink to="/tasks">Tasks</NavLink>
    </nav>
  )
}

export default function App() {
  return (
    <WebSocketProvider>
      <div className="app">
        <Sidebar />
        <main className="content">
          <Routes>
            <Route path="/" element={<NewsFeed />} />
            <Route path="/proposals" element={<Proposals />} />
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
