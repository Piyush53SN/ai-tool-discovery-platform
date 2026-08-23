import { Link } from 'react-router-dom'
import { useState } from 'react'
import { api } from '../api.js'
import { useAuth } from '../AuthContext.jsx'
import StarRating from './StarRating.jsx'
import { useCompare } from './CompareContext.jsx'

const PRICING_CLASS = { free: 'pill-free', freemium: 'pill-freemium', paid: 'pill-paid' }

export default function ToolCard({ tool, onChange, className = '', style }) {
  const { user } = useAuth()
  const { compareIds, toggleCompare } = useCompare()
  const [busy, setBusy] = useState(false)
  const [bookmarked, setBookmarked] = useState(tool.is_bookmarked)
  const inCompare = compareIds.includes(tool.id)

  async function toggleBookmark(e) {
    e.preventDefault()
    if (!user || busy) return
    setBusy(true)
    try {
      if (bookmarked) {
        const list = await api('/bookmarks/')
        const mine = list.results?.find((b) => b.tool.id === tool.id)
        if (mine) await api(`/bookmarks/${mine.id}/`, { method: 'DELETE' })
      } else {
        await api('/bookmarks/', { method: 'POST', body: { tool_id: tool.id } })
      }
      setBookmarked(!bookmarked) // optimistic badge flip
      onChange?.()               // parent may refetch counters
    } catch (err) {
      console.error(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className={`tool-card ${className}`.trim()} style={style}>
      <div className="card-top">
        <Link to={`/tools/${tool.slug}`} className="card-title">{tool.name}</Link>
        <span className={`pill ${PRICING_CLASS[tool.pricing_tier] || ''}`}>
          {tool.pricing_display || tool.pricing_tier}
        </span>
      </div>
      <Link to={`/tools/${tool.slug}`} className="card-category">
        {tool.category?.name}
      </Link>
      <p className="card-desc">{tool.description}</p>
      <div className="card-tags">
        {!tool.is_live && <span className="tag dead-flag" title={tool.http_status ? `HTTP ${tool.http_status}` : "link check failed"}>link down</span>}
        {tool.tags?.slice(0, 4).map((t) => (
          <span key={t.slug} className="tag">#{t.name}</span>
        ))}
        {tool.tags?.length > 4 && <span className="tag more">+{tool.tags.length - 4}</span>}
      </div>
      <div className="card-footer">
        <StarRating value={Number(tool.avg_rating)} count={tool.rating_count} />
        <div className="card-actions">
          <button
            className={`icon-btn ${inCompare ? 'active' : ''}`}
            title={inCompare ? 'Remove from compare' : 'Add to compare'}
            disabled={compareIds.length >= 4 && !inCompare}
            onClick={(e) => { e.preventDefault(); toggleCompare(tool.id, tool.name) }}
          >
            ⇄
          </button>
          {user && (
            <button
              className={`icon-btn bookmark ${bookmarked ? 'active' : ''}`}
              title={bookmarked ? 'Remove bookmark' : 'Bookmark'}
              onClick={toggleBookmark}
              disabled={busy}
            >
              {bookmarked ? '★' : '☆'}
            </button>
          )}
        </div>
      </div>
    </article>
  )
}
