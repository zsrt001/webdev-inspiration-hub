<template>
  <view class="app-container saas-landing" style="padding-top: 64px;">
    <NavBar ref="navBarRef" @show-payment="showPaymentModal = true" />

    <view v-if="homeBanner.enabled" class="hero-section">
      <image :src="heroImageUrl" mode="aspectFill" class="hero-media" />
      <view class="hero-overlay"></view>
      <view class="hero-content">
        <view class="hero-copy">
          <text class="section-label">{{ tr('AI 婚纱影像 SaaS', 'AI Wedding Photo SaaS') }}</text>
          <text class="hero-title heading-serif">
            {{ tr('一站式 AI 婚纱影像创作与交付平台', 'AI wedding portrait creation and delivery in one workflow') }}
          </text>
          <text class="hero-subtitle">
            {{ tr('从人物上传、风格选择、文字定向，到高清交付、积分包、订阅和 Creem 支付，所有现有能力整合成清晰的商业工作流。', 'Upload portraits, choose styles, direct outfits and scenes, then handle HD delivery, credits, subscriptions, and Creem payments in one clear commercial flow.') }}
          </text>
          <view class="hero-actions">
            <view class="btn primary" @tap="goToCustom">{{ tr('开始创作', 'Start Creating') }}</view>
            <view class="btn secondary" @tap="scrollToGallery">{{ tr('查看风格库', 'View Styles') }}</view>
          </view>
        </view>

        <view class="hero-preview" @tap="scrollToGallery">
          <view class="preview-frame">
            <image :src="heroPreviewUrl" mode="aspectFill" class="preview-image" />
          </view>
          <view class="preview-copy">
            <text class="preview-title">{{ tr('完整创作闭环', 'Complete Creation Loop') }}</text>
            <text class="preview-text">{{ tr('风格库、异地合拍、参考图定向、高清下载和支付权益全部保留。', 'Style library, remote couple flow, references, HD delivery, and payment entitlements stay connected.') }}</text>
          </view>
        </view>
      </view>
    </view>

    <view class="landing-body">
      <section class="benefits-section section-block" id="benefits">
        <view class="section-heading">
          <text class="section-label">{{ tr('核心优势', 'Benefits') }}</text>
          <text class="section-title heading-serif">{{ tr('让婚纱影像从灵感到交付更顺', 'Less friction from idea to delivery') }}</text>
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
          <text class="section-title heading-serif">{{ tr('保留所有能力，重新组织成 SaaS 工作台', 'Every capability, reorganized as a SaaS workspace') }}</text>
        </view>

        <view class="feature-layout">
          <view class="feature-panel" @tap="goToCustom">
            <image src="/static/style-previews/custom_mode.jpg" mode="aspectFill" class="feature-image" />
            <view class="feature-copy">
              <text class="feature-kicker">{{ tr('AI 设计中心', 'AI Design Center') }}</text>
              <text class="feature-title heading-serif">{{ tr('自由定制服装、场景与参考图', 'Direct outfits, scenes, and references') }}</text>
              <text class="feature-desc">{{ tr('单人、双人同机、双人异地邀请、文字定向和高级参考图仍然在同一条创作流里。', 'Single, local couple, remote couple invite, text direction, and advanced references remain in the same creation flow.') }}</text>
              <view class="feature-action">{{ tr('进入创作', 'Open Studio') }}</view>
            </view>
          </view>

          <view class="feature-panel alt" @tap="focusCategory('vintage')">
            <image src="/static/legacy_promo_banner.jpg" mode="aspectFill" class="feature-image" />
            <view class="feature-copy">
              <text class="feature-kicker">{{ tr('金婚纪念', 'Legacy Series') }}</text>
              <text class="feature-title heading-serif">{{ tr('长辈纪念照与年代质感重塑', 'Era-aware anniversary portraits') }}</text>
              <text class="feature-desc">{{ tr('金婚入口、复古影楼、庭院合照和现代翻拍都保留，适合父母纪念照和家庭礼物场景。', 'Legacy, retro studio, courtyard keepsakes, and modern remakes stay available for parents and family gifts.') }}</text>
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
          <text class="section-note">{{ tr('点击任一风格进入详情页，并继续上传照片或切换创作模式。', 'Open any style to continue into details, upload, or switch creation mode.') }}</text>
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
            <view class="style-image-frame">
              <image
                :src="resolveTemplateCardImage(template)"
                mode="aspectFill"
                class="style-image"
                @error="onTemplateImageError(template)"
              />
            </view>
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
          <text class="section-label">{{ tr('使用场景', 'Use Cases') }}</text>
          <text class="section-title heading-serif">{{ tr('文案贴近真实功能，不再空泛营销', 'Copy grounded in real product capability') }}</text>
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
        <text class="cta-title heading-serif">{{ tr('先试生成，再按需要升级额度', 'Start creating, then upgrade when usage grows') }}</text>
        <text class="cta-desc">{{ tr('免费额度适合首次试用；需要高清下载、更多生成或持续出图时，再选择积分包或月度订阅。', 'Free credits are for first tests. Use credit packs or monthly subscriptions when you need HD downloads, more generations, or steady production.') }}</text>
        <view class="hero-actions centered">
          <view class="btn primary" @tap="goToCustom">{{ tr('立即开始', 'Start Now') }}</view>
          <view class="btn secondary" @tap="showPaymentModal = true">{{ tr('查看套餐', 'View Plans') }}</view>
        </view>
      </section>

      <section class="pricing-section section-block" id="pricing">
        <view class="section-heading">
          <text class="section-label">{{ tr('价格方案', 'Pricing') }}</text>
          <text class="section-title heading-serif">{{ tr('积分包和订阅保持合理梯度', 'Credit packs and subscriptions with a balanced ladder') }}</text>
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
        <text class="footer-copy">{{ tr('AI 婚纱影像生成、远程合拍、高清交付、积分包、订阅与 Creem 支付已接入。', 'AI wedding generation, remote collaboration, HD delivery, credit packs, subscriptions, and Creem payments are connected.') }}</text>
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
    mark: '01',
    title: tr('创作流完整', 'Complete workflow'),
    desc: tr('人物上传、本地质检、风格选择、文字定向、参考图和订单状态都保留在同一流程。', 'Portrait upload, local quality checks, style selection, text direction, references, and order states stay in one flow.'),
  },
  {
    mark: '02',
    title: tr('高清交付闭环', 'HD delivery loop'),
    desc: tr('预览、高清解锁、下载权限、积分扣费和失败退款逻辑继续串联。', 'Preview, HD unlock, download permissions, credit charging, and refund handling stay connected.'),
  },
  {
    mark: '03',
    title: tr('商业化可测试', 'Commerce ready'),
    desc: tr('Creem Test Mode 支持积分包、月订阅和 webhook 入账，便于上线前完整验证。', 'Creem Test Mode supports credit packs, subscriptions, and webhook grants for pre-launch validation.'),
  },
]);

