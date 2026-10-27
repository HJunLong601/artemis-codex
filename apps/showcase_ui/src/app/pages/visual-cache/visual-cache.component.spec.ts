import { provideHttpClient, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { CacheGroup, CacheStats, VisualCacheComponent } from './visual-cache.component';

describe('Visual cache page identity', () => {
  let component: VisualCacheComponent;
  let fixture: ComponentFixture<VisualCacheComponent>;
  let http: HttpTestingController;
  const group = (): CacheGroup => ({
    device_key: 'test-device', device_name: 'Test device', platform: 'android', category: 'app',
    application: 'example.app', activity: 'example.app.MainActivity', page_name: null,
    fingerprints: [{ page_key: '1234567890abcdef', page_name: null, positions: 1, hits: 0 }],
    pages: 1, positions: 1, hits: 0
  });
  const stats = (groups: CacheGroup[]): CacheStats => ({
    enabled: true, capacity_per_device: 10000, path: '', exists: true, size_bytes: 0,
    updated_at: null, checked_at: 1,
    totals: { devices: 1, applications: 1, pages: groups.reduce((n, g) => n + g.pages, 0), positions: 1, hits: 0 },
    groups
  });
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [VisualCacheComponent],
      providers: [provideHttpClient(withXhr()), provideHttpClientTesting()]
    });
    fixture = TestBed.createComponent(VisualCacheComponent);
    component = fixture.componentInstance;
    http = TestBed.inject(HttpTestingController);
    http.expectOne('/api/system/visual-cache').flush(stats([]));
  });
  afterEach(() => {
    // The independently tested exploration panel reads status even when cache statistics are empty.
    http.match('/api/system/visual-cache/exploration/tasks').forEach(request => request.flush({ items: [] }));
    http.verify();
  });

  function renderHistory(): HTMLElement {
    component.stats.set({ ...stats([]), history: {
      since: null, queries: 1234567890, hits: 100, hit_rate: .5,
      metrics: {}, misses: {}, inventory: [], inventory_counts: {},
      warm_reuse: { queries: 12, hits: 6, hit_rate: .5 }
    } });
    fixture.detectChanges();
    return fixture.nativeElement.querySelector('.history .metrics') as HTMLElement;
  }

  it('keeps six metrics in two balanced rows when the container has room for three columns', () => {
    const grid = renderHistory();
    grid.style.width = '1040px';
    const cards = Array.from(grid.children) as HTMLElement[];
    const rows = new Map<number, number>();
    for (const card of cards) {
      const top = Math.round(card.getBoundingClientRect().top);
      rows.set(top, (rows.get(top) ?? 0) + 1);
    }
    expect([...rows.values()]).toEqual([3, 3]);
    expect(new Set(cards.map(card => Math.round(card.getBoundingClientRect().width))).size).toBe(1);
  });

  for (const width of [240, 320, 390, 480, 640, 780, 960, 1040]) {
    for (const count of [4, 5, 6, 7, 8]) {
      it(`fits ${count} metrics with long values inside a ${width}px container`, () => {
        const grid = renderHistory();
        grid.style.width = `${width}px`;
        const templates = Array.from(grid.children);
        grid.replaceChildren(...Array.from({ length: count }, (_, index) => templates[index % templates.length].cloneNode(true)));
        const cards = Array.from(grid.children) as HTMLElement[];
        for (const card of cards) card.querySelector('strong')!.textContent = '123,456,789,012,345,678 / 99.9%';
        const container = grid.getBoundingClientRect();
        expect(grid.scrollWidth).toBeLessThanOrEqual(grid.clientWidth + 1);
        for (const card of cards) {
          const rect = card.getBoundingClientRect();
          expect(rect.left).toBeGreaterThanOrEqual(container.left - 1);
          expect(rect.right).toBeLessThanOrEqual(container.right + 1);
          expect(card.scrollWidth).toBeLessThanOrEqual(card.clientWidth + 1);
          for (const other of cards) {
            if (other === card) continue;
            const adjacent = other.getBoundingClientRect();
            const overlap = Math.min(rect.right, adjacent.right) - Math.max(rect.left, adjacent.left) > 1 &&
              Math.min(rect.bottom, adjacent.bottom) - Math.max(rect.top, adjacent.top) > 1;
            expect(overlap).toBeFalse();
          }
        }
      });
    }
  }

  it('uses Activity and fingerprint, or package and fingerprint, when the name is unknown', () => {
    const row = group();
    expect(component.pageLabel(row)).toBe('MainActivity · 12345678');
    row.activity = null;
    row.fingerprints[0].page_name = 'unknow page';
    expect(component.pageLabel(row, row.fingerprints[0].page_key)).toBe('example.app · 12345678');
    row.page_name = 'Settings';
    // An explicitly unnamed fingerprint must not borrow another page's name.
    expect(component.pageLabel(row, row.fingerprints[0].page_key)).toBe('example.app · 12345678');
  });

  it('searches names on individual fingerprints within a stable Activity group', () => {
    const row = group();
    row.fingerprints.push({ page_key: 'second-page', page_name: 'Preferences', positions: 1, hits: 0 });
    row.pages = 2;
    component.stats.set(stats([row]));
    component.search.set('preferences');
    expect(component.filteredGroups()).toEqual([row]);
    expect(component.pageLabel(row, 'second-page')).toBe('Preferences');
    component.search.set('absent');
    expect(component.filteredGroups()).toEqual([]);
  });

  it('opens the selected fingerprint with its own label and exact query parameters', () => {
    const row = group();
    component.inspect(row, row.fingerprints[0].page_key);
    expect(component.selection()?.name).toBe('MainActivity · 12345678');
    const request = http.expectOne(req => req.url === '/api/system/visual-cache/elements');
    expect(request.request.params.get('page_key')).toBe('1234567890abcdef');
    expect(request.request.params.get('application')).toBe('example.app');
    request.flush({ total: 0, offset: 0, limit: 25, items: [] });
  });

  const device = (id: string, busy = false, platform = 'android', state = 'ready') => ({
    serial: id, canonical_id: `${platform}:${id}`, name: 'Test phone', platform, state, is_busy: busy
  });

  it('uses cumulative-query wording rather than implying a count of unlearned pages', () => {
    expect(component.missNames['page_missing']).toBe('无可复用页面记录（累计查询次数）');
  });

  it('loads ready Android devices without automatically selecting one', () => {
    component.openWarmup();
    http.expectOne('/api/devices').flush({ devices: [device('one'), device('two', true), device('ios', false, 'ios'), device('offline', false, 'android', 'offline')] });
    expect(component.warmupDevices().map(item => item.serial)).toEqual(['one', 'two']);
    expect(component.warmupDevice()).toBe('');
    component.startWarmup();
    http.expectNone('/api/system/visual-cache/warmup');
  });

  it('requires explicit screenshot consent and prevents duplicate requests', () => {
    component.stats.set(stats([]));
    component.openWarmup();
    http.expectOne('/api/devices').flush({ devices: [device('one')] });
    component.warmupDevice.set('android:one');
    component.startWarmup();
    http.expectNone('/api/system/visual-cache/warmup');
    component.warmupConfirmed.set(true);
    component.startWarmup();
    const request = http.expectOne('/api/system/visual-cache/warmup');
    expect(request.request.body).toEqual({ device_serial: 'android:one', confirm_model_call: true });
    expect(request.request.headers.get('X-Artemis-Cache-Intent')).toBe('warmup');
    component.startWarmup();
    http.expectNone('/api/system/visual-cache/warmup');
    request.flush({ status: 'complete', stored: 3 });
    http.expectOne('/api/system/visual-cache').flush(stats([]));
    expect(component.warming()).toBeFalse();
    expect(component.i18n.t(component.warmupMessage())).toContain('3');
    expect(component.warmupConfirmed()).toBeFalse();
  });

  it('does not start on a busy device or when caching is disabled', () => {
    component.stats.set(stats([]));
    component.openWarmup();
    http.expectOne('/api/devices').flush({ devices: [device('one', true)] });
    component.warmupDevice.set('android:one');
    component.warmupConfirmed.set(true);
    component.startWarmup();
    http.expectNone('/api/system/visual-cache/warmup');
    component.warmupDevices.set([device('one')]);
    component.stats.set({ ...stats([]), enabled: false });
    component.startWarmup();
    http.expectNone('/api/system/visual-cache/warmup');
  });

  it('clears pending state and reports a retryable error without raw server output', () => {
    component.stats.set(stats([]));
    component.openWarmup();
    http.expectOne('/api/devices').flush({ devices: [device('one')] });
    component.warmupDevice.set('android:one');
    component.warmupConfirmed.set(true);
    component.startWarmup();
    http.expectOne('/api/system/visual-cache/warmup').flush({ detail: 'PRIVATE' }, { status: 409, statusText: 'Conflict' });
    http.expectOne('/api/system/visual-cache').flush(stats([]));
    expect(component.warming()).toBeFalse();
    expect(component.warmupConfirmed()).toBeFalse();
    expect(component.i18n.t(component.warmupMessage())).not.toContain('PRIVATE');
  });
});
