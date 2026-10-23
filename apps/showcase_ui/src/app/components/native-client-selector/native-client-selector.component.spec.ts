import { provideHttpClient, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { NativeClientSelectorComponent } from './native-client-selector.component';

describe('NativeClientSelectorComponent', () => {
  let component: NativeClientSelectorComponent;
  let http: HttpTestingController;
  const base = '/api/system/model-clients';
  beforeEach(() => {
    TestBed.configureTestingModule({providers: [provideHttpClient(withXhr()), provideHttpClientTesting()]});
    component = TestBed.createComponent(NativeClientSelectorComponent).componentInstance;
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => { component.ngOnDestroy(); http.verify(); });
  it('loads the selected client without changing or verifying either account', () => {
    component.refresh();
    http.expectOne(base).flush({active_provider: 'codex', automatic_client_fallback: false});
    expect(component.active()).toBe('codex');
    http.expectNone(base + '/select');
    http.expectNone('/api/system/claude-code/auth/verify');
  });
  it('only switches after the user selects a different client', () => {
    component.active.set('codex'); component.loaded.set(true);
    component.select('codex');
    http.expectNone(base + '/select');
    component.select('claude_code');
    const request = http.expectOne(base + '/select');
    expect(request.request.body.provider).toBe('claude_code');
    expect(request.request.headers.get('X-Artemis-Auth-Intent')).toBe('login');
    request.flush({active_provider: 'claude_code'});
    expect(component.active()).toBe('claude_code');
  });
  it('does not fall back to the other client after failure', () => {
    component.active.set('claude_code'); component.loaded.set(true);
    component.select('codex');
    http.expectOne(base + '/select').flush({}, {status: 409, statusText: 'Conflict'});
    expect(component.active()).toBe('claude_code');
    expect(component.message()).toContain('不会自动回退');
    expect(component.busy()).toBeFalse();
  });
});
