import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { AccountExport } from '../../src/services/account';


const { httpRequest } = vi.hoisted(() => ({ httpRequest: vi.fn() }));
vi.mock('../../src/services/http', () => ({ httpRequest }));

import {
  accountExportFilename,
  downloadAccountExport,
  serializeAccountExport,
} from '../../src/services/account';


function exportPayload(): AccountExport {
  return {
    schema_version: 'account-export.v1',
    export_id: '00000000-0000-4000-8000-000000000099',
    generated_at: '2026-07-19T02:00:00Z',
    canonical_user_id: '00000000-0000-4000-8000-000000000001',
    included_user_ids: ['00000000-0000-4000-8000-000000000001'],
    profile: {
      user_id: '00000000-0000-4000-8000-000000000001',
      username: 'owner',
      email: 'owner@example.test',
      nickname: 'Owner',
      role: 'user',
      status: 'active',
      last_login_at: null,
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    },
    merged_accounts: [],
    identities: [],
    orders: [],
    ledger: [],
    purchases: [],
    refunds: [],
    disputes: [],
    subscriptions: [],
    invoices: [],
    invoice_adjustments: [],
    consent_records: [],
    media: [],
    retention: [],
    audit_references: [],
  };
}

describe('account export download', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    httpRequest.mockReset();
  });

  it('uses the typed Cookie endpoint and downloads exact JSON', async () => {
    const payload = exportPayload();
    httpRequest.mockResolvedValue(payload);
    const createObjectURL = vi.fn(() => 'blob:account-export');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    const result = await downloadAccountExport();

    expect(result).toEqual(payload);
    expect(httpRequest).toHaveBeenCalledWith('/account/export', {
      method: 'GET',
      responseType: 'json',
    });
    expect(click).toHaveBeenCalledOnce();
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:account-export');
    expect(accountExportFilename(payload)).toBe(
      'vowpic-account-00000000-0000-4000-8000-000000000099.json',
    );
    expect(serializeAccountExport(payload)).toContain('"schema_version": "account-export.v1"');
  });

  it('rejects an unbound or malformed response before creating a download', async () => {
    httpRequest.mockResolvedValue({
      ...exportPayload(),
      export_id: '../../secret',
    });
    const createObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });

    await expect(downloadAccountExport()).rejects.toThrow('account export response is invalid');
    expect(createObjectURL).not.toHaveBeenCalled();
  });
});
