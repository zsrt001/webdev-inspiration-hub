import { describe, expect, it } from 'vitest';

import {
  buildBaseCreatePayload,
  isTemplateCategoryAllowedForMode,
  templateCategoriesForMode,
} from '../../src/contracts/create';

describe('public create contract', () => {
  it('maps each customer mode to exactly one backend template category', () => {
    expect(templateCategoriesForMode('single')).toEqual(['single']);
    expect(templateCategoriesForMode('couple_local')).toEqual(['couple']);
    expect(templateCategoriesForMode('golden_anniversary')).toEqual(['vintage']);

    expect(isTemplateCategoryAllowedForMode('single', 'single')).toBe(true);
    expect(isTemplateCategoryAllowedForMode('couple', 'couple_local')).toBe(true);
    expect(isTemplateCategoryAllowedForMode('vintage', 'golden_anniversary')).toBe(true);
    expect(isTemplateCategoryAllowedForMode('vintage', 'couple_local')).toBe(false);
    expect(isTemplateCategoryAllowedForMode('couple', 'golden_anniversary')).toBe(false);
  });

  it('keeps ordinary text direction in the base funding class', () => {
    expect(buildBaseCreatePayload({
      templateId: 'solo-korean',
      assetIds: ['asset-1'],
      globalStyleText: '  quiet editorial light  ',
      sceneText: '  garden at dusk ',
      outfitText: '  ivory dress ',
    })).toEqual({
      template_id: 'solo-korean',
      asset_ids: ['asset-1'],
      legal_accepted: true,
      director_mode: false,
      global_style_text: 'quiet editorial light',
      scene_text: 'garden at dusk',
      outfit_text: 'ivory dress',
    });
  });
});
