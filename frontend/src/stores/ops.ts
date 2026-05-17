import { defineStore } from 'pinia';
import { get } from '../utils/api';

export interface HomeBannerConfig {
  enabled: boolean;
  title: string;
  subtitle: string;
  cta_label: string;
  secondary_cta_label: string;
  image_url: string;
  legacy_enabled: boolean;
  portal_enabled: boolean;
}

export interface PublicOpsConfig {
  placements: {
    home_banner: HomeBannerConfig;
  };
  feature_flags: {
    live_portrait: boolean;
    remote_join: boolean;
    local_recommendations: boolean;
    director_mode: boolean;
  };
  recommendations?: {
    lead_lookback_days?: number;
    lead_boost_per_conversion?: number;
    lead_boost_cap?: number;
    manual_boosts?: Record<string, number>;
  };
}

const DEFAULT_CONFIG: PublicOpsConfig = {
  placements: {
    home_banner: {
      enabled: true,
      title: 'AI Wedding Studio',
      subtitle: 'Premium wedding portraits in minutes',
      cta_label: 'Start Now',
      secondary_cta_label: 'Browse Collection',
      image_url: '/legacy_promo_banner.jpg',
      legacy_enabled: true,
      portal_enabled: true,
    },
  },
  feature_flags: {
    live_portrait: false,
    remote_join: true,
    local_recommendations: true,
    director_mode: true,
  },
};

export const useOpsStore = defineStore('ops', {
  state: () => ({
    publicConfig: DEFAULT_CONFIG as PublicOpsConfig,
    loaded: false,
  }),

  actions: {
    async fetchPublicConfig(force = false) {
      if (this.loaded && !force) return this.publicConfig;
      try {
        const res = await get<PublicOpsConfig>('/ops/public_config', {
          showLoading: false,
          showError: false,
        } as any);
        this.publicConfig = {
          ...DEFAULT_CONFIG,
          ...res,
          placements: {
            ...DEFAULT_CONFIG.placements,
            ...(res?.placements || {}),
            home_banner: {
              ...DEFAULT_CONFIG.placements.home_banner,
              ...(res?.placements?.home_banner || {}),
            },
          },
          feature_flags: {
            ...DEFAULT_CONFIG.feature_flags,
            ...(res?.feature_flags || {}),
          },
        };
        const heroImage = String(this.publicConfig.placements.home_banner.image_url || '').trim();
        if (
          !heroImage ||
          heroImage === '/static/hero_banner.jpg' ||
          heroImage === '/hero_banner.jpg' ||
          heroImage === '/style-previews/hero_banner.jpg' ||
          heroImage === '/static/style-previews/hero_banner.jpg' ||
          heroImage === '/style-previews/couple_royal_castle.jpg' ||
          heroImage === '/static/style-previews/couple_royal_castle.jpg'
        ) {
          this.publicConfig.placements.home_banner.image_url =
            DEFAULT_CONFIG.placements.home_banner.image_url;
        }
      } catch {
        this.publicConfig = DEFAULT_CONFIG;
      } finally {
        this.loaded = true;
      }
      return this.publicConfig;
    },
  },
});

