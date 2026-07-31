import { test, expect, Page } from '@playwright/test'

// The shell is a masthead + a poster hero + one numbered run-type nav.
// The old sidebar and the mobile tab bar are gone: run types are the only
// navigation, and they carry full labels at every breakpoint (the leading
// "01"–"07" numerals are aria-hidden, so accessible names stay clean).

const RUN_VIEW_LABELS = ['Weekly Briefing', 'Best Squad', 'Wildcard', 'Free Hit', 'Triple Captain', 'Differentials']

async function gotoApp(page: Page) {
  await page.goto('/')
  // The briefing view is the default; its nav item is always present.
  await expect(page.locator('main').getByText('Weekly Briefing').first()).toBeVisible()
}

async function clickVisible(page: Page, fullLabel: string, short?: string) {
  const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const pattern = short
    ? new RegExp(`^(${escape(fullLabel)}|${escape(short)})$`)
    : new RegExp(`^${escape(fullLabel)}$`)
  await page
    .getByRole('button', { name: pattern })
    .filter({ visible: true })
    .first()
    .click()
}


test.describe('App shell', () => {
  test('navigation is the run-type nav only — no sidebar', async ({ page }) => {
    await gotoApp(page)
    // The sidebar is gone entirely.
    await expect(page.locator('aside')).toHaveCount(0)
    // Every run type is reachable from the one nav.
    for (const label of RUN_VIEW_LABELS) {
      await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible()
    }
    // Old standalone destinations stay gone.
    for (const gone of ['Tasks', 'Transfers', 'Top Picks', 'Track Record']) {
      await expect(page.getByRole('button', { name: gone, exact: true })).toHaveCount(0)
    }
  })

  test('masthead resolves to a gameweek label, never stuck on Loading', async ({ page }) => {
    await gotoApp(page)
    const clock = page.locator('.masthead-clock')
    // In-season: "GW<n> · T−…". Off-season: "Season finished". Never an eternal "Loading…".
    await expect(clock).toHaveText(/GW\d+|Season finished/, { timeout: 15_000 })
  })

  test('no console errors on initial load', async ({ page }) => {
    const errors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    await gotoApp(page)
    await page.waitForTimeout(2_000)
    // Expected-empty-state fetches (fresh database) 404 by design.
    const real = errors.filter((e) => !/404|Failed to load resource/.test(e))
    expect(real).toEqual([])
  })

  test('fonts are self-hosted — nothing fetched from a font CDN', async ({ page }) => {
    const cdn: string[] = []
    page.on('request', (r) => {
      if (/fonts\.(googleapis|gstatic)\.com/.test(r.url())) cdn.push(r.url())
    })
    await gotoApp(page)
    await page.waitForTimeout(1_000)
    expect(cdn).toEqual([])
  })

  test('hash routing deep-links into run views', async ({ page }) => {
    await page.goto('/#differentials')
    await expect(page.getByRole('button', { name: 'Differentials', exact: true })).toHaveAttribute(
      'aria-current',
      'page',
    )
    await page.goto('/#wildcard')
    await expect(page.getByRole('button', { name: 'Wildcard', exact: true })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })
})

test.describe('This Week report', () => {
  test('every run view renders a report or an honest empty state', async ({ page }) => {
    await gotoApp(page)
    for (const label of RUN_VIEW_LABELS) {
      await clickVisible(page, label)
      // Either a report plate, a running state, or the empty state.
      const outcome = page
        .locator('main')
        .getByText(/Agent signals|About this run|Hermes is thinking|Running/i)
      await expect(outcome.first()).toBeVisible({ timeout: 15_000 })
    }
  })

  test('completed run shows the poster verdict and a collapsible full report', async ({ page }) => {
    await gotoApp(page)
    // Runs arrive asynchronously — wait for the page to settle into either the
    // report plate or the empty state before deciding whether to skip.
    const evidence = page.locator('main').getByRole('heading', { name: /Agent signals/ })
    const empty = page.locator('main').getByRole('heading', { name: /About this run/ })
    await expect(evidence.or(empty).first()).toBeVisible({ timeout: 15_000 })
    // Only present when a run exists; skip otherwise (fresh database).
    if ((await evidence.count()) === 0) test.skip(true, 'no Hermes run stored yet')
    // The verdict is the poster headline — the page's only h1.
    await expect(page.locator('main h1')).toHaveCount(1)
    await expect(page.locator('main h1')).toBeVisible()
    // Narrative starts collapsed and expands.
    const fullReport = page.getByRole('button', { name: /full report/i })
    if ((await fullReport.count()) > 0) {
      await fullReport.first().click()
      await expect(page.getByRole('button', { name: /hide full report/i })).toBeVisible()
    }
  })

  test('run button reflects in-flight state instead of allowing duplicates', async ({ page }) => {
    await gotoApp(page)
    const askButton = page.getByRole('button', { name: /Ask Hermes|Hermes is thinking/ }).first()
    await expect(askButton).toBeVisible()
    // If a run is already active (e.g. nightly sweep), the button must be disabled.
    const label = await askButton.innerText()
    if (/thinking/i.test(label)) {
      await expect(askButton).toBeDisabled()
      // Switching views must NOT re-enable it.
      await clickVisible(page, 'Wildcard')
      await clickVisible(page, 'Weekly Briefing')
      await expect(page.getByRole('button', { name: /Hermes is thinking/ }).first()).toBeDisabled()
    }
  })

  test('latest run renders agent signal rows that expand', async ({ page }) => {
    await gotoApp(page)
    const agentRow = page.getByRole('button', { name: /Game Mechanics/ })
    // Only present when a run exists; skip otherwise (fresh database).
    if ((await agentRow.count()) === 0) test.skip(true, 'no Hermes run stored yet')
    await agentRow.first().click()
    await expect(page.getByText(/Season phase|deadline/i).first()).toBeVisible()
  })

  test('chips panel never renders empty', async ({ page }) => {
    await gotoApp(page)
    const heading = page.getByRole('heading', { name: 'Chips' })
    if ((await heading.count()) > 0) {
      const section = heading.first().locator('..')
      const text = (await section.innerText()).replace('Chips', '').trim()
      expect(text.length).toBeGreaterThan(0)
    }
  })

  test('the pitch survives: eleven numbered shirts, captain armband', async ({ page }) => {
    await gotoApp(page)
    const pitch = page.locator('.pitch')
    if ((await pitch.count()) === 0) test.skip(true, 'no squad in the stored run')
    await expect(pitch.locator('.dot')).toHaveCount(11)
    await expect(pitch.locator('.dot-no').first()).toHaveText('1')
    await expect(pitch.locator('.arm', { hasText: 'C' }).first()).toBeVisible()
  })
})
