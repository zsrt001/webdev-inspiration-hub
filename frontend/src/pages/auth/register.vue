<template>
  <view class="auth-page">
    <view class="auth-shell">
      <view class="brand" @tap="goHome">
        <text class="brand-title heading-serif">VowPic</text>
        <text class="brand-subtitle">
          {{ tr('使用 Google 创建账号。访客订单、积分和后续购买会归并到同一个已验证身份。', 'Create your account with Google. Your guest orders, credits, and future purchases stay under one verified identity.') }}
        </text>
      </view>

      <view class="auth-card">
        <text class="auth-kicker">{{ tr('创建账号', 'Create account') }}</text>
        <text class="auth-title heading-serif">{{ tr('从 Google 开始', 'Start with Google') }}</text>
        <text class="auth-copy">
          {{ tr('邮箱密码注册暂时关闭。当前公开注册统一使用 Google，避免验证码问题影响生成体验。', 'Email-and-password registration is temporarily closed while production email delivery is being finalized.') }}
        </text>

        <view class="form-stack">
          <text v-if="error" class="error-text">{{ error }}</text>

          <button
            v-if="supabaseEnabled"
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
        <text class="guest-link" @tap="goHome">{{ tr('先浏览首页', 'Continue as guest') }}</text>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useI18nStore } from '../../stores/i18n';
import { signInWithGoogle } from '../../utils/auth';
import { refreshSupabaseConfig } from '../../utils/supabase';

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const supabaseEnabled = ref(false);
const submitting = ref(false);
const error = ref('');

async function googleSignIn() {
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
  supabaseEnabled.value = await refreshSupabaseConfig(true);
  if (!supabaseEnabled.value) {
    error.value = tr('Google sign-in is not configured on this deployment.', 'Google sign-in is not configured on this deployment.');
  }
});
</script>

<style lang="scss" scoped>
@import './auth.scss';
</style>
