<template>
  <view class="app-container saas-landing" style="padding-top: 64px;">
    <NavBar ref="navBarRef" @show-payment="showPaymentModal = true" />

    <view v-if="homeBanner.enabled" class="hero-section">
      <image :src="heroImageUrl" mode="aspectFill" class="hero-media" />
      <view class="hero-overlay"></view>
      <view class="hero-content">
        <text class="section-label">{{ tr('AI 婚纱影像 SaaS', 'AI Wedding Photo SaaS') }}</text>
        <text class="hero-title heading-serif">
          {{ tr('用 AI 搭建你的高定婚纱影像工作流', 'Launch a couture wedding portrait workflow with AI') }}
        </text>
        <text class="hero-subtitle">
          {{ tr('上传人物照片，选择风格或自由描述服装与场景，从生成、预览、高清下载到订阅充值，一站完成。', 'Upload portraits, choose a style or direct outfit and scene prompts, then generate, preview, download, and pay in one complete flow.') }}
        </text>
        <view class="hero-actions">
          <view class="btn primary" @tap="goToCustom">{{ tr('免费开始创作', 'Start Creating') }}</view>
          <view class="btn secondary" @tap="scrollToGallery">{{ tr('了解更多', 'View Styles') }}</view>
        </view>
        <view class="hero-preview" @tap="scrollToGallery">
          <image :src="heroPreviewUrl" mode="aspectFill" class="preview-image" />
          <view class="preview-copy">
            <text class="preview-title">{{ tr('产品预览', 'Product Preview') }}</text>
            <text class="preview-text">{{ tr('模板库、异地合拍、参考图定向、高清交付全部保留。', 'Style library, remote couple flow, references, and HD delivery stay connected.') }}</text>
          </view>
        </view>
      </view>
    </view>

    <view class="landing-body">
      <section class="benefits-section section-block" id="benefits">
        <view class="section-heading">
          <text class="section-label">{{ tr('核心优势', 'Benefits') }}</text>
          <text class="section-title heading-serif">{{ tr('从灵感到交付，减少每一步阻力', 'Remove friction from idea to delivery') }}</text>
        </view>
        <view class="benefit-grid">
          <view v-for="item in benefitItems" :key="item.title" class="benefit-card">
            <text class="card-mark">{{ item.mark }}</text>
            <text class="card-title">{{ item.title }}</text>
            <text class="card-desc">{{ item.desc }}</text>
          </view>
        </view>
      </section>

      <section class="features-section section-block" id="features">
        <view class="section-heading">
          <text class="section-label">{{ tr('功能介绍', 'Features') }}</text>
          <text class="section-title heading-serif">{{ tr('把现有能力装进清晰的 SaaS 工作台', 'A clear SaaS surface for every existing capability') }}</text>
        </view>

        <view class="feature-layout">
          <view class="feature-panel" @tap="goToCustom">
            <image src="/static/style-previews/custom_mode.jpg" mode="aspectFill" class="feature-image" />
            <view class="feature-copy">
              <text class="feature-kicker">{{ tr('AI 设计中心', 'AI Design Center') }}</text>
              <text class="feature-title heading-serif">{{ tr('自由定制服装、场景与参考图', 'Direct outfits, scenes, and references') }}</text>
              <text class="feature-desc">{{ tr('保留统一创作流：单人、双人同机、双人异地邀请、文字定向和高级参考图。', 'Keeps the unified creation flow: single, local couple, remote couple invite, text direction, and advanced references.') }}</text>
              <view class="feature-action">{{ tr('进入创作', 'Open Studio') }}</view>
            </view>
          </view>

          <view class="feature-panel alt" @tap="focusCategory('vintage')">
            <image src="/static/legacy_promo_banner.jpg" mode="aspectFill" class="feature-image" />
            <view class="feature-copy">
              <text class="feature-kicker">{{ tr('金婚纪念', 'Legacy Series') }}</text>
              <text class="feature-title heading-serif">{{ tr('长辈纪念照与年代质感重塑', 'Era-aware anniversary portraits') }}</text>
              <text class="feature-desc">{{ tr('保留金婚入口与年代风格模板，适合父母纪念照、长辈合照和复古影楼感作品。', 'Keeps the golden anniversary entry for parents, elders, and retro studio keepsakes.') }}</text>
              <view class="feature-action">{{ tr('查看金婚风格', 'View Legacy Styles') }}</view>
            </view>
          </view>
        </view>
      </section>

      <section class="gallery-section section-block" id="gallery">
        <view class="section-heading split">
          <view>
            <text class="section-label">{{ tr('产品功能演示', 'Product Gallery') }}</text>
            <text class="section-title heading-serif">{{ tr('风格库仍然是首页核心入口', 'The style library remains the main entry') }}</text>
          </view>
          <text class="section-note">{{ tr('点击任一风格进入详情与上传流程。', 'Open any style to continue into details and upload.') }}</text>
        </view>

        <view class="category-filter">
          <view class="cat-chip" :class="{ active: selectedCategory === 'all' }" @tap="selectedCategory = 'all'">
            {{ tr('全部', 'All') }}
          </view>
          <view class="cat-chip" :class="{ active: selectedCategory === 'single' }" @tap="selectedCategory = 'single'">
            {{ tr('人像婚纱', 'Portrait') }}
          </view>
          <view class="cat-chip" :class="{ active: selectedCategory === 'vintage' }" @tap="selectedCategory = 'vintage'">
            {{ tr('金婚纪念', 'Legacy') }}
          </view>
          <view class="cat-chip" :class="{ active: selectedCategory === 'custom' }" @tap="selectedCategory = 'custom'">
            {{ tr('自由定制', 'Bespoke') }}
          </view>
        </view>

        <view v-if="filteredTemplates.length > 0" class="style-grid">
          <view
            v-for="template in filteredTemplates"
            :key="template.id"
            class="style-card"
            @tap="goToDetail(template)"
          >
            <image
              :src="resolveTemplateCardImage(template)"
              mode="aspectFill"
              class="style-image"
              @error="onTemplateImageError(template)"
            />
            <view class="style-copy">
              <text class="style-title heading-serif">{{ displayTemplateTitle(template) }}</text>
              <text v-if="displayTemplateMarketingSubtitle(template)" class="style-desc">{{ displayTemplateMarketingSubtitle(template) }}</text>
              <text class="style-action">{{ tr('查看详情', 'View Details') }}</text>
            </view>
          </view>
        </view>
        <view v-else class="empty-gallery">
          <text class="empty-title">{{ tr('正在加载风格作品', 'Loading styles') }}</text>
          <text class="empty-desc">{{ tr('请稍候，模板库会自动使用本地保底数据。', 'Please wait. The gallery falls back safely if the API is unavailable.') }}</text>
        </view>
      </section>

      <section class="testimonials-section section-block" id="testimonials">
        <view class="section-heading">
          <text class="section-label">{{ tr('用户证言', 'Testimonials') }}</text>
          <text class="section-title heading-serif">{{ tr('面向真实使用场景的信任感', 'Trust for real wedding use cases') }}</text>
        </view>
        <view class="testimonial-list">
          <view v-for="item in testimonials" :key="item.name" class="testimonial-card">
            <text class="quote-text">{{ item.quote }}</text>
            <text class="quote-name">{{ item.name }}</text>
          </view>
        </view>
      </section>

      <section class="cta-section section-block" id="cta">
        <text class="section-label">{{ tr('行动召唤', 'CTA') }}</text>
        <text class="cta-title heading-serif">{{ tr('准备好生成第一套婚纱作品了吗？', 'Ready to create your first wedding portrait set?') }}</text>
        <text class="cta-desc">{{ tr('从免费体验开始，需要高清下载或更多生成额度时，再通过积分包或订阅升级。', 'Start with the current creation flow. Upgrade with credit packs or subscriptions when you need HD delivery or more usage.') }}</text>
        <view class="hero-actions centered">
          <view class="btn primary" @tap="goToCustom">{{ tr('立即开始', 'Start Now') }}</view>
          <view class="btn secondary" @tap="showPaymentModal = true">{{ tr('查看套餐', 'View Plans') }}</view>
        </view>
      </section>

      <section class="pricing-section section-block" id="pricing">
        <view class="section-heading">
          <text class="section-label">{{ tr('价格方案', 'Pricing') }}</text>
          <text class="section-title heading-serif">{{ tr('积分包与订阅并存，价格保持合理梯度', 'Credit packs and subscriptions with a balanced ladder') }}</text>
        </view>
        <view class="pricing-grid">
          <view v-for="plan in pricingPlans" :key="plan.name" class="pricing-card" :class="{ featured: plan.featured }">
            <text v-if="plan.badge" class="plan-badge">{{ plan.badge }}</text>
            <text class="plan-name">{{ plan.name }}</text>
            <text class="plan-price heading-serif">{{ plan.price }}</text>
            <text class="plan-desc">{{ plan.desc }}</text>
            <view class="plan-lines">
              <text v-for="line in plan.lines" :key="line" class="plan-line">{{ line }}</text>
            </view>
            <view class="plan-button" @tap="showPaymentModal = true">{{ plan.action }}</view>
          </view>
        </view>
      </section>
    </view>

    <view class="site-footer" id="footer">
      <view class="footer-main">
        <text class="footer-brand heading-serif" @tap="onLogoTap">{{ tr('AI Wedding', 'AI Wedding') }}</text>
        <text class="footer-copy">{{ tr('AI 婚纱影像生成、远程合拍、高清交付与 Creem 支付测试已接入。', 'AI wedding generation, remote collaboration, HD delivery, and Creem payment testing are connected.') }}</text>
      </view>
      <view class="footer-links">
        <text @tap="scrollToGallery">{{ tr('功能', 'Features') }}</text>
        <text @tap="scrollToPricing">{{ tr('价格', 'Pricing') }}</text>
        <text @tap="goToPrivacy">{{ tr('隐私政策', 'Privacy') }}</text>
        <text @tap="goToTerms">{{ tr('服务条款', 'Terms') }}</text>
        <text @tap="goToRefund">{{ tr('退款与客服', 'Refunds') }}</text>
      </view>
      <LegalFooter />
      <view class="secret-admin" @tap="goToAdmin">...</view>
    </view>

    <PaymentModal
      :visible="showPaymentModal"
      @close="showPaymentModal = false"
      @purchase-complete="onPurchaseComplete"
    />
  </view>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import NavBar from '../../components/NavBar.vue';
