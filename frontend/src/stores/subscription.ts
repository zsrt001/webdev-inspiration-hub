import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { get, post } from '../utils/api';

export interface SubscriptionPlan {
  code: string;
  name: string;
  billing_interval: string;
  price_cents: number;
  currency: string;
  monthly_credits: number;
  feature_flags: Record<string, any>;
}

export interface CurrentSubscription {
  status: string;
  plan_code: string | null;
  current_period_start?: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  monthly_credits: number;
}

export interface SubscriptionCheckoutResponse {
  provider: string;
  status: string;
  checkout_url: string;
}

const DEFAULT_SUBSCRIPTION_PLANS: SubscriptionPlan[] = [
  {
    code: 'starter_monthly',
    name: 'Starter Monthly',
    billing_interval: 'month',
    price_cents: 1900,
    currency: 'USD',
    monthly_credits: 80,
    feature_flags: { tier: 'starter' },
  },
  {
    code: 'creator_monthly',
    name: 'Creator Monthly',
    billing_interval: 'month',
    price_cents: 4900,
    currency: 'USD',
    monthly_credits: 260,
    feature_flags: { tier: 'creator' },
  },
  {
    code: 'studio_monthly',
    name: 'Studio Monthly',
    billing_interval: 'month',
    price_cents: 12900,
    currency: 'USD',
    monthly_credits: 900,
    feature_flags: { tier: 'studio', priority_generation: true },
  },
];

function defaultSubscriptionPlans(): SubscriptionPlan[] {
  return DEFAULT_SUBSCRIPTION_PLANS.map((plan) => ({
    ...plan,
    feature_flags: { ...plan.feature_flags },
  }));
}

export const useSubscriptionStore = defineStore('subscription', () => {
  const plans = ref<SubscriptionPlan[]>([]);
  const current = ref<CurrentSubscription | null>(null);
  const loading = ref(false);

  const activePlan = computed(() => {
    const code = current.value?.plan_code;
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
      plans.value = Array.isArray(res) && res.length > 0 ? res : defaultSubscriptionPlans();
    } catch {
      plans.value = defaultSubscriptionPlans();
    }
    return plans.value;
  }

  async function fetchCurrentSubscription(force = false): Promise<CurrentSubscription | null> {
    if (current.value && !force) return current.value;
    const res = await get<CurrentSubscription>('/subscriptions/me', {
      showLoading: false,
      showError: false,
    });
    current.value = res;
    return current.value;
  }

  async function startSubscriptionCheckout(
    planCode: string,
    returnUrl?: string,
  ): Promise<SubscriptionCheckoutResponse> {
    loading.value = true;
    try {
      return await post<SubscriptionCheckoutResponse>(
        '/subscriptions/checkout',
        { plan_code: planCode, return_url: returnUrl },
        { showLoading: false, showError: false },
      );
    } finally {
      loading.value = false;
    }
  }

  async function cancelSubscription(): Promise<CurrentSubscription | null> {
    loading.value = true;
    try {
      const res = await post<CurrentSubscription>('/subscriptions/cancel', {}, {
        showLoading: false,
        showError: false,
      });
      current.value = {
        ...(current.value || {
          status: res.status,
          plan_code: null,
          current_period_end: res.current_period_end,
          monthly_credits: 0,
        }),
        status: res.status,
        current_period_end: res.current_period_end,
        cancel_at_period_end: res.cancel_at_period_end,
      };
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
