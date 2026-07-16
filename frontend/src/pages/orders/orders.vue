<template>
  <view class="app-container orders-page" style="padding-top: 64px;">
    <NavBar />

    <view class="orders-shell">
      <view class="orders-header">
        <text class="orders-kicker">{{ tr('作品档案', 'Archive') }}</text>
        <text class="orders-title heading-serif">{{ t('orders.title') }}</text>
        <text class="orders-subtitle">{{ t('orders.subtitle') }}</text>
      </view>

      <view v-if="orders.length" class="orders-stats">
        <view v-for="stat in orderStats" :key="stat.key" class="stat-card">
          <text class="stat-label">{{ stat.label }}</text>
          <text class="stat-value heading-serif">{{ stat.value }}</text>
        </view>
      </view>

      <view v-if="loading" class="state-card">
        <text class="state-title">{{ t('orders.loading') }}</text>
      </view>

      <view v-else-if="authRequired" class="state-card">
        <text class="state-title">{{ t('orders.signin_required') }}</text>
        <text class="state-subtitle">{{ t('orders.signin_required_subtitle') }}</text>
        <button v-if="googleAuthAvailable" class="btn btn-primary state-action" @tap="goLogin">{{ t('orders.signin') }}</button>
      </view>

      <view v-else-if="orders.length === 0" class="state-card">
        <text class="state-title">{{ t('orders.empty') }}</text>
        <button v-if="creationAvailable" class="btn btn-primary state-action" @tap="goToCreate">{{ t('orders.start') }}</button>
      </view>

      <view v-else class="orders-grid">
        <view v-for="order in orders" :key="order.id" class="order-card" @tap="viewOrder(order.id)">
          <view class="order-media-wrap">
            <image :src="order.previewUrl" mode="aspectFit" class="order-media" />
            <view class="status-badge" :class="badgeClass(order.status)">
              {{ getStatusText(order.status) }}
            </view>
          </view>
          <view class="order-content">
            <text class="order-title heading-serif">{{ order.styleName }}</text>
            <view class="order-meta">
              <text>{{ t('orders.created_at') }}</text>
              <text>{{ order.createdAt }}</text>
            </view>
            <view class="order-footer">
              <text class="order-id">#{{ order.id.slice(0, 8) }}</text>
              <text class="order-link">{{ t('orders.view') }} ></text>
            </view>
          </view>
        </view>
      </view>

      <view v-if="error && !loading" class="inline-error">
        <text>{{ error }}</text>
        <button class="btn btn-outline retry-btn" @tap="refresh">{{ t('orders.retry') }}</button>
      </view>
    </view>
    <LegalFooter />
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import NavBar from '../../components/NavBar.vue';
import LegalFooter from '../../components/LegalFooter.vue';
import { useI18nStore } from '../../stores/i18n';
import { useOpsStore } from '../../stores/ops';
import { getLocalizedTemplateTitle, useTemplateStore } from '../../stores/template';
import { get, resolvePublicUrl } from '../../utils/api';

interface Order {
  id: string;
  template_id: string | null;
  preview_image_urls: Record<string, string> | null;
  final_image_urls: Record<string, string> | null;
  preview_master_image_url?: string | null;
  final_master_image_url?: string | null;
  created_at: string;
  status: string;
  error_message?: string | null;
  failure_code?: string | null;
}

interface DisplayOrder {
  id: string;
  styleName: string;
  status: string;
  previewUrl: string;
  createdAt: string;
}

type OrdersResponse = Order[] | { value?: Order[]; items?: Order[]; results?: Order[]; orders?: Order[] };

const orders = ref<DisplayOrder[]>([]);
const loading = ref(true);
const error = ref('');
const authRequired = ref(false);
const i18nStore = useI18nStore();
const opsStore = useOpsStore();
const templateStore = useTemplateStore();
const t = i18nStore.t;
const tf = i18nStore.tf;
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
const creationAvailable = computed(() => opsStore.creationAvailable);
const googleAuthAvailable = computed(() => opsStore.googleAuthAvailable);

