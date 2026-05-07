<template>
  <view class="legal-footer">
    <text class="footer-note">
      {{ tr('使用本服务前，请确认你拥有上传素材的合法授权。支付、订阅和退款规则以平台条款为准。', 'Before using this service, confirm that you have legal rights to uploaded materials. Billing, subscriptions, and refunds follow platform terms.') }}
    </text>
    <view class="footer-links">
      <text class="footer-link" @tap="open('/pages/legal/privacy')">{{ tr('隐私政策', 'Privacy Policy') }}</text>
      <text class="footer-dot">·</text>
      <text class="footer-link" @tap="open('/pages/legal/terms')">{{ tr('服务条款', 'Terms of Service') }}</text>
      <text class="footer-dot">·</text>
      <text class="footer-link" @tap="open('/pages/legal/refund')">{{ tr('退款与客服', 'Refunds & Support') }}</text>
    </view>
  </view>
</template>

<script setup lang="ts">
import { useI18nStore } from '../stores/i18n';

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

function open(url: string) {
  uni.navigateTo({ url });
}
</script>

<style lang="scss" scoped>
.legal-footer {
  margin: 24px auto 0;
  padding: 28px 20px 34px;
  max-width: 980px;
  text-align: center;
  color: #6b7280;
}

.footer-note {
  display: block;
  font-size: 12px;
  line-height: 1.8;
}

.footer-links {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  margin-top: 12px;
}

.footer-link {
  color: #116a60;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0;
}

.footer-dot {
  color: #aeb6c2;
  font-size: 12px;
}
</style>
