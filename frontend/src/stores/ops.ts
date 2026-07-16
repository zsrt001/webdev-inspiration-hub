import { defineStore } from 'pinia';
import { get } from '../utils/api';

export interface HomeBannerConfig {
  enabled: boolean;
  title: string;
  subtitle: string;
  cta_label: string;
  secondary_cta_label: string;
  image_url: string;
}

export interface ActiveCapabilities {
  google_auth: boolean;
  authenticated_upload: boolean;
  generation: boolean;
  credit_pack_checkout: boolean;
  subscription_billing: boolean;
  private_download: boolean;
  partner_invite: boolean;
}

export interface PublicOpsConfig {
  placements: {
    home_banner: HomeBannerConfig;
  };
  capabilities: ActiveCapabilities;
}

const DEFAULT_CAPABILITIES: ActiveCapabilities = {
  google_auth: false,
  authenticated_upload: false,
  generation: false,
  credit_pack_checkout: false,
  subscription_billing: false,
  private_download: false,
  partner_invite: false,
};

function normalizeCapabilities(value: Partial<ActiveCapabilities> | null | undefined): ActiveCapabilities {
  return {
    google_auth: value?.google_auth === true,
    authenticated_upload: value?.authenticated_upload === true,
    generation: value?.generation === true,
    credit_pack_checkout: value?.credit_pack_checkout === true,
    subscription_billing: value?.subscription_billing === true,
    private_download: value?.private_download === true,
    partner_invite: value?.partner_invite === true,
  };
}

const DEFAULT_CONFIG: PublicOpsConfig = {
  placements: {
    home_banner: {
      enabled: true,
      title: 'VowPic Studio',
      subtitle: 'Premium wedding portraits in minutes',
      cta_label: 'Start Now',
      secondary_cta_label: 'Browse Collection',
      image_url: '/style-previews/couple_old_money.jpg',
    },
  },
  capabilities: { ...DEFAULT_CAPABILITIES },
};

export const useOpsStore = defineStore('ops', {
  state: () => ({
    publicConfig: DEFAULT_CONFIG as PublicOpsConfig,
    loaded: false,
  }),

  getters: {
    googleAuthAvailable: (state) => state.publicConfig.capabilities.google_auth,
    creationAvailable: (state) =>
      state.publicConfig.capabilities.authenticated_upload &&
      state.publicConfig.capabilities.generation,
    billingAvailable: (state) =>
      state.publicConfig.capabilities.credit_pack_checkout ||
      state.publicConfig.capabilities.subscription_billing,
    privateDownloadAvailable: (state) => state.publicConfig.capabilities.private_download,
    partnerInviteAvailable: (state) => state.publicConfig.capabilities.partner_invite,
  },

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
          capabilities: normalizeCapabilities(res?.capabilities),
        };
        const heroImage = String(this.publicConfig.placements.home_banner.image_url || '').trim();
        if (
          !heroImage ||
          heroImage === '/static/hero_banner.jpg' ||
          heroImage === '/hero_banner.jpg' ||
          heroImage === '/style-previews/hero_banner.jpg' ||
          heroImage === '/static/style-previews/hero_banner.jpg' ||
          heroImage === '/hero_wedding_luxury_bg.jpg' ||
          heroImage === '/static/hero_wedding_luxury_bg.jpg' ||
          heroImage === '/style-previews/royal_castle.jpg' ||
          heroImage === '/static/style-previews/royal_castle.jpg' ||
          heroImage === '/style-previews/solo_royal_castle.jpg' ||
          heroImage === '/static/style-previews/solo_royal_castle.jpg' ||
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
