<template>
  <view class="app-container account-page" style="padding-top: 64px;">
    <NavBar />

    <view class="account-shell" role="main">
      <view class="account-hero">
        <view>
          <text class="account-kicker">{{ tr('账户中心', 'Account Center') }}</text>
          <text class="account-title heading-serif" role="heading" aria-level="1">{{ tr('我的 VowPic 空间', 'My VowPic Space') }}</text>
          <text class="account-subtitle">
            {{ tr('使用 Google 登录后，积分、订单和高清交付都会绑定到同一个已验证账号。', 'Sign in with Google to keep credits, orders, and HD deliveries under one verified account.') }}
          </text>
        </view>

        <view class="hero-actions">
          <button v-if="!accountAuthed && googleAuthAvailable && supabaseEnabled" class="btn btn-primary hero-btn" role="button" tabindex="0" @tap="signIn" @keydown.enter.prevent="signIn" @keydown.space.prevent="signIn">
            {{ tr('使用 Google 登录', 'Sign in with Google') }}
          </button>
          <button v-if="accountAuthed && adminAccess" class="btn btn-outline hero-btn" role="button" tabindex="0" @tap="goAdmin" @keydown.enter.prevent="goAdmin" @keydown.space.prevent="goAdmin">{{ tr('后台控制台', 'Admin console') }}</button>
          <button class="btn btn-outline hero-btn" role="button" tabindex="0" @tap="refresh" @keydown.enter.prevent="refresh" @keydown.space.prevent="refresh">{{ tr('刷新', 'Refresh') }}</button>
        </view>
      </view>

      <view v-if="loading" class="state-card">
        <text class="state-title">{{ tr('正在加载账户数据...', 'Loading account data...') }}</text>
      </view>

      <view v-else-if="error" class="state-card error-card">
        <text class="state-title">{{ error }}</text>
        <button class="btn btn-primary state-action" role="button" tabindex="0" @tap="refresh" @keydown.enter.prevent="refresh" @keydown.space.prevent="refresh">{{ tr('重试', 'Retry') }}</button>
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
                <text class="profile-email">{{ profile?.email || tr('尚未登录', 'Not signed in') }}</text>
              </view>
            </view>

            <view class="meta-list">
              <view class="meta-row">
                <text>{{ tr('认证方式', 'Provider') }}</text>
                <text>{{ providerLabel }}</text>
              </view>
              <view class="meta-row">
                <text>{{ tr('账户状态', 'Status') }}</text>
                <text>{{ profile?.status || '--' }}</text>
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

            <button v-if="accountAuthed" class="btn btn-outline logout-btn" role="button" tabindex="0" @tap="signOut" @keydown.enter.prevent="signOut" @keydown.space.prevent="signOut">
              {{ tr('退出登录', 'Sign out') }}
            </button>
          </view>

          <view class="credits-card panel">
            <text class="card-eyebrow">{{ tr('当前积分', 'Current Credits') }}</text>
            <text class="credit-value heading-serif">{{ accountCreditValue }}</text>
            <view class="credit-status" :class="{ blocked: !canGenerate }">
              <text>{{ creditStatusLabel }}</text>
            </view>
            <text class="credit-copy">{{ tr('基础单人生成', 'Base single generation') }} {{ balance?.cost_per_generation || 2 }} {{ tr('积分起', 'credits and up') }}</text>
            <view class="credit-actions">
              <button v-if="creationAvailable" class="btn btn-primary compact-btn" role="button" tabindex="0" @tap="goCreate" @keydown.enter.prevent="goCreate" @keydown.space.prevent="goCreate">{{ tr('开始创作', 'Create') }}</button>
              <button class="btn btn-outline compact-btn" role="button" tabindex="0" @tap="goOrders" @keydown.enter.prevent="goOrders" @keydown.space.prevent="goOrders">{{ tr('查看订单', 'Orders') }}</button>
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
                <text>{{ subscriptionStore.current?.credits_per_paid_period || 0 }}</text>
              </view>
              <view class="meta-row">
                <text>{{ tr('到期时间', 'Period end') }}</text>
                <text>{{ formatDate(subscriptionStore.current?.current_period_end) }}</text>
              </view>
              <view class="meta-row">
                <text>{{ tr('自动续订', 'Renewal') }}</text>
                <text>{{ renewalStatus }}</text>
              </view>
            </view>
            <button
              v-if="subscriptionStore.current?.product_code && !subscriptionStore.current?.cancel_at_period_end"
              class="btn btn-outline logout-btn"
              role="button"
              tabindex="0"
              @tap="cancelSubscription"
              @keydown.enter.prevent="cancelSubscription"
              @keydown.space.prevent="cancelSubscription"
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
              <button class="mini-link" role="button" tabindex="0" @tap="goOrders" @keydown.enter.prevent="goOrders" @keydown.space.prevent="goOrders">{{ tr('全部', 'All') }}</button>
            </view>

            <view v-if="orders.length === 0" class="empty-panel">
              <text>{{ tr('还没有生成记录', 'No generation records yet') }}</text>
              <button v-if="creationAvailable" class="btn btn-primary state-action" role="button" tabindex="0" @tap="goCreate" @keydown.enter.prevent="goCreate" @keydown.space.prevent="goCreate">{{ tr('创建第一组照片', 'Create first set') }}</button>
            </view>
            <view v-else class="order-list">
              <a v-for="order in orders" :key="order.id" class="order-row" :href="orderHref(order.id)" @click.prevent="viewOrder(order.id)">
                <image class="order-thumb" :src="orderPreview(order)" mode="aspectFit" />
                <view class="order-main">
                  <text class="row-title">#{{ shortId(order.id) }}</text>
                  <text class="row-subtitle">{{ formatDate(order.created_at) }}</text>
                  <text class="row-subtitle">{{ tr('图片保留至', 'Images kept until') }} {{ formatDate(order.expires_at) }}</text>
                </view>
                <view class="order-status" :class="statusClass(order.status)">{{ statusText(order.status) }}</view>
                <text class="paused-delete">{{ tr('删除暂不可用', 'Deletion temporarily paused') }}</text>
              </a>
            </view>
          </view>
        </view>

        <view v-if="accountAuthed" class="account-controls-grid">
          <view class="panel account-control-card">
            <text class="section-kicker">{{ tr('账户数据', 'Account Data') }}</text>
            <text class="section-title">{{ tr('下载我的数据', 'Download my data') }}</text>
            <text class="control-copy">
              {{ tr('导出当前账户以及已安全合并的历史账户记录。导出不包含登录令牌、存储地址、模型原始响应或内部路径。', 'Export this account and safely merged account history. The file excludes sign-in tokens, storage locations, raw model responses, and internal paths.') }}
            </text>
            <button class="btn btn-outline control-button" role="button" :tabindex="exportBusy ? -1 : 0" :disabled="exportBusy" @tap="exportAccountData" @keydown.enter.prevent="exportAccountData" @keydown.space.prevent="exportAccountData">
              {{ exportBusy ? tr('正在生成...', 'Preparing...') : tr('下载 JSON', 'Download JSON') }}
            </button>
            <text v-if="exportMessage" class="control-status">{{ exportMessage }}</text>
          </view>

          <view class="panel account-control-card">
            <text class="section-kicker">{{ tr('旧账户恢复', 'Legacy Account Recovery') }}</text>
            <text class="section-title">{{ tr('合并空的旧账户', 'Merge an empty legacy account') }}</text>
            <text class="control-copy">
              {{ tr('只有不含订单、积分、支付、订阅或图片的旧账户可以在此合并。付款记录仅用于服务端核验所有权。', 'Only a legacy account with no orders, credits, payments, subscriptions, or images can be merged here. A payment reference is verified server-side and is never treated as ownership by itself.') }}
            </text>
            <input v-model.trim="legacyAccountId" class="control-input" :placeholder="tr('旧账户 UUID', 'Legacy account UUID')" />
            <input v-model.trim="paymentReference" class="control-input" :placeholder="tr('已支付的付款参考号', 'Verified paid payment reference')" />
            <button class="btn btn-outline control-button" role="button" :tabindex="claimBusy ? -1 : 0" :disabled="claimBusy" @tap="recoverLegacyAccount" @keydown.enter.prevent="recoverLegacyAccount" @keydown.space.prevent="recoverLegacyAccount">
              {{ claimBusy ? tr('正在核验...', 'Verifying...') : tr('核验并合并', 'Verify and merge') }}
            </button>
            <text v-if="claimMessage" class="control-status">{{ claimMessage }}</text>
          </view>

          <view class="panel account-control-card danger-card">
            <text class="section-kicker danger-kicker">{{ tr('账户关闭', 'Account Closure') }}</text>
            <text class="section-title">{{ tr('撤销登录并软关闭账户', 'Revoke sign-in and soft-close the account') }}</text>
            <text class="control-copy">
              {{ tr('关闭会立即撤销身份和所有会话，并去标识可删除的个人资料。财务记录会保留，媒体清理仍处于待处理状态。', 'Closure immediately revokes identity and every session and anonymizes removable profile data. Financial records remain, and media cleanup remains pending.') }}
            </text>
            <input v-model.trim="closeConfirmation" class="control-input" placeholder="CLOSE MY ACCOUNT" />
            <button class="btn danger-button control-button" role="button" :tabindex="closeBusy || closeConfirmation !== 'CLOSE MY ACCOUNT' ? -1 : 0" :disabled="closeBusy || closeConfirmation !== 'CLOSE MY ACCOUNT'" @tap="closeAccount" @keydown.enter.prevent="closeAccount" @keydown.space.prevent="closeAccount">
              {{ closeBusy ? tr('正在关闭...', 'Closing...') : tr('关闭账户', 'Close account') }}
            </button>
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
import {
  type OrderRead,
  displayAsset,
  isOrderDeliverable,
  isOrderManualOrFailed,
} from '../../contracts/order';
import { downloadAccountExport } from '../../services/account';
import { useI18nStore } from '../../stores/i18n';
import { useOpsStore } from '../../stores/ops';
import { useSubscriptionStore } from '../../stores/subscription';
import { get, post, resolvePublicUrl } from '../../utils/api';
import { clearCachedSession, ensureSession, logout, signInWithGoogle } from '../../utils/auth';
import { refreshSupabaseConfig } from '../../utils/supabase';

