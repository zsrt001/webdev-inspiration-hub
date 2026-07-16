<template>
  <view class="callback-page">
    <view class="callback-card">
      <text class="callback-title">{{ error || 'Completing secure sign-in…' }}</text>
      <button v-if="error" class="retry-button" @tap="retry">Start again</button>
    </view>
  </view>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { finishGoogleLogin } from '../../services/auth';

const error = ref('');

function retry() {
  uni.reLaunch({ url: '/pages/auth/login' });
}

onMounted(async () => {
  try {
    await finishGoogleLogin();
  } catch (err: any) {
    error.value = err?.message || 'Sign-in could not be completed.';
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
  width: min(440px, 100%);
  padding: 32px;
  border: 1px solid #dde1e8;
  border-radius: 16px;
  background: #fff;
  text-align: center;
}

.callback-title {
  display: block;
  color: #17191f;
  font-size: 18px;
  font-weight: 700;
}

.retry-button {
  margin-top: 20px;
}
</style>
