import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api, setUnauthorizedHandler } from './api/client'
import { Loading } from './components/bits'
import { ApiKeysPage } from './pages/ApiKeysPage'
import { CreatePage } from './pages/CreatePage'
import { Dashboard } from './pages/Dashboard'
import { EntityPage } from './pages/EntityPage'
import { GatePage } from './pages/GatePage'
import { GraphHome } from './pages/GraphHome'
import { GraphPage } from './pages/GraphPage'
import { ImportPage } from './pages/ImportPage'
import { LoginPage } from './pages/LoginPage'
import { RegistryPage } from './pages/RegistryPage'
import { SourceDetailPage, SourcesPage } from './pages/SourcesPage'

export default function App() {
  const qc = useQueryClient()
  const me = useQuery({
    queryKey: ['me'],
    queryFn: api.auth.me,
    retry: false,
    refetchOnWindowFocus: false,
  })

  // Läuft die Session mitten in der Nutzung ab (401), fällt me auf error →
  // Login-Ansicht erscheint automatisch.
  useEffect(() => {
    setUnauthorizedHandler(() => qc.invalidateQueries({ queryKey: ['me'] }))
    return () => setUnauthorizedHandler(null)
  }, [qc])

  if (me.isLoading) {
    return <div className="login-screen"><Loading label="Lade …" /></div>
  }
  if (!me.data) {
    return <LoginPage onSuccess={() => qc.invalidateQueries({ queryKey: ['me'] })} />
  }

  return <Shell username={me.data.username} />
}

function Shell({ username }: { username: string }) {
  const qc = useQueryClient()
  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats, refetchInterval: 30_000 })
  const pending = stats.data?.pending_proposals ?? 0

  async function logout() {
    try {
      await api.auth.logout()
    } finally {
      qc.invalidateQueries({ queryKey: ['me'] })
    }
  }

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="wordmark">
          WELTMODELL
          <small>reifizierter Statement-Store</small>
        </div>
        <div className="nav">
          <NavLink to="/" end>Graph</NavLink>
          <NavLink to="/browse">Suche</NavLink>
          <NavLink to="/create">Anlegen</NavLink>
          <NavLink to="/import">Import</NavLink>
          <NavLink to="/registry">Registry</NavLink>
          <NavLink to="/gate">
            Gate {pending > 0 && <span className="badge">{pending}</span>}
          </NavLink>
          <NavLink to="/sources">Quellen</NavLink>
          <NavLink to="/keys">API-Keys</NavLink>
        </div>
        <div className="nav-footer">
          <span className="muted small mono">{username}</span>
          <button type="button" className="linklike" onClick={logout}>Abmelden</button>
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<GraphHome />} />
          <Route path="/browse" element={<Dashboard />} />
          <Route path="/create" element={<CreatePage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/entity/:id" element={<EntityPage />} />
          <Route path="/graph/:id" element={<GraphPage />} />
          <Route path="/registry" element={<RegistryPage />} />
          <Route path="/gate" element={<GatePage />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/sources/:id" element={<SourceDetailPage />} />
          <Route path="/keys" element={<ApiKeysPage />} />
        </Routes>
      </main>
    </div>
  )
}
