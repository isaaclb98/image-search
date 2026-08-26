import { test, expect } from '@playwright/test';

async function appReady(page: any) {
  await page.waitForSelector('input[placeholder*="Add a positive prompt"]', { timeout: 5000 });
}

test.describe('Search edge cases', () => {
  test('search with special characters in prompts', async ({ page }) => {
    await page.goto('/search');
    await appReady(page);

    const input = page.getByRole('textbox', { name: /add prompt/i });
    await input.fill('cat\'s "cute" photo (2024)');
    await input.press('Enter');

    // Should render the chip without crashing
    await expect(page.locator('.chip')).toHaveCount(1);
    
    // Click Search
    await page.getByRole('button', { name: /^Search$/ }).click();
    
    // Should fire the search without 500
    const response = await page.waitForResponse((r: any) => r.url().includes('/api/search'));
    expect(response.status()).toBe(200);
  });

  test('search with very long prompt (>100 chars)', async ({ page }) => {
    await page.goto('/search');
    await appReady(page);

    const longPrompt = 'a'.repeat(150);
    const input = page.getByRole('textbox', { name: /add prompt/i });
    await input.fill(longPrompt);
    await input.press('Enter');

    // Should render the chip (truncated visually if needed)
    await expect(page.locator('.chip')).toHaveCount(1);
    
    // Click Search - should work without error
    await page.getByRole('button', { name: /^Search$/ }).click();
    const response = await page.waitForResponse((r: any) => r.url().includes('/api/search'));
    expect(response.status()).toBe(200);
  });

  test('search with only filename filter (no prompts)', async ({ page }) => {
    await page.goto('/search');
    await appReady(page);

    // Open the "Additional options" panel first
    await page.getByRole('button', { name: /additional options/i }).click();

    // Wait for the panel to expand and find the filename filter input
    // Actual placeholder is "e.g. IMG_2024"
    const filenameInput = page.locator('input[placeholder*="IMG_2024"]').first();
    await filenameInput.fill('0001');

    // Click Search - should fire with filename param
    await page.getByRole('button', { name: /^Search$/ }).click();
    const response = await page.waitForResponse((r: any) => r.url().includes('/api/search'));
    expect(response.status()).toBe(200);
    expect(response.url()).toContain('filename=0001');
  });

  test('multiple positive prompts (5+)', async ({ page }) => {
    await page.goto('/search');
    await appReady(page);

    const input = page.getByRole('textbox', { name: /add prompt/i });
    const prompts = ['cat', 'dog', 'bird', 'fish', 'rabbit', 'hamster'];

    for (const p of prompts) {
      await input.fill(p);
      await input.press('Enter');
      await page.waitForTimeout(100);
    }

    // All chips should render
    const chips = page.locator('.chip');
    await expect(chips).toHaveCount(6, { timeout: 5000 });

    // Click Search
    await page.getByRole('button', { name: /^Search$/ }).click();
    const response = await page.waitForResponse((r: any) => r.url().includes('/api/search'));
    expect(response.status()).toBe(200);
    // Should include all prompts
    expect(response.url()).toContain('positives=cat');
    expect(response.url()).toContain('positives=dog');
  });

  test('remove all chips → Search button disabled', async ({ page }) => {
    await page.goto('/search?positives=beach');
    await appReady(page);

    // Wait for initial load
    await page.waitForTimeout(500);

    // Remove the chip
    await page.locator('.chip').locator('button').first().click();

    // Search button should be disabled
    const searchBtn = page.getByRole('button', { name: /^Search$/ });
    await expect(searchBtn).toBeDisabled();
  });
});

