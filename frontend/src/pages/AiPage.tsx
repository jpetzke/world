/** WorldAI — agentischer Chat über dem Weltmodell (/ai).
    SSE-Streaming, Tool-Calls als Step-Karten, Schreib-Gate-Bestätigung,
    Session-Liste + Modell-Override in der Seitenleiste. */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  aiApi,
  aiStream,
  type AiEvent,
  type AiStoredMessage,
  type ToolCallInfo,
} from '../api/ai'
import { Empty, ErrorBox, Loading, fmtRelative } from '../components/bits'
import { StepCard, type StepInfo } from '../components/ai/StepCard'

// --- Chat-Items: persistierte Messages + Live-Events → eine Render-Liste -----

type ChatItem =
  | { type: 'user'; content: string }
  | { type: 'anchors'; anchors: { id: string; label: string | null }[] }
  | { type: 'note'; content: string }
  | { type: 'assistant'; content: string }
  | { type: 'step'; step: StepInfo }

function buildItems(messages: AiStoredMessage[]): ChatItem[] {
  const toolResults = new Map<string, AiStoredMessage['payload']>()
  for (const m of messages) {
    if (m.payload.role === 'tool' && m.payload.tool_call_id) {
      toolResults.set(m.payload.tool_call_id, m.payload)
    }
  }
  const items: ChatItem[] = []
  for (const m of messages) {
    const p = m.payload
    if (p.role === 'user') {
      if (p._ui?.kind === 'anchors') items.push({ type: 'anchors', anchors: p._ui.anchors ?? [] })
      else if (p._ui?.kind === 'system-note') items.push({ type: 'note', content: p.content ?? '' })
      else items.push({ type: 'user', content: p.content ?? '' })
    } else if (p.role === 'assistant') {
      if (p.content) items.push({ type: 'assistant', content: p.content })
      for (const call of p.tool_calls ?? []) {
        const result = toolResults.get(call.id)
        let args: unknown = call.function.arguments
        try {
          args = JSON.parse(call.function.arguments)
        } catch {
          /* Roh-String zeigen */
        }
        let display: unknown
        try {
          display = result?.content != null ? JSON.parse(result.content) : undefined
        } catch {
          display = result?.content
        }
        items.push({
          type: 'step',
          step: {
            id: call.id,
            name: call.function.name,
            arguments: args,
            status: !result
              ? 'pending'
              : result._ui?.rejected
                ? 'rejected'
                : result._ui?.error
                  ? 'error'
                  : 'done',
            durationMs: result?._ui?.duration_ms,
            offloaded: result?._ui?.offloaded,
            error: result?._ui?.error,
            ref:
              display && typeof display === 'object' && 'ref' in (display as object)
                ? String((display as { ref?: string }).ref)
                : null,
            display,
          },
        })
      }
    }
  }
  return items
}

// --- Entity-Chips: [[entity:<id>|<label>]] im Antwort-Text -------------------

const CHIP_RE = /\[\[entity:([0-9a-f-]{36})\|([^\]]+)\]\]/g

function AssistantText({ content }: { content: string }) {
  const parts: (string | { id: string; label: string })[] = []
  let last = 0
  for (const m of content.matchAll(CHIP_RE)) {
    if (m.index! > last) parts.push(content.slice(last, m.index))
    parts.push({ id: m[1], label: m[2] })
    last = m.index! + m[0].length
  }
  if (last < content.length) parts.push(content.slice(last))
  return (
    <div className="ai-assistant-text">
      {parts.map((part, i) =>
        typeof part === 'string' ? (
          <Fragment key={i}>{part}</Fragment>
        ) : (
          <Link key={i} className="chip ai-entity-chip" to={`/entity/${part.id}`}>
            {part.label}
          </Link>
        ),
      )}
    </div>
  )
}

// --- Live-Turn-State ----------------------------------------------------------

type LiveState = {
  running: boolean
  streamText: string
  items: ChatItem[]
  confirm: { tool_call_id: string; name: string; arguments: unknown } | null
  error: string | null
  limitHit: boolean
}

const IDLE: LiveState = {
  running: false,
  streamText: '',
  items: [],
  confirm: null,
  error: null,
  limitHit: false,
}

