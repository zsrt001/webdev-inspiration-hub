import { computed, ref } from 'vue';
import { defineStore } from 'pinia';

export type Locale = 'zh' | 'en';

const LOCALE_STORAGE_KEY = 'aws_locale';
const TAB_BAR_ROUTES = new Set(['pages/index/index', 'pages/orders/orders']);

function isCurrentTabBarPage(): boolean {
  const pages = getCurrentPages();
  if (pages.length === 0) return false;

  const currentRoute = String(pages[pages.length - 1]?.route || '').replace(/^\/+/, '');
  return TAB_BAR_ROUTES.has(currentRoute);
}

const messages: Record<Locale, Record<string, string>> = {
  zh: {
    'tab.home': '首页',
    'tab.orders': '订单',
    'nav.brand': 'VowPic',
    'nav.home': '首页',
    'nav.studio': '创作',
    'nav.orders': '订单',
    'nav.privacy': '隐私',
    'nav.terms': '条款',
    'nav.privacy_policy': '隐私政策',
    'nav.terms_service': '服务条款',

    'index.hero_tag': 'AI 婚纱创作工作室',
    'index.hero_title_line1': '高级',
    'index.hero_title_line2': '婚纱影像',
    'index.hero_descriptor': '上传人像，生成适合收藏、分享和高清交付的 AI 婚纱照。',
    'index.hero_primary_kicker': '立即开始',
    'index.hero_primary_label': '进入创作',
    'index.hero_secondary_kicker': '风格库',
    'index.hero_secondary_label': '浏览模板',
    'index.portal_label': '自由定制',
    'index.portal_title': 'AI 设计中心',
    'index.portal_desc': '用文字、参考图和风格模板，定义你的婚纱影像方向。',
    'index.portal_action': '开始定制',
    'index.portal_enter': '进入',
    'index.legacy_kicker': '纪念系列',
    'index.legacy_title': '金婚重塑',
    'index.legacy_desc': '为父母与长辈生成兼具年代感与真实纹理的纪念合照。',
    'index.legacy_feature_restore': '年代质感修复',
    'index.legacy_feature_texture': '保留真实皮肤纹理',
    'index.legacy_feature_memory': '适合父母纪念照',
    'index.collection_title': '风格作品集',
    'index.collection_meta': '精选风格 · 2026',
    'index.discover': '查看详情 >',
    'index.loading_title': '正在加载风格作品',
    'index.loading_sub': '请稍候...',
    'index.feature_desc': '稳定生成链路，适合社交分享与高清交付。',
    'index.footer_logo': 'VowPic',
    'index.footer_credo': '让重要关系拥有值得珍藏的婚纱影像。',
    'index.footer_legal_intro': '使用前请阅读并同意以下内容：',
    'index.feature_flux': '稳定生成',
    'index.feature_couture': '风格控制',
    'index.feature_masterpiece': '高清交付',

    'category.all': '全部',
    'category.portrait': '单人',
    'category.couple': '双人',
    'category.legacy': '金婚',
    'category.bespoke': '定制',
    'category.collection': '风格',

    'orders.title': '我的作品',
    'orders.subtitle': '查看生成进度、预览结果和高清交付记录。',
    'orders.loading': '正在加载...',
    'orders.empty': '暂时还没有作品',
    'orders.start': '开始创作',
    'orders.custom': '自由定制',
    'orders.signin_required': '登录后查看作品',
    'orders.signin_required_subtitle': '使用 Google 登录后，系统会同步展示你的生成进度、预览图和高清交付记录。',
    'orders.signin': '登录',
    'orders.load_failed': '作品暂时无法刷新，请稍后重试。',
    'orders.retry': '再试一次',
    'orders.view': '查看结果',
    'orders.created_at': '创建时间',
    'orders.status_created': '已创建',
    'orders.status_checking': '检测中',
    'orders.status_generating': '生成中',
    'orders.status_completed': '已完成',
    'orders.status_failed': '已失败',
    'orders.status_refunded': '已退款',
    'orders.just_now': '刚刚',
    'orders.minutes_ago': '{count} 分钟前',
    'orders.hours_ago': '{count} 小时前',
    'orders.days_ago': '{count} 天前',
  },
  en: {
    'tab.home': 'Home',
    'tab.orders': 'Orders',
    'nav.brand': 'VowPic',
    'nav.home': 'Home',
    'nav.studio': 'Studio',
    'nav.orders': 'Orders',
    'nav.privacy': 'Privacy',
    'nav.terms': 'Terms',
    'nav.privacy_policy': 'Privacy Policy',
    'nav.terms_service': 'Terms of Service',

    'index.hero_tag': 'AI WEDDING STUDIO',
    'index.hero_title_line1': 'Premium',
    'index.hero_title_line2': 'Wedding Portraits',
    'index.hero_descriptor': 'Upload portraits and create keepsake-quality AI wedding photos for sharing, printing, and delivery.',
    'index.hero_primary_kicker': 'START NOW',
    'index.hero_primary_label': 'Create Now',
    'index.hero_secondary_kicker': 'COLLECTION',
    'index.hero_secondary_label': 'Browse Styles',
    'index.portal_label': 'BESPOKE STUDIO',
    'index.portal_title': 'AI Design Center',
    'index.portal_desc': 'Shape your wedding aesthetic with text direction, references, and curated templates.',
    'index.portal_action': 'Start Bespoke',
    'index.portal_enter': 'Enter',
    'index.legacy_kicker': 'LEGACY SERIES',
    'index.legacy_title': 'Golden Anniversary Remake',
    'index.legacy_desc': 'A respectful, high-fidelity anniversary portrait for parents and elders.',
    'index.legacy_feature_restore': 'Era-aware restoration',
    'index.legacy_feature_texture': 'Authentic skin texture',
    'index.legacy_feature_memory': 'Ideal for parents',
    'index.collection_title': 'The Collection',
    'index.collection_meta': 'Curated Aesthetics · 2026',
    'index.discover': 'View Details >',
    'index.loading_title': 'Loading styles',
    'index.loading_sub': 'Please wait...',
    'index.feature_desc': 'High-fidelity generation tuned for social sharing and HD delivery.',
    'index.footer_logo': 'VowPic',
    'index.footer_credo': 'Keepsake-quality wedding imagery, generated with care.',
    'index.footer_legal_intro': 'Please review these policies before using the service:',
    'index.feature_flux': 'Stable Engine',
    'index.feature_couture': 'Style Control',
    'index.feature_masterpiece': 'HD Delivery',

    'category.all': 'All',
    'category.portrait': 'Portrait',
    'category.couple': 'Couple',
    'category.legacy': 'Legacy',
    'category.bespoke': 'Bespoke',
    'category.collection': 'Style',

    'orders.title': 'My Gallery',
    'orders.subtitle': 'Track progress, previews, and final high-resolution deliveries.',
    'orders.loading': 'Loading...',
    'orders.empty': 'No creations yet',
    'orders.start': 'Start Creating',
    'orders.custom': 'Custom',
    'orders.signin_required': 'Sign in to view saved creations',
    'orders.signin_required_subtitle': 'Sign in with Google to keep your generation progress, previews, and final deliveries in one gallery.',
    'orders.signin': 'Sign in',
    'orders.load_failed': 'Gallery is temporarily unavailable. Please try again shortly.',
    'orders.retry': 'Try again',
    'orders.view': 'View Result',
    'orders.created_at': 'Created',
    'orders.status_created': 'Created',
    'orders.status_checking': 'Checking',
    'orders.status_generating': 'Generating',
    'orders.status_completed': 'Completed',
    'orders.status_failed': 'Failed',
    'orders.status_refunded': 'Refunded',
    'orders.just_now': 'Just now',
    'orders.minutes_ago': '{count}m ago',
    'orders.hours_ago': '{count}h ago',
    'orders.days_ago': '{count}d ago',
  },
};

