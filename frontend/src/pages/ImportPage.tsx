import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api } from '../api/client'
import type { FollowerListCommitResult, FollowerListPreview, SearchHit } from '../api/types'
import { ErrorBox, Field, OkBox, PageHead } from '../components/bits'
import { EntityAutocomplete } from '../components/EntityAutocomplete'
import { mergeIntoPrevious, parseFollowerList, type ParsedRow } from '../lib/followerListParser'

const STATUS_LABEL: Record<string, string> = {
  new_account: 'neuer Account',
  new_follow: 'Beziehung neu',
  confirmed: 'schon bestätigt',
  invalid: 'ungültig',
}

/** Jetzt im datetime-local-Format (lokale Zeitzone). */
function localNow(): string {
  const d = new Date()
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
}

export function ImportPage() {
  const queryClient = useQueryClient()
  const [owner, setOwner] = useState<SearchHit | null>(null)
  const [direction, setDirection] = useState<'followers' | 'following'>('followers')
  const [observedAt, setObservedAt] = useState(localNow)
  const [paste, setPaste] = useState('')
  const [parsed, setParsed] = useState<ParsedRow[] | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [parseError, setParseError] = useState<string | null>(null)
  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [result, setResult] = useState<FollowerListCommitResult | null>(null)

  const ambiguous = useMemo(
    () => new Set(parsed?.filter((r) => r.ambiguous).map((r) => r.username)),
    [parsed],
  )

  const preview = useMutation({
    mutationFn: (rows: ParsedRow[]) => api.followerListPreview({
      owner_entity_id: owner!.id,
      direction,
      rows: rows.map((r) => ({ username: r.username, display_name: r.displayName })),
    }),
  })

  const runPreview = (rows: ParsedRow[]) => {
    setParsed(rows)
    setExcluded(new Set())
    setResult(null)
    preview.mutate(rows)
  }

  const handleParse = () => {
    setParseError(null)
    try {
      const parseResult = parseFollowerList(paste)
      setWarnings(parseResult.warnings)
      runPreview(parseResult.rows)
    } catch (e) {
      setParseError(e instanceof Error ? e.message : String(e))
      setParsed(null)
      preview.reset()
    }
  }

  const mergeUp = (username: string) => {
    if (!parsed) return
    const index = parsed.findIndex((r) => r.username === username)
    if (index > 0) runPreview(mergeIntoPrevious(parsed, index))
  }

  const rows = preview.data?.rows ?? []
  const committable = rows.filter((r) => r.status !== 'invalid' && r.status !== 'confirmed'
    && !excluded.has(r.username))

  const commit = useMutation({
    mutationFn: () => api.followerListCommit({
      owner_entity_id: owner!.id,
      direction,
      rows: committable.map((r) => ({ username: r.username, display_name: r.display_name })),
      observed_at: new Date(observedAt).toISOString(),
    }),
    onSuccess: (data) => {
      setResult(data)
      setParsed(null)
      preview.reset()
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      queryClient.invalidateQueries({ queryKey: ['entities'] })
      queryClient.invalidateQueries({ queryKey: ['entity'] })
    },
  })

  const toggle = (username: string) => {
    setExcluded((prev) => {
      const next = new Set(prev)
      if (next.has(username)) next.delete(username)
      else next.add(username)
      return next
    })
  }

  return (
    <div className="page">
      <PageHead
        eyebrow="Ingest"
        title="Follower-Listen-Import"
        sub="Instagram-Follower/Following-Liste pasten (HTML aus den DevTools oder Select-All-Text) — parsen, prüfen, eintragen."
      />

      <div className="panel">
        <Field label="Account (wessen Liste ist das?)">
          <EntityAutocomplete
            typeId="SocialMediaAccount"
            placeholder="Account suchen …"
            selected={owner}
            onSelect={setOwner}
          />
        </Field>
        <Field label="Richtung">
          <div className="seg" role="group" aria-label="Richtung der Liste">
            {([
              ['followers', 'Follower-Liste'],
              ['following', 'Following-Liste'],
            ] as const).map(([id, label]) => (
              <button key={id} type="button" className={direction === id ? 'on' : undefined}
                title={id === 'followers'
                  ? 'Diese Accounts folgen dem Account'
                  : 'Der Account folgt diesen Accounts'}
                onClick={() => setDirection(id)}>{label}</button>
            ))}
          </div>
        </Field>
        <Field label="Beobachtet am (valid_from der Beziehungen)">
          <input
            type="datetime-local"
            value={observedAt}
            onChange={(e) => setObservedAt(e.target.value)}
          />
        </Field>
        <Field label="Liste">
          <textarea
            rows={8}
            value={paste}
            placeholder="Hier die kopierte Liste einfügen …"
            onChange={(e) => setPaste(e.target.value)}
          />
        </Field>
        <button
          type="button"
          className="primary"
          disabled={!owner || !paste.trim() || preview.isPending}
          onClick={handleParse}
        >
          {preview.isPending ? 'Prüfe …' : 'Parsen & prüfen'}
        </button>
        {!owner && paste.trim() && <span className="muted small"> erst Account wählen</span>}
        {parseError && <ErrorBox error={parseError} />}
        <ErrorBox error={preview.error} />
        {warnings.map((w) => <p key={w} className="muted small">⚠ {w}</p>)}
      </div>

      {result && (
        <OkBox>
          Eingetragen: {result.accounts_created} neue Accounts, {result.follows_created} neue
          Beziehungen, {result.follows_confirmed} re-bestätigt
          {result.skipped_invalid > 0 && <>, {result.skipped_invalid} ungültig übersprungen</>}.
        </OkBox>
      )}

      {preview.data && (
        <div className="panel">
          <PreviewSummary summary={preview.data.summary} />
          <div style={{ overflowX: 'auto', maxHeight: '60vh', overflowY: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th />
                  <th>Username</th>
                  <th>Display-Name</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.username} className={row.status === 'invalid' ? 'muted' : undefined}>
                    <td>
                      <input
                        type="checkbox"
                        checked={row.status !== 'invalid' && row.status !== 'confirmed'
                          && !excluded.has(row.username)}
                        disabled={row.status === 'invalid' || row.status === 'confirmed'}
                        onChange={() => toggle(row.username)}
                      />
                    </td>
                    <td className="mono">{row.username}</td>
                    <td>{row.display_name ?? <span className="muted">—</span>}</td>
                    <td>
                      <span className={`chip status-${row.status}`}>
                        {STATUS_LABEL[row.status]}
                      </span>
                      {row.reason && <span className="muted small"> {row.reason}</span>}
                    </td>
                    <td>
                      {ambiguous.has(row.username) && (
                        <button
                          type="button"
                          className="ghost small"
                          title="Diese Zeile ist der Display-Name der vorherigen Row, kein eigener Account"
                          onClick={() => mergeUp(row.username)}
                        >
                          ↖ ist Display-Name
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ErrorBox error={commit.error} />
          <button
            type="button"
            className="primary"
            disabled={committable.length === 0 || commit.isPending}
            onClick={() => commit.mutate()}
          >
            {commit.isPending ? 'Trage ein …' : `${committable.length} Einträge eintragen`}
          </button>
        </div>
      )}
    </div>
  )
}

function PreviewSummary({ summary }: { summary: FollowerListPreview['summary'] }) {
  return (
    <p>
      <strong>{summary.total}</strong> erkannt ·{' '}
      <span className="status-new_account">{summary.new_account} neue Accounts</span> ·{' '}
      <span className="status-new_follow">{summary.new_follow} neue Beziehungen</span> ·{' '}
      <span className="status-confirmed">{summary.confirmed} schon bestätigt</span>
      {summary.invalid > 0 && <> · <span className="status-invalid">{summary.invalid} ungültig</span></>}
    </p>
  )
}
