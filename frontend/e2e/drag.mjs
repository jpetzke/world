// Drag-Bug-Verifikation: Screenshot WÄHREND aktiven Drags — Kanten müssen
// sichtbar bleiben und mitgezogen werden. Plus Expansion-Test (Badge-Klick).
import { chromium } from 'playwright'

const BASE = 'http://localhost:5174'
const shots = '/tmp/claude-1000/-home-jonas-Projects-world/0ed988c5-2061-49d9-8d4c-2232910b192e/scratchpad'

const browser = await chromium.launch({ args: ['--enable-gpu', '--use-gl=angle', '--enable-features=Vulkan', '--ignore-gpu-blocklist'] })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()
const errors = []
page.on('pageerror', (e) => errors.push(e.message))

await page.request.post(`${BASE}/api/auth/login`, { data: { username: 'dev', password: 'dev' } })
await page.goto(BASE)
await page.waitForTimeout(4500)

// Position eines echten Nodes aus der Engine holen (Hub mit Kanten)
const target = await page.evaluate(() => {
  const e = window.__graphEngine
  const g = e.graph
  // Node mit vielen geladenen Kanten = Hub
  let best = null, bd = 0
  g.forEachNode((id) => {
    const deg = g.degree(id)
    if (deg > bd) { bd = deg; best = id }
  })
  const a = g.getNodeAttributes(best)
  const p = e.sigma.graphToViewport({ x: a.x, y: a.y })
  return { id: best, label: a.label, deg: bd, x: p.x, y: p.y }
})
console.log('drag target:', target.label, 'degree', target.deg)

const stage = await page.locator('.graph-stage').boundingBox()
const sx = stage.x + target.x
const sy = stage.y + target.y

// Drag: down → schrittweise ziehen → Screenshot MITTEN im Drag → weiterziehen
await page.mouse.move(sx, sy)
await page.mouse.down()
for (let i = 1; i <= 10; i++) {
  await page.mouse.move(sx + i * 18, sy - i * 8)
  await page.waitForTimeout(30)
}
await page.screenshot({ path: `${shots}/drag-mid.png` })
for (let i = 10; i <= 16; i++) {
  await page.mouse.move(sx + i * 18, sy - i * 8)
  await page.waitForTimeout(30)
}
await page.screenshot({ path: `${shots}/drag-late.png` })
await page.mouse.up()
await page.waitForTimeout(400)
await page.screenshot({ path: `${shots}/drag-after.png` })

// Expansion: Badge eines Nodes mit Ghosts klicken
const badge = await page.evaluate(() => {
  const e = window.__graphEngine
  const r = e.badgeRects[0]
  return r ? { x: r.x + r.w / 2, y: r.y + r.h / 2 } : null
})
if (badge) {
  const before = await page.evaluate(() => window.__graphEngine.graph.order)
  const t0 = Date.now()
  await page.mouse.click(stage.x + badge.x, stage.y + badge.y)
  // warten bis Nodes dazukommen
  await page.waitForFunction((n) => window.__graphEngine.graph.order > n, before, { timeout: 5000 })
  const ms = Date.now() - t0
  const after = await page.evaluate(() => window.__graphEngine.graph.order)
  console.log(`expansion: ${before} → ${after} nodes in ${ms}ms (Klick bis sichtbar)`)
  await page.waitForTimeout(1200)
  await page.screenshot({ path: `${shots}/expanded.png` })
} else {
  console.log('no badge found')
}

console.log('errors:', errors.length ? errors : 'none')
await browser.close()