const templateTitleMap = computed(() => {
  const map = new Map<string, string>();
  templateStore.templates.forEach((template) => {
    map.set(template.id, getLocalizedTemplateTitle(template, i18nStore.locale));
  });
  return map;
});

const orderStats = computed(() => {
  const completed = orders.value.filter((order) => order.status === 'COMPLETED').length;
  const generating = orders.value.filter((order) => ['CREATED', 'CHECKING', 'GENERATING'].includes(order.status)).length;
  return [
    { key: 'total', label: tr('总作品', 'Total'), value: orders.value.length },
    { key: 'completed', label: tr('已交付', 'Delivered'), value: completed },
    { key: 'generating', label: tr('处理中', 'In Progress'), value: generating },
  ];
});

const deliveryVariantSuffixes = ['portrait_2x3', 'print_3x2', 'xhs_3x4', 'portrait_4x5', 'wallpaper_9x16', 'square_1x1'];
function pickPrimaryFromMap(urls: Record<string, string> | null): string | null {
  if (!urls) return null;
  if (urls.image_1) return urls.image_1;
  const master = Object.entries(urls).find(([key]) => !deliveryVariantSuffixes.some((suffix) => key.includes(suffix)));
  if (master?.[1]) return master[1];
  return Object.values(urls)[0] || null;
}

function pickPrimaryImage(order: Order): string {
  if (order.final_master_image_url) return resolvePublicUrl(order.final_master_image_url);
  if (order.preview_master_image_url) return resolvePublicUrl(order.preview_master_image_url);

  const final = pickPrimaryFromMap(order.final_image_urls);
  if (final) return resolvePublicUrl(final);

  const preview = pickPrimaryFromMap(order.preview_image_urls);
  if (preview) return resolvePublicUrl(preview);

  return resolvePublicUrl('/style-previews/royal_castle.jpg');
}

