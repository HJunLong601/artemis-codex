import { provideHttpClient, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { SystemReadinessReport } from '../core/models/system.model';
import { SystemService } from './system.service';

describe('SystemService readiness polling', () => {
  let service: SystemService;
  let http: HttpTestingController;

  const report = (timestamp: number): SystemReadinessReport => ({
    overall_ready: true,
    blocker_count: 3,
    passed_blocker_count: 3,
    probes: [],
    active_device: null,
    os_type: 'windows',
    timestamp
  });

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        SystemService,
        provideHttpClient(withXhr()),
        provideHttpClientTesting()
      ]
    });
    service = TestBed.inject(SystemService);
    http = TestBed.inject(HttpTestingController);
    service.stopAutoPolling();
  });

  afterEach(() => http.verify());

  const setCodexDefault = () => service.modelConfigEnv.set({
    config_path: '', config_filename: '', config_content: '',
    default_model: { provider: 'codex' }, presets: {},
    env_path: '', env_filename: '', env_vars: []
  });

  const setCredentials = (metadata: Record<string, unknown>, status: 'pass' | 'fail' = 'pass') => {
    service.readinessReport.set({
      ...report(1),
      probes: [{
        id: 'gemini_api_key', category: 'auth', title: 'Credentials', status,
        is_blocker: true, summary: '', description: '', metadata, actions: []
      }]
    });
  };

  it('does not claim a Codex login from configuration alone or skipped checks', () => {
    setCodexDefault();
    service.skipCredentialsCheck();
    expect(service.codexCredentialState()).toBe('unverified');
  });

  it('updates the Codex banner after failed login, successful login, and logout', () => {
    setCodexDefault();
    for (const valid of [false, true, false]) {
      setCredentials({
        required_providers: ['codex'],
        provider_checks: [{ provider: 'codex', valid }]
      }, valid ? 'pass' : 'fail');
      expect(service.codexCredentialState()).toBe(valid ? 'available' : 'unavailable');
    }
  });

  it('shows verified Codex login on a utility node even when another provider fails', () => {
    setCredentials({
      active_provider: 'google', required_providers: ['google', 'codex'],
      provider_checks: [
        { provider: 'google', valid: false },
        { provider: 'codex', valid: true, roles: ['utils.outputter'] }
      ]
    }, 'fail');
    expect(service.codexCredentialState()).toBe('available');
    expect(service.isCredentialsReady()).toBeFalse();
  });

  it('hides the banner when current routing no longer uses Codex', () => {
    setCodexDefault();
    setCredentials({ required_providers: ['openai'], provider_checks: [{ provider: 'openai', valid: true }] });
    expect(service.codexCredentialState()).toBeNull();
  });

  it('does not confuse an unrelated passing key with a verified Codex login', () => {
    setCodexDefault();
    setCredentials({ providers: [{ provider: 'google' }] });
    expect(service.codexCredentialState()).toBe('unverified');
  });

  it('hides the Codex banner before a provider is configured', () => {
    expect(service.codexCredentialState()).toBeNull();
  });

  it('shares one HTTP request across overlapping readiness callers', () => {
    let firstTimestamp = 0;
    let secondTimestamp = 0;

    service.fetchReadiness().subscribe(value => firstTimestamp = value.timestamp);
    service.fetchReadiness(true).subscribe(value => secondTimestamp = value.timestamp);

    const request = http.expectOne('/api/system/readiness');
    request.flush(report(10));

    expect(firstTimestamp).toBe(10);
    expect(secondTimestamp).toBe(10);
    expect(service.readinessReport()?.timestamp).toBe(10);
    expect(service.isLoading()).toBeFalse();
  });

  it('does not let an older snapshot replace a newer report', () => {
    service.fetchReadiness().subscribe();
    http.expectOne('/api/system/readiness').flush(report(20));

    service.fetchReadiness().subscribe();
    http.expectOne('/api/system/readiness').flush(report(10));

    expect(service.readinessReport()?.timestamp).toBe(20);
  });

  it('requests a forced backend refresh for a manual re-check', () => {
    service.fetchReadiness(false, true).subscribe();

    const request = http.expectOne(req =>
      req.url === '/api/system/readiness' && req.params.get('force') === 'true'
    );
    expect(request.request.params.get('force')).toBe('true');
    request.flush(report(30));
  });

  it('loads the active ADB server endpoint', () => {
    service.fetchAdbServerStatus().subscribe();

    const request = http.expectOne('/api/system/adb/server');
    request.flush({
      endpoint: {
        host: '127.0.0.1',
        port: 5038,
        socket: 'tcp:127.0.0.1:5038',
        mode: 'remote',
        is_local_default: false
      }
    });

    expect(service.isRemoteAdbServer()).toBeTrue();
    expect(service.adbServerStatus()?.endpoint.port).toBe(5038);
  });

  it('activates an ADB server endpoint and applies its readiness report', () => {
    service.connectAdbServer('127.0.0.1', 5038, true).subscribe();

    const request = http.expectOne('/api/system/adb/server/connect');
    expect(request.request.body).toEqual({
      host: '127.0.0.1',
      port: 5038,
      persist: true
    });
    request.flush({
      connection_result: {
        success: true,
        message: 'Connected.',
        endpoint: {
          host: '127.0.0.1',
          port: 5038,
          socket: 'tcp:127.0.0.1:5038',
          mode: 'remote',
          is_local_default: false
        },
        devices: []
      },
      report: report(40)
    });

    expect(service.isRemoteAdbServer()).toBeTrue();
    expect(service.readinessReport()?.timestamp).toBe(40);
  });

  it('probes an ADB server without changing the active endpoint', () => {
    service.probeAdbServer('127.0.0.1', 5038).subscribe();

    const request = http.expectOne('/api/system/adb/server/probe');
    expect(request.request.body).toEqual({
      host: '127.0.0.1',
      port: 5038,
      persist: false
    });
    request.flush({
      connection_result: {
        success: true,
        message: 'Endpoint is reachable.',
        endpoint: {
          host: '127.0.0.1',
          port: 5038,
          socket: 'tcp:127.0.0.1:5038',
          identity: 'tcp:127.0.0.1:5038',
          mode: 'remote',
          is_local_default: false
        },
        devices: []
      }
    });

    expect(service.adbServerStatus()).toBeNull();
  });

  it('restores the standard local ADB server explicitly', () => {
    service.useLocalAdbServer(true).subscribe();

    const request = http.expectOne(req =>
      req.url === '/api/system/adb/server/local' && req.params.get('persist') === 'true'
    );
    request.flush({
      connection_result: {
        success: true,
        message: 'Using local ADB.',
        endpoint: {
          host: '127.0.0.1',
          port: 5037,
          socket: 'tcp:127.0.0.1:5037',
          mode: 'local',
          is_local_default: true
        },
        devices: []
      },
      report: report(50)
    });

    expect(service.isRemoteAdbServer()).toBeFalse();
    expect(service.adbServerStatus()?.endpoint.port).toBe(5037);
  });
});