import PaymentModal from '../../components/PaymentModal.vue';
import LegalFooter from '../../components/LegalFooter.vue';
import { useI18nStore } from '../../stores/i18n';
import { useOpsStore } from '../../stores/ops';
import {
  useTemplateStore,
  type Template,
  getLocalizedTemplateMarketingSubtitle,
  getLocalizedTemplateTitle,
} from '../../stores/template';
import { post, resolvePublicUrl } from '../../utils/api';

const templateStore = useTemplateStore();
const opsStore = useOpsStore();
const i18nStore = useI18nStore();
const tr = (zh: string, en: string) => (i18nStore.locale === 'zh' ? zh : en);

const navBarRef = ref<InstanceType<typeof NavBar> | null>(null);
const templates = ref<Template[]>([]);
const selectedCategory = ref<'all' | 'single' | 'vintage' | 'custom'>('all');
const logoTapCount = ref(0);
const showPaymentModal = ref(false);
const templateImageAttempts = ref<Record<string, number>>({});

const homeBanner = computed(() => opsStore.publicConfig.placements.home_banner);

const styleImageFallbacks: Record<string, string[]> = {
  chn_xiuhe: ['/style-previews/couple_chn_xiuhe.jpg', '/style-previews/solo_chn_xiuhe.jpg'],
  korean_minimal: ['/style-previews/couple_korean_minimal.jpg', '/style-previews/solo_korean_minimal.jpg'],
  royal_castle: ['/style-previews/couple_royal_castle.jpg', '/style-previews/solo_royal_castle.jpg'],
  old_money: ['/style-previews/couple_old_money.jpg', '/style-previews/solo_old_money.jpg'],
  gothic_romance: ['/style-previews/couple_gothic_romance.jpg', '/style-previews/solo_gothic_romance_v2.png'],
  beach_sunset: ['/style-previews/couple_beach_sunset.jpg', '/style-previews/solo_beach_sunset.jpg'],
  hk_retro: ['/style-previews/couple_hk_retro_v2.png', '/style-previews/hk_retro.jpg'],
  twilight_forest: ['/style-previews/couple_twilight_forest.jpg', '/style-previews/twilight_forest.jpg'],
  japanese_shiromuku: ['/style-previews/couple_japanese_shiromuku.jpg', '/style-previews/japanese_shiromuku.jpg'],
  cyberpunk_city: ['/style-previews/couple_cyberpunk_city_v2.png', '/style-previews/cyberpunk_city.jpg'],
  school_days: ['/style-previews/couple_school_days.jpg', '/style-previews/school_days.jpg'],
  classic_bw: ['/style-previews/couple_classic_bw.jpg', '/style-previews/classic_bw.jpg'],
  golden_vintage_studio_8090: ['/style-previews/golden_vintage_studio_8090.jpg'],
  golden_chinese_courtyard: ['/style-previews/golden_chinese_courtyard.jpg'],
  golden_modern_remake: ['/style-previews/golden_modern_remake.jpg'],
  custom_mode: ['/style-previews/custom_mode.jpg'],
};

