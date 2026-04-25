<template>
  <view class="legacy-upload-redirect">
    <text class="redirect-title heading-serif">{{ redirectTitle }}</text>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue';

const currentLocale = computed(() => {
  try {
    return uni.getStorageSync('aws_locale') === 'en' ? 'en' : 'zh';
  } catch {
    return 'zh';
  }
});

const redirectTitle = computed(() => currentLocale.value === 'en' ? 'Redirecting...' : '正在跳转...');

onMounted(() => {
  const pages = getCurrentPages();
  const query = ((pages[pages.length - 1] as any)?.options || {}) as Record<string, string>;
  const id = String(query.id || '').trim();
  const mode = String(query.mode || '').trim();
  const nextQuery = [
    id ? `id=${encodeURIComponent(id)}` : '',
    mode ? `mode=${encodeURIComponent(mode)}` : '',
  ].filter(Boolean).join('&');
  const target = `/pages/create/index${nextQuery ? `?${nextQuery}` : ''}`;

  uni.redirectTo({ url: target });
});
</script>

<style scoped>
.legacy-upload-redirect {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}

.redirect-title {
  font-size: 28px;
  color: #831843;
}
</style>
