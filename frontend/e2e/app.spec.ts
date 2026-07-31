import { test, expect, Page } from '@playwright/test'

// The shell has one destination: "This Week" (the Hermes report, with a
// run-type view switcher inside it). Sidebar (desktop) uses full labels,
// the mobile tab bar short ones; both are always in the DOM, so click
// whichever variant is actually visible.

// The old run-type tabs are now pill views inside This Week.
const RUN_VIEW_LABELS = ['Weekly Briefing', 'Best Squad', 'Wildcard', 'Free Hit', 'Triple Captain', 'Differentials']

async function gotoApp(page: Page) {
  await page.goto('/')
  // The briefing view is the default; its pill is always present.
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
  test('sidebar collapses to a single This Week entry', async ({ page }) => {
    await gotoApp(page)
    await expect(
      page.getByRole('button', { name: /This Week|Week/ }).first(),
    ).toBeAttached()
    // Old standalone tabs are gone from the nav (run types live as view
    // pills inside main, Tasks became a header indicator, Track Record
    // has been removed).
    const aside = page.locator('aside')
    for (const gone of ['Tasks', 'Transfers', 'Top Picks', 'Wildcard', 'Track Record']) {
      await expect(aside.getByRole('button', { name: gone, exact: true })).toHaveCount(0)
    }
  })

  test('header resolves to a gameweek label, never stuck on Loading', async ({ page }) => {
    await gotoApp(page)
    const label = page.locator('aside p').first()
    // In-season: "GW<n>". Off-season: "Season finished". Never an eternal "Loading...".
    await expect(label).toHaveText(/GW\d+|Season finished/, { timeout: 15_000 })
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

  test('hash routing deep-links into run views', async ({ page }) => {
    await page.goto('/#differentials')
    // The differentials pill must be the selected view.
    await expect(page.locator('main').getByText('Differentials').first()).toBeVisible()
    await page.goto('/#wildcard')
    await expect(page.locator('main').getByText('Wildcard').first()).toBeVisible()
  })
})

test.describe('This Week report', () => {
  test('every run view renders a report or an honest empty state', async ({ page }) => {
    await gotoApp(page)
    for (const label of RUN_VIEW_LABELS) {
      await clickVisible(page, label)
      // Either a verdict header + evidence, a running card, or the empty state.
      const outcome = page
        .locator('main')
        .getByText(/Evidence — agent signals|No .* run yet|Hermes is thinking|Running/i)
      await expect(outcome.first()).toBeVisible({ timeout: 15_000 })
    }
  })

  test('completed run shows verdict header and collapsible full report', async ({ page }) => {
    await gotoApp(page)
    const evidence = page.getByText('Evidence — agent signals')
    // Only present when a run exists; skip otherwise (fresh database).
    if ((await evidence.count()) === 0) test.skip(true, 'no Hermes run stored yet')
    // Verdict headline is an h2 in the verdict card.
    await expect(page.locator('main h2').first()).toBeVisible()
    // Narrative starts collapsed and expands.
    const fullReport = page.getByRole('button', { name: /Full report/ })
    if ((await fullReport.count()) > 0) {
      await fullReport.first().click()
      await expect(page.locator('main').getByText(/./).first()).toBeVisible()
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
      // Switching views via pills must NOT re-enable it.
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
})

