<template>
  <view v-if="visible" class="modal-overlay" @tap="handleClose">
    <view class="modal-content" @tap.stop>
      <view class="modal-header">
        <text class="modal-title heading-serif">{{ tr('购买积分', 'Top Up Credits') }}</text>
        <view class="close-btn" @tap="handleClose">x</view>
      </view>

      <view class="balance-display">
        <text class="balance-label">{{ tr('当前余额', 'Current balance') }}</text>
        <text class="balance-value">{{ currentBalance }} {{ tr('积分', 'credits') }}</text>
        <text v-if="currentBalance < 2" class="balance-warning">
          {{ tr('当前余额不足以发起一次生成', 'Balance is below one generation cost') }}
        </text>
      </view>

      <view v-if="processing" class="processing-state">
        <text class="processing-icon">...</text>
        <text class="processing-text">{{ processingText }}</text>
      </view>

      <view v-else-if="purchaseSuccess" class="success-state">
        <text class="success-icon">✓</text>
        <text class="success-title">{{ tr('支付完成', 'Payment completed') }}</text>
        <text class="success-credits">+{{ creditsAdded }} {{ tr('积分已到账', 'credits added') }}</text>
        <text class="success-balance">{{ tr('最新余额', 'Updated balance') }}: {{ newBalance }}</text>
        <button class="btn btn-primary continue-btn" @tap="handleContinue">
          {{ tr('继续创作', 'Continue') }}
        </button>
      </view>

      <view v-else class="packages">
        <view class="billing-tabs">
          <view
            class="billing-tab"
            :class="{ active: activeBillingMode === 'credits' }"
            @tap="activeBillingMode = 'credits'"
          >
            {{ tr('积分包', 'Credit packs') }}
          </view>
          <view
            class="billing-tab"
            :class="{ active: activeBillingMode === 'subscription' }"
            @tap="activeBillingMode = 'subscription'"
          >
            {{ tr('订阅套餐', 'Subscriptions') }}
          </view>
        </view>

        <template v-if="activeBillingMode === 'credits'">
          <text class="packages-title">{{ tr('选择积分包', 'Select a package') }}</text>

          <view
            v-for="pkg in packages"
            :key="pkg.id"
            class="package-card"
            :class="{ popular: pkg.popular, selected: selectedPackage?.id === pkg.id }"
            @tap="selectPackage(pkg)"
          >
            <view v-if="pkg.popular" class="popular-badge">{{ tr('推荐', 'Popular') }}</view>
            <view class="package-copy">
              <text class="package-credits">{{ pkg.credits }} {{ tr('积分', 'credits') }}</text>
              <text class="package-rate">{{ packageRateLabel(pkg) }}</text>
            </view>
            <text class="package-price">${{ pkg.price.toFixed(2) }}</text>
          </view>

          <button class="btn btn-primary buy-btn" :disabled="!selectedPackage || !paymentConsentAccepted" @tap="handlePurchase">
            {{ selectedPackage ? tr('前往支付', 'Proceed to checkout') : tr('请选择积分包', 'Select a package') }}
          </button>
        </template>

        <template v-else>
          <text class="packages-title">{{ tr('选择订阅套餐', 'Select a subscription') }}</text>

          <view
            v-for="plan in subscriptionStore.plans"
            :key="plan.code"
            class="package-card subscription-plan-card"
            :class="{ selected: selectedPlanCode === plan.code }"
            @tap="selectedPlanCode = plan.code"
          >
            <view class="package-copy">
              <text class="package-credits">{{ plan.name }}</text>
              <text class="package-rate">{{ plan.monthly_credits }} {{ tr('积分 / 月', 'credits / month') }}</text>
            </view>
            <text class="package-price">{{ formatPlanPrice(plan) }}</text>
          </view>

          <button class="btn btn-primary buy-btn" :disabled="!selectedPlanCode || !paymentConsentAccepted" @tap="handleSubscriptionPurchase">
            {{ selectedPlanCode ? tr('开始订阅', 'Start subscription') : tr('请选择套餐', 'Select a plan') }}
          </button>
        </template>

        <view class="provider-note">
          <text>{{ tr('支付将跳转到托管结算页面。支付成功后，后端 webhook 会发放积分。', 'You will be redirected to hosted checkout. Credits are issued after webhook confirmation.') }}</text>
        </view>

        <LegalConsentInline v-model="paymentConsentAccepted" mode="payment" compact />
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import LegalConsentInline from './LegalConsentInline.vue';
import { useI18nStore } from '../stores/i18n';
import { useSubscriptionStore, type SubscriptionPlan } from '../stores/subscription';
import { get, post } from '../utils/api';

