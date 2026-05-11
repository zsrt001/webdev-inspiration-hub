<template>
  <view class="auth-page">
    <view class="auth-shell">
      <view class="brand" @tap="goHome">
        <text class="brand-title heading-serif">AI Wedding</text>
        <text class="brand-subtitle">
          {{ tr('Create your account with Google. Your guest orders, credits, and future purchases stay under one verified identity.', 'Create your account with Google. Your guest orders, credits, and future purchases stay under one verified identity.') }}
        </text>
      </view>

      <view class="auth-card">
        <text class="auth-kicker">{{ tr('Create account', 'Create account') }}</text>
        <text class="auth-title heading-serif">{{ tr('Start with Google', 'Start with Google') }}</text>
        <text class="auth-copy">
          {{ tr('Email-and-password registration is temporarily closed while production email delivery is being finalized.', 'Email-and-password registration is temporarily closed while production email delivery is being finalized.') }}
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
            <text>{{ submitting ? tr('Connecting...', 'Connecting...') : tr('Create with Google', 'Create with Google') }}</text>
          </button>

          <button v-else class="btn btn-primary auth-button" disabled>
            {{ tr('Google sign-in unavailable', 'Google sign-in unavailable') }}
          </button>

          <view class="auth-note">
            <text class="auth-note-title">{{ tr('Trial rules', 'Trial rules') }}</text>
            <text class="auth-note-line">{{ tr('Starter credits are granted once per verified Google account.', 'Starter credits are granted once per verified Google account.') }}</text>
            <text class="auth-note-line">{{ tr('Paid credits unlock HD and watermark-free delivery.', 'Paid credits unlock HD and watermark-free delivery.') }}</text>
          </view>
        </view>

        <view class="auth-footer">
          <text>{{ tr('Already signed in?', 'Already signed in?') }}</text>
          <text class="link" @tap="goAccount">{{ tr('Open account', 'Open account') }}</text>
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
