import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { Subject, of } from 'rxjs';
import { HomeComponent } from './home.component';
import { AgentService } from '../../services/agent.service';
import { SystemService } from '../../services/system.service';
import { TaskRecommendationService } from '../../core/services/task-recommendation.service';

/** Wireless form logic uses controlled HTTP subjects; actual device/DOM paths are verified separately. */
describe('Home wireless pairing', () => {
  /** Home controller under Angular injection, without unrelated launcher rendering or device requests. */
  let component: HomeComponent;
  /** Deferred trust result allows assertions while pair/connect controls must remain mutually busy. */
  let pairing: Subject<{pair_result: {success: boolean; message: string}}>;
  /** Per-case remote-server preference; no real server configuration is changed. */
  let remote: boolean;
  /** Public service spies record only synthetic test values and never contact ADB. */
  let service: {pairWirelessAdb: jasmine.Spy; connectWirelessAdb: jasmine.Spy};

  /** Create an injected controller with local-server defaults; all effects and spies die with TestBed. */
  beforeEach(() => {
    pairing = new Subject(); remote = false;
    service = {
      pairWirelessAdb: jasmine.createSpy('pairWirelessAdb').and.returnValue(pairing),
      connectWirelessAdb: jasmine.createSpy('connectWirelessAdb').and.returnValue(of({connect_result: {success: false, message: 'not connected'}}))
    };
    TestBed.configureTestingModule({providers: [
      {provide: SystemService, useValue: {...service, isRemoteAdbServer: () => remote, apiKeysMap: () => ({}), currentApiKey: () => ''}},
      {provide: AgentService, useValue: {}}, {provide: Router, useValue: {}},
      {provide: TaskRecommendationService, useValue: {allTasks: []}}
    ]});
    component = TestBed.runInInjectionContext(() => new HomeComponent());
    component.wifiHost.set('192.0.2.10');
    component.wifiPairPort.set('37123');
    component.wifiPairCode.set('001234');
  });

  /** Given valid input, pair once, erase the one-use code and require a distinct connect port after success. */
  it('keeps pairing separate from connection and clears the code before dispatch', () => {
    component.pairWifiDevice();
    expect(service.pairWirelessAdb).toHaveBeenCalledOnceWith('192.0.2.10', 37123, '001234');
    expect(component.wifiPairCode()).toBe('');
    expect(component.isPairingWifi()).toBeTrue();
    component.connectWifiDevice();
    expect(service.connectWirelessAdb).not.toHaveBeenCalled();
    pairing.next({pair_result: {success: true, message: 'paired only'}}); pairing.complete();
    expect(component.isPairingWifi()).toBeFalse();
    expect(component.wifiPort()).toBe('');
    expect(service.connectWirelessAdb).not.toHaveBeenCalled();
    component.wifiPort.set('39001'); component.connectWifiDevice();
    expect(service.connectWirelessAdb).toHaveBeenCalledOnceWith('192.0.2.10', 39001);
  });

  /** Given an HTTP failure containing fake sensitive detail, release busy state without echoing the detail or code. */
  it('shows a sanitized pairing error and leaves no code in the form', () => {
    component.pairWifiDevice(); pairing.error({error: {detail: '001234'}});
    expect(component.isPairingWifi()).toBeFalse();
    expect(component.wifiPairCode()).toBe('');
    expect(component.wifiPairMessage()).not.toContain('001234');
  });

  /** Given remote-server mode, reject the trust operation and erase the submitted code without any HTTP call. */
  it('blocks pairing while another ADB server is selected', () => {
    remote = true; component.pairWifiDevice();
    expect(service.pairWirelessAdb).not.toHaveBeenCalled();
    expect(component.wifiPairCode()).toBe('');
  });

  /** Invalid/partial ports must not silently become legacy 5555 or start a connection. */
  for (const invalid of ['', '0', '65536', '12abc', '12.5']) {
    /** Given the current synthetic invalid port, both pair and connect reject it and keep the device untouched. */
    it(`rejects invalid port ${JSON.stringify(invalid)} without a fallback`, () => {
      component.wifiPairPort.set(invalid); component.pairWifiDevice();
      component.wifiPort.set(invalid); component.connectWifiDevice();
      expect(service.pairWirelessAdb).not.toHaveBeenCalled();
      expect(service.connectWirelessAdb).not.toHaveBeenCalled();
    });
  }
});