function formatDate(isoString: string): string {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return '--';

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return t('orders.just_now');
  if (diffMins < 60) return tf('orders.minutes_ago', { count: diffMins });
  if (diffHours < 24) return tf('orders.hours_ago', { count: diffHours });
  if (diffDays < 7) return tf('orders.days_ago', { count: diffDays });

  return date.toLocaleDateString(i18nStore.locale === 'zh' ? 'zh-CN' : 'en-US', {
    month: i18nStore.locale === 'zh' ? 'numeric' : 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function resolveStyleName(order: Order): string {
  if (order.template_id && templateTitleMap.value.has(order.template_id)) {
    return templateTitleMap.value.get(order.template_id) || t('orders.custom');
  }
  if (order.template_id) return order.template_id;
  return t('orders.custom');
}

function normalizeOrderRows(response: OrdersResponse): Order[] {
  if (Array.isArray(response)) return response;
  if (Array.isArray(response?.value)) return response.value;
  if (Array.isArray(response?.items)) return response.items;
  if (Array.isArray(response?.results)) return response.results;
  if (Array.isArray(response?.orders)) return response.orders;
  return [];
}

async function fetchOrders() {
  loading.value = true;
  error.value = '';
  authRequired.value = false;
  const hadExistingOrders = orders.value.length > 0;

  try {
    if (!templateStore.templates.length) {
      await templateStore.fetchTemplates();
    }
    const response = await get<OrdersResponse>('/orders', { showLoading: false, showError: false });
    const rows = normalizeOrderRows(response);
    orders.value = rows.map((order) => ({
      id: order.id,
      styleName: resolveStyleName(order),
      status: order.error_message || order.failure_code ? 'FAILED' : String(order.status || '').toUpperCase(),
      previewUrl: pickPrimaryImage(order),
      createdAt: formatDate(order.created_at),
    }));
  } catch (err: any) {
    const statusCode = Number(err?.statusCode || 0);
    if (statusCode === 401 || statusCode === 403) {
      authRequired.value = true;
      error.value = '';
    } else if (hadExistingOrders) {
      error.value = err?.message || t('orders.load_failed');
    } else {
      error.value = '';
    }
    orders.value = [];
  } finally {
    loading.value = false;
  }
}

function getStatusText(status: string): string {
  const statusMap: Record<string, string> = {
    CREATED: t('orders.status_created'),
    CHECKING: t('orders.status_checking'),
    GENERATING: t('orders.status_generating'),
    COMPLETED: t('orders.status_completed'),
    FAILED: t('orders.status_failed'),
    REFUNDED: t('orders.status_refunded'),
  };
  return statusMap[status] || status;
}

function badgeClass(status: string): string {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'completed') return 'completed';
  if (normalized === 'failed' || normalized === 'refunded') return 'failed';
  return 'pending';
}

function goToCreate() {
  if (!creationAvailable.value) return;
  uni.navigateTo({ url: '/pages/create/index' });
}

function goLogin() {
  if (!googleAuthAvailable.value) return;
  uni.navigateTo({ url: '/pages/auth/login' });
}

function viewOrder(orderId: string) {
  uni.navigateTo({ url: `/pages/preview/preview?id=${orderId}` });
}

function refresh() {
  fetchOrders();
}

onMounted(async () => {
  await opsStore.fetchPublicConfig();
  await fetchOrders();
});
</script>

<style lang="scss" scoped>
.orders-page {
  min-height: 100vh;
  background: #f7f8fa;
}

.orders-shell {
  max-width: 1240px;
  margin: 0 auto;
  padding: 36px 28px 88px;
}

.orders-header {
  max-width: 760px;
  margin-bottom: 24px;
}

.orders-kicker,
.stat-label {
  display: block;
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
}

.orders-title {
  display: block;
  margin-top: 8px;
  color: #17191f;
  font-size: 48px;
  line-height: 1.05;
}

.orders-subtitle,
.state-subtitle {
  display: block;
  margin-top: 10px;
  color: #4c5360;
  font-size: 14px;
  line-height: 1.7;
}

.orders-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}

.stat-card,
.state-card,
.order-card,
.inline-error {
  border: 1px solid #dde1e8;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 14px 38px rgba(23, 25, 31, 0.06);
}

.stat-card {
  padding: 18px;
}

.stat-value {
  display: block;
  margin-top: 8px;
  color: #17191f;
  font-size: 32px;
  line-height: 1;
}

.state-card {
  padding: 36px;
  text-align: center;
}

.state-title {
  display: block;
  color: #17191f;
  font-size: 22px;
  font-weight: 900;
}

.state-action {
  margin-top: 18px;
  min-height: 44px;
  padding: 0 22px;
}

.orders-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}

.order-card {
  overflow: hidden;
}

.order-media-wrap {
  position: relative;
  aspect-ratio: 3 / 4;
  background: #eef1f4;
}

.order-media {
  width: 100%;
  height: 100%;
  display: block;
}

.status-badge {
  position: absolute;
  top: 12px;
  left: 12px;
  padding: 7px 10px;
  border-radius: 8px;
  background: rgba(23, 25, 31, 0.78);
  color: #ffffff;
  font-size: 12px;
  font-weight: 900;
}

.status-badge.completed {
  background: rgba(17, 106, 96, 0.9);
}

.status-badge.failed {
  background: rgba(180, 35, 24, 0.9);
}

.order-content {
  padding: 18px;
}

.order-title {
  display: block;
  color: #17191f;
  font-size: 22px;
  line-height: 1.15;
}

.order-meta,
.order-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
  color: #6b7280;
  font-size: 13px;
}

.order-link {
  color: #116a60;
  font-weight: 900;
}

.inline-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 18px;
  padding: 16px 18px;
  color: #b42318;
  font-size: 14px;
}

.retry-btn {
  min-height: 40px;
  padding: 0 16px;
}

@media (max-width: 980px) {
  .orders-stats,
  .orders-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .orders-shell {
    padding: 28px 18px 72px;
  }

  .orders-title {
    font-size: 38px;
  }

  .orders-stats,
  .orders-grid {
    grid-template-columns: 1fr;
  }
}
</style>
