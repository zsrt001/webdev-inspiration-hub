<template>
  <view class="legal-consent" :class="{ compact }">
    <view class="consent-toggle" :class="{ checked: modelValue }" @tap="toggle">
      <text class="consent-mark">{{ modelValue ? '✓' : '' }}</text>
    </view>
    <view class="consent-copy">
      <text class="consent-text">{{ beforeText }}</text>
      <text class="consent-link" @tap.stop="openLegal('/pages/legal/privacy')">{{ tr('《隐私政策》', 'Privacy Policy') }}</text>
      <text class="consent-text">{{ betweenText }}</text>
      <text class="consent-link" @tap.stop="openLegal('/pages/legal/terms')">{{ tr('《服务条款》', 'Terms of Service') }}</text>
      <text class="consent-text">{{ afterText }}</text>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useI18nStore } from '../stores/i18n';

type ConsentMode = 'generate' | 'lead' | 'payment';

const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    mode?: ConsentMode;
    compact?: boolean;
  }>(),
  {
    mode: 'generate',
    compact: false,
  },
);

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void;
}>();

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const beforeText = computed(() => {
  if (props.mode === 'lead') return tr('我同意平台依据', 'I agree that the platform may process my details under the ');
  return tr('我已阅读并同意', 'I have read and agree to the ');
});

const betweenText = computed(() => tr(' 与 ', ' and the '));

const afterText = computed(() => {
  if (props.mode === 'generate') {
    return tr(
      '，确认上传内容合法、真实、已获授权；理解原图会定期删除，AI 结果可能不完全准确。',
      ', confirm uploaded content is lawful, authentic, and authorized; source images are periodically deleted and AI results may be inaccurate.',
    );
  }
  if (props.mode === 'lead') {
    return tr(
      '，用于婚摄咨询、服务联系与预约跟进。',
      ', for consultation, follow-up, and booking communication.',
    );
  }
  return tr(
    '，理解积分非现金，支付和订阅由托管支付方处理，平台不存储银行卡号、CVV 或银行信息。',
    ', and understand credits are not cash, payments are handled by the hosted provider, and the platform does not store card numbers, CVV, or bank data.',
  );
});

function toggle() {
  emit('update:modelValue', !props.modelValue);
}

function openLegal(path: string) {
  uni.navigateTo({ url: path });
}
</script>

<style lang="scss" scoped>
.legal-consent {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-top: 12px;
}

.legal-consent.compact {
  margin-top: 10px;
}

.consent-toggle {
  width: 20px;
  height: 20px;
  border-radius: 999px;
  border: 1px solid rgba($uni-color-primary, 0.35);
  background: white;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 20px;
  margin-top: 1px;
}

.consent-toggle.checked {
  background: $uni-color-primary;
  border-color: $uni-color-primary;
}

.consent-mark {
  color: white;
  font-size: 12px;
  font-weight: 800;
  line-height: 1;
}

.consent-copy {
  flex: 1;
  line-height: 1.5;
}

.consent-text,
.consent-link {
  font-size: 11px;
  color: $uni-text-color-muted;
}

.consent-link {
  color: $uni-color-primary;
  margin: 0 2px;
}
</style>
