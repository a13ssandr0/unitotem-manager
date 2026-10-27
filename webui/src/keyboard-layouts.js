/**
 * Names of the on-screen keyboard layouts, shared by the keyboard itself and the Viewers page.
 *
 * A layout is a language of Gboard ('it', 'pt-BR'...) and, when the language has several, one of its variants ('de/de_neo2'). The twelve
 * names the setting had before the layouts were Gboard's ('english', 'italian'...) still mean the same, and the page keeps saving them
 * for those languages: a version that knows only those names can still read the setting.
 */
export const LEGACY_LAYOUTS = {english: 'en-US', italian: 'it', german: 'de', french: 'fr', spanish: 'es-ES', brazilian: 'pt-BR',
  russian: 'ru', polish: 'pl', czech: 'cs', greek: 'el', turkish: 'tr', ukrainian: 'uk'}
const LEGACY_NAME_OF = Object.fromEntries(Object.entries(LEGACY_LAYOUTS).map(([name, id]) => [id, name]))

/** 'it', 'de/de_neo2' or an old name -> {lang, variant} (variant undefined: the language's own). */
export function parseLayoutId(id) {
  const [lang, variant] = String(id ?? '').split('/')
  return {lang: LEGACY_LAYOUTS[lang] ?? lang, variant}
}

/** The value of the setting for a language and one of its variants (the first variant of a language is the language itself). */
export function layoutId(lang, variant, languageVariants) {
  const base = LEGACY_NAME_OF[lang] ?? lang
  return !variant || variant === languageVariants[0] ? base : `${base}/${variant}`
}