const heroImageUrl = computed(() => {
  const configured = resolvePublicUrl(homeBanner.value.image_url);
  if (configured) return configured;
  return resolvePublicUrl('/hero_banner.jpg');
});

const heroPreviewUrl = computed(() => {
  const candidate = templates.value.find((item) => item.category === 'couple')?.image_url || '/style-previews/couple_royal_castle.jpg';
  return resolvePublicUrl(candidate);
});

const benefitItems = computed(() => [
  {
    mark: 'AI',
    title: tr('稳定生成链路', 'Reliable generation'),
    desc: tr('保留本地质检、积分扣费、失败退款和订单状态追踪。', 'Keeps local quality checks, credit charging, refunds, and order tracking.'),
  },
  {
    mark: 'HD',
    title: tr('高清交付闭环', 'HD delivery loop'),
    desc: tr('预览页、高清解锁、支付弹窗和下载权限仍然连贯。', 'Preview, HD unlock, payment modal, and download permissions stay intact.'),
  },
  {
    mark: 'CRM',
    title: tr('商业化可测试', 'Commerce ready'),
    desc: tr('Creem Test Mode 支持积分包、月订阅和 webhook 入账。', 'Creem Test Mode supports credit packs, subscriptions, and webhook grants.'),
  },
]);

const testimonials = computed(() => [
  {
    quote: tr('“不用重新拍摄，就能快速看到不同婚纱风格，适合婚礼前期选片和灵感沟通。”', '“We could explore different bridal moods without reshooting. It made early wedding planning much easier.”'),
    name: tr('准新人用户', 'Bride-to-be user'),
  },
  {
    quote: tr('“金婚模板对长辈纪念照很友好，复古影楼感和真实纹理都保留得比较自然。”', '“The legacy templates feel respectful for elder anniversary portraits, with natural retro texture.”'),
    name: tr('家庭纪念场景', 'Family keepsake use case'),
  },
]);

