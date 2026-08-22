export default function StarRating({ value = 0, count }) {
  const rounded = Math.round(value * 2) / 2
  return (
    <span className="stars" title={`${value} / 5`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} className={i <= rounded ? 'star on' : 'star'}>
          {i - 0.5 === rounded ? '★' : '★'}
        </span>
      ))}
      <span className="star-num">{Number(value).toFixed(1)}</span>
      {count !== undefined && <span className="star-count">({count})</span>}
    </span>
  )
}
