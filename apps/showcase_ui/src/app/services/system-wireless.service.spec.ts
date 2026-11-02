import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { SystemService } from './system.service';

/** Exercise only the wireless service contract; requests are intercepted and never reach a device. */
describe('SystemService wireless pairing', () => {
  /** Real service under test; automatic readiness polling is disabled for deterministic HTTP assertions. */
  let service: SystemService;
  /** Interceptor verifies the one-use secret appears only in the explicit POST body. */
  let http: HttpTestingController;
  /** Initialize scoped providers; no readiness, inference or device request is issued by setup. */
  beforeEach(() => {
    TestBed.configureTestingModule({providers: [provideHttpClient(), provideHttpClientTesting()]});
    service = TestBed.inject(SystemService); http = TestBed.inject(HttpTestingController);
    service.stopAutoPolling();
  });
  /** Fail the test if pairing unexpectedly triggers connection/readiness requests or leaves a request unfinished. */
  afterEach(() => http.verify());
  /** A six-digit leading-zero fixture must be posted with explicit intent and yield trust status only. */
  it('posts the pairing port and code without triggering a readiness refresh', () => {
    service.pairWirelessAdb('192.0.2.10', 37123, '001234').subscribe();
    /** Captured POST request is local to this test and contains no real pairing credentials. */
    const request = http.expectOne('/api/system/adb/pair');
    expect(request.request.body).toEqual({host: '192.0.2.10', port: 37123, pairing_code: '001234'});
    expect(request.request.headers.get('X-Artemis-ADB-Intent')).toBe('pair');
    request.flush({pair_result: {success: true, message: 'paired'}});
    http.expectNone('/api/system/adb/connect'); http.expectNone('/api/system/readiness');
  });
});