function detectDefaultLocale(): Locale {
  try {
    const stored = uni.getStorageSync(LOCALE_STORAGE_KEY);
    if (stored === 'zh' || stored === 'en') return stored;
    return 'en';
  } catch {
    return 'en';
  }
}

export const useI18nStore = defineStore('i18n', () => {
  const locale = ref<Locale>(detectDefaultLocale());
  const isZh = computed(() => locale.value === 'zh');

  const setLocale = (nextLocale: Locale) => {
    locale.value = nextLocale;
    uni.setStorageSync(LOCALE_STORAGE_KEY, nextLocale);
  };

  const toggleLocale = () => {
    setLocale(locale.value === 'zh' ? 'en' : 'zh');
  };

  const t = (key: string): string => {
    return messages[locale.value][key] || messages.en[key] || key;
  };

  const tf = (key: string, params: Record<string, string | number> = {}): string => {
    let content = t(key);
    Object.entries(params).forEach(([name, value]) => {
      content = content.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value));
    });
    return content;
  };

  const applyTabBarLocale = async (): Promise<void> => {
    if (!isCurrentTabBarPage()) return;

    try {
      await Promise.all([
        uni.setTabBarItem({ index: 0, text: t('tab.home') }),
        uni.setTabBarItem({ index: 1, text: t('tab.orders') }),
      ]);
    } catch (error) {
      console.warn('Failed to update localized tab bar', error);
    }
  };

  return {
    locale,
    isZh,
    setLocale,
    toggleLocale,
    t,
    tf,
    applyTabBarLocale,
  };
});
