/**
 * i18n configuration (Phase 8.5)
 *
 * Usage:
 *   import { useTranslation } from "react-i18next";
 *   const { t } = useTranslation();
 *   t("common.loading")  // → "Loading…"
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
  },
  lng: "en",
  fallbackLng: "en",
  interpolation: {
    escapeValue: false, // React already escapes values
  },
});

export default i18n;
