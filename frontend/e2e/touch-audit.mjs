// Touch-Target-Audit (Spec R8): kein interaktives Element unter 40px
// Hit-Area (Desktop) bzw. 44px (Touch). Misst die EFFEKTIVE Hit-Zone per
// elementFromPoint-Sampling — unsichtbare ::after-Erweiterungen (.tchip,
// button.sm) zählen mit, reine Rect-Höhe würde sie übersehen.
import { chromium } from 'playwright'

const BASE = process.env.BASE ?? 'http://localhost:8100'
const browser = await chromium.launch({ args: ['--enable-gpu', '--use-gl=angle', '--ignore-gpu-blocklist'] })

const PAGES = ['/', '/browse', '/create', '/import', '/registry', '/gate', '/sources', '/keys']

async function audit(label, viewport, minPx, mobile) {
  const ctx = await browser.newContext({
    viewport, ...(mobile ? { hasTouch: true, isMobile: true } : {}),
  })
  const page = await ctx.newPage()
  await page.request.post(`${BASE}/api/auth/login`, { data: { username: 'dev', password: 'dev' } })
  const violations = []
  for (const path of PAGES) {
    await page.goto(`${BASE}${path}`)
    await page.waitForTimeout(path === '/' ? 3000 : 1000)
    const found = await page.evaluate((MIN) => {
      const out = []
      const els = document.querySelectorAll(
        'button, a[href], input, select, textarea, [role="button"], summary')
      for (const el of els) {
        const style = getComputedStyle(el)
        if (style.display === 'none' || style.visibility === 'hidden') continue
        el.scrollIntoView({ block: 'center', behavior: 'instant' })
        const r = el.getBoundingClientRect()
        if (r.width === 0 || r.height === 0) continue
        // Inline-Links in Fließtext/Tabellen: die tragende Row liefert die
        // Hit-Höhe (Spec: „ganze Row hoverbar", min. 44px)
        const row = el.closest('.kv .row, td, .stmt, li, p, .sub, .small')
        const cx = Math.min(Math.max(r.x + r.width / 2, 1), innerWidth - 1)
        const cy = r.y + r.height / 2
        const hitAt = (x, y) => {
          if (y < 0 || y > innerHeight || x < 0 || x > innerWidth) return false
          const t = document.elementFromPoint(x, y)
          return t !== null && (t === el || el.contains(t) || t.contains(el))
        }
        const pad = MIN / 2 - 0.5
        const effH = (hitAt(cx, cy - pad) && hitAt(cx, cy + pad))
          ? MIN
          : (row ? Math.max(r.height, row.getBoundingClientRect().height) : r.height)
        const effW = (hitAt(cx - pad, cy) && hitAt(cx + pad, cy)) ? MIN : r.width
        // Breite: Inline-Links (a) mit tragender Row sind ok; sonst prüfen.
        const isInline = el.tagName === 'A' && row !== null
        if (effH < MIN - 0.5 || (!isInline && effW < MIN - 0.5)) {
          out.push({
            tag: el.tagName.toLowerCase(),
            cls: (el.className && typeof el.className === 'string') ? el.className.slice(0, 40) : '',
            text: (el.textContent ?? '').trim().slice(0, 30),
            w: Math.round(effW), h: Math.round(effH),
          })
        }
      }
      return out
    }, minPx)
    for (const v of found) violations.push({ path, ...v })
  }
  await ctx.close()
  console.log(`\n=== ${label} (min ${minPx}px): ${violations.length} Verstöße ===`)
  for (const v of violations.slice(0, 40)) {
    console.log(`  ${v.path}  <${v.tag} class="${v.cls}"> "${v.text}"  ${v.w}×${v.h}`)
  }
  return violations.length
}

const d = await audit('Desktop 1440', { width: 1440, height: 900 }, 40, false)
const m = await audit('Mobile 393 (Pixel 8)', { width: 393, height: 851 }, 44, true)
await browser.close()
process.exit(d + m > 0 ? 1 : 0)
