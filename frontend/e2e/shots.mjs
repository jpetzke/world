// Screenshot-Suite: 5 Screens × Desktop 1440 / Mobile 393 (Pixel 8).
import { chromium } from 'playwright'

const BASE = process.env.BASE ?? 'http://localhost:8100'
const shots = '/tmp/claude-1000/-home-jonas-Projects-world/0ed988c5-2061-49d9-8d4c-2232910b192e/scratchpad'
const browser = await chromium.launch({ args: ['--enable-gpu', '--use-gl=angle', '--enable-features=Vulkan', '--ignore-gpu-blocklist'] })

const screens = [
  { name: 'graph', path: '/', wait: 4000 },
  { name: 'suche', path: '/browse', wait: 1500 },
  { name: 'anlegen', path: '/create', wait: 1000 },
  { name: 'entity', path: null, wait: 1500 }, // dynamisch: erste Entity
  { name: 'create-form', path: '/create?form=person', wait: 1000 },
]

for (const [label, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 393, height: 851 }]]) {
  const ctx = await browser.newContext({
    viewport,
    ...(label === 'mobile' ? { hasTouch: true, isMobile: true } : {}),
  })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.request.post(`${BASE}/api/auth/login`, { data: { username: 'dev', password: 'dev' } })

  // Entity-ID für den Entity-Screen holen
  const anyEntity = await page.request.get(`${BASE}/api/entities?limit=1`).then((r) => r.json())
  const entityId = anyEntity.items?.[0]?.id

  for (const s of screens) {
    let path = s.path
    if (s.name === 'entity') path = entityId ? `/entity/${entityId}` : null
    if (s.name === 'create-form') path = '/create'
    if (!path) continue
    await page.goto(`${BASE}${path}`)
    await page.waitForTimeout(s.wait)
    if (s.name === 'create-form') {
      // In den Person-Wizard klicken (erste Karte)
      const card = page.locator('.choice-card').first()
      if (await card.count()) { await card.click(); await page.waitForTimeout(800) }
    }
    if (s.name === 'graph') {
      // Node selektieren, damit der Inspector sichtbar ist
      const sel = await page.evaluate(() => {
        const e = window.__graphEngine
        if (!e) return false
        let best = null, bd = 0
        e.graph.forEachNode((id) => { const d = e.graph.degree(id); if (d > bd) { bd = d; best = id } })
        if (best) { e.focusOn(best) }
        return !!best
      })
      if (sel) await page.waitForTimeout(1200)
    }
    await page.screenshot({ path: `${shots}/${s.name}-${label}.png` })
  }
  if (errors.length) console.log(`${label} errors:`, errors.slice(0, 5))
  await ctx.close()
}
console.log('done')
await browser.close()
