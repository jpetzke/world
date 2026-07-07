/** WorldAI-Client: Sessions-CRUD + SSE-Streaming des Agent-Loops. */

import { ApiError } from './client'

export type AiConfig = { provider: string; default_model: string; models: string[] }

export type AiSessionMeta = {
  id: string
  title: string | null
  model: string | null
  created_at: string
  updated_at: string
  message_count?: number
}

export type ToolCallInfo = { id: string; name: string; arguments: unknown }

/** Persistierte Message (OpenAI-Format + _ui-Meta). */
export type AiStoredMessage = {
  seq: number
  payload: {
    role: 'user' | 'assistant' | 'tool'
    content: string | null
    tool_calls?: { id: string; function: { name: string; arguments: string } }[]
    tool_call_id?: string
    _ui?: {
      kind?: string
      name?: string
      duration_ms?: number
      offloaded?: boolean
      error?: string
      rejected?: boolean
      anchors?: { id: string; label: string | null; type_id: string | null }[]
    }
  }
  created_at: string
}

export type AiSessionDetail = AiSessionMeta & {
  pending: { tool_call_id: string; name: string; arguments: unknown } | null
  anchors: { id: string; label: string | null; type_id: string | null }[]
  messages: AiStoredMessage[]
}

export type AiEvent =
  | { event: 'token'; data: { text: string } }
  | { event: 'assistant'; data: { content: string | null; tool_calls: ToolCallInfo[] } }
  | { event: 'tool_start'; data: ToolCallInfo & { approved?: boolean } }
  | {
      event: 'tool_result'
      data: {
        id: string
        name: string
        duration_ms?: number
        offloaded?: boolean
        ref?: string | null
        error?: string
        rejected?: boolean
        display: unknown
      }
    }
  | { event: 'confirm_required'; data: { tool_call_id: string; name: string; arguments: unknown } }
  | { event: 'done'; data: { reason: 'final' | 'confirm' | 'max_iterations' } }
  | { event: 'error'; data: { message: string } }

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch('/api/ai' + path, {
    ...init,
    headers: init?.body ? { 'content-type': 'application/json' } : undefined,
  })
  if (!res.ok) {
    let detail: unknown = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* kein JSON-Body */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export const aiApi = {
  config: () => req<AiConfig>('/config'),
  sessions: () => req<AiSessionMeta[]>('/sessions'),
  createSession: (model?: string | null) =>
    req<AiSessionMeta>('/sessions', { method: 'POST', body: JSON.stringify({ model }) }),
  session: (id: string) => req<AiSessionDetail>(`/sessions/${id}`),
}

/** POST + SSE-Stream parsen; onEvent wird pro Event gefeuert. */
async function streamTurn(
  path: string,
  body: unknown,
  onEvent: (e: AiEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/ai' + path, {
    method: 'POST',
    body: JSON.stringify(body),
    headers: { 'content-type': 'application/json' },
    signal,
  })
  if (!res.ok || !res.body) {
    let detail: unknown = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* kein JSON-Body */
    }
    throw new ApiError(res.status, detail)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = 'message'
  let dataLines: string[] = []

  const dispatch = () => {
    if (!dataLines.length) return
    try {
      const data = JSON.parse(dataLines.join('\n'))
      onEvent({ event: eventName, data } as AiEvent)
    } catch {
      /* halbes Event am Streamende ignorieren */
    }
    eventName = 'message'
    dataLines = []
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let nl: number
    while ((nl = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, nl).replace(/\r$/, '')
      buffer = buffer.slice(nl + 1)
      if (line === '') dispatch()
      else if (line.startsWith('event:')) eventName = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
  }
  dispatch()
}

export const aiStream = {
  message: (
    sessionId: string,
    text: string,
    model: string | null,
    onEvent: (e: AiEvent) => void,
    signal?: AbortSignal,
  ) => streamTurn(`/sessions/${sessionId}/messages`, { text, model }, onEvent, signal),
  confirm: (
    sessionId: string,
    toolCallId: string,
    approved: boolean,
    onEvent: (e: AiEvent) => void,
    signal?: AbortSignal,
  ) =>
    streamTurn(
      `/sessions/${sessionId}/confirm`,
      { tool_call_id: toolCallId, approved },
      onEvent,
      signal,
    ),
}
