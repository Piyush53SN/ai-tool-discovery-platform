import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../AuthContext.jsx'

export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      await login(username, password)
      navigate('/recommendations')
    } catch (err) {
      setError(err.status === 401 ? 'Wrong username or password.' : 'Sign-in failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <h1>Welcome back</h1>
        <p className="muted">Sign in to sync bookmarks, reviews and your taste vector.</p>
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="btn btn-primary btn-block" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <p className="muted demo-hint">
          Demo account: <code>demo</code> / <code>demo-pass-123</code>
        </p>
        <p className="muted">New here? <Link to="/register">Create an account</Link></p>
      </form>
    </div>
  )
}
