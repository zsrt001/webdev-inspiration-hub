<template>
  <view class="app-container product-landing" style="padding-top: 64px;">
    <NavBar ref="navBarRef" @show-payment="showPaymentModal = true" />

    <view v-if="homeBanner.enabled" class="hero-section">
      <image :src="heroImageUrl" mode="aspectFill" class="hero-media" />
      <view class="hero-overlay"></view>
      <view class="hero-content">
        <view class="hero-copy">
          <text class="section-label">{{ tr('AI 婚纱照生成', 'AI Wedding Studio') }}</text>
          <text class="hero-title heading-serif">
            {{ tr('上传照片，生成你的高定婚纱大片', 'Wedding portraits, made from your photos') }}
          </text>
          <text class="hero-subtitle">
            {{ tr('无需预约影棚。上传人像、选择风格或描述想法，快速生成适合请柬、头像、纪念日和社交分享的婚纱影像。', 'Upload a portrait, choose a look, and generate polished AI wedding images for invitations, profiles, anniversaries, and keepsakes.') }}
          </text>
          <view class="hero-actions">
            <view class="btn primary" @tap="goToCustom">{{ tr('开始免费预览', 'Start a Free Preview') }}</view>
            <view class="btn secondary" @tap="scrollToGallery">{{ tr('浏览婚纱风格', 'Explore Styles') }}</view>
          </view>
          <view class="hero-proof-grid">
            <view v-for="item in heroProofs" :key="item.title" class="proof-item">
              <text class="proof-title">{{ item.title }}</text>
              <text class="proof-desc">{{ item.desc }}</text>
            </view>
          </view>
        </view>

        <view class="hero-preview" @tap="scrollToGallery">
          <view class="preview-frame">
            <image :src="heroPreviewUrl" mode="aspectFill" class="preview-image" />
          </view>
          <view class="preview-copy">
            <text class="preview-title">{{ tr('精选婚纱风格', 'Curated Wedding Looks') }}</text>
            <text class="preview-text">{{ tr('中式秀禾、韩系极简、古堡、海边、金婚纪念等风格都可以直接开始。', 'Chinese Xiuhe, Korean minimal, castle romance, beach sunset, and anniversary remakes are ready to try.') }}</text>
          </view>
        </view>
      </view>
    </view>

    <view class="landing-body">
      <section class="benefits-section section-block" id="benefits">
        <view class="section-heading">
          <text class="section-label">{{ tr('为什么选择 AI Wedding', 'Why AI Wedding') }}</text>
          <text class="section-title heading-serif">{{ tr('先看到成片灵感，再决定要不要继续升级', 'Preview the look before you spend more credits') }}</text>
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
          <text class="section-label">{{ tr('核心功能', 'Core Features') }}</text>
          <text class="section-title heading-serif">{{ tr('从一张照片到婚纱大片，只需要几步', 'From upload to keepsake in a few guided steps') }}</text>
        </view>

        <view class="feature-layout">
          <view class="feature-panel" @tap="goToCustom">
            <image src="/static/style-previews/couple_old_money.jpg" mode="aspectFill" class="feature-image" />
            <view class="feature-copy">
              <text class="feature-kicker">{{ tr('自由定制', 'Custom Creation') }}</text>
              <text class="feature-title heading-serif">{{ tr('描述你想要的婚纱、场景和氛围', 'Describe the dress, scene, and mood you want') }}</text>
              <text class="feature-desc">{{ tr('上传人物照片后，可以选择模板，也可以补充服装、场景和参考图，让画面更接近你的审美。', 'Upload portraits, choose a style, then add outfit, scene, or reference guidance to bring the result closer to your taste.') }}</text>
              <view class="feature-action">{{ tr('开始定制', 'Start Customizing') }}</view>
            </view>
          </view>

          <view class="feature-panel alt" @tap="focusCategory('vintage')">
            <image src="/static/style-previews/golden_chinese_courtyard.jpg" mode="aspectFill" class="feature-image" />
            <view class="feature-copy">
              <text class="feature-kicker">{{ tr('金婚纪念', 'Legacy Series') }}</text>
              <text class="feature-title heading-serif">{{ tr('为父母和长辈重做一组纪念婚纱照', 'Create anniversary portraits for parents and elders') }}</text>
              <text class="feature-desc">{{ tr('适合结婚纪念日、金婚礼物和家庭相册，用更体面的方式留下珍贵关系与家庭记忆。', 'Perfect for anniversaries, legacy gifts, and family albums, with a more polished way to preserve important memories.') }}</text>
              <view class="feature-action">{{ tr('查看纪念风格', 'View Legacy Styles') }}</view>
            </view>
          </view>
        </view>
      </section>

      <section class="gallery-section section-block" id="gallery">
        <view class="section-heading split">
          <view>
            <text class="section-label">{{ tr('风格灵感', 'Style Inspiration') }}</text>
            <text class="section-title heading-serif">{{ tr('选择一个喜欢的风格，马上开始生成', 'Choose a direction, then make it yours') }}</text>
          </view>
          <text class="section-note">{{ tr('支持单人婚纱照、双人合拍和异地合拍。进入详情后可继续上传照片。', 'Supports solo portraits, couple portraits, and remote couple creation. Open a style to upload photos.') }}</text>
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
          <text class="section-label">{{ tr('适用场景', 'When to use it') }}</text>
          <text class="section-title heading-serif">{{ tr('适合这些真实需求', 'Useful before and after the wedding') }}</text>
        </view>
        <view class="testimonial-list">
          <view v-for="item in testimonials" :key="item.name" class="testimonial-card">
            <text class="quote-text">{{ item.quote }}</text>
            <text class="quote-name">{{ item.name }}</text>
          </view>
        </view>
      </section>

      <section class="cta-section section-block" id="cta">
        <text class="section-label">{{ tr('开始体验', 'Start Now') }}</text>
        <text class="cta-title heading-serif">{{ tr('先生成预览，满意后再解锁高清', 'Generate a preview, then unlock the image you love') }}</text>
        <text class="cta-desc">{{ tr('新用户可以先体验生成效果。需要更多次数、高清下载或持续创作时，再选择积分包或月度套餐。', 'Start with a real preview using your own photo. Add credits only when you need more attempts, HD downloads, or ongoing creation.') }}</text>
        <view class="hero-actions centered">
          <view class="btn primary" @tap="goToCustom">{{ tr('立即开始', 'Start Now') }}</view>
          <view class="btn secondary" @tap="showPaymentModal = true">{{ tr('查看套餐', 'View Plans') }}</view>
        </view>
      </section>

      <section class="pricing-section section-block" id="pricing">
        <view class="section-heading">
          <text class="section-label">{{ tr('价格方案', 'Pricing') }}</text>
          <text class="section-title heading-serif">{{ tr('按使用频率选择更合适的套餐', 'Choose the plan that fits your usage') }}</text>
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
        <text class="footer-copy">{{ tr('AI Wedding 提供 AI 婚纱照生成、双人合拍、高清下载、积分包和月度套餐服务。', 'AI Wedding offers AI wedding portraits, couple creation, HD downloads, credit packs, and monthly plans.') }}</text>
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
  chn_xiuhe: ['/style-previews/couple_chn_xiuhe.jpg', '/style-previews/chn_xiuhe.jpg'],
  korean_minimal: ['/style-previews/couple_korean_minimal.jpg', '/style-previews/solo_korean_minimal.jpg'],
  royal_castle: ['/style-previews/royal_castle.jpg', '/style-previews/solo_royal_castle.jpg'],
  old_money: ['/style-previews/couple_old_money.jpg', '/style-previews/solo_old_money.jpg'],
  gothic_romance: ['/style-previews/couple_gothic_romance.jpg', '/style-previews/solo_gothic_romance.jpg'],
  beach_sunset: ['/style-previews/couple_beach_sunset.jpg', '/style-previews/solo_beach_sunset.jpg'],
  hk_retro: ['/style-previews/couple_hk_retro.jpg', '/style-previews/hk_retro.jpg'],
  twilight_forest: ['/style-previews/couple_twilight_forest.jpg', '/style-previews/twilight_forest.jpg'],
  japanese_shiromuku: ['/style-previews/couple_japanese_shiromuku.jpg', '/style-previews/japanese_shiromuku.jpg'],
  cyberpunk_city: ['/style-previews/couple_cyberpunk_city.jpg', '/style-previews/cyberpunk_city.jpg'],
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
  return resolvePublicUrl('/style-previews/royal_castle.jpg');
});