const pricingPlans = computed(() => [
  {
    name: tr('Starter 积分包', 'Starter Pack'),
    price: '$12.90',
    desc: tr('50 积分，适合首次体验。', '50 credits for first tests.'),
    lines: [tr('单次购买', 'One-time purchase'), tr('适合试拍与小批量生成', 'Good for trials and small batches')],
    action: tr('购买积分', 'Buy Credits'),
    badge: '',
    featured: false,
  },
  {
    name: tr('Creator 月订阅', 'Creator Monthly'),
    price: '$49/mo',
    desc: tr('300 积分/月，适合持续创作。', '300 credits per month for ongoing creation.'),
    lines: [tr('订阅制额度', 'Subscription credits'), tr('积分单价低于小包', 'Better unit economics than small packs')],
    action: tr('查看订阅', 'View Subscription'),
    badge: tr('推荐', 'Popular'),
    featured: true,
  },
  {
    name: tr('Studio 月订阅', 'Studio Monthly'),
    price: '$129/mo',
    desc: tr('900 积分/月，适合团队与高频产出。', '900 credits per month for teams and heavier usage.'),
    lines: [tr('更高月度额度', 'Higher monthly allowance'), tr('适合工作室场景', 'Built for studio workflows')],
    action: tr('升级套餐', 'Upgrade'),
    badge: '',
    featured: false,
  },
]);

