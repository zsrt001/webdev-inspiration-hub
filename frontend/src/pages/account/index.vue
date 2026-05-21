<template>
  <view class="app-container account-page" style="padding-top: 64px;">
    <NavBar />

    <view class="account-shell">
      <view class="account-hero">
        <view>
          <text class="account-kicker">{{ tr('账户中心', 'Account Center') }}</text>
          <text class="account-title heading-serif">{{ tr('我的 VowPic 空间', 'My VowPic Space') }}</text>
          <text class="account-subtitle">
            {{ tr('使用 Google 登录后，积分、订单和高清交付都会绑定到同一个已验证账号。', 'Sign in with Google to keep credits, orders, and HD deliveries under one verified account.') }}
          </text>
        </view>

        <view class="hero-actions">
          <button v-if="!accountAuthed && supabaseEnabled" class="btn btn-primary hero-btn" @tap="signIn">
            {{ tr('使用 Google 登录', 'Sign in with Google') }}
          </button>
          <button v-if="adminAccess" class="btn btn-outline hero-btn" @tap="goAdmin">{{ tr('后台控制台', 'Admin console') }}</button>
          <button class="btn btn-outline hero-btn" @tap="refresh">{{ tr('刷新', 'Refresh') }}</button>
        </view>
      </view>

      <view v-if="loading" class="state-card">
        <text class="state-title">{{ tr('正在加载账户数据...', 'Loading account data...') }}</text>
      </view>

      <view v-else-if="error" class="state-card error-card">
        <text class="state-title">{{ error }}</text>
        <button class="btn btn-primary state-action" @tap="refresh">{{ tr('重试', 'Retry') }}</button>
      </view>

      <template v-else>
        <view class="overview-grid">
          <view class="profile-card panel">
            <view class="profile-head">
              <image v-if="profile?.avatar_url" class="avatar" :src="profile.avatar_url" mode="aspectFill" />
              <view v-else class="avatar fallback-avatar">
                <text>{{ profileInitial }}</text>
              </view>
              <view>
                <text class="card-eyebrow">{{ tr('登录状态', 'Sign-in Status') }}</text>
                <text class="profile-name">{{ displayName }}</text>
                <text class="profile-email">{{ profile?.email || tr('访客账号', 'Guest account') }}</text>
              </view>
            </view>

            <view class="meta-list">
              <view class="meta-row">
                <text>{{ tr('认证方式', 'Provider') }}</text>
                <text>{{ providerLabel }}</text>
              </view>
              <view class="meta-row">
                <text>{{ tr('账户状态', 'Status') }}</text>
                <text>{{ profile?.status || 'active' }}</text>
              </view>
              <view class="meta-row">
                <text>{{ tr('最近登录', 'Last Login') }}</text>
                <text>{{ formatDate(profile?.last_login_at || profile?.updated_at) }}</text>
              </view>
              <view class="meta-row">
                <text>{{ tr('用户 ID', 'User ID') }}</text>
                <text class="mono">{{ shortId(profile?.id) }}</text>
              </view>
            </view>

            <button v-if="accountAuthed" class="btn btn-outline logout-btn" @tap="signOut">
              {{ tr('退出登录', 'Sign out') }}
            </button>
          </view>

          <view class="credits-card panel">
            <text class="card-eyebrow">{{ tr('当前积分', 'Current Credits') }}</text>
            <text class="credit-value heading-serif">{{ balance?.balance ?? 0 }}</text>
            <view class="credit-status" :class="{ blocked: !balance?.can_generate }">
              <text>{{ balance?.can_generate ? tr('可立即生成', 'Ready to generate') : tr('积分不足', 'Insufficient credits') }}</text>
            </view>
            <text class="credit-copy">{{ tr('基础单人生成', 'Base single generation') }} {{ balance?.cost_per_generation || 2 }} {{ tr('积分起', 'credits and up') }}</text>
            <view class="credit-actions">
              <button class="btn btn-primary compact-btn" @tap="goCreate">{{ tr('开始创作', 'Create') }}</button>
              <button class="btn btn-outline compact-btn" @tap="goOrders">{{ tr('查看订单', 'Orders') }}</button>
            </view>
          </view>

          <view class="subscription-card panel">
            <text class="card-eyebrow">{{ tr('订阅状态', 'Subscription') }}</text>
            <text class="subscription-name heading-serif">{{ activePlanName }}</text>
            <view class="meta-list">
              <view class="meta-row">
                <text>{{ tr('状态', 'Status') }}</text>
                <text>{{ subscriptionStore.current?.status || 'none' }}</text>
              </view>
              <view class="meta-row">
                <text>{{ tr('每月积分', 'Monthly credits') }}</text>
                <text>{{ subscriptionStore.current?.monthly_credits || 0 }}</text>
              </view>
              <view class="meta-row">
                <text>{{ tr('到期时间', 'Period end') }}</text>
                <text>{{ formatDate(subscriptionStore.current?.current_period_end) }}</text>
              </view>
              <view class="meta-row">
                <text>{{ tr('自动续订', 'Renewal') }}</text>
                <text>{{ subscriptionStore.current?.cancel_at_period_end ? tr('已设置取消', 'Cancel scheduled') : tr('开启', 'Active') }}</text>
              </view>
            </view>
            <button
              v-if="subscriptionStore.current?.plan_code && !subscriptionStore.current?.cancel_at_period_end"
              class="btn btn-outline logout-btn"
              @tap="cancelSubscription"
            >
              {{ tr('到期取消续订', 'Cancel at period end') }}
            </button>
          </view>
        </view>

        <view class="content-grid">
          <view class="panel ledger-card">
            <view class="section-head">
              <view>
                <text class="section-kicker">{{ tr('积分流水', 'Credit Ledger') }}</text>
                <text class="section-title">{{ tr('最近积分变化', 'Recent Credit Activity') }}</text>
              </view>
            </view>

            <view v-if="transactions.length === 0" class="empty-panel">
              <text>{{ tr('暂无积分流水', 'No credit activity yet') }}</text>
            </view>
            <view v-else class="ledger-list">
              <view v-for="item in transactions" :key="item.id" class="ledger-row">
                <view>
                  <text class="row-title">{{ transactionTitle(item) }}</text>
                  <text class="row-subtitle">{{ item.description || item.source || tr('系统记录', 'System record') }}</text>
                </view>
                <view class="row-side">
                  <text class="amount" :class="{ positive: item.amount > 0, negative: item.amount < 0 }">{{ formatAmount(item.amount) }}</text>
                  <text class="row-date">{{ formatDate(item.created_at) }}</text>
                </view>
              </view>
            </view>
          </view>

          <view class="panel orders-card">
            <view class="section-head">
              <view>
                <text class="section-kicker">{{ tr('生成记录', 'Generation Records') }}</text>
                <text class="section-title">{{ tr('最近作品', 'Recent Images') }}</text>
                <text class="retention-note">{{ retentionNotice }}</text>
              </view>
              <button class="mini-link" @tap="goOrders">{{ tr('全部', 'All') }}</button>
            </view>

            <view v-if="orders.length === 0" class="empty-panel">
              <text>{{ tr('还没有生成记录', 'No generation records yet') }}</text>
              <button class="btn btn-primary state-action" @tap="goCreate">{{ tr('创建第一组照片', 'Create first set') }}</button>
            </view>
            <view v-else class="order-list">
              <view v-for="order in orders" :key="order.id" class="order-row" @tap="viewOrder(order.id)">
                <image class="order-thumb" :src="orderPreview(order)" mode="aspectFit" />
                <view class="order-main">
                  <text class="row-title">#{{ shortId(order.id) }}</text>
                  <text class="row-subtitle">{{ formatDate(order.created_at) }}</text>
                  <text class="row-subtitle">{{ tr('图片保留至', 'Images kept until') }} {{ formatDate(order.expires_at) }}</text>
                </view>
                <view class="order-status" :class="statusClass(order.status)">{{ statusText(order.status) }}</view>
                <button class="mini-link danger-link" @tap.stop="deleteOrder(order.id)">{{ tr('删除', 'Delete') }}</button>
              </view>
            </view>
          </view>
        </view>
      </template>
    </view>
    <LegalFooter />
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import NavBar from '../../components/NavBar.vue';
import LegalFooter from '../../components/LegalFooter.vue';
import { useI18nStore } from '../../stores/i18n';
import { useSubscriptionStore } from '../../stores/subscription';
import { del, get, resolvePublicUrl } from '../../utils/api';
import { getAuthProvider, isSupabaseLoggedIn, logout, signInWithGoogle } from '../../utils/auth';
import { refreshSupabaseConfig } from '../../utils/supabase';