const testimonials = computed(() => [
  {
    quote: tr('适合新娘先看不同婚纱风格，再决定服装、场景和照片用途，不需要一开始就重拍。', 'Useful for exploring bridal styles before deciding outfit, scene, and final use without reshooting first.'),
    name: tr('婚纱风格打样', 'Bridal style proofing'),
  },
  {
    quote: tr('金婚系列能服务父母纪念照，和主婚纱生成功能区分清楚，但仍复用同一套上传与交付链路。', 'The legacy series serves parent anniversary portraits while reusing the same upload and delivery pipeline.'),
    name: tr('家庭纪念场景', 'Family keepsake use case'),
  },
]);

const pricingPlans = computed(() => [
  {
    name: tr('Starter 积分包', 'Starter Pack'),
    price: '$12.90',
    desc: tr('50 积分，适合首次体验和少量试生成。', '50 credits for first tests and light usage.'),
    lines: [tr('一次性购买', 'One-time purchase'), tr('约 25 次基础生成', 'About 25 base generations')],
    action: tr('购买积分', 'Buy Credits'),
    badge: '',
    featured: false,
  },
  {
    name: tr('Creator 月订阅', 'Creator Monthly'),
    price: '$49/mo',
    desc: tr('300 积分/月，适合持续创作和成套出图。', '300 credits per month for ongoing creation.'),
    lines: [tr('订阅积分每月发放', 'Monthly subscription credits'), tr('单价低于小额积分包', 'Better unit economics than small packs')],
    action: tr('查看订阅', 'View Subscription'),
    badge: tr('推荐', 'Popular'),
    featured: true,
  },
  {
    name: tr('Studio 月订阅', 'Studio Monthly'),
    price: '$129/mo',
    desc: tr('900 积分/月，适合团队和高频交付。', '900 credits per month for teams and heavier usage.'),
    lines: [tr('更高月度额度', 'Higher monthly allowance'), tr('适合工作室批量交付', 'Built for studio workflows')],
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
  background: #f7f8fa;
  color: #17191f;
}

.hero-section {
  position: relative;
  min-height: min(740px, calc(100dvh - 64px));
  display: flex;
  align-items: stretch;
  overflow: hidden;
  background: #d9dde3;
}

.hero-media {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 1;
  filter: saturate(0.9) contrast(1.06);
  object-fit: cover;
  object-position: center;
}

.hero-overlay {
  position: absolute;
  inset: 0;
  z-index: 2;
  background:
    linear-gradient(90deg, rgba(247, 248, 250, 0.98) 0%, rgba(247, 248, 250, 0.86) 43%, rgba(247, 248, 250, 0.24) 100%),
    linear-gradient(0deg, #f7f8fa 0%, rgba(247, 248, 250, 0.04) 34%);
}

.hero-content {
  position: relative;
  z-index: 3;
  width: min(1280px, calc(100% - 48px));
  margin: 0 auto;
  padding: 74px 0 82px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 420px;
  gap: 48px;
  align-items: end;
}

.hero-copy {
  max-width: 760px;
}

.section-label {
  display: block;
  margin-bottom: 14px;
  color: #7c4d2f;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
}

.hero-title {
  display: block;
  font-size: clamp(42px, 6vw, 74px);
  line-height: 1.02;
  color: #17191f;
}

.hero-subtitle {
  display: block;
  max-width: 650px;
  margin-top: 24px;
  color: #454b57;
  font-size: 18px;
  line-height: 1.75;
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
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 900;
  cursor: pointer;
  transition: transform 0.2s ease, background 0.2s ease, border-color 0.2s ease;
}

.btn:active {
  transform: translateY(1px);
}

.btn.primary {
  background: #17191f;
  color: #ffffff;
  border: 1px solid #17191f;
}

.btn.secondary {
  background: rgba(255, 255, 255, 0.78);
  color: #17191f;
  border: 1px solid #b8bec8;
}

.hero-preview {
  align-self: end;
  padding: 12px;
  border: 1px solid rgba(32, 43, 62, 0.12);
  background: rgba(255, 255, 255, 0.82);
  border-radius: 8px;
  box-shadow: 0 24px 64px rgba(23, 25, 31, 0.12);
  cursor: pointer;
}

.preview-frame {
  overflow: hidden;
  border-radius: 6px;
  aspect-ratio: 4 / 5;
  background: #d9dde3;
}

.preview-image {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  object-position: center;
}

.preview-copy {
  padding: 18px 4px 2px;
}

.preview-title {
  display: block;
  margin-bottom: 8px;
  font-size: 16px;
  font-weight: 900;
  color: #17191f;
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
  color: #4c5360;
  font-size: 14px;
  line-height: 1.75;
}

.landing-body {
  width: min(1280px, calc(100% - 48px));
  margin: 0 auto;
}

.section-block {
  padding: 92px 0;
}

.section-heading {
  max-width: 780px;
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
  color: #17191f;
  font-size: clamp(32px, 4vw, 52px);
  line-height: 1.15;
}

.section-note {
  max-width: 360px;
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
  border: 1px solid #dde1e8;
  background: #ffffff;
  border-radius: 8px;
  padding: 26px;
}

.card-mark {
  display: inline-flex;
  width: 46px;
  height: 46px;
  align-items: center;
  justify-content: center;
  margin-bottom: 22px;
  border-radius: 8px;
  background: #eef7f5;
  color: #116a60;
  font-size: 13px;
  font-weight: 900;
}

.card-title,
.plan-name,
.quote-name {
  display: block;
  margin-bottom: 10px;
  color: #17191f;
  font-size: 18px;
  font-weight: 900;
}

.features-section {
  width: 100vw;
  margin-left: calc((100% - 100vw) / 2);
  padding-left: max(24px, calc((100vw - 1280px) / 2));
  padding-right: max(24px, calc((100vw - 1280px) / 2));
  background: #eef1f4;
  border-top: 1px solid #dde1e8;
  border-bottom: 1px solid #dde1e8;
}

.feature-layout {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 22px;
}

.feature-panel {
  overflow: hidden;
  border: 1px solid #dde1e8;
  border-radius: 8px;
  background: #ffffff;
  cursor: pointer;
}

.feature-image {
  width: 100%;
  aspect-ratio: 16 / 10;
  display: block;
  background: #d9dde3;
  object-fit: cover;
  object-position: center;
}

.feature-copy {
  padding: 24px;
}

.feature-kicker,
.plan-badge,
.style-action {
  display: block;
  margin-bottom: 8px;
  color: #116a60;
  font-size: 12px;
  font-weight: 900;
}

.feature-title {
  display: block;
  margin-bottom: 12px;
  color: #17191f;
  font-size: 30px;
  line-height: 1.2;
}

.feature-action {
  margin-top: 18px;
  min-height: 42px;
  display: inline-flex;
  align-items: center;
  padding: 0 18px;
  border-radius: 8px;
  background: #17191f;
  color: #ffffff;
  font-size: 13px;
  font-weight: 900;
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
  border-radius: 8px;
  border: 1px solid #c8ced8;
  background: #ffffff;
  color: #4c5360;
  font-size: 13px;
  font-weight: 900;
  cursor: pointer;
}

.cat-chip.active {
  color: #ffffff;
  background: #17191f;
  border-color: #17191f;
}

.style-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 18px;
}

.style-card {
  overflow: hidden;
  border: 1px solid #dde1e8;
  border-radius: 8px;
  background: #ffffff;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.style-card:active {
  transform: translateY(1px);
}

.style-image-frame {
  aspect-ratio: 4 / 5;
  overflow: hidden;
  background: #d9dde3;
}

.style-image {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  object-position: center top;
}

.style-copy {
  padding: 16px;
}

.style-title {
  display: block;
  margin-bottom: 6px;
  color: #17191f;
  font-size: 20px;
  line-height: 1.25;
}

.style-desc {
  display: block;
  min-height: 42px;
  color: #4c5360;
  font-size: 12px;
  line-height: 1.55;
}

.style-action {
  margin-top: 12px;
  margin-bottom: 0;
}

.empty-gallery {
  padding: 48px 24px;
  border: 1px dashed #aeb6c2;
  border-radius: 8px;
  text-align: center;
}

.empty-title {
  display: block;
  margin-bottom: 8px;
  color: #17191f;
  font-size: 18px;
  font-weight: 900;
}

.empty-desc {
  display: block;
  color: #4c5360;
  font-size: 14px;
}

.testimonials-section {
  border-top: 1px solid #dde1e8;
}

.testimonial-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.quote-text {
  min-height: 96px;
  color: #17191f;
  font-size: 17px;
}

.quote-name {
  margin-top: 20px;
  margin-bottom: 0;
  color: #116a60;
}

.cta-section {
  width: 100vw;
  margin-left: calc((100% - 100vw) / 2);
  padding-left: 24px;
  padding-right: 24px;
  text-align: center;
  background: #e8f3f1;
  border-top: 1px solid rgba(17, 106, 96, 0.16);
  border-bottom: 1px solid rgba(17, 106, 96, 0.16);
}

.cta-title {
  display: block;
  max-width: 760px;
  margin: 0 auto 16px;
  color: #17191f;
  font-size: clamp(34px, 5vw, 58px);
  line-height: 1.1;
}

.cta-desc {
  max-width: 650px;
  margin: 0 auto;
}

.pricing-card {
  position: relative;
  display: flex;
  min-height: 390px;
  flex-direction: column;
}

.pricing-card.featured {
  border-color: #116a60;
  background: #f4fbfa;
  box-shadow: 0 24px 60px rgba(17, 106, 96, 0.1);
}

.plan-badge {
  width: fit-content;
  padding: 6px 10px;
  border-radius: 8px;
  background: #116a60;
  color: #ffffff;
}

.plan-price {
  display: block;
  margin: 8px 0 12px;
  color: #17191f;
  font-size: 42px;
  font-variant-numeric: tabular-nums;
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
  background: #116a60;
}

.plan-button {
  min-height: 46px;
  margin-top: auto;
  border-radius: 8px;
  background: #17191f;
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
  background: #eef1f4;
  border-top: 1px solid #dde1e8;
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
  color: #17191f;
  font-size: 28px;
}

.footer-copy {
  max-width: 560px;
  text-align: right;
}

.footer-links {
  width: min(1280px, 100%);
  margin: 0 auto;
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  justify-content: center;
  color: #4c5360;
  font-size: 13px;
  font-weight: 900;
}

.footer-links text,
.secret-admin {
  cursor: pointer;
}

.secret-admin {
  margin-top: 16px;
  text-align: center;
  color: rgba(76, 83, 96, 0.34);
  font-size: 18px;
}

@media (min-width: 961px) {
  .style-card:hover,
  .feature-panel:hover,
  .benefit-card:hover,
  .pricing-card:hover {
    box-shadow: 0 24px 60px rgba(23, 25, 31, 0.08);
    transform: translateY(-2px);
  }
}

@media (max-width: 1180px) {
  .hero-content {
    grid-template-columns: 1fr;
  }

  .hero-preview {
    width: min(420px, 100%);
  }

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

  .section-heading.split,
  .footer-main {
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
    margin-top: 14px;
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
