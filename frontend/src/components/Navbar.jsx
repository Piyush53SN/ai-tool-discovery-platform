import { Link, NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../AuthContext.jsx'

export default function Navbar() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  return (
    <header className="navbar">
      <Link to="/" className="brand">
        <span className="brand-orbit" aria-hidden />
        <span>AI Tool <b>Discovery</b></span>
      </Link>
      <nav className="nav-links">
        <NavLink to="/" end>Explore</NavLink>
        <NavLink to="/recommendations">For You</NavLink>
        <NavLink to="/bookmarks">Bookmarks</NavLink>
        <NavLink to="/compare">Compare</NavLink>
      </nav>
      <div className="nav-auth">
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
