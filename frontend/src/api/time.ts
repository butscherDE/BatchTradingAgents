export function parseUtc(timestamp: string): Date {
  if (!timestamp) return new Date()
  if (timestamp.endsWith('Z') || timestamp.includes('+')) return new Date(timestamp)
  return new Date(timestamp + 'Z')
}

export function formatTime(timestamp: string): string {
  return parseUtc(timestamp).toLocaleTimeString()
}

export function formatDateTime(timestamp: string): string {
  return parseUtc(timestamp).toLocaleString()
}
