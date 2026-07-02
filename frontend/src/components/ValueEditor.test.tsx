import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { RangeKind, ValuePayload } from '../api/types'
import { ValueEditor } from './ValueEditor'

function Harness({ rangeKind, onChange }: {
  rangeKind: RangeKind
  onChange: (v: ValuePayload | null) => void
}) {
  const [value, setValue] = useState<ValuePayload | null>(null)
  return (
    <ValueEditor
      rangeKind={rangeKind}
      value={value}
      onChange={(v) => {
        setValue(v)
        onChange(v)
      }}
    />
  )
}

describe('ValueEditor — polymorpher Wert (§3.1)', () => {
  it('string → {type:string, text}', async () => {
    const onChange = vi.fn()
    render(<Harness rangeKind="string" onChange={onChange} />)
    await userEvent.type(screen.getByLabelText('Text'), 'Werkstudent')
    expect(onChange.mock.lastCall?.[0]).toEqual({ type: 'string', text: 'Werkstudent' })
  })

  it('quantity → number + unit', async () => {
    const onChange = vi.fn()
    render(<Harness rangeKind="quantity" onChange={onChange} />)
    await userEvent.type(screen.getByLabelText('Wert'), '142.3')
    await userEvent.type(screen.getByLabelText('Einheit'), 'EUR')
    expect(onChange.mock.lastCall?.[0]).toEqual({ type: 'quantity', number: 142.3, unit: 'EUR' })
  })

  it('geo → lat + lon', async () => {
    const onChange = vi.fn()
    render(<Harness rangeKind="geo" onChange={onChange} />)
    await userEvent.type(screen.getByLabelText('Breite (lat)'), '23.7')
    await userEvent.type(screen.getByLabelText('Länge (lon)'), '121')
    expect(onChange.mock.lastCall?.[0]).toEqual({ type: 'geo', lat: 23.7, lon: 121 })
  })

  it('json: ungültiges JSON → null, gültiges → payload', async () => {
    const onChange = vi.fn()
    render(<Harness rangeKind="json" onChange={onChange} />)
    const area = screen.getByLabelText('JSON')
    await userEvent.type(area, '{{"a": 1')
    expect(onChange.mock.lastCall?.[0]).toBeNull()
    await userEvent.clear(area)
    await userEvent.type(area, '{{"a": 1}')
    expect(onChange.mock.lastCall?.[0]).toEqual({ type: 'json', json: { a: 1 } })
  })

  it('number: leeres Feld → null', async () => {
    const onChange = vi.fn()
    render(<Harness rangeKind="number" onChange={onChange} />)
    const input = screen.getByLabelText('Zahl')
    await userEvent.type(input, '7')
    expect(onChange.mock.lastCall?.[0]).toEqual({ type: 'number', number: 7 })
    await userEvent.clear(input)
    expect(onChange.mock.lastCall?.[0]).toBeNull()
  })
})
