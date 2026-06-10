// Squad/formation display helpers extracted from App.tsx

export const getPositionClass = (pos: string) => {
  const classes: Record<string, string> = {
    'GK': 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    'DEF': 'bg-green-500/20 text-green-400 border-green-500/30',
    'MID': 'bg-blue-500/20 text-blue-400 border-blue-500/30',
    'FWD': 'bg-red-500/20 text-red-400 border-red-500/30'
  }
  return classes[pos] || 'bg-gray-500/20 text-gray-400'
}

// Parse formation string (e.g., "3-5-2" -> {def: 3, mid: 5, fwd: 2})
export const parseFormation = (formation: string) => {
  const parts = formation.split('-').map(Number)
  return {
    def: parts[0] || 0,
    mid: parts[1] || 0,
    fwd: parts[2] || 0,
    gk: 1
  }
}
