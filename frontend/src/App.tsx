import { useQuery } from '@tanstack/react-query'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api } from './api/client'
import { CreatePage } from './pages/CreatePage'
import { Dashboard } from './pages/Dashboard'
import { EntityPage } from './pages/EntityPage'
import { GatePage } from './pages/GatePage'
import { GraphHome } from './pages/GraphHome'
import { GraphPage } from './pages/GraphPage'
import { RegistryPage } from './pages/RegistryPage'
import { SourceDetailPage, SourcesPage } from './pages/SourcesPage'

export default function App() {
  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats, refetchInterval: 30_000 })
  const pending = stats.data?.pending_proposals ?? 0

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
          <NavLink to="/registry">Registry</NavLink>
          <NavLink to="/gate">
            Gate {pending > 0 && <span className="badge">{pending}</span>}
          </NavLink>
          <NavLink to="/sources">Quellen</NavLink>
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<GraphHome />} />
          <Route path="/browse" element={<Dashboard />} />
          <Route path="/create" element={<CreatePage />} />
          <Route path="/entity/:id" element={<EntityPage />} />
          <Route path="/graph/:id" element={<GraphPage />} />
          <Route path="/registry" element={<RegistryPage />} />
          <Route path="/gate" element={<GatePage />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/sources/:id" element={<SourceDetailPage />} />
        </Routes>
      </main>
    </div>
  )
}
