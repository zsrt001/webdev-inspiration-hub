import { defineStore } from 'pinia';
import { ref } from 'vue';
import { get } from '../utils/api';

export interface Template {
  id: string;
  category: string;
  title: string;
  image_url: string;
  style_family?: string | null;
  tags?: string[];
  marketing_title?: string | null;
  marketing_subtitle?: string | null;
  recommended_for?: string | null;
  clothing_ref_image_url?: string | null;
  scene_ref_image_url?: string | null;
  prompt_blocks?: Record<string, any> | null;
  clothing_prompt?: string;
  default_background_prompt?: string;
  is_custom?: boolean;
}

interface TemplateListResponse {
  templates: Template[];
}

type SupportedLocale = 'zh' | 'en';

interface TemplateCopyEntry {
  title: Record<SupportedLocale, string>;
  marketingTitle?: Record<SupportedLocale, string>;
  marketingSubtitle?: Record<SupportedLocale, string>;
}

const TEMPLATE_COPY: Record<string, TemplateCopyEntry> = {
  chn_xiuhe: {
    title: { zh: '中式秀禾', en: 'Chinese Xiuhe' },
  },
  korean_minimal: {
    title: { zh: '韩系极简', en: 'Korean Minimal' },
  },
  royal_castle: {
    title: { zh: '古堡皇室', en: 'Royal Castle' },
  },
  old_money: {
    title: { zh: '静奢庄园', en: 'Old Money' },
  },
  gothic_romance: {
    title: { zh: '哥特浪漫', en: 'Gothic Romance' },
  },
  beach_sunset: {
    title: { zh: '海边落日', en: 'Beach Sunset' },
  },
  hk_retro: {
    title: { zh: '港风复古', en: 'Hong Kong Retro' },
  },
  twilight_forest: {
    title: { zh: '暮光森林', en: 'Twilight Forest' },
  },
  japanese_shiromuku: {
    title: { zh: '日式白无垢', en: 'Japanese Shiromuku' },
  },
  cyberpunk_city: {
    title: { zh: '赛博都市', en: 'Cyberpunk City' },
  },
  school_days: {
    title: { zh: '校园时光', en: 'School Days' },
  },
  classic_bw: {
    title: { zh: '经典黑白', en: 'Classic B&W' },
  },
  golden_vintage_studio_8090: {
    title: { zh: '金婚重塑 · 80/90 影楼', en: 'Golden Anniversary · 80s/90s Studio' },
    marketingTitle: { zh: '为父母重塑经典影楼婚照', en: 'Recreate a classic wedding portrait for parents' },
    marketingSubtitle: { zh: '80/90 年代影楼氛围，结合现代修复质感', en: '80s/90s studio mood with modern restoration quality' },
  },
  golden_chinese_courtyard: {
    title: { zh: '金婚重塑 · 中式庭院', en: 'Golden Anniversary · Chinese Courtyard' },
    marketingTitle: { zh: '中式庭院长辈纪念合照', en: 'Traditional courtyard keepsake for elders' },
    marketingSubtitle: { zh: '暖红金色调，保留真实皮肤与岁月纹理', en: 'Warm red-gold palette with realistic skin detail' },
  },
  golden_modern_remake: {
    title: { zh: '金婚重塑 · 现代翻拍', en: 'Golden Anniversary · Modern Remake' },
    marketingTitle: { zh: '现代风纪念翻拍', en: 'Modern remake for milestone memories' },
    marketingSubtitle: { zh: '简洁构图与柔和高级光线', en: 'Clean composition and premium soft lighting' },
  },
  custom_mode: {
    title: { zh: '自由定制', en: 'Custom Mode' },
    marketingTitle: { zh: '设计你的专属婚纱风格', en: 'Design your own wedding style' },
    marketingSubtitle: { zh: '上传参考图并描述服装与场景，直接控制生成方向', en: 'Upload references and describe outfit and scene to direct the generation' },
  },
};

