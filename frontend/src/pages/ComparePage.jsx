import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { useCompare } from '../components/CompareContext.jsx'

export default function ComparePage() {
  const { compareIds } = useCompare()
  const [matrix, setMatrix] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (compareIds.length >= 2) {
      setLoading(true); setError('')
      api('/tools/compare/', { method: 'POST', body: { tool_ids: compareIds } })
        .then(setMatrix)
        .catch((e) => setError('Could not compare these tools'))
        .finally(() => setLoading(false))
    } else {
      setMatrix(null)
    }
  }, [compareIds])

  return (
    <div className="compare-page">
      <h1>Compare tools</h1>
      <p className="muted">
        Pick 2–4 tools with the ⇄ button on any card, then compare pricing, ratings and
        capabilities side by side.
      </p>

      {compareIds.length < 2 && (
        <div className="empty-state">
          <h3>{compareIds.length} of 2 selected</h3>
          <p>Head back to the catalog and add a couple of candidates.</p>
          <Link className="btn btn-primary" to="/">Browse tools</Link>
        </div>
      )}

      {loading && <div className="skeleton compare-skeleton" />}
      {error && <p className="error">{error}</p>}

      {matrix && (
        <div className="compare-table-wrap">
          <table className="compare-table">
            <thead>
              <tr>
                <th>Attribute</th>
                {matrix.tools.map((t) => (
                  <th key={t.id}>
                    <Link to={`/tools/${t.slug}`}>{t.name}</Link>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.rows.map((row) => (
                <tr key={row.attribute}>
                  <td className="attr-label">{row.label}</td>
                  {row.values.map((v, i) => (
                    <td key={i}>
                      {Array.isArray(v) ? v.join(', ') || '—' : typeof v === 'boolean' ? (v ? '✓' : '—') : v || '—'}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
