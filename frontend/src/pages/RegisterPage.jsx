import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useAuth } from '../AuthContext.jsx'

export default function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ username: '', email: '', password: '' })
  const [tags, setTags] = useState([])
  const [picked, setPicked] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { api('/tags/').then(setTags).catch(() => {}) }, [])

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      await register({ ...form, onboarding_tags: picked })
      navigate('/recommendations')
    } catch (err) {
      const p = err.payload || {}
      setError(
        Object.entries(p).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join(' · ')
          || 'Registration failed.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <h1>Create your account</h1>
        <p className="muted">Bookmarks and reviews train a 384-dim taste vector — recommendations get sharper with every interaction.</p>
        <label>
          Username
          <input value={form.username} onChange={set('username')} autoFocus required />
        </label>
        <label>
          Email <span className="muted">(optional)</span>
          <input type="email" value={form.email} onChange={set('email')} />
        </label>
        <label>
          Password <span className="muted">(8+ characters)</span>
          <input type="password" value={form.password} onChange={set('password')} required />
        </label>

        <div className="onboarding">
          <h3>Pick a few interests <span className="muted">(optional — beats the cold start)</span></h3>
          <div className="facet-tags">
            {tags.slice(0, 30).map((t) => (
              <button
                type="button"
                key={t.slug}
                className={`chip ${picked.includes(t.slug) ? 'chip-on' : ''}`}
                onClick={() => setPicked((p) => p.includes(t.slug) ? p.filter((x) => x !== t.slug) : [...p, t.slug])}
              >#{t.name}</button>
            ))}
          </div>
        </div>

        {error && <p className="error">{error}</p>}
        <button className="btn btn-primary btn-block" disabled={busy}>
          {busy ? 'Creating…' : 'Create account'}
        </button>
        <p className="muted">Already registered? <Link to="/login">Sign in</Link></p>
      </form>
    </div>
  )
}
