<template>
  <view class="app-container orders-page" style="padding-top: 64px;">
    <NavBar />

    <view class="orders-shell">
      <view class="orders-hero">
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
        <text class="state-icon">◌</text>
        <text class="state-title">{{ t('orders.loading') }}</text>
      </view>

      <view v-else-if="authRequired" class="state-card">
        <text class="state-icon">◇</text>
        <text class="state-title">{{ t('orders.signin_required') }}</text>
        <text class="state-subtitle">{{ t('orders.signin_required_subtitle') }}</text>
        <button class="btn btn-primary state-action" @tap="goLogin">{{ t('orders.signin') }}</button>
      </view>

      <view v-else-if="orders.length === 0" class="state-card">
        <text class="state-icon">◇</text>
        <text class="state-title">{{ t('orders.empty') }}</text>
        <button class="btn btn-primary state-action" @tap="goToHome">{{ t('orders.start') }}</button>
      </view>

      <view v-else class="orders-grid">
        <view v-for="order in orders" :key="order.id" class="order-card" @tap="viewOrder(order.id)">
          <view class="order-media-wrap">
            <image :src="order.previewUrl" mode="aspectFill" class="order-media" />
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
              <text class="order-link">{{ t('orders.view') }} →</text>
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
import { getLocalizedTemplateTitle, useTemplateStore } from '../../stores/template';
import { get, resolvePublicUrl } from '../../utils/api';

interface Order {
  id: string;
  template_id: string | null;
  preview_image_urls: Record<string, string> | null;
  final_image_urls: Record<string, string> | null;
  created_at: string;
  status: string;
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
const templateStore = useTemplateStore();
const t = i18nStore.t;
const tf = i18nStore.tf;
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const templateTitleMap = computed(() => {
  const map = new Map<string, string>();
  templateStore.templates.forEach((template) => {
    map.set(template.id, getLocalizedTemplateTitle(template, i18nStore.locale));
  });
  return map;
});

const pickPrimaryImage = (order: Order): string => {
  const final = order.final_image_urls ? Object.values(order.final_image_urls) : [];
  if (final.length && final[0]) return resolvePublicUrl(final[0]);

  const preview = order.preview_image_urls ? Object.values(order.preview_image_urls) : [];
  if (preview.length && preview[0]) return resolvePublicUrl(preview[0]);

  return resolvePublicUrl('/style-previews/couple_royal_castle.jpg');
};

const formatDate = (isoString: string): string => {
  const date = new Date(isoString);
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
};

const resolveStyleName = (order: Order): string => {
  if (order.template_id && templateTitleMap.value.has(order.template_id)) {
    return templateTitleMap.value.get(order.template_id) || t('orders.custom');
  }
  if (order.template_id) return order.template_id;
  return t('orders.custom');
};

const normalizeOrderRows = (response: OrdersResponse): Order[] => {
  if (Array.isArray(response)) return response;
  if (Array.isArray(response?.value)) return response.value;
  if (Array.isArray(response?.items)) return response.items;
  if (Array.isArray(response?.results)) return response.results;
  if (Array.isArray(response?.orders)) return response.orders;
  return [];
};

const orderStats = computed(() => {
  const completed = orders.value.filter((order) => order.status === 'COMPLETED').length;
  const generating = orders.value.filter((order) => ['CREATED', 'CHECKING', 'GENERATING'].includes(order.status)).length;
  const drafts = orders.value.filter((order) => order.status !== 'COMPLETED').length;
  return [
    { key: 'total', label: tr('总作品', 'Total'), value: orders.value.length },
    { key: 'completed', label: tr('已交付', 'Delivered'), value: completed },
    { key: 'generating', label: tr('处理中', 'In Progress'), value: generating },
    { key: 'drafts', label: tr('待确认', 'Drafts'), value: drafts },
  ];
});

const fetchOrders = async () => {
  loading.value = true;
  error.value = '';
  authRequired.value = false;
  const hadExistingOrders = orders.value.length > 0;

  try {
    if (!templateStore.templates.length) {
      await templateStore.fetchTemplates();
    }
    const response = await get<OrdersResponse>('/orders/', { showLoading: false, showError: false });
    const rows = normalizeOrderRows(response);
    orders.value = rows.map((order) => ({
      id: order.id,
      styleName: resolveStyleName(order),
      status: String(order.status || '').toUpperCase(),
      previewUrl: pickPrimaryImage(order),
      createdAt: formatDate(order.created_at),
    }));
  } catch (err) {
    console.error('Failed to fetch orders:', err);
    const statusCode = Number((err as any)?.statusCode || 0);
    if (statusCode === 401 || statusCode === 403) {
      authRequired.value = true;
      error.value = '';
    } else if (hadExistingOrders) {
      error.value = t('orders.load_failed');
    } else {
      error.value = '';
    }
    orders.value = [];
  } finally {
    loading.value = false;
  }
};

onMounted(fetchOrders);

const getStatusText = (status: string): string => {
  const statusMap: Record<string, string> = {
    CREATED: t('orders.status_created'),
    CHECKING: t('orders.status_checking'),
    GENERATING: t('orders.status_generating'),
    COMPLETED: t('orders.status_completed'),
    FAILED: t('orders.status_failed'),
    REFUNDED: t('orders.status_refunded'),
  };
  return statusMap[status] || status;
};

const badgeClass = (status: string) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'completed') return 'completed';
  if (normalized === 'failed' || normalized === 'refunded') return 'failed';
  return 'pending';
};