interface CreditPackage {
  id: string;
  credits: number;
  price: number;
  label: string;
  popular: boolean;
}

interface CheckoutResponse {
  purchase_id: string;
  provider: string;
  status: string;
  checkout_url: string;
}

interface PaymentStatusResponse {
  purchase_id: string;
  provider: string;
  package_id: string;
  status: string;
  completed: boolean;
  checkout_url?: string | null;
  credits_added: number;
  balance: number;
  message: string;
}

interface PendingPurchase {
  purchaseId: string;
  checkoutId?: string;
}

const PENDING_PURCHASE_KEY = 'aws_pending_credit_purchase';

const props = defineProps<{ visible: boolean }>();

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'purchase-complete', balance: number): void;
}>();

const i18nStore = useI18nStore();
const subscriptionStore = useSubscriptionStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const currentBalance = ref(0);
const packages = ref<CreditPackage[]>([]);
const selectedPackage = ref<CreditPackage | null>(null);
const selectedPlanCode = ref('');
const activeBillingMode = ref<'credits' | 'subscription'>('credits');
const processing = ref(false);
const processingText = ref('');
const purchaseSuccess = ref(false);
const creditsAdded = ref(0);
const newBalance = ref(0);
const paymentConsentAccepted = ref(false);

function isH5(): boolean {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
}

function packageRateLabel(pkg: CreditPackage): string {
  const pricePerTen = (pkg.price / Math.max(pkg.credits, 1)) * 10;
  return tr(`每 10 积分 $${pricePerTen.toFixed(2)}`, `$${pricePerTen.toFixed(2)} / 10 credits`);
}

function formatPlanPrice(plan: SubscriptionPlan): string {
  const price = (Number(plan.price_cents || 0) / 100).toFixed(2);
  const currency = plan.currency || 'USD';
  return `${currency} ${price}`;
}

function currentReturnUrl(): string | undefined {
  return isH5() ? window.location.href : undefined;
}

function readRouteParams(): { purchaseId: string; checkoutId: string; subscriptionStatus: string } {
  const result = { purchaseId: '', checkoutId: '', subscriptionStatus: '' };

  if (isH5()) {
    const url = new URL(window.location.href);
    result.purchaseId = url.searchParams.get('purchase_id') || '';
    result.checkoutId = url.searchParams.get('checkout_id') || '';
    result.subscriptionStatus = url.searchParams.get('subscription') || '';
    if (result.purchaseId || result.checkoutId || result.subscriptionStatus) return result;
  }

  const pages = getCurrentPages();
  const current = pages[pages.length - 1] as { options?: Record<string, string> } | undefined;
  const options = current?.options || {};
  result.purchaseId = options.purchase_id || '';
  result.checkoutId = options.checkout_id || '';
  result.subscriptionStatus = options.subscription || '';
  return result;
}

function clearRouteParams() {
  if (!isH5()) return;
  const url = new URL(window.location.href);
  ['payment', 'purchase_id', 'checkout_id', 'subscription', 'plan_code'].forEach((key) => {
    url.searchParams.delete(key);
  });
  window.history.replaceState({}, '', url.toString());
}

function savePendingPurchase(data: PendingPurchase) {
  uni.setStorageSync(PENDING_PURCHASE_KEY, JSON.stringify(data));
}

function readPendingPurchase(): PendingPurchase | null {
  try {
    const raw = uni.getStorageSync(PENDING_PURCHASE_KEY);
    if (!raw) return null;
    return typeof raw === 'string' ? JSON.parse(raw) as PendingPurchase : raw as PendingPurchase;
  } catch {
    return null;
  }
}

function clearPendingPurchase() {
  uni.removeStorageSync(PENDING_PURCHASE_KEY);
}

