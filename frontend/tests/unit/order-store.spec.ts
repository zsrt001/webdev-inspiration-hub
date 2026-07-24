import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

import type { OrderRead } from '../../src/contracts/order';

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock('../../src/utils/api', () => ({
  get: getMock,
  post: postMock,
}));

import { useOrderStore } from '../../src/stores/order';

function order(status: OrderRead['status']): OrderRead {
  return {
    id: '00000000-0000-4000-8000-000000000001',
    user_id: '00000000-0000-4000-8000-000000000002',
    status,
    template_id: 'solo-korean',
    assets: [],
    can_download: false,
    settlement_status: status === 'UNKNOWN_EXTERNAL_STATE' ? 'RECONCILING' : 'NOT_CHARGED',
    delivery_status: 'PENDING',
    price_cents: 0,
    created_at: '2026-07-23T00:00:00Z',
    updated_at: '2026-07-23T00:00:00Z',
  };
}

describe('order store settlement refresh', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getMock.mockReset();
    postMock.mockReset();
    setActivePinia(createPinia());
  });

  it('fetches a terminal order before deciding not to resume polling', async () => {
    const store = useOrderStore();
    store.currentOrder = order('UNKNOWN_EXTERNAL_STATE');
    getMock.mockResolvedValue(order('READY'));

    await expect(store.refreshOrder(store.currentOrder.id)).resolves.toMatchObject({
      status: 'READY',
    });

    expect(getMock).toHaveBeenCalledWith(
      '/orders/00000000-0000-4000-8000-000000000001',
      { showLoading: false, showError: false },
    );
    expect(vi.getTimerCount()).toBe(0);
  });

  it('resumes polling only when the refreshed order is still active', async () => {
    const store = useOrderStore();
    store.currentOrder = order('UNKNOWN_EXTERNAL_STATE');
    getMock.mockResolvedValue(order('GENERATING'));

    await store.refreshOrder(store.currentOrder.id);

    expect(store.currentOrder.status).toBe('GENERATING');
    expect(vi.getTimerCount()).toBe(1);
    store.stopPolling();
  });
});
