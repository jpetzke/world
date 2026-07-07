// Perf-Messung gegen Bench-DB: Skeleton-Load, FPS, Long Tasks, Expansion.
import { chromium } from 'playwright'

const BASE = process.env.BASE ?? 'http://localhost:5174'
const shots = '/tmp/claude-1000/-home-jonas-Projects-world/0ed988c5-2061-49d9-8d4c-2232910b192e/scratchpad'

const browser = await chromium.launch({ args: ['--enable-gpu', '--use-gl=angle', '--enable-features=Vulkan', '--ignore-gpu-blocklist'] })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()
const errors = []
page.on('pageerror', (e) => errors.push(e.message))

await page.request.post(`${BASE}/api/auth/login`, { data: { username: 'dev', password: 'dev' } })

// --- Skeleton-Load: API-Request → Graph gerendert -------------------------
let tApiStart = 0
page.on('request', (r) => { if (r.url().includes('/api/graph/skeleton')) tApiStart = Date.now() })
await page.goto(BASE)
await page.waitForFunction(() => {
  const e = window.__graphEngine
  return e && e.graph.order > 0
})
// ein Frame warten = wirklich gezeichnet
await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))
const tRendered = Date.now()
console.log(`skeleton-load (API-Request → gerendert): ${tRendered - tApiStart}ms`)
const order = await page.evaluate(() => window.__graphEngine.graph.order)
console.log(`skeleton nodes: ${order}`)

// --- Instrumentierung: Long Tasks + FPS -----------------------------------
await page.evaluate(() => {
  window.__longTasks = []
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) window.__longTasks.push(Math.round(e.duration))
  }).observe({ entryTypes: ['longtask'] })
  window.__frames = []
  let last = performance.now()
  const loop = (t) => { window.__frames.push(t - last); last = t; requestAnimationFrame(loop) }
  requestAnimationFrame(loop)
})

const resetMetrics = () => page.evaluate(() => { window.__longTasks = []; window.__frames = [] })
const readMetrics = async (label) => {
  const m = await page.evaluate(() => ({ lt: window.__longTasks, fr: window.__frames }))
  const fr = m.fr.filter((f) => f > 0)
  const avg = fr.reduce((a, b) => a + b, 0) / fr.length
  const sorted = [...fr].sort((a, b) => a - b)
  const p95 = sorted[Math.floor(sorted.length * 0.95)]
  console.log(`${label}: avg ${(1000 / avg).toFixed(0)} fps · frame p95 ${p95.toFixed(1)}ms · longtasks>50ms: ${m.lt.length}${m.lt.length ? ' [' + m.lt.join(',') + ']' : ''}`)
}

const stage = await page.locator('.graph-stage').boundingBox()
const cx = stage.x + stage.width / 2
const cy = stage.y + stage.height / 2

// Layout-Simulation läuft noch (frische Bench-DB ohne Positionen)
await resetMetrics()
await page.waitForTimeout(3000)
await readMetrics('layout-simulation')

// --- Pan + Zoom -------------------------------------------------------------
await resetMetrics()
for (let r = 0; r < 3; r++) {
  await page.mouse.move(cx, cy)
  await page.mouse.down()
  for (let i = 0; i < 20; i++) { await page.mouse.move(cx + i * 12, cy + Math.sin(i) * 40); await page.waitForTimeout(16) }
  await page.mouse.up()
  for (let i = 0; i < 8; i++) { await page.mouse.wheel(0, -240); await page.waitForTimeout(50) }
  for (let i = 0; i < 8; i++) { await page.mouse.wheel(0, 240); await page.waitForTimeout(50) }
}
await readMetrics('pan+zoom')

// --- Expansion bis Budget: mehrfach expandieren -----------------------------
await resetMetrics()
let expandTimes = []
for (let round = 0; round < 6; round++) {
  const target = await page.evaluate(() => {
    const e = window.__graphEngine
    let best = null, bg = 0
    e.graph.forEachNode((id) => {
      const g = e.ghostCount(id)
      if (g > bg) { bg = g; best = id }
    })
    if (!best) return null
    const a = e.graph.getNodeAttributes(best)
    const p = e.sigma.graphToViewport({ x: a.x, y: a.y })
    return { id: best, x: p.x, y: p.y, ghosts: bg, order: e.graph.order }
  })
  if (!target || target.ghosts === 0) break
  const t0 = Date.now()
  await page.evaluate(async (id) => {
    // direkter Expansion-Pfad wie Badge-Klick
    const res = await fetch('/api/query/traverse', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start_id: id, max_depth: 1, max_nodes: 150 }),
    }).then((r) => r.json())
    window.__graphEngine.addSubgraph(res.nodes, res.edges, res.start_id)
  }, target.id)
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(r)))
  expandTimes.push(Date.now() - t0)
}
const finalOrder = await page.evaluate(() => window.__graphEngine.graph.order)
console.log(`expansionen: [${expandTimes.join(', ')}]ms · geladen jetzt: ${finalOrder}`)
await readMetrics('expansion-phase')

// --- Drag mit vollem Graph ---------------------------------------------------
await resetMetrics()
const hub = await page.evaluate(() => {
  const e = window.__graphEngine
  let best = null, bd = 0
  e.graph.forEachNode((id) => { const d = e.graph.degree(id); if (d > bd) { bd = d; best = id } })
  const a = e.graph.getNodeAttributes(best)
  const p = e.sigma.graphToViewport({ x: a.x, y: a.y })
  return { x: p.x, y: p.y, deg: bd }
})
await page.mouse.move(stage.x + hub.x, stage.y + hub.y)
await page.mouse.down()
for (let i = 0; i < 40; i++) {
  await page.mouse.move(stage.x + hub.x + i * 6, stage.y + hub.y + Math.sin(i / 3) * 60)
  await page.waitForTimeout(16)
}
await page.screenshot({ path: `${shots}/bench-drag.png` })
await page.mouse.up()
await readMetrics(`node-drag (deg ${hub.deg})`)

await page.screenshot({ path: `${shots}/bench-full.png` })
console.log('errors:', errors.length ? errors.slice(0, 5) : 'none')
await browser.close()
