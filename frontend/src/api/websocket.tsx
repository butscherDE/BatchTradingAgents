import { createContext, useContext, useEffect, useRef, useState, ReactNode } from 'react'

interface WsMessage {
  type: string
  data: Record<string, unknown>
}

interface WsContextValue {
  lastMessage: WsMessage | null
  connected: boolean
}

const WsContext = createContext<WsContextValue>({ lastMessage: null, connected: false })

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [lastMessage, setLastMessage] = useState<WsMessage | null>(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    function connect() {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${proto}//${window.location.host}/ws`)
      wsRef.current = ws

      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        reconnectRef.current = setTimeout(connect, 3000)
      }
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as WsMessage
          setLastMessage(msg)
        } catch {}
      }
    }

    connect()
    return () => {
      wsRef.current?.close()
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
    }
  }, [])

  return (
    <WsContext.Provider value={{ lastMessage, connected }}>
      {children}
    </WsContext.Provider>
  )
}

export function useWebSocket() {
  return useContext(WsContext)
}
