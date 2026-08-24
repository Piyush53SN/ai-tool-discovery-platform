import { createContext, useContext, useState } from 'react'

/**
 * Holds the 2–4 tool ids selected for the compare page (persisted to
 * sessionStorage so a refresh doesn't lose the selection).
 */
const CompareContext = createContext({ compareIds: [], toggleCompare: () => {} })

const KEY = 'aitools.compare'

export function CompareProvider({ children }) {
  const [compareIds, setCompareIds] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem(KEY)) ?? [] } catch { return [] }
  })

  function toggleCompare(id) {
    setCompareIds((prev) => {
      const next = prev.includes(id)
        ? prev.filter((x) => x !== id)
        : prev.length >= 4 ? prev : [...prev, id]
      sessionStorage.setItem(KEY, JSON.stringify(next))
      return next
    })
  }

  return (
    <CompareContext.Provider value={{ compareIds, toggleCompare }}>
      {children}
    </CompareContext.Provider>
  )
}

export const useCompare = () => useContext(CompareContext)
