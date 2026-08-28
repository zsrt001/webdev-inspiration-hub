import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

function source(path: string): string {
  return readFileSync(resolve(__dirname, '../../src', path), 'utf8');
}

describe('public interface localization', () => {
  it.each([
    'pages/auth/login.vue',
    'pages/auth/register.vue',
    'pages/auth/callback.vue',
  ])('%s exposes a local language switch', (path) => {
    expect(source(path)).toContain('i18nStore.toggleLocale()');
  });

  it('localizes the known public text that previously stayed in English', () => {
    expect(source('pages/index/index.vue')).toContain("tr('跳到主要内容', 'Skip to main content')");
    expect(source('components/CompareSlider.vue')).toContain("tr('原图', 'ORIGINAL')");
    expect(source('pages/preview/preview.vue')).toContain("tr('AI 婚纱预览', 'AI WEDDING PREVIEW')");
    expect(source('components/PaymentModal.vue')).toContain("tr('关闭', 'Close')");
    expect(source('pages/legal/privacy.vue')).toContain("tr('隐私政策', 'Privacy Policy')");
  });
});