const goToAdmin = () => {
  uni.navigateTo({ url: '/pages/admin/index' });
};

const onLogoTap = () => {
  logoTapCount.value += 1;
  if (logoTapCount.value >= 5) {
    goToAdmin();
    logoTapCount.value = 0;
  }
};

const displayTemplateTitle = (template: Template): string => getLocalizedTemplateTitle(template, i18nStore.locale);

const displayTemplateMarketingSubtitle = (template: Template): string =>
  getLocalizedTemplateMarketingSubtitle(template, i18nStore.locale);

const goToPrivacy = () => {
  uni.navigateTo({ url: '/pages/legal/privacy' });
};

const goToTerms = () => {
  uni.navigateTo({ url: '/pages/legal/terms' });
};

const goToRefund = () => {
  uni.navigateTo({ url: '/pages/legal/refund' });
};

const goToCustom = () => {
  uni.navigateTo({ url: '/pages/create/index' });
};

const goToDetail = (template: Template) => {
  post(
    '/analytics/click',
    {
      event_type: 'template_click',
      source_page: 'index',
      template_id: template.id,
    },
    { showLoading: false, showError: false } as any
  ).catch(() => {});
  templateStore.selectTemplate(template);
  uni.navigateTo({ url: `/pages/detail/detail?id=${template.id}` });
};

const vintageTemplates = computed(() => templates.value.filter((item) => item.category === 'vintage'));

const categoryPriority = (category: string): number => {
  const rank: Record<string, number> = {
    single: 40,
    solo: 40,
    vintage: 30,
    couple: 20,
    custom: 10,
  };
  return rank[category] || 0;
};

const normalizeTemplateTitle = (template: Template): string => {
  const title = (template.marketing_title || template.title || '').toLowerCase();
  return title
    .replace(/\((solo|couple)\)/g, '')
    .replace(/\bsolo\b|\bcouple\b/g, '')
    .replace(/\s+/g, ' ')
    .trim();
};

const styleFamilyKey = (template: Template): string => {
  const explicitFamily = (template.style_family || '').trim().toLowerCase();
  if (explicitFamily) return explicitFamily;

  const normalizedImageName = (template.image_url || '')
    .split('/')
    .pop()
    ?.toLowerCase()
    .replace(/\.(jpg|jpeg|png|webp)$/i, '')
    .replace(/^(solo_|couple_)/, '')
    .trim();
  if (normalizedImageName) return normalizedImageName;

  return normalizeTemplateTitle(template) || template.id;
};

const resolveTemplateCardImage = (template: Template): string => {
  const familyKey = styleFamilyKey(template);
  const candidates = [
    template.image_url,
    ...(styleImageFallbacks[familyKey] || []),
    '/style-previews/couple_royal_castle.jpg',
  ]
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .filter((item, index, list) => list.indexOf(item) === index);

  const attempt = templateImageAttempts.value[template.id] || 0;
  const target = candidates[Math.min(attempt, candidates.length - 1)] || '/style-previews/couple_royal_castle.jpg';
  return resolvePublicUrl(target);
};

const onTemplateImageError = (template: Template) => {
  templateImageAttempts.value = {
    ...templateImageAttempts.value,
    [template.id]: (templateImageAttempts.value[template.id] || 0) + 1,
  };
};

const dedupeTemplateCards = (source: Template[]): Template[] => {
  const grouped = new Map<string, Template[]>();
  for (const template of source) {
    const key = styleFamilyKey(template);
    const bucket = grouped.get(key) || [];
    bucket.push(template);
    grouped.set(key, bucket);
  }

  return Array.from(grouped.values()).map((group, index) => {
    const sorted = [...group].sort((a, b) => categoryPriority(b.category) - categoryPriority(a.category));
    const singleVariant = sorted.find((item) => item.category === 'single' || item.category === 'solo');
    const coupleVariant = sorted.find((item) => item.category === 'couple');

    if (singleVariant && coupleVariant) {
      return index % 2 === 0 ? singleVariant : coupleVariant;
    }

    return singleVariant || coupleVariant || sorted[0];
  });
};