interface UserProfile {
  id: string;
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
const opsStore = useOpsStore();
const subscriptionStore = useSubscriptionStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
const creationAvailable = computed(() => opsStore.creationAvailable);
const googleAuthAvailable = computed(() => opsStore.googleAuthAvailable);

const loading = ref(true);
const error = ref('');
const profile = ref<UserProfile | null>(null);
const balance = ref<BalanceResponse | null>(null);
const transactions = ref<CreditTransaction[]>([]);
const orders = ref<OrderRead[]>([]);
const legalPolicies = ref<LegalPolicies | null>(null);
const supabaseAuthed = ref(false);
const supabaseEnabled = ref(false);
const adminAccess = ref(false);
const legacyAccountId = ref('');
const paymentReference = ref('');
const claimBusy = ref(false);
const claimMessage = ref('');
const closeConfirmation = ref('');
const closeBusy = ref(false);
const exportBusy = ref(false);
const exportMessage = ref('');

const accountAuthed = computed(() => supabaseAuthed.value);
const displayName = computed(() => profile.value?.nickname || profile.value?.email || tr('尚未登录', 'Not signed in'));
const profileInitial = computed(() => (displayName.value || 'A').slice(0, 1).toUpperCase());
const activePlanName = computed(() => subscriptionStore.activePlan?.code || tr('未订阅', 'No subscription'));
const accountCreditValue = computed(() => (accountAuthed.value ? (balance.value?.balance ?? '--') : '--'));
const canGenerate = computed(() => accountAuthed.value && !!balance.value?.can_generate);
const creditStatusLabel = computed(() => {
  if (!accountAuthed.value) return tr('登录后查看', 'Sign in required');
  return balance.value?.can_generate
    ? tr('可立即生成', 'Ready to generate')
    : tr('积分不足', 'Insufficient credits');
});
const renewalStatus = computed(() => {
  const current = subscriptionStore.current;
  if (!current?.product_code) return tr('不适用', 'Not applicable');
  return current.cancel_at_period_end
    ? tr('已设置取消', 'Cancel scheduled')
    : tr('开启', 'Active');
});
const retentionNotice = computed(() => {
  const retention = legalPolicies.value?.retention || {};
  return tr(
    `计划保留期：原图 ${retention.source_images_days || 7} 天，免费作品 ${retention.free_generated_days || 30} 天，付费积分包 ${retention.paid_generated_days || 90} 天，订阅用户 ${retention.subscription_generated_days || 180} 天。可审计删除流程上线前，自动删除和账户内删除均已暂停。`,
    `Scheduled retention: source images ${retention.source_images_days || 7} days, free images ${retention.free_generated_days || 30} days, paid packs ${retention.paid_generated_days || 90} days, subscriptions ${retention.subscription_generated_days || 180} days. Automated and in-account deletion are temporarily paused until the audited cleanup flow is available.`,
  );
});

const providerLabel = computed(() => {
  if (supabaseAuthed.value) return 'Google';
  return tr('尚未登录', 'Not signed in');
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

function orderPreview(order: OrderRead): string {
  const asset = displayAsset(order);
  return resolvePublicUrl(asset?.download_path || '/style-previews/royal_castle.jpg');
}

function statusText(status: OrderRead['status']): string {
  const normalized = String(status || '').toUpperCase();
  const map: Record<string, string> = {
    CREATED: tr('已创建', 'Created'),
    CHECKING: tr('检测中', 'Checking'),
    GENERATING: tr('生成中', 'Generating'),
    COMPLETED: tr('已完成', 'Completed'),
    FAILED: tr('失败', 'Failed'),
    REFUNDED: tr('已退款', 'Refunded'),
  };
  Object.assign(map, {
    QUEUED: tr('已排队', 'Queued'),
    QA_PENDING: tr('质检中', 'Quality checking'),
    REPAIRING: tr('修复中', 'Repairing'),
    READY: tr('已交付', 'Delivered'),
    CANCELLED: tr('已取消', 'Cancelled'),
    UNKNOWN_EXTERNAL_STATE: tr('等待人工对账', 'Manual reconciliation'),
    CONSENT_REVIEW_REQUIRED: tr('等待授权复核', 'Consent review'),
    DELETED: tr('已删除', 'Deleted'),
  });
  return map[normalized] || normalized;
}

function statusClass(status: OrderRead['status']): string {
  if (isOrderDeliverable(status)) return 'completed';
  if (isOrderManualOrFailed(status)) return 'failed';
  return 'pending';
}

async function loadAccount(): Promise<void> {
  loading.value = true;
  error.value = '';
  adminAccess.value = false;

  try {
    const sessionUser = await ensureSession();
    supabaseAuthed.value = Boolean(sessionUser);
    profile.value = sessionUser;
    if (!sessionUser) {
      balance.value = null;
      transactions.value = [];
      orders.value = [];
      legalPolicies.value = await get<LegalPolicies>('/legal/policies', { showLoading: false, showError: false });
      return;
    }
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
      get<OrderRead[]>('/orders', { showLoading: false, showError: false }),
      get<LegalPolicies>('/legal/policies', { showLoading: false, showError: false }),
      get('/admin/me', { showLoading: false, showError: false }),
      subscriptionStore.fetchPlans(true),
      subscriptionStore.fetchCurrentSubscription(true),
    ]);

    profile.value = profileResult.status === 'fulfilled' ? profileResult.value : null;
    balance.value = balanceResult.status === 'fulfilled' ? balanceResult.value : null;
    transactions.value = transactionsResult.status === 'fulfilled' ? (transactionsResult.value.transactions || []) : [];
    orders.value = ordersResult.status === 'fulfilled'
      ? ordersResult.value.slice(0, 6)
      : [];
    legalPolicies.value = legalResult.status === 'fulfilled' ? legalResult.value : null;
    adminAccess.value = adminResult.status === 'fulfilled';
    supabaseAuthed.value = profile.value !== null;
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
  if (!googleAuthAvailable.value) return;
  try {
    await signInWithGoogle();
  } catch (err: any) {
    uni.showToast({ title: err?.message || tr('登录失败', 'Sign-in failed'), icon: 'none' });
  }
}

async function signOut(): Promise<void> {
  await logout();
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

async function exportAccountData(): Promise<void> {
  exportBusy.value = true;
  exportMessage.value = '';
  try {
    const exported = await downloadAccountExport();
    exportMessage.value = tr(
      `已生成 ${new Date(exported.generated_at).toLocaleString('zh-CN')} 的 ${exported.schema_version} 文件。`,
      `Downloaded ${exported.schema_version}, generated ${new Date(exported.generated_at).toLocaleString('en-US')}.`,
    );
  } catch (err: any) {
    exportMessage.value = err?.message || tr(
      '账户数据暂时无法导出，请稍后重试。',
      'Account data could not be exported. Please try again.',
    );
  } finally {
    exportBusy.value = false;
  }
}

async function recoverLegacyAccount(): Promise<void> {
  if (!legacyAccountId.value || !paymentReference.value) {
    uni.showToast({ title: tr('请填写旧账户 ID 和付款参考号', 'Enter the legacy account ID and payment reference'), icon: 'none' });
    return;
  }
  claimBusy.value = true;
  claimMessage.value = '';
  try {
    const proof = await post<{ proof_id: string; expires_at: string }>(
      '/auth/account-claims/payment-proof',
      { legacy_user_id: legacyAccountId.value, payment_reference: paymentReference.value },
      { showLoading: false },
    );
    await post(
      '/auth/account-claims/merge',
      { legacy_user_id: legacyAccountId.value, proof_id: proof.proof_id },
      { showLoading: false },
    );
    claimMessage.value = tr('空账户已安全合并。', 'The empty legacy account was merged safely.');
    legacyAccountId.value = '';
    paymentReference.value = '';
    await loadAccount();
  } catch (err: any) {
    claimMessage.value = err?.code === 'commercial_lineage_not_ready'
      ? tr('该账户含商业或媒体记录，当前不会自动改绑。', 'This account contains commercial or media facts and will not be rebound automatically.')
      : (err?.message || tr('账户无法合并。', 'The account could not be merged.'));
  } finally {
    claimBusy.value = false;
  }
}

async function closeAccount(): Promise<void> {
  if (closeConfirmation.value !== 'CLOSE MY ACCOUNT') return;
  const confirmation = await uni.showModal({
    title: tr('确认关闭账户', 'Confirm account closure'),
    content: tr('登录会立即失效；财务记录保留，媒体清理仍处于待处理状态。', 'Sign-in ends immediately. Financial records remain, and media cleanup remains pending.'),
    confirmText: tr('确认关闭', 'Close account'),
    cancelText: tr('取消', 'Cancel'),
  });
  if (!confirmation.confirm) return;
  closeBusy.value = true;
  try {
    await post<{ closed_at: string; media_cleanup_pending: boolean }>(
      '/users/me/close',
      { confirmation: 'CLOSE MY ACCOUNT' },
      { showLoading: false },
    );
    clearCachedSession();
    supabaseAuthed.value = false;
    profile.value = null;
    closeConfirmation.value = '';
    await uni.showModal({
      title: tr('账户已关闭', 'Account closed'),
      content: tr('身份与会话已撤销。财务记录会保留，媒体清理仍处于待处理状态。', 'Identity and sessions are revoked. Financial records remain, and media cleanup remains pending.'),
      showCancel: false,
    });
    await loadAccount();
  } catch (err: any) {
    uni.showToast({ title: err?.message || tr('账户关闭失败', 'Account closure failed'), icon: 'none' });
  } finally {
    closeBusy.value = false;
  }
}

function goCreate(): void {
  if (!creationAvailable.value) return;
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

function orderHref(orderId: string): string {
  return `/pages/preview/preview?id=${encodeURIComponent(orderId)}`;
}

onMounted(async () => {
  await Promise.all([
    opsStore.fetchPublicConfig(),
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

.account-controls-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
  margin-top: 20px;
}

.account-control-card {
  padding: 24px;
}

.control-copy,
.control-status {
  display: block;
  margin-top: 12px;
  color: #4c5360;
  font-size: 13px;
  line-height: 1.65;
}

.control-input {
  box-sizing: border-box;
  width: 100%;
  min-height: 44px;
  margin-top: 12px;
  padding: 0 12px;
  border: 1px solid #cfd5dd;
  border-radius: 8px;
  background: #ffffff;
  color: #17191f;
}

.control-button {
  width: 100%;
  min-height: 44px;
  margin-top: 14px;
}

.danger-card {
  border-color: #efc7c2;
}

.danger-kicker {
  color: #b42318;
}

.danger-button {
  border: 1px solid #b42318;
  background: #b42318;
  color: #ffffff;
}

.danger-button[disabled] {
  opacity: 0.5;
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

.order-row {
  color: inherit;
  text-decoration: none;
}

.btn:focus-visible,
.mini-link:focus-visible,
.order-row:focus-visible {
  outline: 3px solid #116a60;
  outline-offset: 3px;
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

.paused-delete {
  color: #8a5b12;
  font-size: 12px;
  font-weight: 800;
}

@media (max-width: 980px) {
  .account-hero {
    align-items: flex-start;
    flex-direction: column;
  }

  .overview-grid,
  .content-grid,
  .account-controls-grid {
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
