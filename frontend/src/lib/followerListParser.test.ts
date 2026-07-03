import { describe, expect, it } from 'vitest'
import { mergeIntoPrevious, parseFollowerList } from './followerListParser'

/** Nachbau der echten Instagram-Dialog-Struktur (DevTools-Sample, gekürzt):
 * pro Row zwei <a href="/user/"> (Profilbild + Name), Display-Name-Span
 * außerhalb der Links, Button mit Viewer-Beziehung. */
function igRow(username: string, displayName: string, button = 'Follow') {
  return `
    <div class="row">
      <a href="/${username}/"><img alt="${username}'s profile picture"></a>
      <div>
        <a href="/${username}/"><span>${username}</span></a>
        <span><span>${displayName}</span></span>
        <button type="button"><div>${button}</div></button>
      </div>
    </div>`
}

const DIALOG_HTML = `<div class="list">
  ${igRow('jonas_ptzk', 'Jonas')}
  ${igRow('leopold.st2729', '')}
  ${igRow('katharina.bzl', 'Katharina', 'Following')}
  ${igRow('natalie.slr', '𝐧𝐚𝐭𝐚𝐥𝐢𝐞')}
  ${igRow('nmr.aed', 'nora 🦔', 'Requested')}
</div>`

describe('parseFollowerList — HTML', () => {
  it('extrahiert Username + Display-Name pro Row', () => {
    const result = parseFollowerList(DIALOG_HTML)
    expect(result.format).toBe('html')
    expect(result.rows).toEqual([
      { username: 'jonas_ptzk', displayName: 'Jonas', ambiguous: false },
      { username: 'leopold.st2729', displayName: null, ambiguous: false },
      { username: 'katharina.bzl', displayName: 'Katharina', ambiguous: false },
      { username: 'natalie.slr', displayName: '𝐧𝐚𝐭𝐚𝐥𝐢𝐞', ambiguous: false },
      { username: 'nmr.aed', displayName: 'nora 🦔', ambiguous: false },
    ])
  })

  it('ignoriert Button-Texte (Viewer-Beziehung) als Display-Name', () => {
    const result = parseFollowerList(igRow('leopold.st2729', '', 'Following'))
    expect(result.rows).toEqual([
      { username: 'leopold.st2729', displayName: null, ambiguous: false },
    ])
  })

  it('überspringt den · Separator vor dem Follow-Link', () => {
    // Reale Dialog-Struktur bei nicht-gefolgten Accounts: Username · Follow
    // inline, Display-Name darunter. Der nackte · darf nicht als Name greifen.
    const row = `
      <div class="row">
        <a href="/marrianna.fd/"><img alt="marrianna.fd's profile picture"></a>
        <div>
          <div>
            <a href="/marrianna.fd/"><span>marrianna.fd</span></a>
            <span>·</span>
            <a href="/marrianna.fd/"><div>Follow</div></a>
          </div>
          <span><span>Mariana</span></span>
        </div>
        <button type="button"><div>Remove</div></button>
      </div>`
    const result = parseFollowerList(row)
    expect(result.rows).toEqual([
      { username: 'marrianna.fd', displayName: 'Mariana', ambiguous: false },
    ])
  })

  it('lehnt HTML ohne Profil-Links ab', () => {
    expect(() => parseFollowerList('<div><a href="/explore/">x</a></div>'))
      .toThrow(/Keine Profil-Links/)
  })

  it('lehnt Ganz-Seiten-Paste ab (Nav/Footer dominieren)', () => {
    const page = `<div>
      <a href="/explore/">Explore</a><a href="/reels/">Reels</a>
      <a href="/direct/inbox/">DM</a><a href="https://about.instagram.com/">About</a>
      <a href="/accounts/login/">Login</a><a href="/legal/terms/">Terms</a>
      ${igRow('jonas_ptzk', 'Jonas')}
    </div>`
    expect(() => parseFollowerList(page)).toThrow(/ganzen Seite/)
  })

  it('dedupliziert Profilbild- und Namens-Link derselben Row', () => {
    const result = parseFollowerList(igRow('jonas_ptzk', 'Jonas'))
    expect(result.rows).toHaveLength(1)
  })
})

describe('parseFollowerList — Plain-Text', () => {
  it('paart Username/Display-Name und verwirft den Search-Header', () => {
    const result = parseFollowerList(
      'Search\njonas_ptzk\nJonas\nnatalie.slr\n𝐧𝐚𝐭𝐚𝐥𝐢𝐞\nnina_a.s.l\nN I N A\n',
    )
    expect(result.format).toBe('text')
    expect(result.rows).toEqual([
      { username: 'jonas_ptzk', displayName: 'Jonas', ambiguous: false },
      { username: 'natalie.slr', displayName: '𝐧𝐚𝐭𝐚𝐥𝐢𝐞', ambiguous: false },
      { username: 'nina_a.s.l', displayName: 'N I N A', ambiguous: false },
    ])
  })

  it('flaggt Username-artige Zeilen nach Display-Name-losen Rows als ambig', () => {
    // Echter Fall: 'peter' ist Display-Name von petologie, sieht aber wie ein
    // Username aus — nicht entscheidbar, also Row + Flag für den Preview.
    const result = parseFollowerList('katharina.bzl\nKatharina\npetologie\npeter\nnatalie.slr')
    expect(result.rows).toEqual([
      { username: 'katharina.bzl', displayName: 'Katharina', ambiguous: false },
      { username: 'petologie', displayName: null, ambiguous: false },
      { username: 'peter', displayName: null, ambiguous: true },
      { username: 'natalie.slr', displayName: null, ambiguous: true },
    ])
  })

  it('mergeIntoPrevious macht aus einer ambigen Row den Display-Name davor', () => {
    const { rows } = parseFollowerList('petologie\npeter')
    const merged = mergeIntoPrevious(rows, 1)
    expect(merged).toEqual([
      { username: 'petologie', displayName: 'peter', ambiguous: false },
    ])
  })

  it('lehnt Text ohne erkennbare Usernames ab', () => {
    expect(() => parseFollowerList('Nur Fließtext\nOhne Usernames'))
      .toThrow(/Keine Usernames/)
  })
})
