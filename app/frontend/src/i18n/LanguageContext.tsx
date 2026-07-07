import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { translations, type Lang, type TranslationKey } from "./translations";

interface LanguageCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string;
}

const Ctx = createContext<LanguageCtx | null>(null);

const STORAGE_KEY = "onefcst_lang";

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(
    () => (localStorage.getItem(STORAGE_KEY) as Lang) || "ja",
  );

  const setLang = (l: Lang) => {
    setLangState(l);
    localStorage.setItem(STORAGE_KEY, l);
  };

  const t = useMemo(() => {
    return (key: TranslationKey, vars?: Record<string, string | number>) => {
      const entry = translations[key];
      let text: string = entry ? entry[lang] : key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          text = text.replace(`{${k}}`, String(v));
        }
      }
      return text;
    };
  }, [lang]);

  return <Ctx.Provider value={{ lang, setLang, t }}>{children}</Ctx.Provider>;
}

export function useLang() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useLang must be used within LanguageProvider");
  return ctx;
}