const filteredTemplates = computed(() => {
  if (selectedCategory.value !== 'all') {
    if (selectedCategory.value === 'single') {
      const portraitLike = templates.value.filter(
        (item) => item.category === 'single' || item.category === 'solo' || item.category === 'couple'
      );
      return dedupeTemplateCards(portraitLike);
    }
    return templates.value.filter((item) => item.category === selectedCategory.value);
  }
  return dedupeTemplateCards(templates.value);
});

const focusCategory = (category: 'single' | 'vintage' | 'custom') => {
  post(
    '/analytics/click',
    {
      event_type: 'collection_focus',
      source_page: 'index',
      template_id: category,
    },
    { showLoading: false, showError: false } as any
  ).catch(() => {});
  selectedCategory.value = category;
  scrollToGallery();
};

const scrollToGallery = () => {
  uni.pageScrollTo({
    selector: '#gallery',
    duration: 500,
  });
};

const scrollToPricing = () => {
  uni.pageScrollTo({
    selector: '#pricing',
    duration: 500,
  });
};

function onPurchaseComplete() {
  showPaymentModal.value = false;
  navBarRef.value?.refreshBalance();
}

onMounted(async () => {
  await opsStore.fetchPublicConfig();
  await templateStore.fetchTemplates();
  templates.value = templateStore.templates;
  if (!vintageTemplates.value.length && selectedCategory.value === 'vintage') {
    selectedCategory.value = 'all';
  }
});
</script>

<style lang="scss" scoped>
.saas-landing {
  min-height: 100vh;
  background: #fbf9f8;
  color: #1b1c1c;
}

.hero-section {
  position: relative;
  min-height: min(760px, calc(100dvh - 64px));
  display: flex;
  align-items: center;
  overflow: hidden;
  background: #eae7e1;
}

.hero-media {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 1;
  filter: saturate(0.92) contrast(1.02);
}

.hero-overlay {
  position: absolute;
  inset: 0;
  z-index: 2;
  background:
    linear-gradient(90deg, rgba(251, 249, 248, 0.94) 0%, rgba(251, 249, 248, 0.76) 42%, rgba(251, 249, 248, 0.18) 100%),
    linear-gradient(0deg, #fbf9f8 0%, rgba(251, 249, 248, 0.06) 30%);
}

.hero-content {
  position: relative;
  z-index: 3;
  width: min(1280px, calc(100% - 48px));
  margin: 0 auto;
  padding: 64px 0 88px;
}

.section-label {
  display: block;
  margin-bottom: 14px;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0;
  color: #735a31;
}

.hero-title {
  display: block;
  max-width: 720px;
  font-size: clamp(42px, 6vw, 76px);
  line-height: 1.02;
  color: #1b1c1c;
}

.hero-subtitle {
  display: block;
  max-width: 580px;
  margin-top: 24px;
  font-size: 18px;
  line-height: 1.75;
  color: #444845;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 34px;
}

.hero-actions.centered {
  justify-content: center;
}

.btn {
  min-height: 48px;
  padding: 0 26px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 800;
  cursor: pointer;
  transition: transform 0.2s ease, background 0.2s ease, border-color 0.2s ease;
}

.btn:active {
  transform: translateY(1px);
}

.btn.primary {
  background: #735a31;
  color: #ffffff;
  border: 1px solid #735a31;
}

.btn.secondary {
  background: rgba(255, 255, 255, 0.72);
  color: #1b1c1c;
  border: 1px solid #747875;
}

.hero-preview {
  width: min(620px, 100%);
  margin-top: 48px;
  display: grid;
  grid-template-columns: 210px minmax(0, 1fr);
  gap: 22px;
  align-items: center;
  padding: 14px;
  border: 1px solid rgba(116, 120, 117, 0.24);
  background: rgba(255, 255, 255, 0.78);
  border-radius: 8px;
  cursor: pointer;
}

.preview-image {
  width: 100%;
  aspect-ratio: 16 / 10;
  border-radius: 6px;
}

.preview-title {
  display: block;
  margin-bottom: 8px;
  font-size: 16px;
  font-weight: 900;
}

.preview-text,
.section-note,
.card-desc,
.feature-desc,
.quote-text,
.cta-desc,
.plan-desc,
.plan-line,
.footer-copy {
  display: block;
  font-size: 14px;
  line-height: 1.75;
  color: #444845;
}

.landing-body {
  width: min(1280px, calc(100% - 48px));
  margin: 0 auto;
}

.section-block {
  padding: 92px 0;
}

.section-heading {
  max-width: 760px;
  margin: 0 auto 42px;
  text-align: center;
}

.section-heading.split {
  max-width: none;
  display: flex;
  justify-content: space-between;
  gap: 32px;
  align-items: flex-end;
  text-align: left;
}

.section-title {
  display: block;
  font-size: clamp(32px, 4vw, 52px);
  line-height: 1.15;
  color: #1b1c1c;
}

.section-note {
  max-width: 320px;
}

.benefit-grid,
.pricing-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}

