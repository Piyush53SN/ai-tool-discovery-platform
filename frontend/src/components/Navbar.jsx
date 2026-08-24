import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../AuthContext.jsx'

const ROUTES = [
  { to: '/', label: 'Explore', exact: true },
  { to: '/recommendations', label: 'For You' },
  { to: '/bookmarks', label: 'Bookmarks' },
  { to: '/compare', label: 'Compare' },
  { to: '/chat', label: 'Model Lab' },
]

export default function Navbar() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  // ---- theme toggle: pre-paint script in index.html resolves the initial
  // value; here we only react to clicks, persist the choice, and flash the
  // scoped .theme-anim cross-fade class for ~320ms.
  const [theme, setTheme] = useState(
    () => document.documentElement.dataset.theme === 'light' ? 'light' : 'dark',
  )
  function toggleTheme() {
    const next = theme === 'light' ? 'dark' : 'light'
    const root = document.documentElement
    root.classList.add('theme-anim')
    root.dataset.theme = next
    try { localStorage.setItem('aitools.theme', next) } catch { /* private mode */ }
    setTheme(next)
    setTimeout(() => root.classList.remove('theme-anim'), 320)
  }

  // ---- sliding active-tab underline: one accent bar, measured and moved
  // between tabs on navigation (transform/width transition in CSS).
  const linkRefs = useRef([])
  const navRef = useRef(null)
  const [indicator, setIndicator] = useState({ width: 0, x: 0, visible: false })

  const activeIndex = ROUTES.findIndex((r) =>
    r.exact ? location.pathname === r.to : location.pathname.startsWith(r.to),
  )

  // Measure the active tab's real bounding box relative to the nav container,
  // which is the indicator's containing block (.nav-links, position: relative)
  // — one shared coordinate frame, so translateX(x) + width land exactly under
  // the active tab regardless of tab order, label length, or viewport width.
  // scrollLeft compensates for the mobile overflow-x container (the indicator
  // scrolls with the tab row, so positions must be in unscrolled content coords).
  const measure = () => {
    const el = activeIndex >= 0 ? linkRefs.current[activeIndex] : null
    const container = navRef.current
    if (el && container) {
      const rect = el.getBoundingClientRect()
      const cRect = container.getBoundingClientRect()
      setIndicator({
        width: rect.width,
        x: rect.left - cRect.left + container.scrollLeft,
        visible: true,
      })
    } else {
      setIndicator((prev) => ({ ...prev, visible: false }))
    }
  }
  useLayoutEffect(measure, [activeIndex])
  useEffect(() => {
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [activeIndex])

  return (
    <header className="navbar">
      <Link to="/" className="brand">
        <span className="brand-mark" aria-hidden>≡</span>
        <span>AI Tool <b>Discovery</b></span>
      </Link>
      <nav className="nav-links" ref={navRef}>
        {ROUTES.map((r, i) => (
          <NavLink
            key={r.to}
            to={r.to}
            end={r.exact}
            ref={(el) => { linkRefs.current[i] = el }}
          >{r.label}</NavLink>
        ))}
        <span
          className="nav-indicator"
          style={{
            width: `${indicator.width}px`,
            transform: `translateX(${indicator.x}px)`,
            opacity: indicator.visible ? 1 : 0,
          }}
          aria-hidden
        />
      </nav>
      <div className="nav-auth">
        <button className="theme-toggle" onClick={toggleTheme} aria-label="Toggle light/dark theme" title="Toggle light/dark theme">
          <svg className="icon-sun" aria-hidden><use href="/icons.svg#sun-icon" /></svg>
          <svg className="icon-moon" aria-hidden><use href="/icons.svg#moon-icon" /></svg>
        </button>
        {user ? (
          <>
            <span className="user-chip" title={user.email || user.username}>
              @{user.username}
            </span>
            <button
              className="btn btn-ghost"
              onClick={() => { logout(); navigate('/') }}
            >
              Sign out
            </button>
          </>
        ) : (
          <>
            <Link className="btn btn-ghost" to="/login">Sign in</Link>
            <Link className="btn btn-primary" to="/register">Get started</Link>
          </>
        )}
      </div>
    </header>
  )
}
