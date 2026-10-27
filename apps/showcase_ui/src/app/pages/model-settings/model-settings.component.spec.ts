import { TestBed, ComponentFixture } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ModelSettingsComponent, ModelSettings, modelEdits } from './model-settings.component';

const base = '/api/system/model-settings';
const config = (): ModelSettings => ({version: 'a'.repeat(64), filename: 'artemis.jsonc',
  default: {provider: 'codex', model: 'main', timeout: 180, fallback: {provider: 'codex', model: 'small'}},
  nodes: {operator: {include_thoughts: true}, object_detector: {}}, roles: ['operator', 'object_detector'],
  providers: ['codex', 'google', 'openai'], credentials: {google: true, openai: false}});

describe('ModelSettingsComponent', () => {
  let fixture: ComponentFixture<ModelSettingsComponent>;
  let component: ModelSettingsComponent;
  let http: HttpTestingController;
  beforeEach(async () => {
    TestBed.configureTestingModule({imports: [ModelSettingsComponent], providers: [provideHttpClient(), provideHttpClientTesting()]});
    fixture = TestBed.createComponent(ModelSettingsComponent);
    component = fixture.componentInstance;
    http = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    http.expectOne(base).flush(config());
    fixture.detectChanges();
    await fixture.whenStable(); fixture.detectChanges();
  });
  afterEach(() => { fixture.destroy(); http.verify(); });

  it('shows actual routes and credential presence without automatically probing or logging in', () => {
    expect(fixture.nativeElement.textContent).toContain('codex / main');
    expect(fixture.nativeElement.textContent).toContain('google：已配置');
    expect(component.dirty()).toBeFalse();
    expect(fixture.nativeElement.querySelector('input[type=password]').value).toBe('');
    http.expectNone('/api/system/codex/auth');
  });

  it('saves only changed fields with version and intent, preserving other role overrides', () => {
    component.set('object_detector', false, 'model', 'vision-model');
    component.save();
    const request = http.expectOne(base);
    expect(request.request.method).toBe('PUT');
    expect(request.request.headers.get('X-Artemis-Config-Intent')).toBe('save');
    expect(request.request.body).toEqual({version: config().version, edits: [{path: 'nodes.object_detector.model', value: 'vision-model'}]});
    const saved = config(); saved.nodes['object_detector'].model = 'vision-model';
    request.flush(saved);
    expect(component.dirty()).toBeFalse();
    expect(component.message()).toContain('外部 MCP');
  });

  it('keeps unsaved edits on conflict and explains reload', () => {
    component.set('default', false, 'model', 'next'); component.save();
    http.expectOne(base).flush({}, {status: 409, statusText: 'Conflict'});
    expect(component.draft!.default.model).toBe('next');
    expect(component.error()).toContain('未覆盖');
    expect(component.dirty()).toBeTrue();
  });

  it('does not erase local edits when reload fails', () => {
    component.set('default', false, 'model', 'next'); component.load();
    http.expectOne(base).flush({}, {status: 503, statusText: 'Unavailable'});
    expect(component.draft!.default.model).toBe('next');
  });

  it('models clearing a role field as removing the override rather than writing an empty model', () => {
    const before = config(), after = structuredClone(before);
    before.nodes['operator'].model = 'override'; after.nodes['operator'].model = '';
    expect(modelEdits(before, after)).toEqual([{path: 'nodes.operator.model', remove: true}]);
  });

  it('preserves false and numeric zero as real parameter values', () => {
    component.set('operator', false, 'include_thoughts', false);
    component.set('operator', false, 'temperature', 0);
    expect(modelEdits(config(), component.draft!)).toEqual([
      {path: 'nodes.operator.temperature', value: 0}, {path: 'nodes.operator.include_thoughts', value: false}]);
  });

  it('uses guarded credential save and clears the key after errors without echoing server details', () => {
    component.credentialProvider = 'openai'; component.credentialKey = 'PRIVATE-TEST-SECRET';
    component.saveCredential();
    const request = http.expectOne(`${base}/credentials`);
    expect(request.request.headers.get('X-Artemis-Config-Intent')).toBe('save');
    expect(request.request.body.provider).toBe('openai');
    request.flush({detail: 'PRIVATE-TEST-SECRET'}, {status: 400, statusText: 'Bad Request'});
    expect(component.credentialKey).toBe('');
    expect(component.credentialMessage()).not.toContain('PRIVATE-TEST-SECRET');
  });

  it('does not lose pending routing edits after saving a credential', () => {
    component.set('default', false, 'model', 'next');
    component.credentialProvider = 'openai'; component.credentialKey = 'PRIVATE-TEST-SECRET';
    component.saveCredential();
    http.expectOne(`${base}/credentials`).flush({saved: true});
    expect(component.credentialKey).toBe('');
    expect(component.original()!.credentials['openai']).toBeTrue();
    expect(component.draft!.default.model).toBe('next');
    expect(component.dirty()).toBeTrue();
  });

});
