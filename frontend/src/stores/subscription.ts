import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { get, post } from '../utils/api';

export interface SubscriptionPlan {
  code: string;
  pre_tax_minor_units: number;
  currency: 'USD';
  credits: number;
  retention_tier: string;
  display_price: string;
}

export interface CurrentSubscription {
  subscription_id: string | null;
  status: 'NONE' | 'PENDING' | 'ACTIVE' | 'PAST_DUE' | 'CANCEL_REQUESTED' | 'CANCELED' | 'EXPIRED';
  product_code: string | null;
  current_period_start: string | null;
  current_period_end: string | null;
  paid_through_at: string | null;
  cancel_at_period_end: boolean;
  credits_per_paid_period: number;
}

export interface SubscriptionCheckoutResponse {
  provider: string;
  status: string;
  checkout_url: string;
}

export const useSubscriptionStore = defineStore('subscription', () => {
  const plans = ref<SubscriptionPlan[]>([]);
  const current = ref<CurrentSubscription | null>(null);
  const loading = ref(false);
  const checkoutKeys = new Map<string, string>();
  let cancelKey: string | null = null;

  function newIdempotencyKey(prefix: string): string {
    const randomUUID = globalThis.crypto?.randomUUID?.bind(globalThis.crypto);
    const suffix = randomUUID
      ? randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    return `${prefix}-${suffix}`;
  }

  const activePlan = computed(() => {
    const code = current.value?.product_code;
    if (!code) return null;
    return plans.value.find((plan) => plan.code === code) || null;
  });

  async function fetchPlans(force = false): Promise<SubscriptionPlan[]> {
    if (plans.value.length > 0 && !force) return plans.value;
    try {
      const res = await get<SubscriptionPlan[]>('/subscriptions/plans', {
        showLoading: false,
        showError: false,
      });
      plans.value = Array.isArray(res) ? res : [];
    } catch {
      plans.value = [];
    }
    return plans.value;
  }

  async function fetchCurrentSubscription(force = false): Promise<CurrentSubscription | null> {
    if (current.value && !force) return current.value;
    try {
      const res = await get<CurrentSubscription>('/subscriptions/me', {
        showLoading: false,
        showError: false,
      });
      current.value = res;
    } catch {
      current.value = null;
    }
    return current.value;
  }

  async function startSubscriptionCheckout(
    planCode: string,
    returnUrl?: string,
  ): Promise<SubscriptionCheckoutResponse> {
    loading.value = true;
    const signature = `${planCode}|${returnUrl || ''}`;
    const idempotencyKey = checkoutKeys.get(signature) || newIdempotencyKey('subscription-checkout');
    checkoutKeys.set(signature, idempotencyKey);
    try {
      const response = await post<SubscriptionCheckoutResponse>(
        '/subscriptions/checkout',
        { plan_code: planCode, return_url: returnUrl },
        {
          showLoading: false,
          showError: false,
          headers: { 'Idempotency-Key': idempotencyKey },
        },
      );
      checkoutKeys.delete(signature);
      return response;
    } finally {
      loading.value = false;
    }
  }

  async function cancelSubscription(): Promise<CurrentSubscription | null> {
    loading.value = true;
    cancelKey ||= newIdempotencyKey('subscription-cancel');
    try {
      const res = await post<{
        subscription_id: string;
        state: 'CONFIRMED';
        cancel_at_period_end: true;
      }>('/subscriptions/cancel', {}, {
        showLoading: false,
        showError: false,
        headers: { 'Idempotency-Key': cancelKey },
      });
      if (current.value?.subscription_id === res.subscription_id) {
        current.value = {
          ...current.value,
          status: 'CANCEL_REQUESTED',
          cancel_at_period_end: true,
        };
      } else {
        await fetchCurrentSubscription(true);
      }
      cancelKey = null;
      return current.value;
    } finally {
      loading.value = false;
    }
  }

  return {
    plans,
    current,
    activePlan,
    loading,
    fetchPlans,
    fetchCurrentSubscription,
    startSubscriptionCheckout,
    cancelSubscription,
  };
});
