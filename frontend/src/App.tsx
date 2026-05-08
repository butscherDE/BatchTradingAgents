import { Routes, Route, NavLink } from 'react-router-dom'
import NewsFeed from './pages/NewsFeed'
import Accounts from './pages/Accounts'
import Holdings from './pages/Holdings'
import Tasks from './pages/Tasks'
import Watchlist from './pages/Watchlist'
import { WebSocketProvider } from './api/websocket'

export default function App() {
  return (
    <WebSocketProvider>
      <div className="app">
        <nav className="sidebar">
          <h2>TradingAgents</h2>
          <NavLink to="/">News</NavLink>
          <NavLink to="/watchlist">Watchlist</NavLink>
          <NavLink to="/accounts">Accounts</NavLink>
          <NavLink to="/holdings">Holdings</NavLink>
          <NavLink to="/tasks">Tasks</NavLink>
        </nav>
        <main className="content">
          <Routes>
            <Route path="/" element={<NewsFeed />} />
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
