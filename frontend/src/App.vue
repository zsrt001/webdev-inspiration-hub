<script setup lang="ts">
import { onLaunch, onShow, onHide } from '@dcloudio/uni-app';
import { watch } from 'vue';
import { useI18nStore } from './stores/i18n';

const i18nStore = useI18nStore();

const applyLocaleUi = () => {
  i18nStore.applyTabBarLocale();
};

onLaunch(() => {
  applyLocaleUi();
  console.log('AI Wedding Studio - Professional UI/UX Refactor');
});

onShow(() => {
  applyLocaleUi();
  console.log('App Show');
});

onHide(() => {
  console.log('App Hide');
});

watch(() => i18nStore.locale, () => {
  applyLocaleUi();
});
</script>

<style lang="scss">
/* Import Professional Typography from Google Fonts */
@import url('https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,wght@0,400;0,500;0,600;0,700;1,400&family=Jost:wght@300;400;500;600;700&display=swap');

/* ========================================
   Global Reset & Base Styles
   ======================================== */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
  -webkit-tap-highlight-color: transparent;
}

page {
  background-color: var(--color-background, #FDF2F8);
  font-family: $uni-font-family-sans;
  color: $uni-text-color;
  line-height: 1.6;
}

/* THE UNIFIER CLASS - The Studio Frame */
page {
  background: radial-gradient(circle at top right, #FDF2F8 0%, #FCE7F3 100%);
  min-height: 100vh;
}

.app-container {
  max-width: $uni-container-width;
  margin: 0 auto;
  min-height: 100vh;
  background-color: #ffffff;
  box-shadow: 0 0 100px rgba(131, 24, 67, 0.05);
  position: relative;
  display: flex;
  flex-direction: column;
  overflow-x: hidden;
}

/* Common Card Style - Liquid Glass Inspired */
.common-card {
  margin: $uni-space-md;
  padding: $uni-space-lg;
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(10px);
  border-radius: $uni-border-radius-base;
  box-shadow: $uni-shadow-base;
  border: 1px solid rgba(252, 231, 243, 0.5);
  transition: all $uni-transition-base;
  cursor: pointer;
  
  &:hover {
    box-shadow: $uni-shadow-lg;
    border-color: rgba($uni-color-primary, 0.2);
  }
}

/* ========================================
   Typography Classes
   ======================================== */
.heading-serif {
  font-family: $uni-font-family;
  font-weight: 500;
  letter-spacing: -0.01em;
  color: $uni-text-color;
  font-style: italic;
}

.heading-1 {
  font-size: $uni-font-size-3xl;
  font-weight: 600;
  line-height: 1.1;
  font-family: $uni-font-family;
}

.heading-2 {
  font-size: $uni-font-size-2xl;
  font-weight: 500;
  line-height: 1.2;
  font-family: $uni-font-family;
}

.heading-3 {
  font-size: $uni-font-size-xl;
  font-weight: 400;
  line-height: 1.3;
  font-family: $uni-font-family;
}

.text-jost {
  font-family: $uni-font-family-sans;
}

.text-muted {
  color: $uni-text-color-muted;
  opacity: 0.8;
}

.text-secondary {
  color: $uni-text-color-secondary;
}

.text-accent {
  color: $uni-color-accent;
}

/* ========================================
   Button Styles - Liquid Glass CTA
   ======================================== */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 16px 32px;
  font-size: $uni-font-size-base;
  font-weight: 600;
  font-family: $uni-font-family-sans;
  letter-spacing: 0.03em;
  cursor: pointer;
  transition: all $uni-transition-base;
  border: none;
  border-radius: $uni-border-radius-base;
}

.btn-primary {
  background: linear-gradient(135deg, $uni-color-primary, $uni-color-secondary);
  color: $uni-color-white;
  box-shadow: 0 8px 20px rgba($uni-color-primary, 0.2);
  
  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 12px 25px rgba($uni-color-primary, 0.3);
    opacity: 0.9;
  }
}

.btn-gold {
  background: linear-gradient(135deg, $uni-color-accent, #EAB308);
  color: $uni-color-white;
  box-shadow: 0 8px 20px rgba($uni-color-accent, 0.2);
  
  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 12px 25px rgba($uni-color-accent, 0.3);
  }
}

.btn-outline {
  background: transparent;
  color: $uni-color-primary;
  border: 1px solid $uni-color-primary;
  
  &:hover {
    background: rgba($uni-color-primary, 0.05);
  }
}

/* ========================================
   Responsive Typography
   ======================================== */
@media (max-width: 768px) {
  .heading-1 { font-size: $uni-font-size-2xl; }
  .heading-2 { font-size: $uni-font-size-xl; }
}

/* Global Studio Logo Animation */
@keyframes logo-float {
  0% { transform: translateY(0); }
  50% { transform: translateY(-3px); }
  100% { transform: translateY(0); }
}

.hover-lift {
  transition: all $uni-transition-base;
  &:hover {
    transform: translateY(-4px);
    box-shadow: $uni-shadow-lg;
  }
}

.cursor-pointer {
  cursor: pointer;
}
</style>