interface UserProfile {
  id: string;
  openid: string;
  auth_provider?: string | null;
  auth_subject?: string | null;
  email?: string | null;
  nickname?: string | null;
  avatar_url?: string | null;
  role?: string | null;
  status?: string | null;
  last_login_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface BalanceResponse {
  balance: number;
  can_generate: boolean;
  cost_per_generation: number;
}

interface CreditTransaction {
  id: string;
  transaction_type: string;
  amount: number;
  balance_after: number;
  source?: string | null;
  source_id?: string | null;
  description?: string | null;
  created_at?: string | null;
}

interface TransactionsResponse {
  transactions: CreditTransaction[];
}

interface Order {
  id: string;
  template_id: string | null;
  preview_image_urls: Record<string, string> | null;
  final_image_urls: Record<string, string> | null;
  preview_master_image_url?: string | null;
  final_master_image_url?: string | null;
  created_at: string;
  expires_at?: string | null;
  storage_cleanup_status?: string | null;
  status: string;
}

type OrdersResponse = Order[] | { value?: Order[]; items?: Order[]; results?: Order[]; orders?: Order[] };

interface LegalPolicies {
  retention?: {
    source_images_days?: number;
    free_generated_days?: number;
    paid_generated_days?: number;
    subscription_generated_days?: number;
    studio_generated_days?: number;
  };
}

const i18nStore = useI18nStore();
const subscriptionStore = useSubscriptionStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const loading = ref(true);
const error = ref('');
const profile = ref<UserProfile | null>(null);
const balance = ref<BalanceResponse | null>(null);
const transactions = ref<CreditTransaction[]>([]);
const orders = ref<Order[]>([]);
const legalPolicies = ref<LegalPolicies | null>(null);
const supabaseAuthed = ref(false);
const passwordAuthed = ref(false);
const supabaseEnabled = ref(false);
const adminAccess = ref(false);

const accountAuthed = computed(() => supabaseAuthed.value);
const displayName = computed(() => profile.value?.nickname || profile.value?.email || tr('访客用户', 'Guest user'));
const profileInitial = computed(() => (displayName.value || 'A').slice(0, 1).toUpperCase());
const activePlanName = computed(() => subscriptionStore.activePlan?.name || tr('未订阅', 'No subscription'));
const retentionNotice = computed(() => {
  const retention = legalPolicies.value?.retention || {};
  return tr(
    `原图 ${retention.source_images_days || 7} 天后删除；免费作品 ${retention.free_generated_days || 30} 天，付费积分包 ${retention.paid_generated_days || 90} 天，订阅用户 ${retention.subscription_generated_days || 180} 天。`,
    `Source images are deleted after ${retention.source_images_days || 7} days. Generated images: free ${retention.free_generated_days || 30} days, paid packs ${retention.paid_generated_days || 90} days, subscriptions ${retention.subscription_generated_days || 180} days.`,
  );
});

const providerLabel = computed(() => {
  if (supabaseAuthed.value) return 'Google';
  if (passwordAuthed.value) return tr('用户名密码', 'Username/password');
  const provider = getAuthProvider() || profile.value?.auth_provider || 'local';
  if (provider === 'password') return tr('用户名密码', 'Username/password');
  return provider === 'local' ? tr('访客会话', 'Guest session') : provider;
});

function shortId(value?: string | null): string {
  const raw = String(value || '').trim();
  if (!raw) return '--';
  return raw.length <= 12 ? raw : `${raw.slice(0, 8)}...${raw.slice(-4)}`;
}

function formatDate(value?: string | null): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString(i18nStore.locale === 'zh' ? 'zh-CN' : 'en-US', {
    month: i18nStore.locale === 'zh' ? 'numeric' : 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatAmount(value: number): string {
  const amount = Number(value || 0);
  return amount > 0 ? `+${amount}` : String(amount);
}

function transactionTitle(item: CreditTransaction): string {
  const key = String(item.transaction_type || '').toUpperCase();
  const map: Record<string, string> = {
    WELCOME_BONUS: tr('新用户赠送', 'Welcome bonus'),
    PURCHASE: tr('购买积分', 'Credit purchase'),
    SUBSCRIPTION_GRANT: tr('订阅积分发放', 'Subscription grant'),
    GENERATION_DEBIT: tr('生成扣费', 'Generation debit'),
    GENERATION_REFUND: tr('生成退款', 'Generation refund'),
    ADMIN_GRANT: tr('人工加积分', 'Admin grant'),
    ADMIN_DEDUCT: tr('人工扣积分', 'Admin deduction'),
  };
  return map[key] || key || tr('积分变化', 'Credit activity');
}

const deliveryVariantSuffixes = ['portrait_2x3', 'print_3x2', 'xhs_3x4', 'portrait_4x5', 'wallpaper_9x16', 'square_1x1'];
function pickPrimaryFromMap(urls: Record<string, string> | null): string | null {
  if (!urls) return null;
  if (urls.image_1) return urls.image_1;
  const master = Object.entries(urls).find(([key]) => !deliveryVariantSuffixes.some((suffix) => key.includes(suffix)));
  if (master?.[1]) return master[1];
  return Object.values(urls)[0] || null;
}

function orderPreview(order: Order): string {
  if (order.final_master_image_url) return resolvePublicUrl(order.final_master_image_url);
  if (order.preview_master_image_url) return resolvePublicUrl(order.preview_master_image_url);
  const final = pickPrimaryFromMap(order.final_image_urls);
  if (final) return resolvePublicUrl(final);
  const preview = pickPrimaryFromMap(order.preview_image_urls);
  if (preview) return resolvePublicUrl(preview);
  return resolvePublicUrl('/style-previews/royal_castle.jpg');
}

function normalizeOrderRows(response: OrdersResponse): Order[] {
  if (Array.isArray(response)) return response;
  if (Array.isArray(response?.value)) return response.value;
  if (Array.isArray(response?.items)) return response.items;
  if (Array.isArray(response?.results)) return response.results;
  if (Array.isArray(response?.orders)) return response.orders;
  return [];
}

function statusText(status: string): string {
  const normalized = String(status || '').toUpperCase();
  const map: Record<string, string> = {
    CREATED: tr('已创建', 'Created'),
    CHECKING: tr('检测中', 'Checking'),
    GENERATING: tr('生成中', 'Generating'),
    COMPLETED: tr('已完成', 'Completed'),
    FAILED: tr('失败', 'Failed'),
    REFUNDED: tr('已退款', 'Refunded'),
  };
  return map[normalized] || normalized;
}

function statusClass(status: string): string {
  const normalized = String(status || '').toUpperCase();
  if (normalized === 'COMPLETED') return 'completed';
  if (normalized === 'FAILED' || normalized === 'REFUNDED') return 'failed';
  return 'pending';
}

async function loadAccount(): Promise<void> {
  loading.value = true;
  error.value = '';
  supabaseAuthed.value = isSupabaseLoggedIn();
  adminAccess.value = false;

  try {
    const [
      profileResult,
      balanceResult,
      transactionsResult,
      ordersResult,
      legalResult,
      adminResult,
    ] = await Promise.allSettled([
      get<UserProfile>('/users/me', { showLoading: false, showError: false }),
      get<BalanceResponse>('/credits/balance', { showLoading: false, showError: false }),
      get<TransactionsResponse>('/credits/transactions?limit=8', { showLoading: false, showError: false }),
      get<OrdersResponse>('/orders', { showLoading: false, showError: false }),
      get<LegalPolicies>('/legal/policies', { showLoading: false, showError: false }),
      get('/admin/me', { showLoading: false, showError: false }),
      subscriptionStore.fetchPlans(true),
      subscriptionStore.fetchCurrentSubscription(true),
    ]);

    profile.value = profileResult.status === 'fulfilled' ? profileResult.value : null;
    balance.value = balanceResult.status === 'fulfilled' ? balanceResult.value : null;
    transactions.value = transactionsResult.status === 'fulfilled' ? (transactionsResult.value.transactions || []) : [];
    orders.value = ordersResult.status === 'fulfilled'
      ? normalizeOrderRows(ordersResult.value).slice(0, 6)
      : [];
    legalPolicies.value = legalResult.status === 'fulfilled' ? legalResult.value : null;
    adminAccess.value = adminResult.status === 'fulfilled';
    supabaseAuthed.value = isSupabaseLoggedIn();
  } catch (err: any) {
    error.value = err?.message || tr('账户暂时无法刷新，请稍后重试。', 'Account details are temporarily unavailable. Please try again shortly.');
  } finally {
    loading.value = false;
  }
}

async function refresh(): Promise<void> {
  await loadAccount();
}

async function signIn(): Promise<void> {
  try {
    await signInWithGoogle();
  } catch (err: any) {
    uni.showToast({ title: err?.message || tr('登录失败', 'Sign-in failed'), icon: 'none' });
  }
}

async function signOut(): Promise<void> {
  logout();
  uni.showToast({ title: tr('已退出登录', 'Signed out'), icon: 'none' });
  await loadAccount();
}

async function cancelSubscription(): Promise<void> {
  try {
    await subscriptionStore.cancelSubscription();
    uni.showToast({ title: tr('已设置到期取消续订', 'Cancellation scheduled'), icon: 'none' });
  } catch (err: any) {
    uni.showToast({ title: err?.message || tr('取消失败', 'Cancel failed'), icon: 'none' });
  }
}

async function deleteOrder(orderId: string): Promise<void> {
  uni.showModal({
    title: tr('删除作品', 'Delete image'),
    content: tr('删除后图片文件会被移除，订单记录不再展示。', 'Image files will be removed and this order will no longer be shown.'),
    success: async (res) => {
      if (!res.confirm) return;
      try {
        await del(`/orders/${orderId}`, { showLoading: true, showError: false });
        orders.value = orders.value.filter((order) => order.id !== orderId);
        uni.showToast({ title: tr('已删除', 'Deleted'), icon: 'success' });
      } catch (err: any) {
        uni.showToast({ title: err?.message || tr('删除失败', 'Delete failed'), icon: 'none' });
      }
    },
  });
}

function goCreate(): void {
  uni.navigateTo({ url: '/pages/create/index' });
}

function goOrders(): void {
  uni.reLaunch({ url: '/pages/orders/orders' });
}

function goAdmin(): void {
  uni.navigateTo({ url: '/admin' });
}

function viewOrder(orderId: string): void {
  uni.navigateTo({ url: `/pages/preview/preview?id=${orderId}` });
}

onMounted(async () => {
  await Promise.all([
    loadAccount(),
    refreshSupabaseConfig().then((enabled) => {
      supabaseEnabled.value = enabled;
    }),
  ]);
});
</script>

<style lang="scss" scoped>
.account-page {
  min-height: 100vh;
  background: #f7f8fa;
}

.account-shell {
  max-width: 1240px;
  margin: 0 auto;
  padding: 40px 28px 96px;
}

.account-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
}

.account-kicker,
.card-eyebrow,
.section-kicker {
  display: block;
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
}

.account-title {
  display: block;
  max-width: 760px;
  margin-top: 10px;
  color: #17191f;
  font-size: 52px;
  line-height: 1.02;
}

.account-subtitle,
.retention-note {
  display: block;
  max-width: 760px;
  margin-top: 12px;
  color: #4c5360;
  font-size: 14px;
  line-height: 1.8;
}

.hero-actions,
.credit-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.hero-btn,
.compact-btn,
.logout-btn,
.state-action {
  min-height: 44px;
  padding: 0 20px;
  font-size: 14px;
}

.overview-grid,
.content-grid {
  display: grid;
  gap: 20px;
}

.overview-grid {
  grid-template-columns: minmax(0, 1.2fr) minmax(280px, 0.65fr) minmax(300px, 0.75fr);
  margin-bottom: 20px;
}

.content-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.panel,
.state-card {
  border: 1px solid #dde1e8;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 14px 38px rgba(23, 25, 31, 0.06);
}

.profile-card,
.credits-card,
.subscription-card,
.ledger-card,
.orders-card {
  padding: 24px;
}

.profile-head {
  display: flex;
  align-items: center;
  gap: 18px;
  margin-bottom: 22px;
}

.avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: #edf2f1;
  flex: 0 0 auto;
}

.fallback-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #116a60;
  font-size: 24px;
  font-weight: 900;
}