export function AiPage() {
  const { id: sessionId } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const config = useQuery({ queryKey: ['ai-config'], queryFn: aiApi.config, staleTime: 300_000 })
  const sessionList = useQuery({ queryKey: ['ai-sessions'], queryFn: aiApi.sessions })
  const session = useQuery({
    queryKey: ['ai-session', sessionId],
    queryFn: () => aiApi.session(sessionId!),
    enabled: !!sessionId,
  })

  const [live, setLive] = useState<LiveState>(IDLE)
  const [draft, setDraft] = useState('')
  const [modelOverride, setModelOverride] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const liveRef = useRef(live)
  liveRef.current = live

  useEffect(() => {
    setLive(IDLE)
    setModelOverride(null)
  }, [sessionId])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [live, session.data])

  const onEvent = useCallback((e: AiEvent) => {
    setLive((s) => {
      switch (e.event) {
        case 'token':
          return { ...s, streamText: s.streamText + e.data.text }
        case 'assistant': {
          const items = [...s.items]
          if (e.data.content) items.push({ type: 'assistant', content: e.data.content })
          return { ...s, items, streamText: '' }
        }
        case 'tool_start': {
          const step: StepInfo = { ...(e.data as ToolCallInfo), status: 'running' }
          return { ...s, items: [...s.items, { type: 'step', step }] }
        }
        case 'tool_result': {
          const items = s.items.map((it) =>
            it.type === 'step' && it.step.id === e.data.id
              ? {
                  ...it,
                  step: {
                    ...it.step,
                    status: e.data.rejected ? 'rejected' : e.data.error ? 'error' : 'done',
                    durationMs: e.data.duration_ms,
                    offloaded: e.data.offloaded,
                    ref: e.data.ref,
                    error: e.data.error,
                    display: e.data.display,
                  } as StepInfo,
                }
              : it,
          )
          return { ...s, items }
        }
        case 'confirm_required':
          return { ...s, confirm: e.data }
        case 'error':
          return { ...s, error: e.data.message, running: false }
        case 'done':
          return { ...s, limitHit: e.data.reason === 'max_iterations' }
        default:
          return s
      }
    })
  }, [])

  const finishTurn = useCallback(async () => {
    await qc.invalidateQueries({ queryKey: ['ai-session', sessionId] })
    await qc.invalidateQueries({ queryKey: ['ai-sessions'] })
    setLive((s) => ({ ...IDLE, confirm: s.confirm, error: s.error, limitHit: s.limitHit }))
  }, [qc, sessionId])

  const runStream = useCallback(
    async (fn: () => Promise<void>) => {
      setLive((s) => ({ ...IDLE, limitHit: false, error: null, confirm: null, running: true, items: s.items }))
      try {
        await fn()
      } catch (err) {
        setLive((s) => ({ ...s, error: String(err instanceof Error ? err.message : err) }))
      }
      await finishTurn()
    },
    [finishTurn],
  )

  const send = useCallback(async () => {
    const text = draft.trim()
    if (!text || live.running) return
    let target = sessionId
    if (!target) {
      const created = await aiApi.createSession(modelOverride)
      await qc.invalidateQueries({ queryKey: ['ai-sessions'] })
      navigate(`/ai/${created.id}`, { replace: true })
      target = created.id
    }
    setDraft('')
    setLive((s) => ({ ...s, items: [...s.items, { type: 'user', content: text }] }))
    await runStream(() => aiStream.message(target!, text, modelOverride, onEvent))
  }, [draft, live.running, sessionId, modelOverride, qc, navigate, runStream, onEvent])

  const decide = useCallback(
    async (approved: boolean) => {
      const confirm = liveRef.current.confirm ?? session.data?.pending
      if (!confirm || !sessionId) return
      await runStream(() => aiStream.confirm(sessionId, confirm.tool_call_id, approved, onEvent))
    },
    [sessionId, session.data, runStream, onEvent],
  )

  const persisted = session.data ? buildItems(session.data.messages) : []
  // Live-Items nur zeigen, was noch nicht persistiert nachgeladen wurde:
  // nach finishTurn() sind sie geleert; währenddessen hängen sie hinten an.
  const pendingConfirm = live.confirm ?? (!live.running ? session.data?.pending ?? null : null)
  const model = modelOverride ?? session.data?.model ?? config.data?.default_model ?? ''

  return (
    <div className="ai-layout">
      <aside className="ai-sidebar">
        <div className="ai-sidebar-head">
          <button type="button" className="btn" onClick={() => navigate('/ai')}>
            Neuer Chat
          </button>
          <label className="ai-model-label">
            Modell
            <select
              className="ai-model-select"
              value={model}
              onChange={(e) => setModelOverride(e.target.value)}
            >
              {(config.data?.models ?? (model ? [model] : [])).map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="ai-session-list">
          {sessionList.isLoading && <Loading label="Lade Chats …" />}
          {sessionList.data?.map((s) => (
            <Link
              key={s.id}
              to={`/ai/${s.id}`}
              className={`ai-session-item ${s.id === sessionId ? 'on' : ''}`}
            >
              <span className="ai-session-title">{s.title ?? 'Neuer Chat'}</span>
              <span className="ai-session-meta">{fmtRelative(s.updated_at)}</span>
            </Link>
          ))}
          {sessionList.data?.length === 0 && (
            <p className="muted small">Noch keine Chats.</p>
          )}
        </div>
      </aside>

      <section className="ai-chat">
        <div className="ai-messages" ref={scrollRef}>
          {sessionId && session.isLoading && <Loading label="Lade Chat …" />}
          {session.error ? <ErrorBox error={session.error} /> : null}
          {!sessionId && (
            <Empty title="WorldAI">
              Stelle eine Frage über das Weltmodell — z.&nbsp;B. „Wer folgt sowohl
              Account A als auch Account B?" Der Agent nutzt die Analyse-Tools des
              Servers und zeigt jeden Zwischenschritt.
            </Empty>
          )}
          {[...persisted, ...live.items].map((item, i) => (
            <ChatItemView key={i} item={item} />
          ))}
          {live.streamText && (
            <div className="ai-msg ai-msg-assistant">
              <AssistantText content={live.streamText} />
            </div>
          )}
          {live.running && !live.streamText && <Loading label="Denkt nach …" />}
          {live.error && <ErrorBox error={new Error(live.error)} />}
          {live.limitHit && (
            <p className="muted small">Iterationslimit erreicht — Zwischenstand oben.</p>
          )}
          {pendingConfirm && (
            <div className="ai-confirm panel">
              <div className="ai-confirm-head">
                Schreibaktion bestätigen: <code>{pendingConfirm.name}</code>
              </div>
              <pre className="ai-confirm-args">
                {JSON.stringify(pendingConfirm.arguments, null, 2)}
              </pre>
              <div className="ai-confirm-actions">
                <button type="button" className="btn primary" disabled={live.running} onClick={() => decide(true)}>
                  Ausführen
                </button>
                <button type="button" className="btn" disabled={live.running} onClick={() => decide(false)}>
                  Ablehnen
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="ai-composer">
          <textarea
            value={draft}
            placeholder="Frage ans Weltmodell …"
            rows={2}
            disabled={live.running}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void send()
              }
            }}
          />
          <button
            type="button"
            className="btn primary"
            disabled={live.running || !draft.trim()}
            onClick={() => void send()}
          >
            Senden
          </button>
        </div>
      </section>
    </div>
  )
}

function ChatItemView({ item }: { item: ChatItem }) {
  switch (item.type) {
    case 'user':
      return (
        <div className="ai-msg ai-msg-user">
          <div className="ai-assistant-text">{item.content}</div>
        </div>
      )
    case 'assistant':
      return (
        <div className="ai-msg ai-msg-assistant">
          <AssistantText content={item.content} />
        </div>
      )
    case 'step':
      return <StepCard step={item.step} />
    case 'anchors':
      return (
        <div className="ai-anchors muted small">
          Anker: {item.anchors.map((a) => a.label ?? a.id.slice(0, 8)).join(', ')}
        </div>
      )
    case 'note':
      return <div className="ai-anchors muted small">{item.content}</div>
  }
}
