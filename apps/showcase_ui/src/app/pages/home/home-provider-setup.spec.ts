import { of } from 'rxjs';
import { HomeComponent, modelSetupModeForProvider } from './home.component';

describe('Model setup selection', () => {
  it('does not ask native-client users for an unrelated Gemini key', () => {
    expect(modelSetupModeForProvider('codex')).toBe('custom');
    expect(modelSetupModeForProvider('claude_code')).toBe('custom');
    expect(modelSetupModeForProvider('google')).toBe('gemini');
    expect(modelSetupModeForProvider(undefined)).toBeNull();
  });
  it('does not bypass actual credentials by opening custom configuration', () => {
    const skip = jasmine.createSpy('setSkipCredentialsCheck');
    const subject = {modelSetupMode: {set: jasmine.createSpy('set')}, systemService: {
      setSkipCredentialsCheck: skip, fetchModelConfigEnv: () => of({})
    }};
    HomeComponent.prototype.setModelSetupMode.call(subject as unknown as HomeComponent, 'custom');
    expect(skip).toHaveBeenCalledOnceWith(false);
  });
});