const goToHome = () => {
  uni.reLaunch({ url: '/pages/index/index' });
};

const goLogin = () => {
  uni.navigateTo({ url: '/pages/auth/login' });
};

const viewOrder = (orderId: string) => {
  uni.navigateTo({ url: `/pages/preview/preview?id=${orderId}` });
};

const refresh = () => {
  fetchOrders();
};
</script>

<style lang="scss" scoped>
.orders-page {
  min-height: 100vh;
  background: #f7f8fa;
}

.orders-shell {
  max-width: 1400px;
  margin: 0 auto;
  padding: 36px 28px 88px;
}

.orders-hero {
  max-width: 760px;
  margin-bottom: 24px;
}

.orders-kicker {
  display: block;
  margin-bottom: 10px;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
  color: #116a60;
}

.orders-title {
  display: block;
  font-size: 52px;
  line-height: 1;
  color: #17191f;
  margin-bottom: 10px;
}

.orders-subtitle {
  display: block;
  max-width: 640px;
  font-size: 15px;
  line-height: 1.8;
  color: #4c5360;
}

.orders-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 28px;
}

.stat-card,
.order-card,
.state-card,
.inline-error {
  background: #ffffff;
  border: 1px solid #dde1e8;
  border-radius: 8px;
  box-shadow: 0 14px 38px rgba(23, 25, 31, 0.06);
}

.stat-card {
  padding: 18px 18px 20px;
}

.stat-label {
  display: block;
  margin-bottom: 8px;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0;
  color: #6b7280;
}

.stat-value {
  display: block;
  font-size: 34px;
  line-height: 1;
  color: #17191f;
}

.orders-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 24px;
}

.order-card {
  overflow: hidden;
}

.order-media-wrap {
  position: relative;
  background: #d9dde3;
}

.order-media {
  width: 100%;
  aspect-ratio: 4 / 5;
  display: block;
  object-fit: cover;
  object-position: center top;
}

.status-badge {
  position: absolute;
  top: 16px;
  right: 16px;
  min-height: 34px;
  padding: 0 12px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0;
  background: rgba(255, 255, 255, 0.95);
  color: #17191f;

  &.completed {
    background: rgba(16, 185, 129, 0.14);
    color: #047857;
  }

  &.failed {
    background: rgba(239, 68, 68, 0.12);
    color: #b91c1c;
  }

  &.pending {
    background: rgba(17, 106, 96, 0.12);
    color: #116a60;
  }
}

.order-content {
  padding: 18px 18px 20px;
}

.order-title {
  display: block;
  font-size: 28px;
  line-height: 1.08;
  color: #17191f;
  margin-bottom: 12px;
}

.order-meta,
.order-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.order-meta {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 14px;
}

.order-footer {
  padding-top: 14px;
  border-top: 1px solid #edf0f4;
}

.order-id,
.order-link {
  font-size: 12px;
  font-weight: 700;
}

.order-id {
  color: #6b7280;
}

.order-link {
  color: #116a60;
}

.state-card,
.inline-error {
  padding: 56px 24px;
  text-align: center;
}

.state-icon {
  display: block;
  font-size: 34px;
  color: #116a60;
  margin-bottom: 14px;
}

.state-title {
  display: block;
  font-size: 16px;
  color: #17191f;
}

.state-subtitle {
  display: block;
  max-width: 460px;
  margin: 10px auto 0;
  font-size: 13px;
  line-height: 1.7;
  color: #6b7280;
}

.state-action,
.retry-btn {
  margin: 20px auto 0;
}

.inline-error {
  margin-top: 22px;
}

@media (max-width: 1180px) {
  .orders-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .orders-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
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

  .orders-grid {
    gap: 18px;
  }

  .order-title {
    font-size: 24px;
  }

  .order-meta,
  .order-footer {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
