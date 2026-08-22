import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { useAuth } from '../AuthContext.jsx'
import ToolCard from '../components/ToolCard.jsx'
import Pagination from '../components/Pagination.jsx'

export default function BookmarksPage() {
  const { user, loading: authLoading } = useAuth()
  const [data, setData] = useState({ results: [], count: 0, pages: 1 })
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!user) return
    setLoading(true)
    api(`/bookmarks/?page=${page}`)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [user, page])

  if (authLoading) return null
  if (!user) {
    return (
      <div className="empty-state">
        <h3>Sign in to see bookmarks</h3>
        <p>Bookmarks are the strongest signal for your recommendations.</p>
        <Link className="btn btn-primary" to="/login">Sign in</Link>
      </div>
    )
  }

  return (
      <div>
        <h1>Your bookmarks</h1>
        <p className="muted">
          {data.count} saved · every bookmark writes a weight-3.0 interaction that steers
          your taste vector.
        </p>
        {loading ? (
          <div className="grid">{Array.from({ length: 3 }).map((_, i) => <div key={i} className="tool-card skeleton" />)}</div>
        ) : data.results.length ? (
          <>
            <div className="grid">
              {data.results.map((b) => (
                <ToolCard key={b.id} tool={{ ...b.tool, is_bookmarked: true }} onChange={() => setPage(page)} />
              ))}
            </div>
            <Pagination page={page} pages={data.pages} onPage={setPage} />
          </>
        ) : (
          <div className="empty-state">
            <h3>No bookmarks yet</h3>
            <p>Tap ☆ on any tool card to save it here.</p>
            <Link className="btn btn-primary" to="/">Explore tools</Link>
          </div>
        )}
      </div>
  )
}
