import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, qs } from '../api.js'
import { useAuth } from '../AuthContext.jsx'

/** Ease a number from 0 to target over ~700ms (requestAnimationFrame) —
 *  the match figure counts up on mount; the fill bar shares the same eased
 *  value so number and bar move together. */
function useCountUp(target, duration = 700) {
  const [value, setValue] = useState(0)
  const raf = useRef(0)
  useEffect(() => {
    const start = performance.now()
    const tick = (now) => {
      const p = Math.min(1, (now - start) / duration)
      setValue(target * (1 - Math.pow(1 - p, 3)))
      if (p < 1) raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [target, duration])
  return value
}

function ScoreBlock({ similarity }) {
  const value = useCountUp(similarity * 100)
  return (
    <div className="score">
      <span className="score-val">{value.toFixed(0)}%</span>
      <span className="score-label">match</span>
      <div className="score-bar"><span style={{ width: `${value}%` }} /></div>
    </div>
  )
}

const STRATEGY_INFO = {
  personalised: {
    label: 'Personalised',
    blurb: 'Ranked by cosine similarity between each tool embedding and your taste vector, computed in Postgres with pgvector.',
  },
  personalised_diverse: {
    label: 'Personalised + diverse',
    blurb: 'Cosine similarity re-ranked with MMR so the results are not all near-duplicates of one category.',
  },
  cold_start_tags: {
    label: 'Cold start · your interests',
    blurb: 'No interaction history yet — ranking by your onboarding interests blended with catalog popularity.',
  },
  cold_start_popularity: {
    label: 'Cold start · popular',
    blurb: 'No history yet — Bayesian-smoothed rating × bookmark traction. Pick interests below (or interact!) to personalise.',
  },
}

export default function RecommendationsPage() {
  const { user, loading: authLoading, reload } = useAuth()
  const [data, setData] = useState(null)
  const [tags, setTags] = useState([])
  const [picked, setPicked] = useState([])
  const [limit, setLimit] = useState(8)
  const [diversify, setDiversify] = useState(true)
  const [loading, setLoading] = useState(false)
  const [savingTags, setSavingTags] = useState(false)

  useEffect(() => { api('/tags/').then(setTags).catch(() => {}) }, [])
  useEffect(() => {
    if (user) setPicked(user.onboarding_tags?.map((t) => t.slug) || [])
  }, [user])

  useEffect(() => {
    if (!user) return
    setLoading(true)
    api(`/recommendations/${qs({ limit, diversify })}`)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [user, limit, diversify])

  async function saveTags() {
    setSavingTags(true)
    try {
      await api('/auth/me/', { method: 'PATCH', body: { onboarding_tags: picked } })
      await reload()
      setLoading(true)
      api(`/recommendations/${qs({ limit, diversify })}`).then(setData).finally(() => setLoading(false))
    } finally {
      setSavingTags(false)
    }
  }

  if (authLoading) return null
  if (!user) {
    return (
      <div className="empty-state">
        <h3>Sign in to get recommendations</h3>
        <p>Sign in, bookmark a few tools, and the engine builds a 384-dim taste vector from your behaviour.</p>
        <Link className="btn btn-primary" to="/login">Sign in</Link>
      </div>
    )
  }

  const info = STRATEGY_INFO[data?.strategy] || {}
  const coldStart = data?.strategy?.startsWith('cold_start')

  return (
      <div className="rec-page">
        <div className="rec-head">
          <div>
            <h1>For you</h1>
            {data && (
              <p className="muted">{info.blurb}</p>
            )}
          </div>
          <div className="rec-controls">
            <label>
              Results
              <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
                {[4, 8, 12, 24].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <label className="toggle">
              <input type="checkbox" checked={diversify} onChange={(e) => setDiversify(e.target.checked)} />
              Diversity re-rank (MMR)
            </label>
          </div>
        </div>

        {data && (
          <div className="strategy-banner">
            <span className={`badge ${coldStart ? 'badge-cold' : 'badge-warm'}`}>{info.label || data.strategy}</span>
            <span className="muted">
              taste vector: {data.preference_vector_ready ? 'ready ✓' : 'warming up…'} ·
              {' '}{data.count} results
            </span>
          </div>
        )}

        {coldStart && (
          <section className="onboarding">
            <h3>Tune your interests</h3>
            <div className="facet-tags">
              {tags.slice(0, 30).map((t) => (
                <button
                  key={t.slug}
                  className={`chip ${picked.includes(t.slug) ? 'chip-on' : ''}`}
                  onClick={() => setPicked((p) => p.includes(t.slug) ? p.filter((x) => x !== t.slug) : [...p, t.slug])}
                >#{t.name}</button>
              ))}
            </div>
            <button className="btn btn-primary" disabled={savingTags} onClick={saveTags}>
              {savingTags ? 'Saving…' : 'Save interests'}
            </button>
          </section>
        )}

        {loading ? (
          <div className="rec-list">
            {Array.from({ length: 4 }).map((_, i) => <div key={i} className="rec-row skeleton" />)}
          </div>
        ) : data?.results?.length ? (
          <div className="rec-list">
            {data.results.map((r, i) => (
              <div key={r.tool.id} className="rec-row">
                <span className="rec-rank">#{i + 1}</span>
                <div className="rec-main">
                  <Link to={`/tools/${r.tool.slug}`} className="rec-name">{r.tool.name}</Link>
                  <span className="muted">{r.tool.category?.name} · {r.tool.pricing_display}</span>
                  <p>{r.tool.description}</p>
                </div>
                <div className="rec-scores">
                  {r.similarity > 0 && (
                    <ScoreBlock key={`${r.tool.id}-${limit}-${diversify}`} similarity={r.similarity} />
                  )}
                  <span className="rec-reason">{r.reason}</span>
                </div>
                <Link className="btn btn-ghost" to={`/tools/${r.tool.slug}`}>View →</Link>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <h3>Nothing to recommend yet</h3>
            <p>Bookmark or review a few tools and check back.</p>
          </div>
        )}
      </div>
  )
}