const PORTRAIT_TEMPLATE_FAMILIES = [
  {
    style_family: 'chn_xiuhe',
    single: {
      id: 'solo_chn_xiuhe',
      title: 'Chinese Xiuhe',
      image_url: '/style-previews/solo_chn_xiuhe.jpg',
      tags: ['chinese', 'xiuhe', 'solo'],
    },
    couple: {
      id: 'chn_xiuhe',
      title: 'Chinese Xiuhe',
      image_url: '/style-previews/couple_chn_xiuhe.jpg',
      tags: ['chinese', 'xiuhe', 'couple'],
    },
  },
  {
    style_family: 'korean_minimal',
    single: {
      id: 'solo_korean_minimal',
      title: 'Korean Minimal',
      image_url: '/style-previews/solo_korean_minimal.jpg',
      tags: ['korean', 'minimal', 'solo'],
    },
    couple: {
      id: 'korean_minimal',
      title: 'Korean Minimal',
      image_url: '/style-previews/couple_korean_minimal.jpg',
      tags: ['korean', 'minimal', 'couple'],
    },
  },
  {
    style_family: 'royal_castle',
    single: {
      id: 'solo_royal_castle',
      title: 'Royal Castle',
      image_url: '/style-previews/solo_royal_castle.jpg',
      tags: ['castle', 'royal', 'solo'],
    },
    couple: {
      id: 'royal_castle',
      title: 'Royal Castle',
      image_url: '/style-previews/couple_royal_castle.jpg',
      tags: ['castle', 'royal', 'couple'],
    },
  },
  {
    style_family: 'old_money',
    single: {
      id: 'solo_old_money',
      title: 'Old Money',
      image_url: '/style-previews/solo_old_money.jpg',
      tags: ['old_money', 'classic', 'solo'],
    },
    couple: {
      id: 'old_money',
      title: 'Old Money',
      image_url: '/style-previews/couple_old_money.jpg',
      tags: ['old_money', 'classic', 'couple'],
    },
  },
  {
    style_family: 'gothic_romance',
    single: {
      id: 'solo_gothic_romance',
      title: 'Gothic Romance',
      image_url: '/style-previews/solo_gothic_romance_v2.png',
      tags: ['gothic', 'dramatic', 'solo'],
    },
    couple: {
      id: 'gothic_romance',
      title: 'Gothic Romance',
      image_url: '/style-previews/couple_gothic_romance.jpg',
      tags: ['gothic', 'dramatic', 'couple'],
    },
  },
  {
    style_family: 'beach_sunset',
    single: {
      id: 'solo_beach_sunset',
      title: 'Beach Sunset',
      image_url: '/style-previews/solo_beach_sunset.jpg',
      tags: ['beach', 'sunset', 'solo'],
    },
    couple: {
      id: 'beach_sunset',
      title: 'Beach Sunset',
      image_url: '/style-previews/couple_beach_sunset.jpg',
      tags: ['beach', 'sunset', 'couple'],
    },
  },
  {
    style_family: 'hk_retro',
    single: {
      id: 'solo_hk_retro',
      title: 'Hong Kong Retro',
      image_url: '/style-previews/hk_retro.jpg',
      tags: ['hong_kong', 'retro', 'solo'],
    },
    couple: {
      id: 'hk_retro',
      title: 'Hong Kong Retro',
      image_url: '/style-previews/couple_hk_retro_v2.png',
      tags: ['hong_kong', 'retro', 'couple'],
    },
  },
  {
    style_family: 'twilight_forest',
    single: {
      id: 'solo_twilight_forest',
      title: 'Twilight Forest',
      image_url: '/style-previews/twilight_forest.jpg',
      tags: ['forest', 'dreamy', 'solo'],
    },
    couple: {
      id: 'twilight_forest',
      title: 'Twilight Forest',
      image_url: '/style-previews/couple_twilight_forest.jpg',
      tags: ['forest', 'dreamy', 'couple'],
    },
  },
  {
    style_family: 'japanese_shiromuku',
    single: {
      id: 'solo_japanese_shiromuku',
      title: 'Japanese Shiromuku',
      image_url: '/style-previews/japanese_shiromuku.jpg',
      tags: ['japanese', 'shiromuku', 'solo'],
    },
    couple: {
      id: 'japanese_shiromuku',
      title: 'Japanese Shiromuku',
      image_url: '/style-previews/couple_japanese_shiromuku.jpg',
      tags: ['japanese', 'shiromuku', 'couple'],
    },
  },
  {
    style_family: 'cyberpunk_city',
    single: {
      id: 'solo_cyberpunk_city',
      title: 'Cyberpunk City',
      image_url: '/style-previews/cyberpunk_city.jpg',
      tags: ['cyberpunk', 'city', 'solo'],
    },
    couple: {
      id: 'cyberpunk_city',
      title: 'Cyberpunk City',
      image_url: '/style-previews/couple_cyberpunk_city_v2.png',
      tags: ['cyberpunk', 'city', 'couple'],
    },
  },
  {
    style_family: 'school_days',
    single: {
      id: 'solo_school_days',
      title: 'School Days',
      image_url: '/style-previews/school_days.jpg',
      tags: ['youth', 'campus', 'solo'],
    },
    couple: {
      id: 'school_days',
      title: 'School Days',
      image_url: '/style-previews/couple_school_days.jpg',
      tags: ['youth', 'campus', 'couple'],
    },
  },
  {
    style_family: 'classic_bw',
    single: {
      id: 'solo_classic_bw',
      title: 'Classic B&W',
      image_url: '/style-previews/classic_bw.jpg',
      tags: ['black_white', 'timeless', 'solo'],
    },
    couple: {
      id: 'classic_bw',
      title: 'Classic B&W',
      image_url: '/style-previews/couple_classic_bw.jpg',
      tags: ['black_white', 'timeless', 'couple'],
    },
  },
] as const;