.benefit-card,
.pricing-card,
.testimonial-card {
  border: 1px solid #e4e2e1;
  background: #ffffff;
  border-radius: 8px;
  padding: 26px;
}

.card-mark {
  display: inline-flex;
  width: 48px;
  height: 48px;
  align-items: center;
  justify-content: center;
  margin-bottom: 22px;
  border-radius: 999px;
  background: #fddba7;
  color: #59431c;
  font-size: 13px;
  font-weight: 900;
}

.card-title,
.plan-name,
.quote-name {
  display: block;
  margin-bottom: 10px;
  font-size: 18px;
  font-weight: 900;
  color: #1b1c1c;
}

.features-section {
  width: 100vw;
  margin-left: calc((100% - 100vw) / 2);
  padding-left: max(24px, calc((100vw - 1280px) / 2));
  padding-right: max(24px, calc((100vw - 1280px) / 2));
  background: #f6f3f2;
  border-top: 1px solid #e4e2e1;
  border-bottom: 1px solid #e4e2e1;
}

.feature-layout {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 22px;
}

.feature-panel {
  background: #ffffff;
  border: 1px solid #e4e2e1;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
}

.feature-image {
  width: 100%;
  aspect-ratio: 16 / 10;
  background: #eae7e1;
}

.feature-copy {
  padding: 24px;
}

.feature-kicker,
.plan-badge,
.style-action {
  display: block;
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 900;
  color: #735a31;
}

.feature-title {
  display: block;
  margin-bottom: 12px;
  font-size: 30px;
  line-height: 1.2;
}

.feature-action {
  margin-top: 18px;
  min-height: 42px;
  display: inline-flex;
  align-items: center;
  padding: 0 18px;
  border-radius: 999px;
  background: #1b1c1c;
  color: #ffffff;
  font-size: 13px;
  font-weight: 800;
}

.category-filter {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 24px;
}

.cat-chip {
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  padding: 0 16px;
  border-radius: 999px;
  border: 1px solid #c4c7c4;
  background: #ffffff;
  color: #444845;
  font-size: 13px;
  font-weight: 800;
  cursor: pointer;
}

.cat-chip.active {
  color: #ffffff;
  background: #735a31;
  border-color: #735a31;
}

.style-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 18px;
}

.style-card {
  overflow: hidden;
  border: 1px solid #e4e2e1;
  border-radius: 8px;
  background: #ffffff;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.style-card:active {
  transform: translateY(1px);
}

.style-image {
  width: 100%;
  aspect-ratio: 3 / 4;
  background: #eae7e1;
}

.style-copy {
  padding: 14px;
}

.style-title {
  display: block;
  margin-bottom: 6px;
  font-size: 20px;
  line-height: 1.25;
  color: #1b1c1c;
}

.style-desc {
  display: block;
  min-height: 38px;
  font-size: 12px;
  line-height: 1.55;
  color: #444845;
}

.style-action {
  margin-top: 12px;
  margin-bottom: 0;
}

.empty-gallery {
  padding: 48px 24px;
  border: 1px dashed #c4c7c4;
  border-radius: 8px;
  text-align: center;
}

.empty-title {
  display: block;
  margin-bottom: 8px;
  font-size: 18px;
  font-weight: 900;
}

.empty-desc {
  display: block;
  font-size: 14px;
  color: #444845;
}

.testimonials-section {
  border-top: 1px solid #e4e2e1;
}

