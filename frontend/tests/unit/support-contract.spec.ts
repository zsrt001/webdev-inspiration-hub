import { describe, expect, it } from 'vitest';

import { normalizeSupportConfig, supportHref } from '../../src/utils/support';


describe('public support contract', () => {
  it('fails closed when the API does not confirm availability', () => {
    const support = normalizeSupportConfig({
      available: false,
      email: 'support@example.com',
      url: 'https://support.example.com/tickets',
    });

    expect(support).toEqual({ available: false, email: '', url: '' });
    expect(supportHref(support)).toBe('');
  });

  it('accepts a confirmed public email and creates a mailto link', () => {
    const support = normalizeSupportConfig({
      available: true,
      email: 'Support@Example.com',
      url: '',
    });

    expect(support).toEqual({ available: true, email: 'Support@example.com', url: '' });
    expect(supportHref(support)).toBe('mailto:Support@example.com');
  });

  it('prefers a confirmed HTTPS support URL', () => {
    const support = normalizeSupportConfig({
      available: true,
      email: 'support@example.com',
      url: 'https://support.example.com/tickets?source=vowpic',
    });

    expect(supportHref(support)).toBe('https://support.example.com/tickets?source=vowpic');
  });

  it.each([
    'http://support.example.com/tickets',
    'javascript:alert(1)',
    'https://user:secret@support.example.com/tickets',
    'https://localhost/tickets',
    'https://support.example.com/tickets#token',
  ])('rejects an unsafe support URL: %s', (url) => {
    const support = normalizeSupportConfig({ available: true, email: '', url });

    expect(support).toEqual({ available: false, email: '', url: '' });
  });
});
