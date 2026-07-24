import { expect, type Page, type Request } from '@playwright/test';

export type UiGenerationMode = 'single' | 'couple_local' | 'golden_anniversary';

export interface UiMainJourneyInput {
  templateId: string;
  assetPaths: string[];
  styleText: string;
  mode: UiGenerationMode;
  timeout: number;
}

export interface UiMainJourneyResult {
  orderId: string;
  idempotencyKey: string;
  uploadedAssetIds: string[];
}

function apiPath(request: Request): string {
  return new URL(request.url()).pathname;
}

async function acceptOptionalQualityWarning(page: Page): Promise<void> {
  const confirm = page.getByText(/^(Continue|继续生成)$/, { exact: true }).last();
  if (await confirm.isVisible({ timeout: 1_500 }).catch(() => false)) {
    await confirm.click();
  }
}

/**
 * Drives the real customer UI instead of bypassing it with request helpers.
 * The caller must establish the ordinary Google Cookie session first.
 */
export async function runUiMainJourney(
  page: Page,
  input: UiMainJourneyInput,
): Promise<UiMainJourneyResult> {
  expect(input.assetPaths).toHaveLength(input.mode === 'single' ? 1 : 2);
  expect(input.timeout).toBeGreaterThan(0);

  const uploads: Request[] = [];
  let createRequest: Request | null = null;
  const observeRequest = (request: Request) => {
    const path = apiPath(request);
    if (request.method() === 'POST' && path === '/api/v1/media/uploads') {
      uploads.push(request);
    }
    if (request.method() === 'POST' && path === '/api/v1/orders/create') {
      createRequest = request;
    }
  };
  page.on('request', observeRequest);

  try {
    const query = new URLSearchParams({
      mode: input.mode,
      id: input.templateId,
    });
    await page.goto(`/pages/create/index?${query.toString()}`);
    await expect(page.locator('.create-page')).toBeVisible();
    await expect(page.locator('.capability-unavailable')).toHaveCount(0);
    await expect(page.locator('.style-card.active')).toHaveCount(1);

    for (const [index, assetPath] of input.assetPaths.entries()) {
      const chooserPromise = page.waitForEvent('filechooser');
      await page.locator('.empty-box.primary-empty').first().click();
      const chooser = await chooserPromise;
      await chooser.setFiles(assetPath);
      await acceptOptionalQualityWarning(page);
      await expect(page.locator('.preview-box')).toHaveCount(index + 1);
    }
    await expect(page.locator('.preview-box')).toHaveCount(input.assetPaths.length);

    await page.locator('.text-area.large textarea, textarea.text-area.large').fill(
      input.styleText,
    );
    await page.locator('.consent-toggle').click();
    await expect(page.locator('.consent-toggle')).toHaveClass(/checked/);

    const submit = page.locator('.create-btn');
    await expect(submit).toBeEnabled();
    await submit.click();
    await expect(
      page.getByText(/^(Confirm Generation|确认生成)$/, { exact: true }),
    ).toBeVisible();

    const acceptedResponsePromise = page.waitForResponse(
      (response) => (
        response.request().method() === 'POST'
        && new URL(response.url()).pathname === '/api/v1/orders/create'
        && response.status() === 202
      ),
      { timeout: input.timeout },
    );
    await page.getByText(/^(Generate|开始生成)$/, { exact: true }).last().click();
    const acceptedResponse = await acceptedResponsePromise;
    const accepted = await acceptedResponse.json() as {
      order_id?: unknown;
      status?: unknown;
      status_url?: unknown;
    };
    const orderId = String(accepted.order_id || '');
    expect(orderId).toMatch(/^[0-9a-f-]{36}$/);
    expect(accepted.status).toBe('QUEUED');
    expect(accepted.status_url).toBe(`/api/v1/orders/${orderId}`);

    expect(uploads).toHaveLength(input.assetPaths.length);
    for (const upload of uploads) {
      expect(apiPath(upload)).toBe('/api/v1/media/uploads');
      expect(upload.headers().authorization).toBeUndefined();
    }

    expect(createRequest).not.toBeNull();
    const create = createRequest as unknown as Request;
    const idempotencyKey = String(create.headers()['idempotency-key'] || '');
    expect(idempotencyKey).toMatch(/^order-create-[A-Za-z0-9-]+$/);
    expect(create.headers().authorization).toBeUndefined();
    const body = create.postDataJSON() as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual([
      'asset_ids',
      'director_mode',
      'global_style_text',
      'legal_accepted',
      'template_id',
    ]);
    expect(body).toMatchObject({
      template_id: input.templateId,
      legal_accepted: true,
      director_mode: false,
      global_style_text: input.styleText.trim(),
    });
    const uploadedAssetIds = Array.isArray(body.asset_ids)
      ? body.asset_ids.map(String)
      : [];
    expect(uploadedAssetIds).toHaveLength(input.assetPaths.length);
    expect(uploadedAssetIds.every((assetId) => /^[0-9a-f-]{36}$/.test(assetId))).toBe(true);

    await page.waitForURL(
      (url) => (
        url.pathname === '/pages/preview/preview'
        && url.searchParams.get('id') === orderId
      ),
      { timeout: input.timeout },
    );
    await expect(page.locator('.exhibition-tag.hd')).toBeVisible({
      timeout: input.timeout,
    });
    const renderedMaster = page.locator('.folio-frame img').first();
    await expect(renderedMaster).toBeVisible();
    await expect(renderedMaster).toHaveAttribute(
      'src',
      new RegExp(`/api/v1/orders/${orderId}/assets/[0-9a-f-]{36}/download$`),
    );

    const downloadPromise = page.waitForEvent('download', { timeout: input.timeout });
    await page.locator('.exhibition-actions .e-action-btn.primary').click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.(jpg|jpeg|png|webp)$/i);
    expect(await download.failure()).toBeNull();
    expect(await download.path()).toBeTruthy();

    return { orderId, idempotencyKey, uploadedAssetIds };
  } finally {
    page.off('request', observeRequest);
  }
}
