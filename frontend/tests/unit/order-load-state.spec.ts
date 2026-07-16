import { describe, expect, it } from 'vitest';

import { resolveOrderLoadFailure } from '../../src/pages/orders/orderLoadState';

describe('order load failure state', () => {
  it('keeps authentication failures separate from service failures', () => {
    expect(resolveOrderLoadFailure({ statusCode: 401 }, 'fallback')).toEqual({
      authRequired: true,
      message: '',
    });
    expect(resolveOrderLoadFailure({ statusCode: 403 }, 'fallback')).toEqual({
      authRequired: true,
      message: '',
    });
  });

  it('shows runtime failures instead of presenting an empty gallery', () => {
    expect(resolveOrderLoadFailure(
      { statusCode: 503, message: 'This deployment is not ready to serve application requests.' },
      'Gallery is temporarily unavailable.',
    )).toEqual({
      authRequired: false,
      message: 'This deployment is not ready to serve application requests.',
    });
  });

  it('uses the localized fallback when the failure has no message', () => {
    expect(resolveOrderLoadFailure({}, 'Gallery is temporarily unavailable.')).toEqual({
      authRequired: false,
      message: 'Gallery is temporarily unavailable.',
    });
  });
});
