import { useEffect, useRef, useState } from 'react'

/** „Frage die Welt" — der KI-Assistent als Orb (Frontend-Mock, §3/§6 der
    Designphilosophie). Zustände sprechen die Ontologie-Farben: in Ruhe ist
    er ein Continuant (cyan), im Denken ein Occurrent (amber). Die Antwort-
    Pipeline (Retrieval über Statements mit Provenance) folgt später. */

type OrbState = 'idle' | 'listening' | 'thinking'

const TARGETS: Record<OrbState, { c: [number, number, number]; t: number }> = {
  idle: { c: [86, 190, 255], t: 1 },
  listening: { c: [150, 230, 255], t: 1.8 },
  thinking: { c: [255, 150, 58], t: 3.4 },
}

const rnd = (a: number, b: number) => a + Math.random() * (b - a)

/** Kollisions-Kern: weißer Kern, Strahlen, Partikelsphäre — additiv gezeichnet.
    Verkleinerte Fassung der Engine aus dem Philosophie-Artefakt. */
function createEngine(canvas: HTMLCanvasElement, getState: () => OrbState) {
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  let size = 0
  function fit() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    size = canvas.clientWidth
    canvas.width = size * dpr
    canvas.height = size * dpr
    ctx?.setTransform(dpr, 0, 0, dpr, 0, 0)
  }
  fit()

  const sphere = Array.from({ length: 240 }, () => ({
    th: rnd(0, 6.283),
    cp: Math.acos(rnd(-1, 1)),
    r: 0.25 + Math.random() ** 1.15 * 1.9,
    w: rnd(0.02, 0.14) * (Math.random() < 0.5 ? -1 : 1),
    jp: rnd(0, 6.283),
    js: rnd(0.5, 2.2),
    ja: rnd(0.03, 0.2),
    s: rnd(0.3, 1),
    a: rnd(0.25, 0.9),
  }))
  const rays = Array.from({ length: 42 }, (_, k) => ({
    ang: rnd(0, 6.283),
    len: k < 8 ? rnd(2.4, 4) : 0.8 + Math.random() ** 2 * 2,
    w: k < 8 ? rnd(0.7, 1.2) : rnd(0.3, 0.8),
    a: k < 8 ? rnd(0.5, 0.85) : rnd(0.16, 0.5),
    fs: rnd(0.6, 3),
    fp: rnd(0, 6.283),
  }))

  const col: [number, number, number] = [86, 190, 255]
  let tempo = 1
  let yaw = 0
  let last = 0

  return {
    fit,
    frame(now: number) {
      const t = now / 1000
      const dt = last ? Math.min(0.05, t - last) : 0.016
      last = t
      const tg = TARGETS[getState()]
      for (let j = 0; j < 3; j++) col[j] += (tg.c[j] - col[j]) * 0.05
      tempo += (tg.t - tempo) * 0.05
      const energy = 0.55 + tempo * 0.28
      yaw += dt * 0.12 * tempo

      const c = size / 2
      const R = size * 0.14
      const cm = `rgba(${col[0] | 0},${col[1] | 0},${col[2] | 0},`
      const ch = `rgba(${(col[0] + (255 - col[0]) * 0.55) | 0},${(col[1] + (255 - col[1]) * 0.55) | 0},${(col[2] + (255 - col[2]) * 0.55) | 0},`

      ctx.clearRect(0, 0, size, size)
      ctx.globalCompositeOperation = 'lighter'

      for (const p of sphere) {
        const az = p.th + t * p.w * tempo + yaw
        const rr = p.r * (1 + Math.sin(t * p.js + p.jp) * p.ja * energy * 0.4)
        const depth = (Math.sin(p.cp) * Math.sin(az) + 1) / 2
        const alpha = p.a * (0.2 + depth * 0.7) * (0.5 + energy * 0.45)
        ctx.fillStyle = cm + Math.min(0.95, alpha).toFixed(2) + ')'
        ctx.beginPath()
        ctx.arc(
          c + Math.sin(p.cp) * Math.cos(az) * rr * R,
          c + Math.cos(p.cp) * 0.92 * rr * R,
          p.s * (0.5 + depth * 0.7),
          0, 6.283,
        )
        ctx.fill()
      }

      ctx.lineCap = 'round'
      for (let i = 0; i < rays.length; i++) {
        const ry = rays[i]
        const ang = ry.ang + Math.sin(t * 0.3 + i) * 0.02
        const L = ry.len * R * (0.75 + 0.14 * tempo)
        const flick = 0.55 + 0.45 * Math.sin(t * ry.fs * tempo + ry.fp)
        const alpha = ry.a * flick * (0.35 + energy * 0.45)
        if (alpha < 0.03) continue
        const x0 = c + Math.cos(ang) * R * 0.3
        const y0 = c + Math.sin(ang) * R * 0.3
        const x1 = c + Math.cos(ang) * L
        const y1 = c + Math.sin(ang) * L
        const g = ctx.createLinearGradient(x0, y0, x1, y1)
        g.addColorStop(0, ch + alpha.toFixed(2) + ')')
        g.addColorStop(1, cm + '0)')
        ctx.strokeStyle = g
        ctx.lineWidth = ry.w
        ctx.beginPath()
        ctx.moveTo(x0, y0)
        ctx.lineTo(x1, y1)
        ctx.stroke()
      }

      const pulse = 1 + 0.06 * Math.sin(t * 2.2 * tempo)
      const g1 = ctx.createRadialGradient(c, c, 0, c, c, R * 1.6 * pulse)
      g1.addColorStop(0, ch + '0.85)')
      g1.addColorStop(0.3, cm + '0.4)')
      g1.addColorStop(1, cm + '0)')
      ctx.fillStyle = g1
      ctx.beginPath()
      ctx.arc(c, c, R * 1.6 * pulse, 0, 6.283)
      ctx.fill()
      const g2 = ctx.createRadialGradient(c, c, 0, c, c, R * 0.55 * pulse)
      g2.addColorStop(0, 'rgba(255,255,255,0.95)')
      g2.addColorStop(0.5, ch + '0.8)')
      g2.addColorStop(1, ch + '0)')
      ctx.fillStyle = g2
      ctx.beginPath()
      ctx.arc(c, c, R * 0.55 * pulse, 0, 6.283)
      ctx.fill()
    },
  }
}