.profile-name,
.subscription-name,
.credit-value {
  display: block;
  color: #17191f;
}

.profile-name {
  margin-top: 6px;
  font-size: 22px;
  font-weight: 900;
}

.profile-email {
  display: block;
  margin-top: 4px;
  color: #6b7280;
  font-size: 13px;
}

.meta-list {
  display: grid;
  gap: 12px;
}

.meta-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: #4c5360;
  font-size: 13px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.logout-btn {
  width: 100%;
  margin-top: 22px;
}

.credit-value {
  margin-top: 8px;
  font-size: 60px;
  line-height: 1;
}

.credit-status {
  display: inline-flex;
  margin-top: 12px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #eef8f5;
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
}

.credit-status.blocked {
  background: #fff4f2;
  color: #b42318;
}

.credit-copy {
  display: block;
  margin-top: 14px;
  color: #4c5360;
  font-size: 13px;
  line-height: 1.6;
}

.credit-actions {
  margin-top: 18px;
}

.subscription-name {
  margin: 10px 0 16px;
  font-size: 28px;
  line-height: 1.1;
}

.state-card,
.empty-panel {
  padding: 28px;
  text-align: center;
}

.state-title {
  display: block;
  color: #17191f;
  font-size: 20px;
  font-weight: 900;
}

