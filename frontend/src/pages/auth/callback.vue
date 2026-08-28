<template>
  <view class="callback-page">
    <view class="callback-card">
      <button
        class="callback-language-toggle"
        :aria-label="tr('切换到英文', 'Switch to Chinese')"
        @tap="i18nStore.toggleLocale()"
      >{{ localeButtonText }}</button>
      <text class="callback-title">{{ error || tr('正在完成安全登录…', 'Completing secure sign-in…') }}</text>
      <text v-if="!error" class="callback-copy">{{ tr('通常几秒内即可完成，请勿关闭页面。', 'This usually takes only a few seconds. Keep this page open.') }}</text>
      <button v-if="error" class="retry-button" @tap="retry">{{ tr('重新开始', 'Start again') }}</button>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18nStore } from '../../stores/i18n';
import { finishGoogleLogin, localizedAuthError } from '../../services/auth';

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
const localeButtonText = computed(() => (i18nStore.locale === 'zh' ? 'EN' : '中文'));
const rawError = ref<unknown>(null);
const error = computed(() => rawError.value ? localizedAuthError(rawError.value, i18nStore.locale) : '');

function retry() {
  uni.reLaunch({ url: '/pages/auth/login' });
}

onMounted(async () => {
  try {
    await finishGoogleLogin();
  } catch (err: any) {
    rawError.value = err || new Error('Google sign-in was not completed.');
  }
});
</script>

<style scoped>
.callback-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: #f7f8fa;
}

.callback-card {
  position: relative;
  width: min(440px, 100%);
  padding: 32px;
  border: 1px solid #dde1e8;
  border-radius: 16px;
  background: #fff;
  text-align: center;
}

.callback-language-toggle {
  position: absolute;
  top: 14px;
  right: 14px;
  width: auto;
  min-width: 48px;
  margin: 0;
  padding: 6px 10px;
  border: 1px solid #d7dfdd;
  border-radius: 999px;
  background: #fff;
  color: #116a60;
  font-size: 12px;
  font-weight: 800;
  line-height: 1.2;
}

.callback-title {
  display: block;
  color: #17191f;
  font-size: 18px;
  font-weight: 700;
}

.callback-copy {
  display: block;
  margin-top: 10px;
  color: #4c5360;
  font-size: 13px;
  line-height: 1.6;
}

.retry-button {
  margin-top: 20px;
}
</style>
