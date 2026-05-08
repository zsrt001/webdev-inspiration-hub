<template>
  <view v-if="visible" class="pricing-overlay" @tap="handleClose">
    <view class="pricing-dialog" @tap.stop>
      <button class="close-button" aria-label="Close" @tap="handleClose">x</button>

      <view class="pricing-header">
        <text class="pricing-title heading-serif">{{ tr('选择适合你的套餐', 'Choose Your Plan') }}</text>
        <text class="pricing-subtitle">{{ modeSummary }}</text>

        <view class="billing-tabs" role="tablist">
          <view
            class="billing-tab"
            :class="{ active: activeBillingMode === 'credits' }"
            role="tab"
            :aria-selected="activeBillingMode === 'credits'"
            @tap="activeBillingMode = 'credits'"
          >
            {{ tr('积分包', 'Credit packs') }}
          </view>
          <view
            class="billing-tab"
            :class="{ active: activeBillingMode === 'subscription' }"
            role="tab"
            :aria-selected="activeBillingMode === 'subscription'"
            @tap="activeBillingMode = 'subscription'"
          >
            {{ tr('订阅套餐', 'Subscriptions') }}
          </view>
        </view>
      </view>

      <view class="balance-strip">
        <view>
          <text class="balance-label">{{ tr('当前积分', 'Current credits') }}</text>
          <text class="balance-value">{{ currentBalance }}</text>
        </view>
        <view class="balance-meta">
          <text>{{ tr('基础生成', 'Base generation') }} {{ costPerGeneration }} {{ tr('积分起', 'credits and up') }}</text>
          <text v-if="activePlanName">{{ tr('当前订阅', 'Current subscription') }}: {{ activePlanName }}</text>
        </view>
      </view>

      <view v-if="processing" class="state-panel">
        <view class="loading-ring"></view>
        <text class="state-title">{{ processingText }}</text>
      </view>

      <view v-else-if="purchaseSuccess" class="state-panel success-panel">
        <view class="success-mark">✓</view>
        <text class="state-title">{{ tr('支付完成', 'Payment completed') }}</text>
        <text class="state-copy">+{{ creditsAdded }} {{ tr('积分已到账', 'credits added') }}</text>
        <text class="state-copy">{{ tr('最新余额', 'Updated balance') }}: {{ newBalance }}</text>
        <button class="primary-action state-action" @tap="handleContinue">
          {{ tr('继续创作', 'Continue') }}
        </button>
      </view>

      <template v-else>
        <view v-if="activeBillingMode === 'credits'" class="pricing-grid">
          <view
            v-for="pkg in packages"
            :key="pkg.id"
            class="pricing-card"
            :class="{ highlighted: pkg.popular, selected: selectedPackage?.id === pkg.id }"
            @tap="selectPackage(pkg)"
          >
            <view class="card-top">
              <view>
                <text class="plan-name">{{ packageTitle(pkg) }}</text>
                <text class="plan-caption">{{ tr('一次性购买', 'One-time purchase') }}</text>
              </view>
              <view v-if="pkg.popular" class="plan-badge">{{ tr('推荐', 'Popular') }}</view>
            </view>

            <view class="price-line">
              <text class="currency">$</text>
              <text class="price-value">{{ pkg.price.toFixed(2) }}</text>
              <text class="price-unit">USD</text>
            </view>

            <text class="plan-description">
              {{ pkg.credits }} {{ tr('积分，可用于生成预览、继续出图和高清权益', 'credits for previews, more generations, and HD access') }}
            </text>

            <button
              class="plan-button"
              :class="{ primary: pkg.popular }"
              :disabled="!paymentConsentAccepted"
              @tap.stop="handlePurchase(pkg)"
            >
              {{ tr('购买', 'Buy') }} {{ packageShortName(pkg) }}
            </button>

            <view class="feature-list">
              <view v-for="line in packageFeatureLines(pkg)" :key="line" class="feature-row">
                <view class="feature-mark"></view>
                <text>{{ line }}</text>
              </view>
            </view>
          </view>

          <view v-if="packages.length === 0" class="empty-pricing">
            <text>{{ tr('暂未配置积分包', 'No credit packs configured yet') }}</text>
          </view>
        </view>

        <view v-else class="pricing-grid">
          <view
            v-for="(plan, index) in subscriptionStore.plans"
            :key="plan.code"
            class="pricing-card"
            :class="{
              highlighted: isPlanHighlighted(plan, index),
              selected: selectedPlanCode === plan.code,
              current: isCurrentPlan(plan),
            }"
            @tap="selectedPlanCode = plan.code"
          >
            <view class="card-top">
              <view>
                <text class="plan-name">{{ planDisplayName(plan) }}</text>
                <text class="plan-caption">{{ billingIntervalLabel(plan) }}</text>
              </view>
              <view v-if="isCurrentPlan(plan)" class="plan-badge muted">{{ tr('当前', 'Current') }}</view>
              <view v-else-if="isPlanHighlighted(plan, index)" class="plan-badge">{{ tr('推荐', 'Popular') }}</view>
            </view>

            <view class="price-line">
              <text class="currency">{{ currencySymbol(plan.currency) }}</text>
              <text class="price-value">{{ priceFromCents(plan.price_cents) }}</text>
              <text class="price-unit">{{ plan.currency }} / {{ tr('月', 'mo') }}</text>
            </view>

            <text class="plan-description">
              {{ plan.monthly_credits }} {{ tr('积分 / 月，进入同一个积分余额', 'credits / month added to the same balance') }}
            </text>

            <button
              class="plan-button"
              :class="{ primary: isPlanHighlighted(plan, index) }"
              :disabled="!paymentConsentAccepted || isCurrentPlan(plan)"
              @tap.stop="handleSubscriptionPurchase(plan.code)"
            >
              {{ isCurrentPlan(plan) ? tr('你当前的套餐', 'Current plan') : tr('开通', 'Subscribe') + ' ' + planShortName(plan) }}
            </button>

            <view class="feature-list">
              <view v-for="line in planFeatureLines(plan)" :key="line" class="feature-row">
                <view class="feature-mark"></view>
                <text>{{ line }}</text>
              </view>
            </view>
          </view>

          <view v-if="subscriptionStore.plans.length === 0" class="empty-pricing">
            <text>{{ tr('暂未配置订阅套餐', 'No subscription plans configured yet') }}</text>
          </view>
        </view>

        <view class="pricing-footer">
          <LegalConsentInline v-model="paymentConsentAccepted" mode="payment" compact />
          <text class="provider-note">
            {{ tr('支付完成后通常会自动到账，如遇延迟可在账户中心查看记录。', 'Credits are usually added automatically after payment. Check your account if there is a delay.') }}
          </text>
        </view>
      </template>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
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