export function AskOrb() {
  const [open, setOpen] = useState(false)
  const [question, setQuestion] = useState('')
  const [answered, setAnswered] = useState<string | null>(null)
  const [thinking, setThinking] = useState(false)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const stateRef = useRef<OrbState>('idle')
  const inputRef = useRef<HTMLInputElement>(null)

  stateRef.current = thinking ? 'thinking' : open ? 'listening' : 'idle'

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const engine = createEngine(canvas, () => stateRef.current)
    if (!engine) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced) {
      engine.frame(16)
      return
    }
    let raf = 0
    const loop = (now: number) => {
      engine.frame(now)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    const onResize = () => engine.fit()
    window.addEventListener('resize', onResize)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize)
    }
  }, [])

  // ⌘K / Ctrl+K öffnet, Esc schließt.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      } else if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) inputRef.current?.focus()
    else {
      setAnswered(null)
      setQuestion('')
      setThinking(false)
    }
  }, [open])

  function submit(e: React.FormEvent) {
    e.preventDefault()
    const q = question.trim()
    if (!q || thinking) return
    setAnswered(null)
    setThinking(true)
    window.setTimeout(() => {
      setThinking(false)
      setAnswered(q)
    }, 1600)
  }

  return (
    <>
      {open && (
        <div className="ask-orb-panel">
          <form onSubmit={submit}>
            <label className="field" style={{ marginBottom: 0 }}>
              <span className="field-label">
                Frage die Welt <kbd>⌘K</kbd>
              </span>
              <input
                ref={inputRef}
                value={question}
                placeholder="„Wer hält Anteile an …?"
                onChange={(e) => setQuestion(e.target.value)}
              />
            </label>
          </form>
          {thinking && <div className="ask-orb-answer">Der Orb denkt …</div>}
          {answered && (
            <div className="ask-orb-answer">
              <span className="q">„{answered}"</span>
              Mock — die Antwort-Pipeline folgt: Retrieval über Statements, jede
              Antwort zitiert ihre Quellen (Provenance, Invariante 3).
            </div>
          )}
        </div>
      )}
      <button
        type="button"
        className="ask-orb"
        aria-label={open ? 'Assistent schließen' : 'Assistent öffnen (⌘K)'}
        onClick={() => setOpen((o) => !o)}
      >
        <canvas ref={canvasRef} aria-hidden />
      </button>
    </>
  )
}
