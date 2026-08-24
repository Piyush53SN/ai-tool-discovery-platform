import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { useAuth } from '../AuthContext.jsx'
import ToolCard from '../components/ToolCard.jsx'
import StarRating from '../components/StarRating.jsx'

export default function ToolDetailPage() {
  const { slug } = useParams()
  const { user } = useAuth()
  const navigate = useNavigate()
  const [tool, setTool] = useState(null)
  const [reviews, setReviews] = useState([])
  const [rating, setRating] = useState(5)
  const [comment, setComment] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setTool(null); setError('')
    api(`/tools/${slug}/`).then(setTool).catch(() => setError('Tool not found'))
  }, [slug])

  useEffect(() => {
    if (tool) {
      api(`/reviews/?tool=${tool.slug}`).then(setReviews).catch(() => {})
      // fire-and-forget view signal (feeds the recommender when signed in)
      api(`/tools/${tool.slug}/view/`, { method: 'POST', body: {} }).catch(() => {})
    }
  }, [tool])

  async function submitReview(e) {
    e.preventDefault()
    setSubmitting(true)
    try {
      await api('/reviews/', { method: 'POST', body: { tool_id: tool.id, rating, comment } })
      const [t, r] = await Promise.all([api(`/tools/${slug}/`), api(`/reviews/?tool=${slug}`)])
      setTool(t); setReviews(r); setComment('')
    } catch (err) {
      setError(err.payload?.detail || 'Could not submit review')
    } finally {
      setSubmitting(false)
    }
  }

  async function toggleBookmark() {
    if (!user) { navigate('/login'); return }
    if (tool.is_bookmarked) {
      const list = await api('/bookmarks/')
      const mine = list.results?.find((b) => b.tool.id === tool.id)
      if (mine) await api(`/bookmarks/${mine.id}/`, { method: 'DELETE' })
    } else {
      await api('/bookmarks/', { method: 'POST', body: { tool_id: tool.id } })
    }
    setTool(await api(`/tools/${slug}/`))
  }

  if (error) return <div className="empty-state"><h3>{error}</h3><Link to="/" className="btn btn-primary">Back to explore</Link></div>
  if (!tool) return <div className="skeleton detail-skeleton" />

  return (
    <div className="detail">
      <div className="detail-header">
        <div>
          <div className="detail-breadcrumb">
            <Link to="/">Explore</Link> / <Link to={`/?category=${tool.category.slug}`}>{tool.category.name}</Link>
          </div>
          <h1>{tool.name}</h1>
          <div className="detail-meta">
            <span className={`pill pricing-${tool.pricing_tier}`}>{tool.pricing_display}</span>
            <StarRating value={Number(tool.avg_rating)} count={tool.rating_count} />
            <span className="muted">· {tool.bookmark_count} bookmarks</span>
            {!tool.embedding_ready && <span className="muted">· embedding pending</span>}
          </div>
        </div>
        <div className="detail-actions">
          <a className="btn btn-ghost" href={tool.url} target="_blank" rel="noreferrer">Visit website ↗</a>
          <button className={`btn ${tool.is_bookmarked ? 'btn-secondary' : 'btn-primary'}`} onClick={toggleBookmark}>
            {tool.is_bookmarked ? '★ Bookmarked' : '☆ Bookmark'}
          </button>
        </div>
      </div>

      <p className="detail-desc">{tool.description}</p>

      <div className="detail-tags">
        {tool.tags.map((t) => <span key={t.slug} className="tag">#{t.name}</span>)}
      </div>

      <div className="link-status mono">
        <span>URL</span>
        <span className={tool.is_live ? "ok" : "down"}>{tool.is_live ? "LINK OK" : "LINK DOWN"}</span>
        {tool.http_status && <span>HTTP {tool.http_status}</span>}
        <span>
          checked {tool.last_checked_at ? new Date(tool.last_checked_at).toLocaleString() : "never"}
        </span>
        <a href={tool.url} target="_blank" rel="noreferrer">{tool.url}</a>
      </div>

      <section className="detail-section">
        <h2>Reviews</h2>
        {user ? (
          <form className="review-form" onSubmit={submitReview}>
            <label>
              Your rating
              <select value={rating} onChange={(e) => setRating(Number(e.target.value))}>
                {[5, 4, 3, 2, 1].map((r) => <option key={r} value={r}>{'★'.repeat(r)} {r}/5</option>)}
              </select>
            </label>
            <textarea
              placeholder="What did you think? (optional)"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              maxLength={4000}
            />
            <button className="btn btn-primary" disabled={submitting}>
              {submitting ? 'Submitting…' : 'Submit review'}
            </button>
          </form>
        ) : (
          <p className="muted"> <Link to="/login">Sign in</Link> to review — reviews (especially 4★+) strongly shape your recommendations.</p>
        )}

        {reviews.results?.length ? (
          <ul className="review-list">
            {reviews.results.map((r) => (
              <li key={r.id} className="review">
                <div className="review-head">
                  <b>@{r.username}</b>
                  <StarRating value={r.rating} />
                </div>
                {r.comment && <p>{r.comment}</p>}
                <time>{new Date(r.created_at).toLocaleDateString()}</time>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No reviews yet.</p>
        )}
      </section>

      {tool.similar_tools?.length > 0 && (
        <section className="detail-section">
          <h2>Semantically similar tools</h2>
          <p className="muted">Nearest neighbours by embedding cosine distance, computed in Postgres with pgvector.</p>
          <div className="grid">
            {tool.similar_tools.map((t) => <ToolCard key={t.id} tool={t} />)}
          </div>
        </section>
      )}
    </div>
  )
}