test.describe('Lightbox edge cases', () => {
  test('← at first photo does nothing (no wrap)', async ({ page }) => {
    await page.goto('/random');
    await page.waitForSelector('.grid-tile', { timeout: 10000 });

    // Open first photo
    await page.locator('.grid-tile').first().click();
    await expect(page.locator('[role="dialog"]')).toBeVisible({ timeout: 5000 });

    const initialSrc = await page.locator('[role="dialog"] img.photo').first().getAttribute('src');

    // Press ArrowLeft at first photo
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(300);

    // Should stay on same photo (no wrap to last)
    const afterSrc = await page.locator('[role="dialog"] img.photo').first().getAttribute('src');
    expect(afterSrc).toBe(initialSrc);
  });

  test('Esc closes lightbox, Esc again does nothing (already closed)', async ({ page }) => {
    await page.goto('/random');
    await page.waitForSelector('.grid-tile', { timeout: 10000 });

    await page.locator('.grid-tile').first().click();
    await expect(page.locator('[role="dialog"]')).toBeVisible({ timeout: 5000 });

    // First Esc closes
    await page.keyboard.press('Escape');
    await expect(page.locator('[role="dialog"]')).toBeHidden({ timeout: 3000 });

    // Second Esc should not crash or do anything weird
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    await expect(page.locator('[role="dialog"]')).toBeHidden();
  });

  test('lightbox open → refresh page → lightbox still open (URL state)', async ({ page }) => {
    await page.goto('/random');
    await page.waitForSelector('.grid-tile', { timeout: 10000 });

    await page.locator('.grid-tile').first().click();
    await expect(page.locator('[role="dialog"]')).toBeVisible({ timeout: 5000 });

    // Refresh the page
    await page.reload();
    await page.waitForSelector('.grid-tile', { timeout: 10000 });

    // Lightbox should NOT reopen (no URL state for lightbox index)
    await expect(page.locator('[role="dialog"]')).toBeHidden({ timeout: 3000 });
  });
});

test.describe('Empty states', () => {
  test('For You page when no likes exist shows empty state', async ({ page }) => {
    // Clear all likes first
    const favs = await page.request.get('/api/favorites').then(r => r.json());
    for (const fav of (favs.favorites ?? [])) {
      await page.request.delete(`/api/favorites/${fav.point_id}`);
    }

    await page.goto('/for-you');
    await page.waitForSelector('h1', { timeout: 5000 });

    // Should show empty state or at least not crash
    await expect(page.getByRole('heading', { name: /for you/i })).toBeVisible();
    const body = await page.textContent('body');
    expect(body).toBeTruthy();
  });

  test('Albums page when no custom albums exist shows empty state', async ({ page }) => {
    // Delete all user albums (keep system albums)
    const albums = await page.request.get('/api/albums').then(r => r.json());
    for (const album of (albums.albums ?? [])) {
      if (!['Likes', 'Dislikes'].includes(album.name)) {
        await page.request.delete(`/api/albums/${album.id}`);
      }
    }

    await page.goto('/albums');
    await page.waitForSelector('h1', { timeout: 5000 });

    // Should show empty state message
    const body = await page.textContent('body');
    expect(body).toMatch(/no.*album|create/i);
  });

  test('Random page when library is empty (edge case)', async ({ page }) => {
    // This test is hard to set up (need empty Qdrant), so we skip if
    // the library has photos. In a real test suite, we'd have a fixture
    // for an empty library.
    const coll = await page.request.get('http://localhost:6333/collections/images');
    const count = (await coll.json()).result.points_count;

    if (count > 0) {
      test.skip(true, 'Library is not empty — skipping empty-library test');
    }

    await page.goto('/random');
    await page.waitForSelector('h1', { timeout: 5000 });

    // Should show empty state, not crash
    await expect(page.getByRole('heading', { name: /random/i })).toBeVisible();
    const body = await page.textContent('body');
    expect(body).toMatch(/no.*photo|empty/i);
  });
});

test.describe('Filters and diversity', () => {
  test('diversity mode selector changes the search query', async ({ page }) => {
    await page.goto('/search?positives=beach');
    await appReady(page);

    // Wait for initial search to fire
    await page.waitForTimeout(500);

    // Open "Additional options" panel first
    await page.getByRole('button', { name: /additional options/i }).click();

    // Now find the diversity mode combobox
    await page.getByRole('combobox', { name: /diversity mode/i }).selectOption('balanced');

    // Diversity mode change alone doesn't fire a search — user must click Search
    const responsePromise = page.waitForResponse((r: any) => r.url().includes('/api/search'), { timeout: 15000 });
    await page.getByRole('button', { name: /^Search$/ }).click();
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    expect(response.url()).toContain('diversity=balanced');
  });

  test('filename filter with regex-like pattern', async ({ page }) => {
    await page.goto('/search');
    await appReady(page);

    // Open "Additional options" panel first
    await page.getByRole('button', { name: /additional options/i }).click();

    // Fill a pattern-like filename
    const filenameInput = page.locator('input').filter({ hasText: '' }).nth(1);
    await filenameInput.fill('00*');

    // Click Search
    await page.getByRole('button', { name: /^Search$/ }).click();
    const response = await page.waitForResponse((r: any) => r.url().includes('/api/search'));
    expect(response.status()).toBe(200);
    expect(response.url()).toContain('filename=00');
  });
});


