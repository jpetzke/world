import { useEffect, useRef } from 'react'

/** Sternfeld hinter dem Glas (§1/§4): statisch gezeichnet, einmal pro
    Viewport-Größe — kein Animation-Loop, keine Interaktion, z-index -1. */
export function SpaceBackdrop() {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    function draw() {
      if (!canvas || !ctx) return
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const w = window.innerWidth
      const h = window.innerHeight
      canvas.width = w * dpr
      canvas.height = h * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      const tints = ['255,255,255', '156,200,255', '156,200,255', '255,196,138']
      for (let i = 0; i < 170; i++) {
        const tint = tints[Math.floor(Math.random() * tints.length)]
        ctx.fillStyle = `rgba(${tint},${(0.06 + Math.random() * 0.3).toFixed(2)})`
        ctx.beginPath()
        ctx.arc(Math.random() * w, Math.random() * h, 0.4 + Math.random() * 0.9, 0, 6.283)
        ctx.fill()
      }
    }

    draw()
    window.addEventListener('resize', draw)
    return () => window.removeEventListener('resize', draw)
  }, [])

  return <canvas ref={ref} className="space-backdrop" aria-hidden />
}
