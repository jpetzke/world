import { useState } from 'react'
import { ApiError, api } from '../api/client'

/** Login-Ansicht. Setzt bei Erfolg das Server-Session-Cookie; onSuccess
 *  lässt die App den /auth/me-Query neu ziehen und die Shell rendern. */
export function LoginPage({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.auth.login(username, password)
      onSuccess()
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError('Zu viele Fehlversuche. Bitte später erneut.')
      } else {
        setError('Falsche Zugangsdaten.')
      }
      setBusy(false)
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <div className="wordmark">
          WELTMODELL
          <small>reifizierter Statement-Store</small>
        </div>
        <label className="field">
          <span>Benutzer</span>
          <input
            autoFocus
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Passwort</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <div className="error-box" role="alert">{error}</div>}
        <button type="submit" className="primary" disabled={busy || !username || !password}>
          {busy ? 'Anmelden …' : 'Anmelden'}
        </button>
      </form>
    </div>
  )
}