const heroPreviewUrl = computed(() => {
  const candidate = templates.value.find((item) => item.category === 'couple')?.image_url || '/style-previews/royal_castle.jpg';
  return resolvePublicUrl(candidate);
});

const heroProofs = computed(() => [
  {
    title: tr('先预览', 'Preview first'),
    desc: tr('先看真实照片效果，再决定是否继续。', 'See the look with your own photo before buying more credits.'),
  },
  {
    title: tr('支持双人', 'Couple-ready'),
    desc: tr('支持单人、双人和异地邀请合拍。', 'Create solo, couple, or remote partner sessions.'),
  },
  {
    title: tr('高清解锁', 'HD unlock'),
    desc: tr('满意后再解锁高清下载。', 'Unlock high-resolution delivery only when it feels right.'),
  },
]);

const benefitItems = computed(() => [
  {
    mark: '01',
    title: tr('先看效果再决定', 'Preview with your real photos'),
    desc: tr('先用自己的照片试不同婚纱风格，看清楚氛围、构图和人物感觉，再决定是否继续高清下载。', 'Test the look with your own portrait before buying more credits or unlocking HD.'),
  },
  {
    mark: '02',
    title: tr('风格选择更自由', 'Solo, couple, or remote'),
    desc: tr('可选择现成风格，也能用文字补充服装、场景和氛围，适合婚礼灵感、情侣写真和纪念礼物。', 'Create individual portraits, local couple sessions, or invite a partner from another device.'),
  },
  {
    mark: '03',
    title: tr('额度清楚，按需升级', 'Clear credits, no guesswork'),
    desc: tr('积分包适合偶尔生成，月度套餐适合持续创作；先体验，再按使用频率选择。', 'Starter credits cover a base solo preview; larger modes show their credit cost before generation.'),
  },
]);