.section-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.section-title {
  display: block;
  margin-top: 6px;
  color: #17191f;
  font-size: 22px;
  font-weight: 900;
}

.ledger-list,
.order-list {
  display: grid;
  gap: 12px;
}

.ledger-row,
.order-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 14px 0;
  border-bottom: 1px solid #edf0f4;
}

.ledger-row:last-child,
.order-row:last-child {
  border-bottom: none;
}

.row-title,
.row-subtitle,
.row-date {
  display: block;
}

.row-title {
  color: #17191f;
  font-size: 14px;
  font-weight: 900;
}

.row-subtitle,
.row-date {
  margin-top: 4px;
  color: #6b7280;
  font-size: 12px;
}

.row-side {
  text-align: right;
}

.amount {
  color: #4c5360;
  font-size: 18px;
  font-weight: 900;
}

.amount.positive {
  color: #116a60;
}

.amount.negative {
  color: #b42318;
}

.order-thumb {
  width: 68px;
  height: 88px;
  border-radius: 8px;
  background: #eef1f4;
  flex: 0 0 auto;
}

.order-main {
  flex: 1 1 auto;
  min-width: 0;
}

.order-status {
  padding: 7px 10px;
  border-radius: 8px;
  background: #f1f3f6;
  color: #4c5360;
  font-size: 12px;
  font-weight: 900;
}

.order-status.completed {
  background: #eef8f5;
  color: #116a60;
}

.order-status.failed {
  background: #fff4f2;
  color: #b42318;
}

.mini-link {
  min-height: 36px;
  padding: 0 12px;
  border: none;
  background: transparent;
  color: #116a60;
  font-size: 13px;
  font-weight: 900;
}

.danger-link {
  color: #b42318;
}

@media (max-width: 980px) {
  .account-hero {
    align-items: flex-start;
    flex-direction: column;
  }

  .overview-grid,
  .content-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .account-shell {
    padding: 28px 18px 72px;
  }

  .account-title {
    font-size: 38px;
  }

  .ledger-row,
  .order-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .row-side {
    text-align: left;
  }
}
</style>
