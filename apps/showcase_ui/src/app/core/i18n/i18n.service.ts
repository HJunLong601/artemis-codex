import { DOCUMENT, registerLocaleData } from '@angular/common';
import zh from '@angular/common/locales/zh';
import { computed, DestroyRef, inject, Injectable, InjectionToken, signal } from '@angular/core';
import { EN, ZH } from './messages';

registerLocaleData(zh);

export type Language = 'en' | 'zh';
export type LanguagePreference = Language | 'system';
export const LANGUAGE_STORAGE_KEY = 'artemis.console.language';

export interface LanguageEnvironment {
  languages: readonly string[];
  storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
}

/** Browser language settings inherit the user's OS preferences by default. */
export const LANGUAGE_ENVIRONMENT = new InjectionToken<LanguageEnvironment>('console language environment', {
  providedIn: 'root',
  factory: () => {
    const browser = inject(DOCUMENT).defaultView;
    let storage: Storage | undefined;
    try { storage = browser?.localStorage; } catch { /* Private/restricted storage: use this session. */ }
    return {
      get languages() {
        return browser?.navigator.languages?.length ? browser.navigator.languages : [browser?.navigator.language || ''];
      },
      storage,
    };
  },
});

export function detectLanguage(languages: readonly string[] = []): Language {
  const primary = languages.find(value => value?.trim())?.trim().toLowerCase() || '';
  return /^zh(?:[-_]|$)/.test(primary) ? 'zh' : 'en';
}

@Injectable({ providedIn: 'root' })
export class I18nService {
  private readonly environment = inject(LANGUAGE_ENVIRONMENT);
  private readonly document = inject(DOCUMENT);
  private readonly system = signal(detectLanguage(this.environment.languages));
  private readonly selected = signal<LanguagePreference>(this.readPreference());
  readonly preference = this.selected.asReadonly();
  readonly language = computed<Language>(() => this.selected() === 'system' ? this.system() : this.selected() as Language);
  readonly locale = computed(() => this.language() === 'zh' ? 'zh-CN' : 'en-US');

  constructor() {
    this.updateDocument();
    const browser = this.document.defaultView;
    const languageChanged = () => {
      this.system.set(detectLanguage(this.environment.languages));
      this.updateDocument();
    };
    const storageChanged = (event: StorageEvent) => {
      if (event.key === LANGUAGE_STORAGE_KEY || event.key === null) {
        this.selected.set(this.readPreference());
        this.updateDocument();
      }
    };
    browser?.addEventListener('languagechange', languageChanged);
    browser?.addEventListener('storage', storageChanged);
    inject(DestroyRef).onDestroy(() => {
      browser?.removeEventListener('languagechange', languageChanged);
      browser?.removeEventListener('storage', storageChanged);
    });
  }

  setPreference(value: string): void {
    if (value !== 'system' && value !== 'zh' && value !== 'en') return;
    this.selected.set(value);
    try {
      if (value === 'system') this.environment.storage?.removeItem(LANGUAGE_STORAGE_KEY);
      else this.environment.storage?.setItem(LANGUAGE_STORAGE_KEY, value);
    } catch { /* Selection remains usable when storage is disabled or full. */ }
    this.updateDocument();
  }

  /** Only call for console-owned copy. User goals, logs and model output stay verbatim. */
  t(value: unknown, parameters: readonly unknown[] = []): string {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object' && 'key' in value && typeof value.key === 'string'
      && 'parameters' in value && Array.isArray(value.parameters)) {
      return this.t(value.key, value.parameters);
    }
    const key = String(value);
    const messages = this.language() === 'zh' ? ZH : EN;
    const message = Object.hasOwn(messages, key) ? messages[key] : Object.hasOwn(EN, key) ? EN[key] : key;
    // Replace in one pass, so placeholder-looking user data cannot be expanded again.
    return message.replace(/\{(\d+)\}/g, (placeholder, index: string) =>
      Number(index) < parameters.length ? String(parameters[Number(index)] ?? '') : placeholder);
  }

  private readPreference(): LanguagePreference {
    try {
      const value = this.environment.storage?.getItem(LANGUAGE_STORAGE_KEY);
      return value === 'zh' || value === 'en' ? value : 'system';
    } catch { return 'system'; }
  }

  private updateDocument(): void {
    this.document.documentElement.lang = this.language() === 'zh' ? 'zh-CN' : 'en';
    this.document.title = this.t('Artemis Console');
  }
}