const testimonials = computed(() => [
  {
    quote: tr('婚礼前想快速试不同风格，可以先用 AI 看一版效果，再和伴侣、化妆师或摄影师沟通方向。', 'Explore several bridal directions before the wedding, then discuss the look with your partner, stylist, or photographer.'),
    name: tr('婚礼灵感与试片', 'Style proofing before the shoot'),
  },
  {
    quote: tr('给父母准备结婚纪念日礼物时，可以用一组更正式的纪念婚纱照，补上过去没有好好拍过的遗憾。', 'Create a more formal anniversary portrait set for parents and family milestones.'),
    name: tr('金婚 / 结婚周年纪念', 'Anniversary and legacy gifts'),
  },
]);

const pricingPlans = computed(() => [
  {
    name: tr('Starter 积分包', 'Starter Pack'),
    price: '$12.90',
    desc: tr('50 积分，适合第一次体验、少量试片和挑选风格。', '50 credits for first tests, light proofing, and style selection.'),
    lines: [tr('一次性购买', 'One-time purchase'), tr('适合偶尔生成', 'Good for occasional creation')],
    action: tr('购买积分', 'Buy Credits'),
    badge: '',
    featured: false,
  },
  {
    name: tr('Creator 月订阅', 'Creator Monthly'),
    price: '$49/mo',
    desc: tr('300 积分/月，适合持续尝试风格、成套出图和高清下载。', '300 credits per month for ongoing styles, sets, and HD downloads.'),
    lines: [tr('每月自动获得积分', 'Monthly credits included'), tr('适合持续创作', 'Best for regular creation')],
    action: tr('查看订阅', 'View Subscription'),
    badge: tr('推荐', 'Popular'),
    featured: true,
  },
  {
    name: tr('Studio 月订阅', 'Studio Monthly'),
    price: '$129/mo',
    desc: tr('900 积分/月，适合高频生成、多人试片和商业素材准备。', '900 credits per month for frequent creation and larger content needs.'),
    lines: [tr('更高月度额度', 'Higher monthly allowance'), tr('适合高频使用', 'For heavier usage')],
    action: tr('升级套餐', 'Upgrade'),
    badge: '',
    featured: false,
  },
]);

const goToAdmin = () => {
  uni.navigateTo({ url: '/admin' });
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
    '/style-previews/royal_castle.jpg',
  ]
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .filter((item, index, list) => list.indexOf(item) === index);

  const attempt = templateImageAttempts.value[template.id] || 0;
  const target = candidates[Math.min(attempt, candidates.length - 1)] || '/style-previews/royal_castle.jpg';
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
.product-landing {
  min-height: 100vh;
  background: #f6f7f8;
  color: #17191f;
}

