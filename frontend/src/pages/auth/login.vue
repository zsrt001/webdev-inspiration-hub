<template>
  <view class="auth-page" role="main">
    <view class="auth-shell">
      <view class="brand" @tap="goHome">
        <text class="brand-title heading-serif">VowPic</text>
        <text class="brand-subtitle">
          {{ tr('使用 Google 登录后，积分、订单和生成记录都会绑定到同一个已验证账号。', 'Sign in with Google to keep credits, orders, and generation records tied to one verified account.') }}
        </text>
      </view>

      <view class="auth-card">
        <text class="auth-kicker">{{ tr('安全登录', 'Secure sign in') }}</text>
        <text class="auth-title heading-serif" role="heading" aria-level="1">{{ tr('使用 Google 继续', 'Continue with Google') }}</text>
        <text class="auth-copy">
          {{ tr('VowPic 使用 Google 作为网站的统一登录方式。', 'VowPic uses Google as the single sign-in method for the web app.') }}
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
            <text>{{ submitting ? tr('连接中...', 'Connecting...') : tr('使用 Google 登录', 'Sign in with Google') }}</text>
          </button>

          <button v-else class="btn btn-primary auth-button" disabled>
            {{ tr('Google 登录暂不可用', 'Google sign-in unavailable') }}
          </button>

          <view class="auth-note">
            <text class="auth-note-title">{{ tr('账户保护', 'Account protection') }}</text>
            <text class="auth-note-line">{{ tr('每个已验证 Google 账号只发放一次欢迎积分。', 'One welcome credit grant per verified Google account.') }}</text>
            <text class="auth-note-line">{{ tr('设备和网络限流会继续保护免费试用，防止刷积分。', 'Device and network limits still protect free trials from abuse.') }}</text>
          </view>
        </view>

        <view class="auth-footer">
          <text>{{ tr('第一次使用？Google 会自动创建你的账号。', 'New here? Google creates your account automatically.') }}</text>
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
