import { provideHttpClient, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ClaudeCodeAuthComponent } from './claude-code-auth.component';

describe('ClaudeCodeAuthComponent', () => {
  let component: ClaudeCodeAuthComponent;
  let http: HttpTestingController;
  const base = '/api/system/claude-code/auth';
  beforeEach(() => {
    TestBed.configureTestingModule({providers: [provideHttpClient(withXhr()), provideHttpClientTesting()]});
    component = TestBed.createComponent(ClaudeCodeAuthComponent).componentInstance;
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => { component.ngOnDestroy(); http.verify(); });
  it('refresh never performs paid inference', () => {
    component.refresh();
    http.expectOne(base).flush({state: 'authenticated', required: true});
    expect(component.authenticated()).toBeTrue();
    expect(component.message()).toContain('尚未验证实际推理');
    http.expectNone(base + '/verify');
  });
  it('does not treat connection failure as signed out or success', () => {
    component.refresh();
    http.expectOne(base).flush({state: 'credential_check_failed'});
    expect(component.authenticated()).toBeFalse();
    expect(component.message()).toContain('不等于已确认退出登录');
    component.verify();
    http.expectNone(base + '/verify');
  });
  it('requires a configured route and explicit inference intent', () => {
    component.status.set({state: 'authenticated', required: false});
    component.verify();
    http.expectNone(base + '/verify');
    component.status.set({state: 'authenticated', required: true});
    component.verify();
    const request = http.expectOne(base + '/verify');
    expect(request.request.headers.get('X-Artemis-Auth-Intent')).toBe('login');
    request.flush({ok: true, inference_tested: false});
    expect(component.message()).toContain('未通过或未执行');
    component.verify();
    http.expectOne(base + '/verify').flush({ok: true, inference_tested: true});
    expect(component.message()).toContain('推理验证通过');
  });
  it('clears busy state after a failed request', () => {
    component.refresh();
    http.expectOne(base).flush({}, {status: 503, statusText: 'Unavailable'});
    expect(component.busy()).toBeFalse();
    expect(component.message()).toContain('诊断失败');
  });
  it('does not replace routing without explicit confirmation', () => {
    component.configure('sonnet', 'haiku');
    http.expectNone(base + '/configure');
    component.confirmReplace.set(true);
    component.configure('sonnet', 'haiku');
    const request = http.expectOne(base + '/configure');
    expect(request.request.body.confirm_save_claude_settings).toBeTrue();
    expect(request.request.headers.get('X-Artemis-Auth-Intent')).toBe('login');
    request.flush({ok: true});
    http.expectOne(base).flush({state: 'not_logged_in', required: true});
    expect(component.confirmReplace()).toBeFalse();
  });
});
