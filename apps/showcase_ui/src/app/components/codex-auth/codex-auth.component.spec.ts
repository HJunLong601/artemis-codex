import { provideHttpClient, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { CodexAuthComponent, trustedCodexLoginUrl } from './codex-auth.component';

describe('CodexAuthComponent', () => {
  let component: CodexAuthComponent;
  let http: HttpTestingController;
  const base = '/api/system/codex/auth';
  beforeEach(() => {
    TestBed.configureTestingModule({providers: [provideHttpClient(withXhr()), provideHttpClientTesting()]});
    component = TestBed.createComponent(CodexAuthComponent).componentInstance;
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => { component.ngOnDestroy(); http.verify(); });
  it('accepts exact official HTTPS login origins only', () => {
    for (const host of ['auth.openai.com', 'chatgpt.com']) {
      expect(trustedCodexLoginUrl(`https://${host}/authorize?state=test`)).not.toBeNull();
    }
    for (const value of ['bad-url', 'http://chatgpt.com/', 'https://chatgpt.com.attacker.invalid/',
      'https://sub.chatgpt.com/', 'https://user@chatgpt.com/', 'https://chatgpt.com:444/', 'javascript:alert(1)']) {
      expect(trustedCodexLoginUrl(value)).toBeNull();
    }
  });
  it('does not call inference during diagnostics', () => {
    component.refresh();
    http.expectOne(base).flush({state: 'authenticated'});
    expect(component.authenticated()).toBeTrue();
    expect(component.message()).toContain('尚未验证实际推理');
    http.expectNone(base + '/verify');
  });
  it('shows credential access failure separately from signed out', () => {
    component.refresh();
    http.expectOne(base).flush({state: 'credential_check_failed'});
    expect(component.message()).toContain('不等于已确认退出登录');
    expect(component.authenticated()).toBeFalse();
  });
  it('polls pending login then removes the authorization URL on success', fakeAsync(() => {
    component.login();
    const start = http.expectOne(base + '/start');
    expect(start.request.headers.get('X-Artemis-Auth-Intent')).toBe('login');
    start.flush({state: 'login_pending', auth_url: 'https://auth.openai.com/authorize?state=test'});
    http.expectOne(base).flush({state: 'login_pending'});
    expect(component.authUrl()).toContain('auth.openai.com');
    tick(3000);
    http.expectOne(base).flush({state: 'authenticated'});
    expect(component.authUrl()).toBeNull();
    expect(component.authenticated()).toBeTrue();
  }));
  it('requires explicit inference and distinguishes skipped inference from pass', () => {
    component.status.set({state: 'authenticated'});
    component.verify();
    http.expectOne(base + '/verify').flush({ok: true, inference_tested: false});
    expect(component.message()).toContain('未通过或未执行');
    component.verify();
    http.expectOne(base + '/verify').flush({ok: true, inference_tested: true});
    expect(component.message()).toContain('实际推理验证通过');
  });
  it('restores the pending login link after a page reload', fakeAsync(() => {
    component.ngOnInit();
    http.expectOne(base).flush({state: 'login_pending', auth_url: 'https://chatgpt.com/authorize?state=test', expires_in: 60});
    expect(component.authUrl()).toContain('chatgpt.com');
    tick(3000);
    http.expectOne(base).flush({state: 'login_failed'});
    expect(component.message()).toContain('授权失败');
    expect(component.authUrl()).toBeNull();
    expect(component.busy()).toBeFalse();
  }));
  it('retries a transient polling error without losing the pending link', fakeAsync(() => {
    component.refresh();
    http.expectOne(base).flush({state: 'login_pending', auth_url: 'https://chatgpt.com/authorize?state=test', expires_in: 60});
    tick(3000);
    http.expectOne(base).flush({}, {status: 503, statusText: 'Unavailable'});
    expect(component.authUrl()).toContain('chatgpt.com');
    tick(3000);
    http.expectOne(base).flush({state: 'authenticated'});
    expect(component.authUrl()).toBeNull();
    expect(component.authenticated()).toBeTrue();
  }));
  it('clears stale URLs on expiry and does not accept an injected resume URL', fakeAsync(() => {
    component.refresh();
    http.expectOne(base).flush({state: 'login_pending', auth_url: 'https://attacker.invalid/', expires_in: 0});
    expect(component.authUrl()).toBeNull();
    expect(component.status()?.state).toBe('login_expired');
    expect(component.authenticated()).toBeFalse();
  }));
  it('does not remain permanently busy after a stalled diagnostic request', fakeAsync(() => {
    component.refresh();
    const request = http.expectOne(base);
    tick(55000);
    expect(request.cancelled).toBeTrue();
    expect(component.busy()).toBeFalse();
  }));
});
