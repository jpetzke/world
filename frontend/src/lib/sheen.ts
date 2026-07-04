/** Liquid Glass (§4): ein Glanz folgt dem Zeiger über Glasflächen.
    Eine delegierte pointermove-Listener-Registrierung für die ganze App —
    setzt --mx/--my auf dem Glas-Element, CSS zeichnet den Schein. */
export function installSheen() {
  document.addEventListener('pointermove', (e) => {
    const target = e.target as Element | null
    const glass = target?.closest?.('.panel, .choice-card, .login-card')
    if (!(glass instanceof HTMLElement)) return
    const r = glass.getBoundingClientRect()
    glass.style.setProperty('--mx', `${(((e.clientX - r.left) / r.width) * 100).toFixed(1)}%`)
    glass.style.setProperty('--my', `${(((e.clientY - r.top) / r.height) * 100).toFixed(1)}%`)
  })
}
