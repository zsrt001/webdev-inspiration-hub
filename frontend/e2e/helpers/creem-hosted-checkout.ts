import { expect, type Frame, type Locator, type Page } from '@playwright/test';

import { exactKeys, requiredString, type JsonObject } from './linked-acceptance';

export interface HostedPaymentInstrument {
  card_number: string;
  expiry: string;
  cvc: string;
  cardholder_name: string;
  country_code: string;
  postal_code: string;
}

export function paymentInstrument(value: unknown): HostedPaymentInstrument {
  const object = exactKeys(
    value,
    ['card_number', 'expiry', 'cvc', 'cardholder_name', 'country_code', 'postal_code'],
    'Creem Test Mode payment instrument',
  );
  const result = {
    card_number: requiredString(object.card_number, 'card number', 32),
    expiry: requiredString(object.expiry, 'card expiry', 12),
    cvc: requiredString(object.cvc, 'card CVC', 8),
    cardholder_name: requiredString(object.cardholder_name, 'cardholder name', 128),
    country_code: requiredString(object.country_code, 'billing country', 2).toUpperCase(),
    postal_code: requiredString(object.postal_code, 'billing postal code', 24),
  };
  if (
    !/^[0-9 ]{12,24}$/.test(result.card_number)
    || !/^[0-9/ -]{4,12}$/.test(result.expiry)
    || !/^[0-9]{3,4}$/.test(result.cvc)
    || !/^[A-Z]{2}$/.test(result.country_code)
  ) {
    throw new Error('Creem Test Mode payment instrument format is invalid');
  }
  return result;
}

async function visibleLocator(page: Page, selectors: readonly string[]): Promise<Locator> {
  const frames: Frame[] = page.frames();
  for (const frame of frames) {
    for (const selector of selectors) {
      const locator = frame.locator(selector).first();
      if (await locator.isVisible({ timeout: 500 }).catch(() => false)) return locator;
    }
  }
  throw new Error('Creem checkout field was not found');
}

async function fillIfVisible(
  page: Page,
  selectors: readonly string[],
  value: string,
): Promise<void> {
  try {
    await (await visibleLocator(page, selectors)).fill(value);
  } catch (error) {
    if (String(error).includes('was not found')) return;
    throw error;
  }
}

export async function completeCreemCheckout(
  page: Page,
  checkoutUrl: unknown,
  instrumentValue: JsonObject,
  returnOrigin: string,
  timeoutSeconds: number,
): Promise<void> {
  const url = new URL(requiredString(checkoutUrl, 'Creem checkout URL', 2048));
  if (
    url.protocol !== 'https:'
    || !url.hostname.endsWith('.creem.io')
    || url.username
    || url.password
  ) {
    throw new Error('Creem checkout URL is not an exact Creem HTTPS URL');
  }
  const instrument = paymentInstrument(instrumentValue);
  await page.goto(url.toString(), { waitUntil: 'domcontentloaded' });

  await (await visibleLocator(page, [
    'input[autocomplete="cc-number"]',
    'input[name="cardNumber"]',
    'input[name="number"]',
    'input[placeholder*="1234"]',
  ])).fill(instrument.card_number);
  await (await visibleLocator(page, [
    'input[autocomplete="cc-exp"]',
    'input[name="cardExpiry"]',
    'input[name="expiry"]',
    'input[placeholder*="MM"]',
  ])).fill(instrument.expiry);
  await (await visibleLocator(page, [
    'input[autocomplete="cc-csc"]',
    'input[name="cardCvc"]',
    'input[name="cvc"]',
    'input[placeholder*="CVC"]',
  ])).fill(instrument.cvc);
  await fillIfVisible(page, [
    'input[autocomplete="cc-name"]',
    'input[name="cardholderName"]',
    'input[name="name"]',
  ], instrument.cardholder_name);
  await fillIfVisible(page, [
    'input[autocomplete="postal-code"]',
    'input[name="postalCode"]',
    'input[name="postal_code"]',
  ], instrument.postal_code);
  const country = await visibleLocator(page, [
    'select[autocomplete="country"]',
    'select[name="country"]',
    'select[name="countryCode"]',
  ]).catch(() => null);
  if (country) await country.selectOption(instrument.country_code);

  const submit = await visibleLocator(page, [
    'button[type="submit"]',
    'button:has-text("Pay")',
    'button:has-text("Subscribe")',
    'button:has-text("Complete")',
  ]);
  await expect(submit).toBeEnabled();
  await submit.click();
  await page.waitForURL(
    (candidate) => candidate.origin === returnOrigin,
    { timeout: timeoutSeconds * 1000, waitUntil: 'domcontentloaded' },
  );
}
