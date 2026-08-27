import { describe, expect, it } from 'vitest';

import {
  buildBillingReturnPath,
  creditPackageDisplayName,
  filterCreditPackages,
  readBillingIntent,
  retentionDescription,
  sanitizeLoginNextPath,
  subscriptionDisplayName,
} from '../../src/utils/billingDisplay';

describe('billing display boundary', () => {
  it('keeps subscription products out of one-time credit packs', () => {
    const products = [
      { id: 'pack_50', product_kind: 'credit_pack' },
      { id: 'pack_120', product_kind: 'credit_pack' },
      { id: 'creator_monthly', product_kind: 'subscription' },
    ];

    expect(filterCreditPackages(products).map((item) => item.id)).toEqual([
      'pack_50',
      'pack_120',
    ]);
  });

  it('never exposes internal product or retention codes as customer copy', () => {
    expect(creditPackageDisplayName({ id: 'pack_50' }, 'en')).toBe('Starter Credits');
    expect(subscriptionDisplayName('creator_monthly', 'en')).toBe('Creator Monthly');
    expect(retentionDescription('subscription_180d', 'en')).toContain('180 days');
    expect(subscriptionDisplayName('unknown_internal_code', 'en')).toBe('Monthly Subscription');
    expect(retentionDescription('unknown_internal_code', 'en')).not.toContain('unknown_internal_code');
  });

  it('preserves a local billing intent across Google sign-in', () => {
    const path = buildBillingReturnPath(
      'https://www.vowpic.com/pages/index/index?source=hero',
      'credits',
      'pack_120',
    );
    expect(path).toBe('/pages/index/index?source=hero&pricing=credits&product=pack_120');
    expect(readBillingIntent(path)).toEqual({ mode: 'credits', productCode: 'pack_120' });
    expect(sanitizeLoginNextPath(encodeURIComponent(path))).toBe(path);
  });

  it('rejects external and auth-loop login return paths', () => {
    expect(sanitizeLoginNextPath('https://evil.example')).toBe('/pages/account/index');
    expect(sanitizeLoginNextPath('//evil.example')).toBe('/pages/account/index');
    expect(sanitizeLoginNextPath('/pages/auth/login?next=/')).toBe('/pages/account/index');
  });
});
