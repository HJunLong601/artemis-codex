import { provideHttpClient, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { CacheGroup, CacheStats, VisualCacheComponent } from './visual-cache.component';

describe('Visual cache page identity', () => {
  let component: VisualCacheComponent;
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
    component = TestBed.createComponent(VisualCacheComponent).componentInstance;
    http = TestBed.inject(HttpTestingController);
    http.expectOne('/api/system/visual-cache').flush(stats([]));
  });
  afterEach(() => http.verify());

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
});
