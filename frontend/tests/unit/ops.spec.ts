import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

const { getMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
}));

vi.mock('../../src/utils/api', () => ({
  get: getMock,
}));

import { useOpsStore } from '../../src/stores/ops';

describe('operations capability fallback', () => {
  beforeEach(() => {
    getMock.mockReset();
    setActivePinia(createPinia());
  });

  it('defaults every high-risk capability to unavailable', () => {
    const store = useOpsStore();

    expect(store.googleAuthAvailable).toBe(false);
    expect(store.creationAvailable).toBe(false);
    expect(store.billingAvailable).toBe(false);
    expect(store.privateDownloadAvailable).toBe(false);
    expect(store.partnerInviteAvailable).toBe(false);
  });

  it('requires upload and generation together before creation is available', async () => {
    getMock.mockResolvedValue({
      capabilities: {
        google_auth: true,
        authenticated_upload: true,
        generation: false,
        credit_pack_checkout: false,
        subscription_billing: true,
        private_download: false,
        partner_invite: false,
      },
    });
    const store = useOpsStore();

    await store.fetchPublicConfig();

    expect(store.googleAuthAvailable).toBe(true);
    expect(store.creationAvailable).toBe(false);
    expect(store.billingAvailable).toBe(true);
  });

  it('returns to the all-off fallback when public config cannot be read', async () => {
    getMock.mockRejectedValue(new Error('runtime_not_ready'));
    const store = useOpsStore();
    store.publicConfig = {
      placements: store.publicConfig.placements,
      support: store.publicConfig.support,
      capabilities: {
        google_auth: true,
        authenticated_upload: true,
        generation: true,
        credit_pack_checkout: true,
        subscription_billing: true,
        private_download: true,
        partner_invite: true,
      },
    };

    await store.fetchPublicConfig(true);

    expect(store.googleAuthAvailable).toBe(false);
    expect(store.creationAvailable).toBe(false);
    expect(store.billingAvailable).toBe(false);
    expect(store.privateDownloadAvailable).toBe(false);
    expect(store.partnerInviteAvailable).toBe(false);
  });
});
