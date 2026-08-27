export type BillingMode = 'credits' | 'subscription';

export interface CreditPackageDisplaySource {
  id: string;
  product_kind?: string;
  label?: string;
}

const CREDIT_PACKAGE_NAMES: Record<string, { zh: string; en: string }> = {
  pack_50: { zh: '入门积分包', en: 'Starter Credits' },
  pack_120: { zh: '进阶积分包', en: 'Popular Credits' },
  pack_300: { zh: '专业积分包', en: 'Premium Credits' },
};

const SUBSCRIPTION_NAMES: Record<string, { zh: string; en: string }> = {
  starter_monthly: { zh: '入门月度订阅', en: 'Starter Monthly' },
  creator_monthly: { zh: '创作者月度订阅', en: 'Creator Monthly' },
  studio_monthly: { zh: '工作室月度订阅', en: 'Studio Monthly' },
};

export function filterCreditPackages<T extends CreditPackageDisplaySource>(items: T[]): T[] {
  return items.filter((item) => {
    if (item.product_kind) return item.product_kind === 'credit_pack';
    return Object.prototype.hasOwnProperty.call(CREDIT_PACKAGE_NAMES, item.id);
  });
}

export function creditPackageDisplayName(
  item: CreditPackageDisplaySource,
  locale: string,
): string {
  const names = CREDIT_PACKAGE_NAMES[item.id];
  if (names) return locale === 'zh' ? names.zh : names.en;
  return locale === 'zh' ? '积分包' : 'Credit Pack';
}

export function subscriptionDisplayName(code: string, locale: string): string {
  const names = SUBSCRIPTION_NAMES[code];
  if (names) return locale === 'zh' ? names.zh : names.en;
  return locale === 'zh' ? '月度订阅' : 'Monthly Subscription';
}

export function retentionDescription(retentionTier: string, locale: string): string {
  const daysByTier: Record<string, number> = {
    paid_90d: 90,
    subscription_180d: 180,
    studio_365d: 365,
  };
  const days = daysByTier[retentionTier];
  if (!days) {
    return locale === 'zh'
      ? '私密文件保留期以隐私政策为准'
      : 'Private-file retention follows the Privacy Policy';
  }
  return locale === 'zh'
    ? `私密文件计划保留最长 ${days} 天`
    : `Private files scheduled for retention for up to ${days} days`;
}

export function buildBillingReturnPath(
  currentUrl: string,
  mode: BillingMode,
  productCode: string,
): string {
  const parsed = new URL(currentUrl, 'https://www.vowpic.com');
  const pathname = parsed.pathname === '/' ? '/pages/index/index' : parsed.pathname;
  const params = new URLSearchParams(parsed.search);
  ['payment', 'purchase_id', 'checkout_id', 'subscription', 'plan_code'].forEach((key) => {
    params.delete(key);
  });
  params.set('pricing', mode);
  if (productCode) params.set('product', productCode);
  return `${pathname}?${params.toString()}`;
}

export function readBillingIntent(url: string): {
  mode: BillingMode;
  productCode: string;
} | null {
  const parsed = new URL(url, 'https://www.vowpic.com');
  const mode = parsed.searchParams.get('pricing');
  if (mode !== 'credits' && mode !== 'subscription') return null;
  const productCode = String(parsed.searchParams.get('product') || '').trim();
  if (productCode && !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(productCode)) return null;
  return { mode, productCode };
}

export function sanitizeLoginNextPath(value: string | null | undefined): string {
  const fallback = '/pages/account/index';
  let path = String(value || '').trim();
  try {
    path = decodeURIComponent(path);
  } catch {
    return fallback;
  }
  if (
    !path.startsWith('/')
    || path.startsWith('//')
    || path.includes('\\')
    || /[\u0000-\u001f\u007f]/.test(path)
    || path === '/api'
    || path.startsWith('/api/')
    || path === '/auth'
    || path.startsWith('/auth/')
    || path.startsWith('/pages/auth/')
    || path.length > 512
  ) {
    return fallback;
  }
  return path;
}
