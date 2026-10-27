import { Component, ChangeDetectionStrategy } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { I18nService, LANGUAGE_ENVIRONMENT, LANGUAGE_STORAGE_KEY, detectLanguage } from './i18n.service';
import { EN, ZH } from './messages';
import { uiMessage } from './message';
import { TranslatePipe } from './translate.pipe';
import { getToolDisplayLabel } from '../../utils/tool-formatter.util';
import { SMART_TASK_LIBRARY } from '../data/smart-tasks.data';
import { VERIFICATION_LEVELS, EXPLORER_MODES } from '../models/pro-tuning.model';

@Component({
  imports: [TranslatePipe], changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<h1>{{ '页面缓存' | t }}</h1><p>{{ feedback | t }}</p><input value="my unsaved goal" />
    <pre>{{ raw }}</pre><span>{{ 'Task Queue' | t }}</span>`,
})
class LanguageTestComponent {
  feedback = uiMessage('学习完成，已保存 {0} 个常用控件。', [12]);
  raw = 'Task Queue <script> & {0} 原始模型结果';
}

describe('Console internationalization', () => {
  let storage: Map<string, string>;
  let languages: string[];
  let originalTitle: string;
  let originalLang: string;
  const configure = (persisted?: string) => {
    if (persisted) storage.set(LANGUAGE_STORAGE_KEY, persisted);
    TestBed.configureTestingModule({
      imports: [LanguageTestComponent],
      providers: [{ provide: LANGUAGE_ENVIRONMENT, useValue: {
        get languages() { return languages; },
        storage: {
          getItem: (key: string) => storage.get(key) ?? null,
          setItem: (key: string, value: string) => { storage.set(key, value); },
          removeItem: (key: string) => { storage.delete(key); },
        },
      } }],
    });
    return TestBed.inject(I18nService);
  };
  beforeEach(() => {
    storage = new Map(); languages = ['en-US'];
    originalTitle = document.title; originalLang = document.documentElement.lang;
  });
  afterEach(() => { document.title = originalTitle; document.documentElement.lang = originalLang; });

  it('detects Chinese regions and scripts, and defaults unsupported or absent locales to English', () => {
    for (const locale of ['zh', 'zh-CN', 'zh-TW', 'zh-Hant-HK', 'ZH_hans_CN']) expect(detectLanguage([locale])).toBe('zh');
    for (const locales of [[], [''], ['en-GB'], ['fr-FR'], ['fr-FR', 'zh-CN'], ['zho']]) expect(detectLanguage(locales)).toBe('en');
  });
  it('uses the system language on the first visit', () => {
    languages = ['zh-TW']; const service = configure();
    expect(service.language()).toBe('zh'); expect(service.preference()).toBe('system');
    expect(document.documentElement.lang).toBe('zh-CN'); expect(document.title).toBe('Artemis 控制台');
  });
  it('honors valid saved choices and ignores corrupted or unsupported preferences', () => {
    languages = ['zh-CN']; const service = configure('fr');
    expect(service.preference()).toBe('system');
    service.setPreference('en'); expect(storage.get(LANGUAGE_STORAGE_KEY)).toBe('en');
    expect(service.language()).toBe('en'); expect(document.documentElement.lang).toBe('en');
    service.setPreference('invalid'); expect(service.language()).toBe('en');
  });
  it('restores a saved manual choice over the system preference', () => {
    languages = ['zh-CN']; expect(configure('en').language()).toBe('en');
  });
  it('can return to system defaults and tracks later system language changes', () => {
    const service = configure('zh'); service.setPreference('system');
    expect(storage.has(LANGUAGE_STORAGE_KEY)).toBeFalse(); expect(service.language()).toBe('en');
    languages = ['zh-CN']; window.dispatchEvent(new Event('languagechange'));
    expect(service.language()).toBe('zh');
    service.setPreference('en'); languages = ['zh-TW']; window.dispatchEvent(new Event('languagechange'));
    expect(service.language()).toBe('en');
  });
  it('synchronizes manual choices and cleared preferences across tabs', () => {
    const service = configure(); storage.set(LANGUAGE_STORAGE_KEY, 'zh');
    window.dispatchEvent(new StorageEvent('storage', {key: LANGUAGE_STORAGE_KEY, newValue: 'zh'}));
    expect(service.language()).toBe('zh'); storage.clear();
    window.dispatchEvent(new StorageEvent('storage', {key: null})); expect(service.preference()).toBe('system');
  });
  it('works when storage is missing, blocked or full', () => {
    TestBed.configureTestingModule({ providers: [{provide: LANGUAGE_ENVIRONMENT, useValue: {
      languages: [], storage: {
        getItem: () => { throw new Error('blocked'); },
        setItem: () => { throw new Error('quota'); },
        removeItem: () => { throw new Error('blocked'); },
      },
    }}] });
    const service = TestBed.inject(I18nService); expect(service.language()).toBe('en');
    expect(() => service.setPreference('zh')).not.toThrow(); expect(service.language()).toBe('zh');
    expect(() => service.setPreference('system')).not.toThrow(); expect(service.language()).toBe('en');
  });
  it('renders instant language changes in OnPush views without losing edits or translating raw data', async () => {
    const service = configure(); const fixture = TestBed.createComponent(LanguageTestComponent);
    fixture.detectChanges(); const input: HTMLInputElement = fixture.nativeElement.querySelector('input');
    input.value = '未保存的输入 / unsaved';
    expect(fixture.nativeElement.querySelector('h1').textContent).toBe('Page Cache');
    expect(fixture.nativeElement.querySelector('p').textContent).toContain('12 common controls');
    service.setPreference('zh'); await fixture.whenStable(); fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('h1').textContent).toBe('页面缓存');
    expect(fixture.nativeElement.querySelector('p').textContent).toBe('学习完成，已保存 12 个常用控件。');
    expect(fixture.nativeElement.querySelector('input')).toBe(input); expect(input.value).toBe('未保存的输入 / unsaved');
    expect(fixture.nativeElement.querySelector('pre').textContent).toBe(fixture.componentInstance.raw);
    expect(fixture.nativeElement.querySelector('script')).toBeNull();
    service.setPreference('en'); fixture.detectChanges(); expect(fixture.nativeElement.querySelector('span').textContent).toBe('Task Queue');
  });
  it('keeps interpolated data verbatim and does not recursively expand placeholders', () => {
    const service = configure('zh');
    expect(service.t('Tapping on "{0}"', ['<b>{1}</b>'])).toBe('点击“<b>{1}</b>”');
    expect(service.t('unknown.message')).toBe('unknown.message'); expect(service.t(null)).toBe('');
  });
  it('localizes generated action descriptions while keeping tool arguments verbatim', () => {
    const service = configure('zh'); const translate = (key: string, args?: readonly unknown[]) => service.t(key, args);
    const tool = {name: 'click', args: {target_text: 'Task Queue'}};
    expect(getToolDisplayLabel(tool, false, translate)).toBe('点击“Task Queue”');
    expect(getToolDisplayLabel(tool)).toBe('Tapping on "Task Queue"');
  });
  it('ships complete catalogs with matching placeholders and English fallbacks for all Chinese source copy', () => {
    expect(Object.keys(ZH).sort()).toEqual(Object.keys(EN).sort());
    for (const key of Object.keys(EN)) {
      const slots = (s: string) => [...new Set(s.match(/\{\d+\}/g) ?? [])].sort();
      // Optional English plural suffix is deliberately omitted in Chinese.
      const optional = ['{0} local virtual device{1} available', 'Waiting for {0} second{1}...'].includes(key);
      if (!optional) expect(slots(ZH[key])).withContext(key).toEqual(slots(EN[key]));
      expect(EN[key].length).withContext(key).toBeGreaterThan(0);
      if (/[\u3400-\u9fff]/.test(key)) expect(EN[key]).withContext(key).not.toMatch(/[\u3400-\u9fff]/);
    }
  });
  it('covers every recommended task and Pro tuning explanation', () => {
    const copy = SMART_TASK_LIBRARY.flatMap(task => [task.title, task.description, task.goal]);
    for (const level of [...VERIFICATION_LEVELS, ...EXPLORER_MODES]) {
      copy.push(level.label, level.tagline, level.latency, level.bestFor, ...level.runs, ...(level.skips ?? []));
    }
    for (const key of copy) {
      expect(Object.hasOwn(EN, key)).withContext(key).toBeTrue();
      expect(Object.hasOwn(ZH, key)).withContext(key).toBeTrue();
    }
  });
});
