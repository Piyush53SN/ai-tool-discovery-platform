import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { api, tokens } from './api.js'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  const reload = useCallback(async () => {
    if (!tokens.access) { setUser(null); setLoading(false); return }
    try {
      setUser(await api('/auth/me/'))
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  const login = async (username, password) => {
    const data = await api('/auth/token/', { method: 'POST', body: { username, password } })
    tokens.set({ access: data.tokens?.access ?? data.access, refresh: data.tokens?.refresh ?? data.refresh })
    await reload()
  }

  const register = async (payload) => {
    const data = await api('/auth/register/', { method: 'POST', body: payload })
    tokens.set(data.tokens)
    await reload()
  }

  const logout = () => {
    tokens.clear()
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, reload }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