async function sleep(ms: number) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchData() {
  try {
    const [balanceRes, packagesRes] = await Promise.all([
      get<{ balance: number }>('/credits/balance', { showLoading: false, showError: false }),
      get<{ packages: CreditPackage[] }>('/credits/packages', { showLoading: false, showError: false }),
      subscriptionStore.fetchPlans(true),
    ]);
    currentBalance.value = balanceRes.balance;
    packages.value = packagesRes.packages;
    selectedPackage.value = packagesRes.packages.find((pkg) => pkg.popular) || packagesRes.packages[0] || null;
    selectedPlanCode.value = subscriptionStore.plans[1]?.code || subscriptionStore.plans[0]?.code || '';
  } catch {
    currentBalance.value = 0;
    packages.value = [];
    selectedPackage.value = null;
  }
}

async function reconcilePendingPurchase() {
  const routeParams = readRouteParams();
  if (routeParams.subscriptionStatus === 'success') {
    clearRouteParams();
    await subscriptionStore.fetchCurrentSubscription(true);
    uni.showToast({ title: tr('订阅处理中，积分到账以后会自动显示', 'Subscription is processing'), icon: 'none' });
  }

  const pending = readPendingPurchase();
  const purchaseId = routeParams.purchaseId || pending?.purchaseId || '';
  const checkoutId = routeParams.checkoutId || pending?.checkoutId || '';
  if (!purchaseId) return;

  processing.value = true;
  processingText.value = tr('正在确认支付状态...', 'Verifying payment status...');

  try {
    let status: PaymentStatusResponse | null = null;
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const query = checkoutId ? `?checkout_id=${encodeURIComponent(checkoutId)}` : '';
      status = await get<PaymentStatusResponse>(`/payments/status/${purchaseId}${query}`, {
        showLoading: false,
        showError: false,
      });
      if (status.completed || ['failed', 'expired', 'refunded', 'FAILED', 'CANCELED'].includes(status.status)) break;
      await sleep(1500);
    }

    if (!status) return;
    currentBalance.value = status.balance;
    if (status.completed) {
      purchaseSuccess.value = true;
      creditsAdded.value = status.credits_added;
      newBalance.value = status.balance;
      emit('purchase-complete', status.balance);
      clearPendingPurchase();
      clearRouteParams();
      uni.showToast({ title: tr('支付成功，积分已到账', 'Payment succeeded'), icon: 'success' });
      return;
    }
    if (['failed', 'expired', 'refunded', 'FAILED', 'CANCELED'].includes(status.status)) {
      clearPendingPurchase();
      clearRouteParams();
      uni.showToast({ title: tr('支付未完成', 'Payment was not completed'), icon: 'none' });
    }
  } catch (error: any) {
    uni.showToast({ title: error?.message || tr('支付状态确认失败', 'Payment verification failed'), icon: 'none' });
  } finally {
    processing.value = false;
  }
}

function selectPackage(pkg: CreditPackage) {
  selectedPackage.value = pkg;
}

async function handlePurchase() {
  if (!selectedPackage.value) return;
  if (!paymentConsentAccepted.value) {
    uni.showToast({ title: tr('请先同意隐私政策与服务条款', 'Accept the legal terms first'), icon: 'none' });
    return;
  }

  processing.value = true;
  processingText.value = tr('正在创建支付订单...', 'Creating checkout...');
  try {
    const response = await post<CheckoutResponse>(
      '/payments/checkout',
      { package_id: selectedPackage.value.id, return_url: currentReturnUrl() },
      { showLoading: false, showError: false },
    );
    savePendingPurchase({ purchaseId: response.purchase_id });
    if (isH5()) {
      window.location.href = response.checkout_url;
      return;
    }
    processing.value = false;
    uni.setClipboardData({ data: response.checkout_url });
  } catch (error: any) {
    processing.value = false;
    uni.showToast({ title: error?.message || tr('支付创建失败', 'Unable to start payment'), icon: 'none' });
  }
}

async function handleSubscriptionPurchase() {
  if (!selectedPlanCode.value) return;
  if (!paymentConsentAccepted.value) {
    uni.showToast({ title: tr('请先同意隐私政策与服务条款', 'Accept the legal terms first'), icon: 'none' });
    return;
  }

  processing.value = true;
  processingText.value = tr('正在创建订阅订单...', 'Creating subscription checkout...');
  try {
    const response = await subscriptionStore.startSubscriptionCheckout(selectedPlanCode.value, currentReturnUrl());
    if (isH5()) {
      window.location.href = response.checkout_url;
      return;
    }
    processing.value = false;
    uni.setClipboardData({ data: response.checkout_url });
  } catch (error: any) {
    processing.value = false;
    uni.showToast({ title: error?.message || tr('订阅创建失败', 'Unable to start subscription'), icon: 'none' });
  }
}

