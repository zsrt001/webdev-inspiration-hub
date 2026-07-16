<script setup lang="ts">
// Global styles only; page routing owns all runtime behavior.
</script>

<style lang="scss">
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
  background-color: var(--color-background, #F7F8FA);
  font-family: $uni-font-family-sans;
  color: $uni-text-color;
  line-height: 1.6;
}

page {
  background: #F7F8FA;
  min-height: 100vh;
}

/* All pages use custom Web navigation; disable Uni-app's delayed CDN shadow preload. */
body::after {
  content: none !important;
  animation: none !important;
  background-image: none !important;
}

.app-container {
  width: 100%;
  max-width: none;
  margin: 0 auto;
  min-height: 100vh;
  background-color: #F7F8FA;
  box-shadow: none;
  position: relative;
  display: flex;
  flex-direction: column;
  overflow-x: hidden;
}

/* Common Card Style */
.common-card {
  margin: $uni-space-md;
  padding: $uni-space-lg;
  background: #ffffff;
  border-radius: $uni-border-radius-base;
  box-shadow: $uni-shadow-base;
  border: 1px solid $uni-color-border;
  transition: all $uni-transition-base;
  cursor: pointer;
  
  &:hover {
    box-shadow: $uni-shadow-lg;
    border-color: rgba($uni-color-primary, 0.28);
  }
}

/* ========================================
   Typography Classes
   ======================================== */
.heading-serif {
  font-family: 'Noto Serif SC', 'Source Han Serif SC', 'Songti SC', 'SimSun', $uni-font-family;
  font-weight: 500;
  letter-spacing: 0;
  color: $uni-text-color;
  font-style: normal;
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
   Button Styles
   ======================================== */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 16px 32px;
  font-size: $uni-font-size-base;
  font-weight: 600;
  font-family: $uni-font-family-sans;
  letter-spacing: 0;
  cursor: pointer;
  transition: all $uni-transition-base;
  border: none;
  border-radius: $uni-border-radius-base;
}

.btn-primary {
  background: $uni-color-primary;
  color: $uni-color-white;
  box-shadow: 0 8px 20px rgba($uni-color-primary, 0.16);
  
  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 12px 25px rgba($uni-color-primary, 0.3);
    opacity: 0.9;
  }
}

.btn-gold {
  background: $uni-color-accent;
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