const FALLBACK_TEMPLATES: Template[] = [
  ...PORTRAIT_TEMPLATE_FAMILIES.flatMap((family) => [
    {
      id: family.single.id,
      category: 'single',
      title: family.single.title,
      image_url: family.single.image_url,
      style_family: family.style_family,
      tags: [...family.single.tags],
    },
    {
      id: family.couple.id,
      category: 'couple',
      title: family.couple.title,
      image_url: family.couple.image_url,
      style_family: family.style_family,
      tags: [...family.couple.tags],
    },
  ]),
  {
    id: 'golden_vintage_studio_8090',
    category: 'vintage',
    title: 'Golden Anniversary · 80s/90s Studio',
    image_url: '/style-previews/golden_vintage_studio_8090.jpg',
    style_family: 'golden_vintage_studio_8090',
    marketing_title: 'Recreate a classic wedding portrait for parents',
    marketing_subtitle: '80s/90s studio mood with modern restoration quality',
  },
  {
    id: 'golden_chinese_courtyard',
    category: 'vintage',
    title: 'Golden Anniversary · Chinese Courtyard',
    image_url: '/style-previews/golden_chinese_courtyard.jpg',
    style_family: 'golden_chinese_courtyard',
    marketing_title: 'Traditional courtyard keepsake for elders',
    marketing_subtitle: 'Warm red-gold palette with realistic skin detail',
  },
  {
    id: 'golden_modern_remake',
    category: 'vintage',
    title: 'Golden Anniversary · Modern Remake',
    image_url: '/style-previews/golden_modern_remake.jpg',
    style_family: 'golden_modern_remake',
    marketing_title: 'Modern remake for milestone memories',
    marketing_subtitle: 'Clean composition and premium soft lighting',
  },
  {
    id: 'custom',
    category: 'custom',
    title: 'Custom Mode',
    image_url: '/style-previews/custom_mode.jpg',
    style_family: 'custom_mode',
    is_custom: true,
  },
];

function normalizeCategory(category: string | null | undefined): string {
  const normalized = String(category || '').trim().toLowerCase();
  if (!normalized) return 'single';
  if (normalized === 'solo') return 'single';
  return normalized;
}