interface BalanceResponse {
  balance: number;
  cost_per_generation: number;
}

interface PackagesResponse {
  packages: CreditPackage[];
}

const DEFAULT_CREDIT_PACKAGES: CreditPackage[] = [
  { id: 'pack_50', credits: 50, price: 12.90, label: 'AI Wedding Starter', popular: false },
  { id: 'pack_120', credits: 120, price: 24.90, label: 'AI Wedding Popular', popular: true },
  { id: 'pack_300', credits: 300, price: 49.90, label: 'AI Wedding Premium', popular: false },
];

function defaultCreditPackages(): CreditPackage[] {
  return DEFAULT_CREDIT_PACKAGES.map((pkg) => ({ ...pkg }));
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

type BillingMode = 'credits' | 'subscription';

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
const costPerGeneration = ref(2);
const packages = ref<CreditPackage[]>([]);
const selectedPackage = ref<CreditPackage | null>(null);
const selectedPlanCode = ref('');
const activeBillingMode = ref<BillingMode>('credits');
const processing = ref(false);
const processingText = ref('');
const purchaseSuccess = ref(false);
const creditsAdded = ref(0);
const newBalance = ref(0);
const paymentConsentAccepted = ref(false);

const activePlanName = computed(() => {
  const plan = subscriptionStore.activePlan;
  return plan ? planDisplayName(plan) : '';
});

const modeSummary = computed(() => {
  if (activeBillingMode.value === 'credits') {
    return tr('一次性补充积分，适合临时生成、试片或解锁高清。', 'One-time credits for extra generations, proofing, or HD access.');
  }
  return tr('每月自动获得固定积分，适合持续创作和成套出图。', 'Monthly credits for ongoing creation and portrait sets.');
});

function isH5(): boolean {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
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

function currencySymbol(currency: string): string {
  return String(currency || 'USD').toUpperCase() === 'USD' ? '$' : '';
}

function priceFromCents(cents: number): string {
  return (Number(cents || 0) / 100).toFixed(2);
}

function generationEstimate(credits: number): string {
  const estimate = Math.max(1, Math.floor(Number(credits || 0) / Math.max(costPerGeneration.value, 1)));
  return tr(`约 ${estimate} 次基础生成`, `About ${estimate} base generations`);
}

function unitPriceLabel(amount: number, credits: number, currency = 'USD'): string {
  const perTenCredits = (Number(amount || 0) / Math.max(Number(credits || 0), 1)) * 10;
  const prefix = currencySymbol(currency);
  const suffix = String(currency || 'USD').toUpperCase() === 'USD' ? '' : ` ${currency}`;
  return `${prefix}${perTenCredits.toFixed(2)}${suffix} / ${tr('10 积分', '10 credits')}`;
}

function packageTitle(pkg: CreditPackage): string {
  const label = String(pkg.label || pkg.id).replace(/^AI Wedding\s*/i, '').trim();
  if (i18nStore.locale !== 'zh') return label || pkg.id;
  const zhNames: Record<string, string> = {
    pack_50: 'Starter 积分包',
    pack_120: 'Popular 积分包',
    pack_300: 'Premium 积分包',
  };
  return zhNames[pkg.id] || label || pkg.id;
}

function packageShortName(pkg: CreditPackage): string {
  return packageTitle(pkg).replace(/\s*积分包$/, '');
}

function packageFeatureLines(pkg: CreditPackage): string[] {
  return [
    `${pkg.credits} ${tr('积分一次性到账', 'credits added once')}`,
    unitPriceLabel(pkg.price, pkg.credits, 'USD'),
    generationEstimate(pkg.credits),
    tr('适合试风格、继续生成和高清权益', 'Good for style tests, more generations, and HD access'),
    tr('支付完成后自动加入账户', 'Added to your account after payment'),
  ];
}

function planDisplayName(plan: SubscriptionPlan): string {
  const name = String(plan.name || plan.code).replace(/\s*Monthly$/i, '').trim();
  if (i18nStore.locale !== 'zh') return name || plan.code;
  const zhNames: Record<string, string> = {
    starter_monthly: 'Starter 月度',
    creator_monthly: 'Creator 月度',
    studio_monthly: 'Studio 月度',
  };
  return zhNames[plan.code] || name || plan.code;
}

function planShortName(plan: SubscriptionPlan): string {
  return planDisplayName(plan).replace(/\s*月度$/, '');
}

function billingIntervalLabel(plan: SubscriptionPlan): string {
  return plan.billing_interval === 'year'
    ? tr('年付订阅', 'Yearly subscription')
    : tr('月付订阅', 'Monthly subscription');
}

function isCurrentPlan(plan: SubscriptionPlan): boolean {
  return subscriptionStore.current?.plan_code === plan.code
    && ['active', 'trialing', 'past_due'].includes(subscriptionStore.current?.status || '');
}

function isPlanHighlighted(plan: SubscriptionPlan, index: number): boolean {
  if (isCurrentPlan(plan)) return true;
  if (plan.code.includes('creator')) return true;
  if (!subscriptionStore.plans.some((item) => item.code.includes('creator'))) return index === 1;
  return false;
}

function planFeatureLines(plan: SubscriptionPlan): string[] {
  const flags = plan.feature_flags || {};
  const lines = [
    `${plan.monthly_credits} ${tr('积分 / 月', 'credits / month')}`,
    unitPriceLabel(Number(plan.price_cents || 0) / 100, plan.monthly_credits, plan.currency),
    generationEstimate(plan.monthly_credits),
  ];
  if (flags.remote_join) lines.push(tr('支持双人异地合拍', 'Remote couple creation included'));
  if (flags.live_portrait) lines.push(tr('适合动态人像等进阶玩法', 'Works for advanced portrait features'));
  if (flags.priority_generation) lines.push(tr('高峰期享有更高处理优先级', 'Higher processing priority during busy periods'));
  lines.push(tr('每月积分自动加入账户余额', 'Monthly credits are added to your account balance'));
  return lines;
}

function selectPackage(pkg: CreditPackage) {
  selectedPackage.value = pkg;
}

function normalizeSelections() {
  if (packages.value.length > 0) {
    const existing = selectedPackage.value
      ? packages.value.find((pkg) => pkg.id === selectedPackage.value?.id)
      : null;
    selectedPackage.value = existing || packages.value.find((pkg) => pkg.popular) || packages.value[0] || null;
  }

  if (subscriptionStore.plans.length > 0) {
    const existingPlan = subscriptionStore.plans.find((plan) => plan.code === selectedPlanCode.value);
    selectedPlanCode.value = existingPlan?.code
      || subscriptionStore.current?.plan_code
      || subscriptionStore.plans.find((plan) => plan.code.includes('creator'))?.code
      || subscriptionStore.plans[0]?.code
      || '';
  }
}

async function fetchData() {
  const balanceTask = get<BalanceResponse>('/credits/balance', { showLoading: false, showError: false })
    .then((res) => {
      currentBalance.value = res.balance;
      costPerGeneration.value = Number(res.cost_per_generation || 2);
    })
    .catch(() => undefined);

  const currentSubscriptionTask = subscriptionStore.fetchCurrentSubscription(true)
    .then(() => normalizeSelections())
    .catch(() => undefined);

  const [packagesResult] = await Promise.allSettled([
    get<PackagesResponse>('/credits/packages', { showLoading: false, showError: false }),
    subscriptionStore.fetchPlans(true),
  ]);

  if (packagesResult.status === 'fulfilled') {
    packages.value = Array.isArray(packagesResult.value.packages) && packagesResult.value.packages.length > 0
      ? packagesResult.value.packages
      : defaultCreditPackages();
  } else {
    packages.value = defaultCreditPackages();
  }
  normalizeSelections();

  void Promise.allSettled([balanceTask, currentSubscriptionTask]);
}

async function reconcilePendingPurchase() {
  const routeParams = readRouteParams();
  if (routeParams.subscriptionStatus === 'success') {
    clearRouteParams();
    await subscriptionStore.fetchCurrentSubscription(true);
    uni.showToast({ title: tr('订阅处理中，积分到账后会自动显示', 'Subscription is processing'), icon: 'none' });
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

async function handlePurchase(pkg?: CreditPackage) {
  const targetPackage = pkg || selectedPackage.value;
  if (!targetPackage) return;
  selectedPackage.value = targetPackage;
  if (!paymentConsentAccepted.value) {
    uni.showToast({ title: tr('请先同意隐私政策与服务条款', 'Accept the legal terms first'), icon: 'none' });
    return;
  }

  processing.value = true;
  processingText.value = tr('正在创建支付订单...', 'Creating checkout...');
  try {
    const response = await post<CheckoutResponse>(
      '/payments/checkout',
      { package_id: targetPackage.id, return_url: currentReturnUrl() },
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

async function handleSubscriptionPurchase(planCode?: string) {
  const targetPlanCode = planCode || selectedPlanCode.value;
  if (!targetPlanCode) return;
  selectedPlanCode.value = targetPlanCode;
  const targetPlan = subscriptionStore.plans.find((plan) => plan.code === targetPlanCode);
  if (targetPlan && isCurrentPlan(targetPlan)) return;
  if (!paymentConsentAccepted.value) {
    uni.showToast({ title: tr('请先同意隐私政策与服务条款', 'Accept the legal terms first'), icon: 'none' });
    return;
  }

  processing.value = true;
  processingText.value = tr('正在创建订阅订单...', 'Creating subscription checkout...');
  try {
    const response = await subscriptionStore.startSubscriptionCheckout(targetPlanCode, currentReturnUrl());
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
.pricing-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 28px;
  background: rgba(247, 248, 250, 0.88);
  backdrop-filter: blur(10px);
}

.pricing-dialog {
  position: relative;
  width: min(1180px, 100%);
  max-height: 92vh;
  overflow-y: auto;
  border: 1px solid #dde1e8;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 24px 64px rgba(23, 25, 31, 0.14);
  padding: 42px 36px 30px;
  animation: modalEnter 0.22s ease-out;
}

@keyframes modalEnter {
  from {
    opacity: 0;
    transform: translateY(14px) scale(0.99);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.close-button {
  position: absolute;
  top: 16px;
  right: 16px;
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: #f0f3f6;
  color: #4c5360;
  font-size: 24px;
  line-height: 1;
}

.close-button::after,
.plan-button::after,
.primary-action::after {
  border: 0;
}

.pricing-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  text-align: center;
  margin-bottom: 24px;
}

.pricing-title {
  color: #17191f;
  font-size: 30px;
  font-weight: 700;
}

.pricing-subtitle {
  max-width: 620px;
  color: #4c5360;
  font-size: 14px;
  line-height: 1.6;
}

.billing-tabs {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  min-width: 220px;
  padding: 4px;
  border-radius: 8px;
  background: #eef1f4;
}

.billing-tab {
  min-height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  color: #6b7280;
  font-size: 14px;
  font-weight: 700;
  transition: background 0.18s ease, color 0.18s ease, box-shadow 0.18s ease;
}

.billing-tab.active {
  background: #ffffff;
  color: #17191f;
  box-shadow: 0 4px 14px rgba(23, 25, 31, 0.1);
}

.balance-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 22px;
  padding: 16px 18px;
  border: 1px solid rgba(17, 106, 96, 0.18);
  border-radius: 8px;
  background: #f3faf8;
}

.balance-label,
.plan-caption,
.price-unit,
.provider-note {
  color: #6b7280;
}

.balance-label {
  display: block;
  font-size: 12px;
  font-weight: 700;
}

.balance-value {
  display: block;
  margin-top: 2px;
  color: #17191f;
  font-size: 30px;
  font-weight: 800;
}

.balance-meta {
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: #4c5360;
  font-size: 13px;
  text-align: right;
}

.pricing-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}

.pricing-card {
  min-height: 520px;
  display: flex;
  flex-direction: column;
  padding: 24px;
  border: 1px solid #dde1e8;
  border-radius: 8px;
  background: #fff;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease, background 0.18s ease;
}

.pricing-card.selected,
.pricing-card:hover {
  border-color: rgba(17, 106, 96, 0.4);
  box-shadow: 0 14px 34px rgba(23, 25, 31, 0.1);
  transform: translateY(-2px);
}

.pricing-card.highlighted {
  border-color: rgba(17, 106, 96, 0.34);
  background: #f3faf8;
}

.pricing-card.current {
  border-color: rgba(202, 138, 4, 0.38);
}

.card-top {
  min-height: 58px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.plan-name {
  display: block;
  color: #17191f;
  font-size: 28px;
  font-weight: 800;
  line-height: 1.2;
}

.plan-caption {
  display: block;
  margin-top: 6px;
  font-size: 13px;
}

.plan-badge {
  flex: 0 0 auto;
  min-width: 54px;
  min-height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 10px;
  border-radius: 8px;
  background: #17191f;
  color: #fff;
  font-size: 12px;
  font-weight: 800;
}

.plan-badge.muted {
  background: #e9e3d6;
  color: #6e561d;
}

.price-line {
  display: flex;
  align-items: flex-end;
  gap: 5px;
  margin-top: 24px;
  color: #17191f;
}

.currency {
  padding-bottom: 9px;
  color: #6b7280;
  font-size: 20px;
}

.price-value {
  font-size: 52px;
  font-weight: 500;
  line-height: 0.95;
  font-variant-numeric: tabular-nums;
}

.price-unit {
  padding-bottom: 9px;
  font-size: 13px;
  font-weight: 700;
}

.plan-description {
  display: block;
  min-height: 48px;
  margin-top: 18px;
  color: #4c5360;
  font-size: 15px;
  line-height: 1.55;
}

.plan-button,
.primary-action {
  width: 100%;
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 28px 0 22px;
  border: 1px solid #dde1e8;
  border-radius: 8px;
  background: #fff;
  color: #17191f;
  font-size: 15px;
  font-weight: 800;
  line-height: 1.2;
}

.plan-button.primary,
.primary-action {
  border-color: transparent;
  background: #116a60;
  color: #fff;
}

.plan-button[disabled] {
  opacity: 0.48;
}

.feature-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: auto;
}

.feature-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  color: #17191f;
  font-size: 14px;
  line-height: 1.45;
}

.feature-mark {
  width: 9px;
  height: 9px;
  flex: 0 0 9px;
  margin-top: 6px;
  border-radius: 999px;
  background: #116a60;
  box-shadow: 0 0 0 4px rgba(17, 106, 96, 0.11);
}

.pricing-footer {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 22px;
  align-items: center;
  text-align: center;
}

.provider-note {
  font-size: 12px;
  line-height: 1.5;
}

.state-panel,
.empty-pricing {
  min-height: 340px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
}

.loading-ring {
  width: 44px;
  height: 44px;
  border: 4px solid rgba(17, 106, 96, 0.16);
  border-top-color: #116a60;
  border-radius: 999px;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.success-mark {
  width: 56px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: #1f8f58;
  color: #fff;
  font-size: 30px;
  font-weight: 800;
}

.state-title {
  display: block;
  margin-top: 18px;
  color: #17191f;
  font-size: 22px;
  font-weight: 800;
}

.state-copy {
  display: block;
  margin-top: 8px;
  color: #4c5360;
  font-size: 15px;
}

.state-action {
  max-width: 260px;
}

@media (max-width: 980px) {
  .pricing-grid {
    grid-template-columns: 1fr;
  }

  .pricing-card {
    min-height: auto;
  }
}

@media (max-width: 560px) {
  .pricing-overlay {
    align-items: flex-start;
    padding: 12px;
  }

  .pricing-dialog {
    max-height: calc(100vh - 24px);
    padding: 58px 16px 22px;
    border-radius: 8px;
  }

  .pricing-title {
    font-size: 26px;
  }

  .balance-strip {
    align-items: flex-start;
    flex-direction: column;
  }

  .balance-meta {
    text-align: left;
  }

  .pricing-card {
    padding: 20px;
  }

  .plan-name {
    font-size: 24px;
  }

  .price-value {
    font-size: 44px;
  }
}
</style>
