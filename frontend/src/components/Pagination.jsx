export default function Pagination({ page, pages, onPage }) {
  if (!pages || pages <= 1) return null
  const window = 2
  const items = []
  for (let p = 1; p <= pages; p++) {
    if (p === 1 || p === pages || Math.abs(p - page) <= window) items.push(p)
    else if (items[items.length - 1] !== '…') items.push('…')
  }
  return (
    <nav className="pagination">
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}>‹ Prev</button>
      {items.map((p, i) =>
        p === '…' ? <span key={`gap-${i}`}>…</span> : (
          <button key={p} className={p === page ? 'current' : ''} onClick={() => onPage(p)}>
            {p}
          </button>
        ),
      )}
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}>Next ›</button>
    </nav>
  )
}