function cleanTemplateLabel(value: string | null | undefined): string {
  return String(value || '')
    .replace(/\s*\((solo|couple)\)\s*/gi, ' ')
    .replace(/\bsolo\b|\bcouple\b/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function toFamilyKey(template: Template | null | undefined): string {
  if (!template) return '';

  const explicit = String(template.style_family || '').trim().toLowerCase();
  if (explicit) return explicit;

  const normalizedTitle = cleanTemplateLabel(template.title).toLowerCase();
  if (normalizedTitle) return normalizedTitle.replace(/\s+/g, '_');

  const fileName = String(template.image_url || '')
    .split('/')
    .pop()
    ?.toLowerCase()
    .replace(/\.(jpg|jpeg|png|webp)$/i, '')
    .replace(/^(solo_|couple_)/, '')
    .replace(/_v\d+$/, '')
    .trim();

  return fileName || String(template.id || '').trim().toLowerCase();
}

export function getTemplateFamilyKey(template: Template | null | undefined): string {
  return toFamilyKey(template);
}

export function getLocalizedTemplateTitle(
  template: Template | null | undefined,
  locale: SupportedLocale = 'zh'
): string {
  const key = toFamilyKey(template);
  const copy = TEMPLATE_COPY[key] || TEMPLATE_COPY[String(template?.id || '').trim().toLowerCase()];
  if (copy?.title?.[locale]) return cleanTemplateLabel(copy.title[locale]);
  return cleanTemplateLabel(template?.title);
}

export function getLocalizedTemplateMarketingTitle(
  template: Template | null | undefined,
  locale: SupportedLocale = 'zh'
): string {
  const key = toFamilyKey(template);
  const copy = TEMPLATE_COPY[key] || TEMPLATE_COPY[String(template?.id || '').trim().toLowerCase()];
  return copy?.marketingTitle?.[locale] || getLocalizedTemplateTitle(template, locale);
}

export function getLocalizedTemplateMarketingSubtitle(
  template: Template | null | undefined,
  locale: SupportedLocale = 'zh'
): string {
  const key = toFamilyKey(template);
  const copy = TEMPLATE_COPY[key] || TEMPLATE_COPY[String(template?.id || '').trim().toLowerCase()];
  return copy?.marketingSubtitle?.[locale] || String(template?.marketing_subtitle || '').trim();
}

function normalizeTemplates(items: Template[] | undefined | null): Template[] {
  const list = Array.isArray(items) ? items : [];
  const byId = new Map<string, Template>();

  for (const item of list) {
    if (!item?.id) continue;
    const id = String(item.id).trim();
    if (!id) continue;

    byId.set(id, {
      ...item,
      id,
      category: normalizeCategory(item.category),
      image_url: String(item.image_url || '').trim() || '/style-previews/couple_royal_castle.jpg',
    });
  }

  return Array.from(byId.values());
}

export const useTemplateStore = defineStore('template', () => {
  const templates = ref<Template[]>([]);
  const loading = ref(false);
  const selectedTemplate = ref<Template | null>(null);

  async function fetchTemplates() {
    loading.value = true;
    try {
      const res = await get<TemplateListResponse>('/templates', {
        showLoading: false,
        showError: false,
      } as any);
      const normalized = normalizeTemplates(res?.templates);
      templates.value = normalized.length ? normalized : FALLBACK_TEMPLATES;
    } catch (error) {
      console.error('Failed to fetch templates:', error);
      templates.value = FALLBACK_TEMPLATES;
    } finally {
      loading.value = false;
    }
  }

  function selectTemplate(template: Template) {
    selectedTemplate.value = template;
  }

  function getRequiredImageCount(category: string): number {
    return normalizeCategory(category) === 'vintage' ? 2 : 1;
  }

  function resolveTemplateForMode(
    templateInput: Template | string | null | undefined,
    mode: 'single' | 'couple'
  ): Template | null {
    const current =
      typeof templateInput === 'string'
        ? templates.value.find((item) => item.id === templateInput) || null
        : templateInput || null;

    if (!current) return null;
    if (current.category === 'custom' || current.category === 'vintage') return current;

    const familyKey = toFamilyKey(current);
    if (!familyKey) return current;

    const desiredCategory = mode === 'couple' ? 'couple' : 'single';
    const matched = templates.value.find((item) => {
      if ((item.category || '').toLowerCase() !== desiredCategory) return false;
      return toFamilyKey(item) === familyKey;
    });

    return matched || current;
  }

  return {
    templates,
    loading,
    selectedTemplate,
    fetchTemplates,
    selectTemplate,
    getRequiredImageCount,
    resolveTemplateForMode,
  };
});
