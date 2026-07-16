<template>
  <view class="auth-page" role="main">
    <view class="auth-shell">
      <view class="brand" @tap="goHome">
        <text class="brand-title heading-serif">VowPic</text>
        <text class="brand-subtitle">
          {{ tr('使用 Google 创建账号，积分、订单和后续购买都会绑定到同一个已验证身份。', 'Create your account with Google so credits, orders, and future purchases stay under one verified identity.') }}
        </text>
      </view>

      <view class="auth-card">
        <text class="auth-kicker">{{ tr('创建账号', 'Create account') }}</text>
        <text class="auth-title heading-serif" role="heading" aria-level="1">{{ tr('从 Google 开始', 'Start with Google') }}</text>
        <text class="auth-copy">
          {{ tr('VowPic 使用 Google 作为网站的统一注册和登录方式。', 'VowPic uses Google as the single registration and sign-in method for the web app.') }}
        </text>

        <view class="form-stack">
          <text v-if="error" class="error-text">{{ error }}</text>

          <button
            v-if="googleAuthAvailable && supabaseEnabled"
            class="btn btn-primary auth-button google-button"
            :disabled="submitting"
            @tap="googleSignIn"
          >
            <text class="google-mark">G</text>
            <text>{{ submitting ? tr('连接中...', 'Connecting...') : tr('使用 Google 创建', 'Create with Google') }}</text>
          </button>

          <button v-else class="btn btn-primary auth-button" disabled>
            {{ tr('Google 登录暂不可用', 'Google sign-in unavailable') }}
          </button>

          <view class="auth-note">
            <text class="auth-note-title">{{ tr('试用规则', 'Trial rules') }}</text>
            <text class="auth-note-line">{{ tr('欢迎积分只发放给首次验证的 Google 账号。', 'Starter credits are granted once per verified Google account.') }}</text>
            <text class="auth-note-line">{{ tr('付费积分用于解锁高清和无水印交付。', 'Paid credits unlock HD and watermark-free delivery.') }}</text>
          </view>
        </view>

        <view class="auth-footer">
          <text>{{ tr('已经登录？', 'Already signed in?') }}</text>
          <text class="link" @tap="goAccount">{{ tr('打开账户', 'Open account') }}</text>
        </view>
        <text class="home-link" @tap="goHome">{{ tr('返回首页', 'Back to home') }}</text>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18nStore } from '../../stores/i18n';
import { useOpsStore } from '../../stores/ops';
import { signInWithGoogle } from '../../utils/auth';
import { refreshSupabaseConfig } from '../../utils/supabase';

const i18nStore = useI18nStore();
const opsStore = useOpsStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);
const googleAuthAvailable = computed(() => opsStore.googleAuthAvailable);

const supabaseEnabled = ref(false);
const submitting = ref(false);
const error = ref('');

async function googleSignIn() {
  if (!googleAuthAvailable.value) return;
  if (submitting.value) return;
  submitting.value = true;
  error.value = '';
  try {
    await signInWithGoogle();
  } catch (err: any) {
    error.value = err?.message || tr('Google sign-in failed.', 'Google sign-in failed.');
    submitting.value = false;
  }
}

function goAccount() {
  uni.navigateTo({ url: '/pages/account/index' });
}

function goHome() {
  uni.reLaunch({ url: '/pages/index/index' });
}

onMounted(async () => {
  await opsStore.fetchPublicConfig();
  if (!googleAuthAvailable.value) {
    error.value = tr('当前部署暂未开放登录。', 'Sign-in is temporarily unavailable on this deployment.');
    return;
  }
  supabaseEnabled.value = await refreshSupabaseConfig(true);
  if (!supabaseEnabled.value) {
    error.value = tr('Google sign-in is not configured on this deployment.', 'Google sign-in is not configured on this deployment.');
  }
});
</script>

<style lang="scss" scoped>
@use './auth.scss';
</style>