.testimonial-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.quote-text {
  min-height: 100px;
  font-size: 17px;
  color: #1b1c1c;
}

.quote-name {
  margin-top: 20px;
  margin-bottom: 0;
  color: #735a31;
}

.cta-section {
  width: 100vw;
  margin-left: calc((100% - 100vw) / 2);
  padding-left: 24px;
  padding-right: 24px;
  text-align: center;
  background: #fddba7;
  border-top: 1px solid rgba(89, 67, 28, 0.16);
  border-bottom: 1px solid rgba(89, 67, 28, 0.16);
}

.cta-title {
  display: block;
  max-width: 720px;
  margin: 0 auto 16px;
  font-size: clamp(34px, 5vw, 60px);
  line-height: 1.1;
}

.cta-desc {
  max-width: 620px;
  margin: 0 auto;
}

.pricing-card {
  position: relative;
  display: flex;
  min-height: 390px;
  flex-direction: column;
}

.pricing-card.featured {
  border-color: #735a31;
  background: #fbf8f2;
  box-shadow: 0 24px 60px rgba(40, 25, 0, 0.08);
}

.plan-badge {
  width: fit-content;
  padding: 6px 10px;
  border-radius: 999px;
  background: #735a31;
  color: #ffffff;
}

.plan-price {
  display: block;
  margin: 8px 0 12px;
  font-size: 42px;
  color: #1b1c1c;
}

.plan-lines {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 18px;
}

.plan-line::before {
  content: '';
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 8px;
  border-radius: 999px;
  background: #735a31;
}

.plan-button {
  min-height: 46px;
  margin-top: auto;
  border-radius: 999px;
  background: #1b1c1c;
  color: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 900;
  cursor: pointer;
}

.site-footer {
  padding: 64px 24px 20px;
  background: #f6f3f2;
  border-top: 1px solid #e4e2e1;
}

.footer-main {
  width: min(1280px, 100%);
  margin: 0 auto 24px;
  display: flex;
  justify-content: space-between;
  gap: 28px;
  align-items: flex-start;
}

.footer-brand {
  display: block;
  font-size: 28px;
  color: #1b1c1c;
}

.footer-copy {
  max-width: 540px;
  text-align: right;
}

.footer-links {
  width: min(1280px, 100%);
  margin: 0 auto;
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  justify-content: center;
  color: #444845;
  font-size: 13px;
  font-weight: 800;
}

.footer-links text,
.secret-admin {
  cursor: pointer;
}

.secret-admin {
  margin-top: 16px;
  text-align: center;
  color: rgba(116, 120, 117, 0.34);
  font-size: 18px;
}

@media (min-width: 961px) {
  .style-card:hover,
  .feature-panel:hover,
  .benefit-card:hover,
  .pricing-card:hover {
    box-shadow: 0 24px 60px rgba(40, 25, 0, 0.07);
    transform: translateY(-2px);
  }
}

@media (max-width: 1180px) {
  .style-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .hero-section {
    min-height: auto;
  }

  .hero-content {
    padding: 54px 0 66px;
  }

  .hero-preview,
  .section-heading.split,
  .footer-main {
    grid-template-columns: 1fr;
    flex-direction: column;
    align-items: flex-start;
  }

  .section-heading.split {
    display: block;
  }

  .section-note {
    max-width: none;
    margin-top: 14px;
  }

  .benefit-grid,
  .feature-layout,
  .testimonial-list,
  .pricing-grid {
    grid-template-columns: 1fr;
  }

  .style-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .footer-copy {
    text-align: left;
  }
}

@media (max-width: 560px) {
  .landing-body,
  .hero-content {
    width: min(100% - 32px, 1280px);
  }

  .section-block {
    padding: 64px 0;
  }

  .hero-subtitle {
    font-size: 16px;
  }

  .hero-actions,
  .hero-actions.centered {
    flex-direction: column;
    align-items: stretch;
  }

  .btn {
    width: 100%;
    box-sizing: border-box;
  }

  .style-grid {
    grid-template-columns: 1fr;
  }

  .feature-title {
    font-size: 26px;
  }

  .benefit-card,
  .pricing-card,
  .testimonial-card {
    padding: 22px;
  }
}
</style>