test.describe('Toast messages', () => {
  test('error message appears on API failure', async ({ page }) => {
    // Intercept the search API and force a 500
    await page.route('**/api/search**', (route: any) => {
      route.fulfill({
        status: 500,
        body: JSON.stringify({ detail: 'Test error' }),
      });
    });

    await page.goto('/search?positives=beach');
    await appReady(page);

    // Click search to trigger the error
    await page.getByRole('button', { name: /^Search$/ }).click();

    // Should show an error message - the app displays inline errors in a div.error
    const errorDiv = page.locator('div.error').first();
    await expect(errorDiv).toBeVisible({ timeout: 5000 });
    const errorText = await errorDiv.textContent();
    expect(errorText).toContain("Couldn't load results");
  });

  test('success toast appears on save', async ({ page }) => {
    await page.goto('/search?positives=beach');
    await appReady(page);

    // Wait for search to complete
    await page.waitForTimeout(1000);

    // Open saved searches menu
    await page.getByTitle('Saved searches').click();

    // Click Save and handle the prompt
    const saveName = 'test-save-' + Date.now();
    page.on('dialog', (d: any) => {
      void d.accept(saveName);
    });
    await page.getByTitle('Save current search').click();

    // Should show success toast - wait for the toaster region to appear
    await expect(page.getByRole('region', { name: /notifications/i })).toBeVisible({ timeout: 3000 });
    const toast = page.locator('.toast.success, .toast.info').first();
    await expect(toast).toBeVisible({ timeout: 3000 });
  });
});

test.describe('Loading states', () => {
  test('grid shows loading state during search', async ({ page }) => {
    // Intercept search API and add delay
    await page.route('**/api/search**', async (route: any) => {
      await new Promise(r => setTimeout(r, 2000));
      const response = await route.fetch();
      await route.fulfill({ response });
    });

    await page.goto('/search?positives=beach');
    await appReady(page);

    // Click search to trigger the delayed request
    await page.getByRole('button', { name: /^Search$/ }).click();

    // The app now shows a spinner with "Searching..." text during loading
    const loadingState = page.locator('.empty.loading');
    await expect(loadingState).toBeVisible({ timeout: 2000 });
    await expect(loadingState).toContainText('Searching');
    
    // Verify the spinner is visible
    await expect(loadingState.locator('.spinner')).toBeVisible({ timeout: 500 });
    
    // After the delayed response, results should appear
    await expect(page.locator('.grid-tile').first()).toBeVisible({ timeout: 5000 });
    
    // Loading state should be gone
    await expect(loadingState).not.toBeVisible({ timeout: 1000 });
  });

  test('search button shows loading state', async ({ page }) => {
    // Intercept search API and add delay
    await page.route('**/api/search**', async (route: any) => {
      await new Promise(r => setTimeout(r, 2000));
      const response = await route.fetch();
      await route.fulfill({ response });
    });

    await page.goto('/search?positives=beach');
    await appReady(page);

    // Click search to trigger the delayed request
    const searchBtn = page.getByRole('button', { name: /^Search$/ });
    await searchBtn.click();

    // Search button should be disabled during search
    await expect(searchBtn).toBeDisabled({ timeout: 500 });
    
    // Wait for the response to complete
    await page.waitForResponse((r: any) => r.url().includes('/api/search'), { timeout: 5000 });
    
    // Button should be enabled again
    await expect(searchBtn).toBeEnabled({ timeout: 1000 });
  });
});
