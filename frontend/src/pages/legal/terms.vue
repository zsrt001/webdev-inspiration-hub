<template>
  <view class="legal-page">
    <NavBar />
    <view class="legal-shell">
      <view class="legal-hero">
        <text class="eyebrow">{{ tr('法务与合规', 'Legal & Compliance') }}</text>
        <text class="title heading-serif">{{ tr('服务条款', 'Terms of Service') }}</text>
        <text class="subtitle">{{ tr('最后更新：2026 年 3 月 7 日', 'Last updated: March 7, 2026') }}</text>
      </view>

      <view class="legal-layout">
        <view class="legal-nav">
          <view v-for="section in sections" :key="section.anchor" class="nav-item" @tap="scrollTo(section.anchor)">
            <text class="nav-index">{{ section.index }}</text>
            <text class="nav-label">{{ tr(section.titleZh, section.titleEn) }}</text>
          </view>
        </view>

        <view class="legal-content">
          <view v-for="section in sections" :id="section.anchor" :key="section.anchor" class="section-card">
            <text class="section-title heading-serif">{{ tr(section.titleZh, section.titleEn) }}</text>
            <text v-for="line in activeLines(section)" :key="line" class="section-line">{{ line }}</text>
          </view>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import NavBar from '../../components/NavBar.vue';
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
      '本服务为 AI 婚纱影像生成与交付平台，支持网页端与小程序端浏览、上传、下单、支付、下载与分享。',
      '我们可在不影响核心服务能力的前提下调整模板、价格、功能入口、限额与风控策略。',
    ],
    en: [
      'This service is an AI wedding imagery platform that supports browsing, uploads, ordering, payment, download, and sharing across web and mini program surfaces.',
      'We may adjust templates, pricing, feature entry points, quotas, and risk controls as long as core service availability is preserved.',
    ],
  },
  {
    anchor: 'eligibility',
    index: '02',
    titleZh: '使用资格与账户',
    titleEn: 'Eligibility and Accounts',
    zh: [
      '你应确保提交的信息真实、合法，并拥有上传内容和参考素材的使用权。',
      '你不得借用、盗用他人身份或未经授权上传他人肖像、联系方式、证件和其他敏感信息。',
    ],
    en: [
      'You must ensure the information you submit is accurate, lawful, and that you hold the necessary rights to the uploaded content and references.',
      'You must not impersonate others or upload portraits, contact details, IDs, or other sensitive data without authorization.',
    ],
  },
  {
    anchor: 'prohibited',
    index: '03',
    titleZh: '禁止内容',
    titleEn: 'Prohibited Content',
    zh: [
      '禁止上传、请求或生成违法、侵权、涉黄、NSFW、性暗示、未成年人敏感内容，以及证件、支付码、银行卡等高风险素材。',
      '如系统检测到相关内容，我们可拒绝生成、阻断支付、删除内容、冻结账户或按监管要求处理。',
    ],
    en: [
      'You may not upload, request, or generate unlawful, infringing, pornographic, NSFW, sexually explicit, minor-related, or other high-risk content, including IDs, payment codes, and bank cards.',
      'If such content is detected, we may reject generation, block payment, delete content, freeze access, or comply with regulatory requirements.',
    ],
  },
  {
    anchor: 'payment',
    index: '04',
    titleZh: '支付、积分与退款',
    titleEn: 'Payments, Credits, and Refunds',
    zh: [
      '积分购买通过当前配置的托管结算流程完成。支付成功或人工确认后，积分将按订单记录发放到对应账户。',
      '当生成任务未成功入队或因系统故障导致任务失败时，我们可按规则返还积分；已完成且可交付的服务一般不支持无条件退款。',
    ],
    en: [
      'Credit purchases are settled through the configured hosted checkout flow. After successful payment, credits are automatically issued to the corresponding account based on the recorded purchase.',
      'If a generation job fails before enqueue or due to system failure, credits may be refunded according to policy. Completed and deliverable services are generally non-refundable.',
    ],
  },
  {
    anchor: 'ip',
    index: '05',
    titleZh: '知识产权与许可',
    titleEn: 'Intellectual Property and License',
    zh: [
      '平台界面、模板、系统文案、模型工作流和品牌标识归平台或其授权方所有。',
      '在你遵守本条款并完成付款的前提下，你可按业务规则使用你获得的生成结果，但不因此取得平台、模型或模板本身的知识产权。',
    ],
    en: [
      'The platform UI, templates, system copy, model workflows, and branding are owned by the platform or its licensors.',
      'Subject to these terms and successful payment, you may use the generated results you receive under the applicable business rules, but you do not obtain ownership of the platform, models, or templates themselves.',
    ],
  },
  {
    anchor: 'liability',
    index: '06',
    titleZh: '可用性与责任限制',
    titleEn: 'Availability and Limitation of Liability',
    zh: [
      'AI 生成存在概率性偏差。我们会持续优化质量与风控，但不保证每一次输出都完全符合你的主观预期。',
      '在法律允许范围内，平台对间接损失、预期收益损失、第三方中断或不可抗力导致的损失不承担责任。',
    ],
    en: [
      'AI generation is probabilistic. We continuously improve quality and moderation, but we do not guarantee every output will match subjective expectations perfectly.',
      'To the extent permitted by law, we are not liable for indirect damages, loss of expected profit, third-party outages, or force majeure events.',
    ],
  },
  {
    anchor: 'updates',
    index: '07',
    titleZh: '条款更新与联系',
    titleEn: 'Updates and Contact',
    zh: [
      '我们可根据产品、合规或支付要求更新本条款。更新后继续使用服务即视为你接受修订版本。',
      '如需合同、退款、侵权或法务支持，请通过部署环境中配置的官方渠道联系我们。',
    ],
    en: [
      'We may update these terms to reflect product, compliance, or payment changes. Continued use after an update constitutes acceptance of the revised version.',
      'For contract, refund, infringement, or legal support, contact us through the official channels configured for your deployment.',
    ],
  },
]);