.hero-section {
  position: relative;
  min-height: clamp(520px, calc(100dvh - 110px), 620px);
  display: flex;
  align-items: stretch;
  overflow: hidden;
  background: #e4e8eb;
}

.hero-media {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 1;
  filter: saturate(0.9) contrast(1.06);
  object-fit: cover;
  object-position: 58% center;
}

.hero-overlay {
  position: absolute;
  inset: 0;
  z-index: 2;
  background:
    linear-gradient(90deg, rgba(246, 247, 248, 0.99) 0%, rgba(246, 247, 248, 0.9) 43%, rgba(246, 247, 248, 0.5) 72%, rgba(246, 247, 248, 0.22) 100%),
    linear-gradient(0deg, #f6f7f8 0%, rgba(246, 247, 248, 0.34) 18%, rgba(246, 247, 248, 0.04) 46%);
}

.hero-content {
  position: relative;
  z-index: 3;
  width: min(1280px, calc(100% - 48px));
  margin: 0 auto;
  padding: 42px 0 44px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 300px);
  gap: clamp(36px, 5vw, 72px);
  align-items: center;
}

.hero-copy {
  max-width: 720px;
}

.section-label {
  display: block;
  margin-bottom: 14px;
  color: #9a5b16;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
}

.hero-title {
  display: block;
  max-width: 720px;
  font-size: clamp(42px, 4.6vw, 64px);
  line-height: 1.02;
  color: #17191f;
  text-wrap: balance;
}

.hero-subtitle {
  display: block;
  max-width: 600px;
  margin-top: 22px;
  color: #454b57;
  font-size: 17px;
  line-height: 1.65;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 30px;
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

.hero-proof-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  max-width: 640px;
  margin-top: 22px;
}

.proof-item {
  min-height: 84px;
  padding: 14px;
  border: 1px solid rgba(32, 43, 62, 0.12);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.72);
  box-sizing: border-box;
}

.proof-title {
  display: block;
  margin-bottom: 6px;
  color: #17191f;
  font-size: 13px;
  font-weight: 900;
  line-height: 1.25;
}

.proof-desc {
  display: block;
  color: #4c5360;
  font-size: 12px;
  line-height: 1.45;
}

.hero-preview {
  align-self: center;
  width: 100%;
  padding: 12px;
  border: 1px solid rgba(32, 43, 62, 0.12);
  background: rgba(255, 255, 255, 0.88);
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
  object-position: center top;
}

.preview-copy {
  padding: 16px 4px 10px;
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
  padding: 88px 0;
}

.benefits-section {
  padding-top: 70px;
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
  font-size: clamp(32px, 4vw, 48px);
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
  background: #edf1f2;
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
  display: grid;
  grid-template-columns: minmax(190px, 0.46fr) minmax(0, 1fr);
  min-height: 340px;
  border: 1px solid #dde1e8;
  border-radius: 8px;
  background: #ffffff;
  cursor: pointer;
}

.feature-image {
  width: 100%;
  height: 100%;
  min-height: 340px;
  display: block;
  background: #d9dde3;
  object-fit: cover;
  object-position: center top;
}

.feature-copy {
  padding: 28px;
  display: flex;
  flex-direction: column;
  justify-content: center;
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
  font-size: 28px;
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
  background: #e6f1ef;
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
    align-items: start;
  }

  .hero-preview {
    width: min(360px, 100%);
    justify-self: start;
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
    padding: 46px 0 52px;
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
  .testimonial-list,
  .pricing-grid {
    grid-template-columns: 1fr;
  }

  .feature-layout {
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

  .benefits-section {
    padding-top: 56px;
  }

  .hero-content {
    padding: 36px 0 42px;
  }

  .hero-title {
    font-size: 38px;
    line-height: 1.06;
  }

  .hero-subtitle {
    font-size: 16px;
  }

  .hero-proof-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .proof-item {
    min-height: auto;
    padding: 10px 8px;
    text-align: center;
  }

  .proof-title {
    margin-bottom: 0;
    font-size: 12px;
  }

  .proof-desc {
    display: none;
  }

  .hero-preview {
    display: none;
  }

  .feature-panel {
    grid-template-columns: 1fr;
  }

  .feature-image {
    min-height: auto;
    height: auto;
    aspect-ratio: 4 / 5;
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
  margin-bottom: 8px;
