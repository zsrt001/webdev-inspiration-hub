<template>
  <view class="result-redirect-page">
    <NavBar />

    <view class="result-shell">
      <view class="result-card shadow-xl">
        <text class="eyebrow">{{ tr('结果中心', 'Result Center') }}</text>
        <text class="title heading-serif">{{ tr('成片结果已统一到结果页查看', 'Results now live in the preview page') }}</text>
        <text class="desc">
          {{ redirecting
            ? tr('正在跳转到统一结果页，请稍候。', 'Redirecting to the unified result page...')
            : tr('如果未自动跳转，可以手动进入结果页或返回首页。', 'If the redirect does not happen automatically, open the preview page or go back home.') }}
        </text>

        <view class="action-row">
          <button class="btn btn-primary" @tap="openPreview" :disabled="!resolvedOrderId">
            {{ tr('进入结果页', 'Open Preview') }}
          </button>
          <button class="btn btn-outline" @tap="goHome">
            {{ tr('返回首页', 'Back Home') }}
          </button>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import NavBar from '../../components/NavBar.vue';
import { useOrderStore } from '../../stores/order';
import { useI18nStore } from '../../stores/i18n';

const orderStore = useOrderStore();
const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const redirecting = ref(true);
const routeOrderId = ref('');

const resolvedOrderId = computed(() => {
  return String(routeOrderId.value || orderStore.currentOrder?.id || '').trim();
});

const previewUrl = computed(() => {
  return resolvedOrderId.value
    ? `/pages/preview/preview?id=${encodeURIComponent(resolvedOrderId.value)}`
    : '';
});

const openPreview = () => {
  if (!previewUrl.value) {
    uni.showToast({ title: tr('未找到结果记录', 'No result found'), icon: 'none' });
    return;
  }
  uni.redirectTo({ url: previewUrl.value });
};

const goHome = () => {
  uni.switchTab({ url: '/pages/index/index' });
};

onMounted(() => {
  const pages = getCurrentPages();
  const currentPage = pages[pages.length - 1] as any;
  routeOrderId.value = String(currentPage?.options?.id || '').trim();

  if (previewUrl.value) {
    setTimeout(() => {
      openPreview();
    }, 120);
    return;
  }

  redirecting.value = false;
});
</script>

<style lang="scss" scoped>
.result-redirect-page {
  min-height: 100vh;
  background:
    radial-gradient(circle at top right, rgba(244, 114, 182, 0.08), transparent 30%),
    linear-gradient(180deg, #fffdfd 0%, #fff7fb 100%);
}

.result-shell {
  min-height: calc(100vh - 64px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 96px 24px 48px;
}

.result-card {
  width: min(680px, 100%);
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba($uni-color-primary, 0.12);
  border-radius: 32px;
  padding: 40px;
  box-shadow: 0 24px 60px rgba(23, 25, 31, 0.1);
}

.eyebrow {
  display: block;
  margin-bottom: 14px;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.24em;
  color: $uni-color-accent;
}

.title {
  display: block;
  margin-bottom: 16px;
  font-size: clamp(32px, 4vw, 48px);
  line-height: 1.05;
  color: $uni-text-color;
  font-style: italic;
}

.desc {
  display: block;
  font-size: 15px;
  line-height: 1.8;
  color: $uni-text-color-muted;
}

.action-row {
  margin-top: 28px;
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}

@media (max-width: 768px) {
  .result-shell {
    padding: 84px 16px 32px;
  }

  .result-card {
    padding: 28px 22px;
    border-radius: 24px;
  }

  .title {
    font-size: 34px;
  }

  .action-row {
    flex-direction: column;
  }
}
</style>