const activeLines = (section: { anchor?: string; zh: string[]; en: string[] }) => {
  if (section.anchor === 'payment') {
    return i18nStore.locale === 'zh'
      ? [
          '积分购买通过当前配置的托管结算流程完成。支付成功后，积分将按订单记录自动发放到对应账户。',
          '当生成任务未成功入队或因系统故障导致任务失败时，我们可按规则返还积分；已完成且可交付的服务一般不支持无条件退款。',
        ]
      : [
          'Credit purchases are settled through the configured hosted checkout flow. After successful payment, credits are automatically issued to the corresponding account based on the recorded purchase.',
          'If a generation job fails before enqueue or due to system failure, credits may be refunded according to policy. Completed and deliverable services are generally non-refundable.',
        ];
  }
  return i18nStore.locale === 'zh' ? section.zh : section.en;
};

const scrollTo = (anchor: string) => {
  const query = uni.createSelectorQuery();
  query.select(`#${anchor}`).boundingClientRect();
  query.selectViewport().scrollOffset();
  query.exec((res) => {
    const target = res?.[0];
    const viewport = res?.[1];
    if (!target || !viewport) return;
    uni.pageScrollTo({ scrollTop: target.top + viewport.scrollTop - 92, duration: 220 });
  });
};
</script>

<style lang="scss" scoped>
.legal-page {
  min-height: 100vh;
  background: #faf7f8;
}

.legal-shell {
  max-width: 1320px;
  margin: 0 auto;
  padding: 96px 28px 56px;
}

.legal-hero {
  max-width: 820px;
  padding: 12px 0 28px;
}

.eyebrow {
  display: block;
  color: $uni-color-accent;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  margin-bottom: 10px;
}

.title {
  display: block;
  color: $uni-color-primary;
  font-size: 52px;
  line-height: 1;
  margin-bottom: 10px;
}

.subtitle {
  color: $uni-text-color-muted;
  font-size: 14px;
}

.legal-layout {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}

.legal-nav {
  position: sticky;
  top: 88px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.nav-item,
.section-card {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(131, 24, 67, 0.08);
  box-shadow: 0 18px 38px rgba(131, 24, 67, 0.06);
}

.nav-item {
  border-radius: 20px;
  padding: 14px 16px;
}

.nav-index {
  display: block;
  margin-bottom: 4px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.12em;
  color: $uni-color-accent;
}

.nav-label {
  display: block;
  font-size: 14px;
  color: $uni-color-primary;
  line-height: 1.5;
}

.legal-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section-card {
  border-radius: 24px;
  padding: 28px;
}

.section-title {
  display: block;
  font-size: 30px;
  color: $uni-color-primary;
  margin-bottom: 16px;
}

.section-line {
  display: block;
  color: $uni-text-color;
  line-height: 1.85;
  margin-bottom: 12px;
}

@media (max-width: 980px) {
  .legal-layout {
    grid-template-columns: 1fr;
  }

  .legal-nav {
    position: static;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .legal-shell {
    padding: 88px 18px 40px;
  }

  .title {
    font-size: 38px;
  }

  .section-card {
    padding: 22px 18px;
  }

  .section-title {
    font-size: 24px;
  }

  .legal-nav {
    grid-template-columns: 1fr;
  }
}
</style>
