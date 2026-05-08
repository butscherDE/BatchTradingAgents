import { useState, useEffect, useRef } from 'react'
import { fetchJson } from '../api/client'

interface SearchResult {
  symbol: string
  name: string
  price: number | null
  day_change: number | null
  day_change_pct: number | null
}

interface Props {
  onSelect: (symbol: string) => void
  disabled?: boolean
}

export default function TickerSearch({ onSelect, disabled }: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!query || query.length < 1) {
      setResults([])
      setOpen(false)
      return
    }

    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const data = await fetchJson<SearchResult[]>(`/api/watchlist/search?q=${encodeURIComponent(query)}`)
        setResults(data)
        setOpen(data.length > 0)
      } catch {
        setResults([])
      }
      setLoading(false)
    }, 300)

    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [query])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  function handleSelect(symbol: string) {
    onSelect(symbol)
    setQuery('')
    setResults([])
    setOpen(false)
  }

  return (
    <div className="ticker-search" ref={containerRef}>
      <input
        type="text"
        placeholder="Search ticker or company..."
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => { if (results.length > 0) setOpen(true) }}
        disabled={disabled}
        style={{ width: 220 }}
      />
      {loading && <span className="search-spinner">...</span>}

      {open && results.length > 0 && (
        <div className="search-dropdown">
          {results.map(r => (
            <div key={r.symbol} className="search-result" onClick={() => handleSelect(r.symbol)}>
              <div className="search-result-left">
                <span className="search-symbol">{r.symbol}</span>
                {r.day_change != null && (
                  <span className={r.day_change >= 0 ? 'positive' : 'negative'} style={{ fontSize: 12 }}>
                    {r.day_change >= 0 ? '+' : ''}${r.day_change.toFixed(2)}({r.day_change_pct!.toFixed(2)}%)
                  </span>
                )}
              </div>
              <div className="search-result-right">
                {r.price != null && <span className="search-price">${r.price.toFixed(2)}</span>}
              </div>
              <div className="search-result-name">{r.name}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
