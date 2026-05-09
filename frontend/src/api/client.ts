export async function fetchJson<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => { if (v) url.searchParams.set(k, v) })
  }
  const res = await fetch(url.toString())
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export interface NewsArticle {
  id: number
  alpaca_id: string | null
  headline: string
  summary: string | null
  source: string | null
  symbols: string[]
  published_at: string | null
  received_at: string
  status: string
  quick_result: Record<string, unknown> | null
  deep_result: Record<string, unknown> | null
  escalation_reason: string | null
}

export interface TaskItem {
  id: number
  task_id: string
  model_tier: string
  task_type: string
  ticker: string | null
  priority: number
  status: string
  created_at: string
  started_at: string | null
  completed_at: string | null
  error: string | null
}

export interface TaskStats {
  queue_depth_quick: number
  queue_depth_deep: number
  total_completed: number
  total_failed: number
  current_model: string | null
  worker_state: string | null
  model_switches: number
  tasks_per_minute: number
}

export interface AccountSummary {
  id: string
  name: string
  is_paper: boolean
  strategy: string
  watchlist: string
  portfolio_value: number
  cash: number
  day_pl: number
  day_pl_pct: number
}

export interface Holding {
  symbol: string
  qty: number
  avg_entry_price: number
  current_price: number
  total_pl: number
  total_pl_pct: number
  day_pl: number
  day_pl_pct: number
  market_value: number
  portfolio_pct: number
}

export interface AccountHoldings {
  account: AccountSummary
  holdings: Holding[]
}

export interface HealthResponse {
  status: string
  worker_state: string | null
  queue_depths: Record<string, number>
  uptime_seconds: number
}

export interface NewsSourceStatus {
  alpaca: {
    status: string
    last_message_at: string | null
    error: string | null
  }
  yfinance: {
    status: string
    last_poll_at: string | null
    last_error: string | null
    consecutive_failures: number
    tickers_total: number
    articles_found: number
  }
}

export const api = {
  getNews: (params?: { limit?: string; status?: string; symbol?: string }) =>
    fetchJson<NewsArticle[]>('/api/news', params),

  getNewsState: (id: number) =>
    fetchJson<Record<string, unknown>>(`/api/news/${id}/state`),

  getTasks: (params?: { limit?: string; status?: string; model_tier?: string }) =>
    fetchJson<TaskItem[]>('/api/tasks', params),

  getTaskStats: () =>
    fetchJson<TaskStats>('/api/tasks/stats'),

  getAccounts: () =>
    fetchJson<AccountSummary[]>('/api/accounts'),

  getHoldings: (accountId: string) =>
    fetchJson<AccountHoldings>(`/api/accounts/${accountId}/holdings`),

  getHealth: () =>
    fetchJson<HealthResponse>('/api/health'),

  getNewsSourceStatus: () =>
    fetchJson<NewsSourceStatus>('/api/status/news-sources'),
}
