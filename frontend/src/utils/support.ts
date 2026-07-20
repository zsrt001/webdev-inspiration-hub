export interface PublicSupportConfig {
  available: boolean;
  email: string;
  url: string;
}

const SUPPORT_EMAIL_PATTERN =
  /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$/;
const SUPPORT_HOST_PATTERN =
  /^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$/;

function normalizeEmail(value: unknown): string {
  const candidate = String(value || '').trim();
  if (!SUPPORT_EMAIL_PATTERN.test(candidate)) return '';
  const [local, domain] = candidate.split('@');
  if (!local || !domain || local.startsWith('.') || local.endsWith('.') || local.includes('..')) return '';
  return `${local}@${domain.toLowerCase()}`;
}

function normalizeUrl(value: unknown): string {
  const candidate = String(value || '').trim();
  if (!candidate) return '';
  try {
    const parsed = new URL(candidate);
    const host = parsed.hostname.toLowerCase();
    if (
      parsed.protocol !== 'https:' ||
      !SUPPORT_HOST_PATTERN.test(host) ||
      host === 'localhost' ||
      host.endsWith('.local') ||
      parsed.username ||
      parsed.password ||
      parsed.hash ||
      (parsed.port && parsed.port !== '443')
    ) {
      return '';
    }
    return parsed.toString();
  } catch {
    return '';
  }
}

export function normalizeSupportConfig(value: Partial<PublicSupportConfig> | null | undefined): PublicSupportConfig {
  const email = normalizeEmail(value?.email);
  const url = normalizeUrl(value?.url);
  const available = value?.available === true && Boolean(email || url);
  return available
    ? { available: true, email, url }
    : { available: false, email: '', url: '' };
}

export function supportHref(value: PublicSupportConfig): string {
  const support = normalizeSupportConfig(value);
  if (!support.available) return '';
  return support.url || `mailto:${support.email}`;
}
