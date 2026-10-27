import { provideHttpClient, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { CacheExplorationComponent, ExplorationTask } from './cache-exploration.component';

describe('Safe cache exploration', () => {
  let fixture: ComponentFixture<CacheExplorationComponent>;
  let component: CacheExplorationComponent;
  let http: HttpTestingController;
  const base = '/api/system/visual-cache/exploration';
  const task = (status = 'running'): ExplorationTask => ({
    id: 'task-one', status, reason: '', phase: 'learning', version: 1, updated_at: 1, created_at: 1,
    config: { device_serial: 'android:test-device', package: 'org.example.app', duration_minutes: 5, max_steps: 10, max_tokens: 50000 },
    elapsed_ms: 1000, steps: 2, tokens: 300, unsettled: 0, reserved_tokens: 0,
    blocked_actions: 0, page_counts: {}, token_sources: { learning: 300 }
  });
  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [CacheExplorationComponent], providers: [provideHttpClient(withXhr()), provideHttpClientTesting()] });
    fixture = TestBed.createComponent(CacheExplorationComponent);
    component = fixture.componentInstance; http = TestBed.inject(HttpTestingController);
    http.expectOne(`${base}/tasks`).flush({ items: [] });
  });
  afterEach(() => http.verify());

  it('does not start until target, finite budgets and screenshot consent are valid', () => {
    component.capabilities.set({enabled: true, routes: {}, limits: {}});
    component.serial = 'android:test-device'; component.package = 'org.example.app';
    component.apps.set([{package: 'org.example.app', name: 'Test App'}]);
    component.duration = 5; component.steps = 10; component.tokens = 50000;
    component.unlimitedSteps = false; component.unlimitedTokens = false;
    component.start(); http.expectNone(`${base}/tasks`);
    component.confirmed = true;
    component.steps = 0; component.start(); http.expectNone(`${base}/tasks`);
    component.steps = 10; component.start();
    const request = http.expectOne(`${base}/tasks`);
    expect(request.request.method).toBe('POST');
    expect(request.request.headers.get('X-Artemis-Cache-Intent')).toBe('exploration');
    expect(request.request.body.confirm_model_call).toBeTrue();
    request.flush(task('starting'));
    http.expectOne(`${base}/tasks`).flush({items: [task('starting')]});
    http.expectOne(`${base}/tasks/task-one/pages?offset=0&limit=25`).flush({items: []});
    expect(component.confirmed).toBeFalse();
  });

  it('reload reads tasks without automatically resuming', () => {
    component.refresh(); http.expectOne(`${base}/tasks`).flush({items: [task('paused')]});
    http.expectOne(`${base}/tasks/task-one/pages?offset=0&limit=25`).flush({items: []});
    http.expectNone(`${base}/tasks/task-one/resume`);
    expect(component.resumable(component.task()!)).toBeTrue();
    expect(component.task()!.tokens).toBe(300);
  });

  it('has no continue control for terminal tasks and discloses unknown usage', () => {
    const stopped = task('stopped');
    component.tasks.set([stopped]); component.selected.set(stopped.id);
    fixture.detectChanges();
    expect(component.resumable(stopped)).toBeFalse();
    expect(fixture.nativeElement.textContent).not.toContain('继续（保留累计预算）');
    const paused = {...task('paused'), unsettled: 1};
    component.tasks.set([paused]); fixture.detectChanges();
    expect(component.resumable(paused)).toBeFalse();
    expect(fixture.nativeElement.textContent).toContain('用量暂不可确认');
  });

  it('a stop requires confirmation and never resets the cumulative budget', () => {
    const current = task(); component.tasks.set([current]); component.selected.set(current.id);
    fixture.detectChanges();
    const stop = Array.from(fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>).find(b => b.textContent?.trim() === '停止')!;
    stop.click(); fixture.detectChanges();
    http.expectNone(`${base}/tasks/task-one/stop`);
    expect(fixture.nativeElement.textContent).toContain('停止后本任务不能继续');
    component.control('stop');
    const request = http.expectOne(`${base}/tasks/task-one/stop`);
    expect(request.request.body).toEqual({});
    request.flush({...current, status: 'stopping'});
    http.expectOne(`${base}/tasks`).flush({items: [{...current, status: 'stopping'}]});
    http.expectOne(`${base}/tasks/task-one/pages?offset=0&limit=25`).flush({items: []});
    expect(component.task()!.tokens).toBe(300);
  });

  it('preserves last known state on polling error and does not expose raw errors', () => {
    component.tasks.set([task()]); component.selected.set('task-one');
    component.refresh(); http.expectOne(`${base}/tasks`).flush({detail: 'PRIVATE'}, {status: 503, statusText: 'Unavailable'});
    expect(component.stale()).toBeTrue();
    expect(component.task()!.steps).toBe(2);
    expect(component.message()).not.toContain('PRIVATE');
  });

  it('defaults to time-only control and sends null instead of a disguised numeric limit', () => {
    component.capabilities.set({enabled: true, routes: {}, limits: {}});
    component.serial = 'android:test-device'; component.package = 'org.example.app';
    component.apps.set([{package: 'org.example.app', name: 'Test App'}]);
    component.confirmed = true;
    expect(component.unlimitedSteps && component.unlimitedTokens).toBeTrue();
    expect(component.valid()).toBeFalse(); // Time is always mandatory.
    component.duration = 1;
    expect(component.valid()).toBeTrue();
    component.start();
    const request = http.expectOne(`${base}/tasks`);
    expect(request.request.body.max_steps).toBeNull();
    expect(request.request.body.max_tokens).toBeNull();
    expect(request.request.body.duration_minutes).toBe(1);
    request.flush(task('starting'));
    http.expectOne(`${base}/tasks`).flush({items: []});
  });

  it('shows unlimited counters while retaining known cumulative usage', () => {
    const unlimited = {...task(), config: {...task().config, max_steps: null, max_tokens: null}};
    component.tasks.set([unlimited]); component.selected.set(unlimited.id); fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('不限制');
    expect(fixture.nativeElement.textContent).toContain('300');
    expect(component.task()!.steps).toBe(2);
  });

  for (const width of [240, 320, 390, 780, 1280]) {
    it(`fits long budget values in ${width}px`, () => {
      component.tasks.set([{...task(), tokens: 123456789012345}]); component.selected.set('task-one');
      fixture.detectChanges();
      const host = fixture.nativeElement as HTMLElement;
      host.style.display = 'block'; host.style.width = `${width}px`;
      const cards = Array.from(host.querySelectorAll('.budgets article')) as HTMLElement[];
      for (const card of cards) expect(card.scrollWidth).toBeLessThanOrEqual(card.clientWidth + 1);
      expect(host.scrollWidth).toBeLessThanOrEqual(host.clientWidth + 1);
    });
  }

  for (const width of [240, 390, 780, 1280]) {
    it(`keeps configuration and unlimited controls within a ${width}px container`, () => {
      component.open.set(true);
      component.capabilities.set({enabled: true, routes: {operator: {provider: 'codex', model: 'a-very-long-model-name-without-spaces-for-layout-verification'}}, limits: {}});
      fixture.detectChanges();
      const host = fixture.nativeElement as HTMLElement;
      host.style.display = 'block'; host.style.width = `${width}px`;
      expect(host.scrollWidth).toBeLessThanOrEqual(host.clientWidth + 1);
      for (const field of Array.from(host.querySelectorAll('.fields > *')) as HTMLElement[]) {
        expect(field.scrollWidth).toBeLessThanOrEqual(field.clientWidth + 1);
      }
    });
  }
});
