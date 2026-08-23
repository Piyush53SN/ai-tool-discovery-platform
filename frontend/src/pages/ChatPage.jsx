import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { useAuth } from '../AuthContext.jsx'

/**
 * Model Lab — side-by-side live comparison of up to 4 LLMs.
 *
 * Each selected model gets its own SSE connection
 * (GET /api/chat/turns/{id}/stream/{model}/) streamed via fetch +
 * ReadableStream so the JWT Authorization header can ride along (EventSource
 * can't set headers). Columns are fully independent: a slow or erroring
 * provider never blocks its siblings.
 */
export default function ChatPage() {
  const { user, loading: authLoading } = useAuth()
  const [models, setModels] = useState([])
  const [selected, setSelected] = useState([])
  const [prompt, setPrompt] = useState('')
  const [conversationId, setConversationId] = useState(null)
  const [runs, setRuns] = useState([]) // {key, cols: [{modelId, label, provider, state, text, meta}]}
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [byokFor, setByokFor] = useState(null)   // provider awaiting a pasted key
  const [byokKey, setByokKey] = useState('')
  const [byokBusy, setByokBusy] = useState(false)
  const scroller = useRef(null)

  const refreshModels = () => api('/chat/models/').then(setModels).catch(() => {})
  useEffect(() => {
    if (user) refreshModels()
  }, [user]) // eslint-disable-line react-hooks/exhaustive-deps

  async function submitKey() {
    if (!byokFor || !byokKey.trim() || byokBusy) return
    setByokBusy(true); setError('')
    try {
      await api('/chat/keys/', {
        method: 'POST',
        body: { provider: byokFor, api_key: byokKey.trim() },
      })
      setByokFor(null); setByokKey('')
      await refreshModels() // chip flips to connected, same as a global key
    } catch (err) {
      setError(err.payload?.api_key || JSON.stringify(err.payload) || 'Could not save key')
    } finally {
      setByokBusy(false)
    }
  }

  const connected = models.filter((m) => m.connected)
  const toggle = (id) =>
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id)
        : prev.length >= 4 ? prev : [...prev, id],
    )

  async function send() {
    if (!prompt.trim() || !selected.length || busy) return
    setBusy(true); setError('')
    const runKey = crypto.randomUUID()
    const cols = selected.map((id) => {
      const m = models.find((x) => x.id === id)
      return {
        modelId: id, label: m?.label || id, provider: m?.provider || '',
        state: 'connecting', text: '', meta: null,
      }
    })
    try {
      setRuns((prev) => [...prev, { key: runKey, cols }])
      const created = await api('/chat/turns/', {
        method: 'POST',
        body: { prompt, model_ids: selected, conversation_id: conversationId },
      })
      setConversationId(created.conversation_id)
      const sentPrompt = prompt
      setRuns((prev) => prev.map((r) => (r.key === runKey ? { ...r, prompt: sentPrompt } : r)))
      setPrompt('')
      selected.forEach((modelId) => streamOne(runKey, created.turn_id, modelId))
    } catch (err) {
      setError(err.payload?.detail || JSON.stringify(err.payload) || 'Failed to start turn')
      setRuns((prev) => prev.filter((r) => r.key !== runKey))
    } finally {
      setBusy(false)
    }
  }

  async function streamOne(runKey, turnId, modelId) {
    const patch = (updater) =>
      setRuns((prev) =>
        prev.map((r) =>
          r.key === runKey
            ? { ...r, cols: r.cols.map((c) => (c.modelId === modelId ? updater(c) : c)) }
            : r,
        ),
      )

    try {
      const res = await fetch(`/api/chat/turns/${turnId}/stream/${modelId}/`, {
        headers: { Authorization: `Bearer ${localStorage.getItem('aitools.access')}` },
      })
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)

      patch((c) => ({ ...c, state: 'streaming' }))
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { value, done: readerDone } = await reader.read()
        if (readerDone) break
        buffer += decoder.decode(value, { stream: true })
        const frames = buffer.split('\n\n')
        buffer = frames.pop() // keep the partial tail
        for (const frame of frames) {
          const line = frame.trim()
          if (!line.startsWith('data:')) continue
          const event = JSON.parse(line.slice(5).trim())
          if (event.token) {
            patch((c) => ({ ...c, text: c.text + event.token }))
          } else if (event.done) {
            patch((c) => ({ ...c, state: 'done', meta: event }))
            return
          } else if (event.error) {
            patch((c) => ({ ...c, state: 'error', text: event.error }))
            return
          }
        }
      }
      patch((c) => (c.state === 'streaming' ? { ...c, state: 'done' } : c))
    } catch (err) {
      patch((c) => ({ ...c, state: 'error', text: String(err.message || err) }))
    }
  }

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [runs])

  if (authLoading) return null
  if (!user) {
    return (
      <div className="empty-state">
        <h3>Sign in to run the comparison</h3>
        <p>Live model calls are metered per user — sign-in is the cost gate.</p>
        <Link className="btn btn-primary" to="/login">Sign in</Link>
      </div>
    )
  }

  const lastRun = runs[runs.length - 1]
  const historyRuns = runs.slice(0, -1)

  return (
    <div className="lab">
      <header className="lab-head">
        <div>
          <h1 className="display">Model Lab</h1>
          <p className="muted">
            One prompt, up to four models, streamed live. Tokens arrive as each
            provider produces them — the columns are independent by design.
          </p>
        </div>
        <div className="lab-status mono">
          <span>{connected.length}/{models.length}</span> CONNECTED
        </div>
      </header>

      <div className="model-picker">
        {models.map((m) => (
          <button
            key={m.id}
            className={`model-chip ${selected.includes(m.id) ? 'on' : ''} ${m.connected ? '' : 'off'}`}
            onClick={() => (m.connected ? toggle(m.id) : setByokFor(m.provider))}
            disabled={false}
            title={m.connected ? m.model_name : 'Not connected — set the provider API key server-side'}
          >
            <span className="dot" data-state={m.connected ? 'on' : 'off'} />
            {m.label}
            <span className="chip-provider">{m.provider}</span>
            {!m.connected && <span className="chip-flag">NOT CONNECTED</span>}
          </button>
        ))}
        {!models.length && <span className="muted mono">loading registry…</span>}
      </div>

      {byokFor && (
        <div className="byok-form">
          <span className="mono byok-label">BRING YOUR OWN KEY — {byokFor.toUpperCase()}</span>
          <p className="muted">
            Paste your own {byokFor} API key to enable {models.filter((m) => m.provider === byokFor).length || ''} model
            {models.filter((m) => m.provider === byokFor).length === 1 ? '' : 's'} for your account.
            It is stored encrypted server-side and never displayed again.
          </p>
          <div className="byok-row">
            <input
              type="password"
              value={byokKey}
              onChange={(e) => setByokKey(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submitKey()}
              placeholder={`${byokFor} API key`}
              autoFocus
            />
            <button className="btn btn-primary" onClick={submitKey} disabled={byokBusy || !byokKey.trim()}>
              {byokBusy ? 'SAVING…' : 'SAVE KEY'}
            </button>
            <button className="btn btn-ghost" onClick={() => { setByokFor(null); setByokKey('') }}>
              CANCEL
            </button>
          </div>
        </div>
      )}

      <div className="lab-console" ref={scroller}>
        {!runs.length && (
          <div className="lab-empty mono">
            <span className="cursor">▌</span> awaiting first run — pick models, type a prompt, hit RUN
          </div>
        )}
        {historyRuns.map((run) => (
          <div key={run.key} className="history-block">
            <span className="history-prompt mono">› {run.prompt}</span>
            <div className="history-cols">
              {run.cols.map((c) => (
                <span key={c.modelId} className="history-col mono" data-state={c.state}>
                  {c.label}: {c.state === 'error' ? c.text.slice(0, 60) : `${c.text.length} ch · ${c.meta?.latency_ms ?? '—'} ms`}
                </span>
              ))}
            </div>
          </div>
        ))}
        {lastRun && (
          <div className={`columns cols-${lastRun.cols.length}`}>
            {lastRun.cols.map((c) => <ModelColumn key={c.modelId} col={c} />)}
          </div>
        )}
      </div>

      <footer className="lab-input">
        {error && <p className="error">{error}</p>}
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') send()
          }}
          placeholder="Prompt sent verbatim to every selected model…"
          rows={3}
        />
        <div className="lab-input-row">
          <span className="mono muted">
            {selected.length} selected · ctrl/⌘+↵ to run
          </span>
          <button className="btn btn-primary" onClick={send} disabled={busy || !selected.length || !prompt.trim()}>
            {busy ? 'STARTING…' : 'RUN →'}
          </button>
        </div>
      </footer>
    </div>
  )
}

function ModelColumn({ col }) {
  return (
    <div className={`model-col state-${col.state}`}>
      <header>
        <span className="col-label">{col.label}</span>
        <span className="col-state mono" data-state={col.state}>
          {col.state === 'streaming' && 'LIVE'}
          {col.state === 'connecting' && 'CONNECTING'}
          {col.state === 'done' && 'DONE'}
          {col.state === 'error' && 'ERROR'}
        </span>
      </header>
      <div className="col-body">
        {col.text || (col.state === 'connecting' ? '' : '')}
        {(col.state === 'streaming' || col.state === 'connecting') && <span className="cursor">▌</span>}
      </div>
      <footer className="col-metrics mono">
        {col.meta ? (
          <>
            <span>{col.meta.latency_ms} ms</span>
            <span>{col.meta.token_count ?? '—'} tok</span>
          </>
        ) : (
          <span className="muted">— · —</span>
        )}
      </footer>
    </div>
  )
}
