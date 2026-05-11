<template>
  <view class="auth-page">
    <view class="auth-shell">
      <view class="brand" @tap="goHome">
        <text class="brand-title heading-serif">AI Wedding</text>
        <text class="brand-subtitle">
          {{ tr('Sign in with Google to keep credits, orders, and generation records tied to one verified account.', 'Sign in with Google to keep credits, orders, and generation records tied to one verified account.') }}
        </text>
      </view>

      <view class="auth-card">
        <text class="auth-kicker">{{ tr('Secure sign in', 'Secure sign in') }}</text>
        <text class="auth-title heading-serif">{{ tr('Continue with Google', 'Continue with Google') }}</text>
        <text class="auth-copy">
          {{ tr('Google is the only public sign-in method right now, so email verification will not block image creation.', 'Google is the only public sign-in method right now, so email verification will not block image creation.') }}
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
            <text>{{ submitting ? tr('Connecting...', 'Connecting...') : tr('Sign in with Google', 'Sign in with Google') }}</text>
          </button>

          <button v-else class="btn btn-primary auth-button" disabled>
            {{ tr('Google sign-in unavailable', 'Google sign-in unavailable') }}
          </button>

          <view class="auth-note">
            <text class="auth-note-title">{{ tr('Account protection', 'Account protection') }}</text>
            <text class="auth-note-line">{{ tr('One welcome credit grant per verified Google account.', 'One welcome credit grant per verified Google account.') }}</text>
            <text class="auth-note-line">{{ tr('Device and network limits still protect free trials from abuse.', 'Device and network limits still protect free trials from abuse.') }}</text>
          </view>
        </view>

        <view class="auth-footer">
          <text>{{ tr('New here? Google creates your account automatically.', 'New here? Google creates your account automatically.') }}</text>
        </view>
        <text class="guest-link" @tap="goHome">{{ tr('Continue as guest', 'Continue as guest') }}</text>
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
