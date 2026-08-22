import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, qs } from '../api.js'
import ToolCard from '../components/ToolCard.jsx'
import Pagination from '../components/Pagination.jsx'

const PRICING = [
  ['free', 'Free'],
  ['freemium', 'Freemium'],
  ['paid', 'Paid'],
]
const SORTS = [
  ['-avg_rating', 'Top rated'],
  ['-created_at', 'Newest'],
  ['name', 'Name A→Z'],
  ['-bookmark_count', 'Most bookmarked'],
]

export default function ExplorePage() {
  const [categories, setCategories] = useState([])
  const [trending, setTrending] = useState([])
  const [tags, setTags] = useState([])
  const [data, setData] = useState({ results: [], count: 0, pages: 1 })
  const [loading, setLoading] = useState(true)

  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [pickedCategories, setPickedCategories] = useState([])
  const [pickedPricing, setPickedPricing] = useState([])
  const [pickedTags, setPickedTags] = useState([])
  const [minRating, setMinRating] = useState('')
  const [ordering, setOrdering] = useState('-avg_rating')
  const [page, setPage] = useState(1)
  const [refresh, setRefresh] = useState(0)

  // Debounce the search box so hybrid search fires after typing pauses.
  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPage(1) }, 350)
    return () => clearTimeout(t)
  }, [searchInput])

  useEffect(() => {
    api('/categories/').then(setCategories).catch(() => {})
    api('/tags/').then(setTags).catch(() => {})
    api('/tools/trending/?limit=5').then((d) => setTrending(d.results)).catch(() => {})
  }, [])

  const query = qs({
    search,
    category: pickedCategories.join(','),
    pricing: pickedPricing.join(','),
    tags: pickedTags.join(','),
    min_rating: minRating,
    ordering: search ? undefined : ordering,
    page,
    page_size: 12,
  })

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api(`/tools/${query}`)
      .then((d) => { if (!cancelled) setData(d) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [query, refresh])

  function toggle(value, list, setList) {
    setList(list.includes(value) ? list.filter((v) => v !== value) : [...list, value])
    setPage(1)
  }

  const activeFilters = pickedCategories.length + pickedPricing.length + pickedTags.length + (minRating ? 1 : 0)
  const tagsMemo = useMemo(() => tags.slice(0, 24), [tags])

  return (
      <div className="explore">
        <aside className="facets">
          <div className="facet-head">
            <h3>Filters {activeFilters > 0 && <span className="badge">{activeFilters}</span>}</h3>
            {activeFilters > 0 && (
              <button className="link-btn" onClick={() => {
                setPickedCategories([]); setPickedPricing([]); setPickedTags([]); setMinRating(''); setPage(1)
              }}>Clear</button>
            )}
          </div>

          <h4>Category</h4>
          <div className="facet-list">
            {categories.map((c) => (
              <label key={c.slug} className={pickedCategories.includes(c.slug) ? 'checked' : ''}>
                <input
                  type="checkbox"
                  checked={pickedCategories.includes(c.slug)}
                  onChange={() => toggle(c.slug, pickedCategories, setPickedCategories)}
                />
                {c.name} <span className="facet-count">{c.tool_count}</span>
              </label>
            ))}
          </div>

          <h4>Pricing</h4>
          <div className="facet-row">
            {PRICING.map(([slug, label]) => (
              <button
                key={slug}
                className={`chip ${pickedPricing.includes(slug) ? 'chip-on' : ''}`}
                onClick={() => toggle(slug, pickedPricing, setPickedPricing)}
              >{label}</button>
            ))}
          </div>

          <h4>Min rating</h4>
          <div className="facet-row">
            {['', '3', '4', '4.5'].map((v) => (
              <button
                key={v}
                className={`chip ${minRating === v ? 'chip-on' : ''}`}
                onClick={() => { setMinRating(v); setPage(1) }}
              >{v === '' ? 'Any' : `${v}+ ★`}</button>
            ))}
          </div>

          <h4>Tags</h4>
          <div className="facet-tags">
            {tagsMemo.map((t) => (
              <button
                key={t.slug}
                className={`chip ${pickedTags.includes(t.slug) ? 'chip-on' : ''}`}
                onClick={() => toggle(t.slug, pickedTags, setPickedTags)}
              >#{t.name}</button>
            ))}
          </div>
        </aside>

        <section className="results">
          <div className="toolbar">
            <input
              className="search-input"
              placeholder="Search the catalog — try “write seo blog posts” or “clone a voice”…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
            />
            {!search && (
              <select value={ordering} onChange={(e) => { setOrdering(e.target.value); setPage(1) }}>
                {SORTS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
              </select>
            )}
          </div>

          {search && data.search && (
            <p className="hybrid-note">
              Hybrid ranking for “<b>{data.search.query}</b>” — blending Postgres full-text
              relevance with embedding cosine similarity. <button className="link-btn" onClick={() => setSearchInput('')}>clear</button>
            </p>
          )}

          {trending.length > 0 && (
            <div className="trending-strip">
              <span className="trending-label mono">Trending · 7 days</span>
              {trending.map((t) => (
                <Link key={t.id} to={`/tools/${t.slug}`} className="trending-item">{t.name}</Link>
              ))}
            </div>
          )}

          <p className="result-count">
            {loading ? 'Searching…' : `${data.count} tool${data.count === 1 ? '' : 's'}`}
          </p>

          {loading ? (
            <div className="grid">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="tool-card skeleton" />)}</div>
          ) : data.results.length ? (
            <div className="grid">
              {data.results.map((tool) => (
                <ToolCard key={tool.id} tool={tool} onChange={() => setRefresh((r) => r + 1)} />
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <h3>No tools match</h3>
              <p>Loosen a filter or try a broader search.</p>
            </div>
          )}

          <Pagination page={page} pages={data.pages} onPage={setPage} />
        </section>
      </div>
  )
}
