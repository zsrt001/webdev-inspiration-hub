import type { CreateOrderPayload } from './order';

export type GenerationMode = 'single' | 'couple_local' | 'golden_anniversary';
export type TemplateCategory = 'single' | 'couple' | 'vintage';

const TEMPLATE_CATEGORIES_BY_MODE: Record<GenerationMode, readonly TemplateCategory[]> = {
  single: ['single'],
  couple_local: ['couple'],
  golden_anniversary: ['vintage'],
};

export function templateCategoriesForMode(
  mode: GenerationMode,
): readonly TemplateCategory[] {
  return TEMPLATE_CATEGORIES_BY_MODE[mode];
}

export function isTemplateCategoryAllowedForMode(
  category: string | null | undefined,
  mode: GenerationMode,
): boolean {
  return templateCategoriesForMode(mode).includes(
    String(category || '').trim().toLowerCase() as TemplateCategory,
  );
}

export interface CreatePayloadInput {
  templateId: string;
  assetIds: string[];
  globalStyleText?: string;
  sceneText?: string;
  outfitText?: string;
}

function optionalText(value: string | undefined): string | undefined {
  return value?.trim() || undefined;
}

/**
 * The public create page has no paid Director control. Ordinary text direction
 * remains part of the base product and must not silently switch funding class.
 */
export function buildBaseCreatePayload(input: CreatePayloadInput): CreateOrderPayload {
  return {
    template_id: input.templateId,
    asset_ids: [...input.assetIds],
    legal_accepted: true,
    director_mode: false,
    global_style_text: optionalText(input.globalStyleText),
    scene_text: optionalText(input.sceneText),
    outfit_text: optionalText(input.outfitText),
  };
}
