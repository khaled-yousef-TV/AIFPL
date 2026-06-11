import { test, expect, Page } from '@playwright/test'

// The known FPL team id used across the repo's check_* scripts.
const TEST_TEAM_ID = '4843814'

async function gotoHome(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Welcome to FPL Squad Suggester' })).toBeVisible()
}

// Mobile tab bar uses short labels, the desktop sidebar uses full ones; both
// are always in the DOM, so click whichever variant is actually visible.
const TAB_FULL_LABELS: Record<string, string> = {
  WC: 'Wildcard',
  TC: 'Triple Captain',
  Picks: 'Top Picks',
  Diffs: 'Differentials',
  'Free Hit': 'Free Hit of the Week',
}

async function openTab(page: Page, shortLabel: string) {
  const full = TAB_FULL_LABELS[shortLabel] ?? shortLabel
  const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  await page
    .getByRole('button', { name: new RegExp(`^(${escape(shortLabel)}|${escape(full)})$`) })
    .filter({ visible: true })
    .first()
    .click()
}

test.describe('App shell', () => {
  test('home page renders the navigation cards', async ({ page }) => {
    await gotoHome(page)
    for (const card of ['Hermes', 'Transfers', 'Wildcard', 'Free Hit of the Week', 'Triple Captain', 'Top Picks', 'Differentials', 'Tasks']) {
      await expect(page.getByRole('button', { name: new RegExp(card) }).first()).toBeVisible()
    }
  })

  test('header resolves to a gameweek label, never stuck on Loading', async ({ page }) => {
    await gotoHome(page)
    const label = page.locator('aside p').first()
    // In-season: "GW<n>". Off-season: "Season finished". Never an eternal "Loading...".
    await expect(label).toHaveText(/GW\d+|Season finished/, { timeout: 15_000 })
  })

  test('no console errors on initial load', async ({ page }) => {
    const errors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    await gotoHome(page)
    await page.waitForTimeout(2_000)
    // Expected-empty-state fetches (e.g. TC recs before the midnight job) 404 by design.
    const real = errors.filter((e) => !/404|Failed to load resource/.test(e))
    expect(real).toEqual([])
  })
})

test.describe('Hermes', () => {
  test('hermes tab shows run types and agent signals for the latest run', async ({ page }) => {
    await gotoHome(page)
    await openTab(page, 'Hermes')
    await expect(page.getByText('Ask Hermes').first()).toBeVisible()
    for (const runType of ['Weekly Briefing', 'Best Squad', 'Wildcard', 'Free Hit', 'Triple Captain', 'Differentials']) {
      // Scope to main — nav bars contain hidden spans with the same labels.
      await expect(page.locator('main').getByText(runType, { exact: true }).first()).toBeVisible()
    }
  })

  test('latest run renders agent signal rows that expand', async ({ page }) => {
    await gotoHome(page)
    await openTab(page, 'Hermes')
    const agentRow = page.getByRole('button', { name: /Game Mechanics/ })
    // Only present when a run exists; skip otherwise (fresh database).
    if ((await agentRow.count()) === 0) test.skip(true, 'no Hermes run stored yet')
    await agentRow.first().click()
    await expect(page.getByText(/Season phase|deadline/i).first()).toBeVisible()
  })

  test('never renders an empty Chip advice section', async ({ page }) => {
    await gotoHome(page)
    await openTab(page, 'Hermes')
    const heading = page.getByRole('heading', { name: 'Chip advice' })
    if ((await heading.count()) > 0) {
      // If the section exists it must have visible content under it.
      const section = heading.first().locator('..')
      const text = (await section.innerText()).replace('Chip advice', '').trim()
      expect(text.length).toBeGreaterThan(0)
    }
  })
})

test.describe('Transfers', () => {
  test('player search finds and adds a player to the squad', async ({ page }) => {
    await gotoHome(page)
    await openTab(page, 'Transfers')
    await page.evaluate(() => localStorage.clear())
    await page.reload()
    await openTab(page, 'Transfers')

    await page.getByPlaceholder(/Search player name or team/).fill('Haaland')
    const result = page.getByRole('button', { name: /Haaland/ }).first()
    await expect(result).toBeVisible()
    await result.click()
    await expect(page.getByText('Your Squad (1/15)')).toBeVisible()
  })

  test('invalid FPL team import shows an inline error, not a blocking dialog or crash', async ({ page }) => {
    await gotoHome(page)
    await openTab(page, 'Transfers')

    // A native alert() would hang the test; fail loudly instead.
    page.on('dialog', async (dialog) => {
      await dialog.dismiss()
      throw new Error(`Unexpected native dialog: ${dialog.message()}`)
    })

    await page.getByPlaceholder('Enter FPL Team ID').fill('999999999')
    await page.getByRole('button', { name: 'Import', exact: true }).click()

    await expect(page.getByRole('status')).toContainText(/No team data found|Failed to import/, { timeout: 20_000 })
    // The app shell must survive (no full-screen error page).
    await expect(page.locator('aside')).toBeVisible()
  })

  test('imports a real FPL team and gets transfer suggestions', async ({ page }) => {
    test.setTimeout(240_000) // suggestion generation runs the predictor (~2 min cold)
    await gotoHome(page)
    await openTab(page, 'Transfers')

    await page.getByPlaceholder('Enter FPL Team ID').fill(TEST_TEAM_ID)
    await page.getByRole('button', { name: 'Import', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('Successfully imported', { timeout: 30_000 })
    await expect(page.getByText('Your Squad (15/15)')).toBeVisible()

    await page.getByRole('button', { name: 'Get Suggestions' }).click()
    await expect(
      page.getByText('Transfer Suggestions').first().or(page.getByRole('alert').first())
    ).toBeVisible({ timeout: 220_000 })
  })
})

test.describe('Wildcard trajectory', () => {
  test('generate either succeeds or explains why, never a bare try-again', async ({ page }) => {
    test.setTimeout(180_000)
    await gotoHome(page)
    await openTab(page, 'WC')
    await expect(page.getByText('Wildcard Trajectory Optimizer')).toBeVisible()

    await page.getByRole('button', { name: 'Generate Trajectory' }).click()
    const outcome = page
      .getByText(/No upcoming gameweek|already in progress/)
      .or(page.getByText(/Total Predicted|Optimal Squad|Starting XI/).first())
    await expect(outcome.first()).toBeVisible({ timeout: 150_000 })
    await expect(page.getByText('Failed to generate wildcard trajectory. Please try again.')).toHaveCount(0)
  })
})

test.describe('Read-only tabs', () => {
  test('Picks, Diffs, Free Hit, TC and Tasks all render content or an honest empty state', async ({ page }) => {
    await gotoHome(page)

    await openTab(page, 'Picks')
    await expect(page.getByText('Top Goalkeepers')).toBeVisible({ timeout: 30_000 })

    await openTab(page, 'Diffs')
    await expect(page.getByText(/Differentials \(Under/)).toBeVisible({ timeout: 30_000 })

    await openTab(page, 'Free Hit')
    await expect(page.getByText(/Free Hit of the Week/).first()).toBeVisible()

    await openTab(page, 'TC')
    await expect(
      page.getByText('Triple Captain Recommendations').or(page.getByText(/No recommendations available/)).first()
    ).toBeVisible()

    await openTab(page, 'Tasks')
    await expect(page.getByText('Background Tasks').first()).toBeVisible()
  })
})