function handleClose() {
  if (!processing.value) emit('close');
}

function handleContinue() {
  emit('purchase-complete', newBalance.value || currentBalance.value);
  emit('close');
}

watch(
  () => props.visible,
  async (value) => {
    if (!value) return;
    purchaseSuccess.value = false;
    creditsAdded.value = 0;
    newBalance.value = 0;
    paymentConsentAccepted.value = false;
    await fetchData();
    await reconcilePendingPurchase();
  },
);

onMounted(async () => {
  await fetchData();
  await reconcilePendingPurchase();
});
</script>

<style lang="scss" scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.68);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

.modal-content {
  background: $uni-color-white;
  border-radius: 20px;
  width: 100%;
  max-width: 460px;
  max-height: 92vh;
  overflow-y: auto;
  animation: slideUp 0.25s ease-out;
}

@keyframes slideUp {
  from {
    opacity: 0;
    transform: translateY(18px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 24px;
  border-bottom: 1px solid $uni-color-border;
}

.modal-title {
  font-size: 22px;
  color: $uni-color-primary;
}

.close-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  color: $uni-text-color-muted;
}

.balance-display {
  padding: 20px 24px;
  background: linear-gradient(135deg, #fcf7fb 0%, #fffaf6 100%);
  text-align: center;
}

.balance-label,
.packages-title,
.provider-note,
.processing-text,
.success-balance,
.package-rate {
  color: $uni-text-color-muted;
}

.balance-label,
.packages-title {
  display: block;
  font-size: 12px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 8px;
}

.balance-value {
  display: block;
  font-size: 28px;
  font-weight: 700;
  color: $uni-color-primary;
}

.balance-warning {
  display: block;
  margin-top: 8px;
  color: #d64545;
  font-size: 12px;
}

.processing-state,
.success-state {
  padding: 44px 24px;
  text-align: center;
}

.processing-icon,
.success-icon {
  display: block;
  font-size: 42px;
  margin-bottom: 14px;
}

.success-title {
  display: block;
  font-size: 24px;
  font-weight: 700;
  color: $uni-color-primary;
  margin-bottom: 12px;
}

.success-credits {
  display: block;
  color: #2b8a57;
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 8px;
}

.continue-btn,
.buy-btn {
  width: 100%;
  margin-top: 18px;
}

.packages {
  padding: 24px;
}

.billing-tabs {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  margin-bottom: 18px;
  padding: 4px;
  border-radius: 999px;
  background: #f8f1f5;
}

.billing-tab {
  min-height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  color: $uni-text-color-muted;
  font-size: 13px;
  font-weight: 700;
}

.billing-tab.active {
  background: $uni-color-white;
  color: $uni-color-primary;
  box-shadow: 0 8px 18px rgba(131, 24, 67, 0.1);
}

.package-card {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px;
  border: 2px solid $uni-color-border;
  border-radius: 14px;
  margin-bottom: 12px;
  transition: all 0.2s ease;
}

.package-card.selected,
.package-card.popular {
  border-color: $uni-color-accent;
}

.package-card.selected {
  background: rgba(201, 169, 110, 0.08);
}

.package-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.popular-badge {
  position: absolute;
  top: -10px;
  right: 16px;
  background: $uni-color-accent;
  color: $uni-color-white;
  font-size: 10px;
  font-weight: 700;
  padding: 4px 8px;
  border-radius: 999px;
}

.package-credits {
  font-size: 18px;
  font-weight: 700;
  color: $uni-color-primary;
}

.package-price {
  font-size: 22px;
  font-weight: 700;
  color: $uni-color-accent;
}

.buy-btn[disabled] {
  opacity: 0.5;
}

.provider-note {
  display: block;
  margin-top: 14px;
  font-size: 12px;
  line-height: 1.6;
  text-align: center;
}

@media (max-width: 480px) {
  .package-card {
    align-items: flex-start;
    flex-direction: column;
  }

  .package-price {
    font-size: 20px;
  }
}
</style>
