<template>
  <view class="legal-page">
    <NavBar />
    <view class="legal-shell">
      <view class="legal-hero">
        <text class="eyebrow">{{ tr('法务与合规', 'Legal & Compliance') }}</text>
        <text class="title heading-serif">{{ tr('服务条款', 'Terms of Service') }}</text>
        <text class="subtitle">{{ tr('最后更新：2026 年 4 月 26 日', 'Last updated: April 26, 2026') }}</text>
      </view>

      <view class="legal-content">
        <view v-for="section in sections" :key="section.anchor" class="section-card">
          <text class="section-index">{{ section.index }}</text>
          <text class="section-title heading-serif">{{ tr(section.titleZh, section.titleEn) }}</text>
          <text v-for="line in activeLines(section)" :key="line" class="section-line">{{ line }}</text>
        </view>
      </view>
      <LegalFooter />
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import NavBar from '../../components/NavBar.vue';
import LegalFooter from '../../components/LegalFooter.vue';
import { useI18nStore } from '../../stores/i18n';

const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const sections = computed(() => [
  {
    anchor: 'service',
    index: '01',
    titleZh: '服务说明',
    titleEn: 'Service Description',
    zh: [
      '本服务提供 AI 婚纱影像生成、异地合拍、作品历史、积分包和订阅制商业化能力。',
      '平台可基于成本、风控、模型能力和商业策略调整模板、价格、积分扣减、保留周期和功能入口。',
    ],
    en: [
      'The service provides AI wedding image generation, remote couple join, image history, credit packs, and subscription billing.',
      'The platform may adjust templates, prices, credit costs, retention periods, and feature entry points based on cost, safety, model capability, and commercial strategy.',
    ],
  },
  {
    anchor: 'pricing',
    index: '02',
    titleZh: '积分与定价',
    titleEn: 'Credits and Pricing',
    zh: [
      '积分是平台内的使用额度，不是现金、储值卡或可提现资产。',
      '当前扣费规则：单人基础生成 2 积分，Director/高级单人 3 积分，双人本地合拍 3 积分，异地合拍 4 积分，高级场景 5 积分，Live Portrait 5 秒 6 积分，每额外 5 秒加 4 积分。',
      '积分在任务成功入队后扣减；如果任务在入队前失败，应自动退回。支付成功后的发放以支付 webhook 和后台账本为准。',
    ],
    en: [
      'Credits are in-product usage units, not cash, stored value, or withdrawable assets.',
      'Current costs: base single image 2 credits, Director/advanced single 3, local couple 3, remote couple 4, premium scene 5, Live Portrait 5 seconds 6, and each extra 5 seconds +4.',
      'Credits are deducted after a task is accepted into the queue. Queue-start failures should be refunded automatically. Payment grants are based on provider webhooks and the backend ledger.',
    ],
  },
  {
    anchor: 'content',
    index: '03',
    titleZh: '内容与授权',
    titleEn: 'Content and Authorization',
    zh: [
      '你必须确认上传的人像、服装、场景、文字和参考素材属于本人、已获授权或具有合法使用权。',
      '禁止上传或生成违法、侵权、色情、未成年人敏感、冒用他人身份、证件、支付码、银行卡等高风险内容。',
    ],
    en: [
      'You must confirm that uploaded portraits, outfits, scenes, text, and references belong to you, are authorized, or are lawful to use.',
      'You may not upload or generate unlawful, infringing, pornographic, minor-sensitive, impersonation, ID, payment code, bank card, or other high-risk content.',
    ],
  },
  {
    anchor: 'disclaimer',
    index: '04',
    titleZh: 'AI 结果免责',
    titleEn: 'AI Output Disclaimer',
    zh: [
      'AI 生成结果可能存在人脸细节、服装、场景、文字理解、构图或风格偏差，不保证完全还原真实人物或商业拍摄效果。',
      '你应在下载、发布、商用或交付客户前自行审核结果。因未经授权使用肖像、素材或生成结果引发的纠纷，由上传和使用方承担相应责任。',
    ],
    en: [
      'AI outputs may contain face detail, outfit, scene, prompt interpretation, composition, or style inaccuracies and are not guaranteed to fully match real people or professional shoot outcomes.',
      'You must review outputs before download, publishing, commercial use, or client delivery. Disputes caused by unauthorized portraits, materials, or outputs are the responsibility of the uploader/user.',
    ],
  },
  {
    anchor: 'billing',
    index: '05',
    titleZh: '支付与订阅',
    titleEn: 'Billing and Subscriptions',
    zh: [
      '一次性积分包和订阅积分都会进入同一个积分余额。订阅不是无限生成，每个账期按套餐发放固定积分。',
      '取消订阅默认在当前账期结束时生效。退款、补偿或异常处理会通过账本调整记录，不会改写历史流水。',
    ],
    en: [
      'One-time credit packs and subscription grants share one credit balance. Subscriptions are not unlimited usage; each billing period grants fixed credits.',
      'Subscription cancellation defaults to taking effect at the end of the current period. Refunds, compensation, and exceptions are recorded as ledger adjustments, not by rewriting history.',
    ],
  },
]);

function activeLines(section: any): string[] {
  return i18nStore.locale === 'zh' ? section.zh : section.en;
}
</script>

<style lang="scss" scoped>
.legal-page {
  min-height: 100vh;
  background: #f7f8fa;
  color: #17191f;
}

.legal-shell {
  max-width: 980px;
  margin: 0 auto;
  padding: 110px 24px 80px;
}

.legal-hero {
  margin-bottom: 26px;
}

.eyebrow {
  display: block;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
  color: #116a60;
}

.title {
  display: block;
  margin-top: 10px;
  font-size: 48px;
  line-height: 1.05;
}

.subtitle {
  display: block;
  margin-top: 10px;
  color: #4c5360;
}

.legal-content {
  display: grid;
  gap: 16px;
}

.section-card {
  padding: 24px;
  border-radius: 8px;
  background: #ffffff;
  border: 1px solid #dde1e8;
  box-shadow: 0 14px 38px rgba(23, 25, 31, 0.06);
}

.section-index {
  display: block;
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
}

.section-title {
  display: block;
  margin: 6px 0 12px;
  font-size: 26px;
}

.section-line {
  display: block;
  margin-top: 10px;
  color: #4c5360;
  line-height: 1.8;
  font-size: 14px;
}
</style>
