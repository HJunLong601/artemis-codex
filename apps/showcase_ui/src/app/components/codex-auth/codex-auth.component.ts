import { Component, EventEmitter, OnDestroy, OnInit, Output, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Subscription, timeout } from 'rxjs';

export function trustedCodexLoginUrl(value: unknown): string | null {
  if (typeof value !== 'string' || /[\s\\]/.test(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && ['auth.openai.com', 'chatgpt.com'].includes(url.hostname)
      && !url.username && !url.password && !url.port ? url.href : null;
  } catch { return null; }
}

export interface CodexAuthStatus {
  state: string;
  binary?: string;
  codex_home?: string;
  home_source?: string;
  runtime_user?: string;
  credential_store?: string;
  auth_mode?: string;
  auth_url?: string | null;
  expires_in?: number;
  diagnostics_warning?: string;
}

@Component({
  selector: 'app-codex-auth',
  standalone: true,
  template: `
    <section aria-label="Codex authentication" class="auth-panel">
      <h3>Codex 登录与认证诊断</h3>
      <p role="status">{{ message() }}</p>
      <p>使用后台进程可访问的登录凭据，不复用桌面当前对话。登录成功不代表推理权限已通过验证。</p>
      @if (status(); as info) {
        <dl>
          <dt>后台用户</dt><dd>{{ info.runtime_user || '未知' }}</dd>
          <dt>Codex 程序</dt><dd>{{ info.binary || '未找到' }}</dd>
          <dt>CODEX_HOME</dt><dd>{{ info.codex_home }}（{{ info.home_source }}）</dd>
          <dt>凭据存储</dt><dd>{{ info.credential_store || '未知' }}</dd>
          <dt>认证模式</dt><dd>{{ info.auth_mode || '未确认' }}</dd>
        </dl>
      }
      <div class="actions">
        <button type="button" (click)="refresh()" [disabled]="busy()">刷新认证状态</button>
        <button type="button" (click)="login()" [disabled]="busy() || status()?.state === 'login_pending' || authenticated()">登录 Codex</button>
        @if (status()?.state === 'login_pending') {
          <button type="button" (click)="cancel()" [disabled]="busy()">取消登录</button>
        }
        <button type="button" (click)="verify()" [disabled]="busy() || !authenticated()">验证实际推理</button>
      </div>
      @if (authUrl()) {
        <p><a [href]="authUrl()" target="_blank" rel="noopener noreferrer">前往 OpenAI 完成登录</a>（10 分钟内有效；请自行授权，不要复制凭据）</p>
      }
      @if (status()?.credential_store === 'ephemeral') {
        <p role="alert">当前凭据仅保存在进程内存中，无法供独立任务复用。请在 Codex 中选择允许持久化的凭据存储，再登录。</p>
      }
      @if (status()?.diagnostics_warning) {
        <p>凭据存储信息暂不可用；若连接已确认，不会因此阻断任务。</p>
      }
    </section>
  `,
  styles: [`
    .auth-panel { padding: 16px; margin-bottom: 16px; border: 1px solid var(--border-color, #ccc); border-radius: 12px; }
    dl { display: grid; grid-template-columns: minmax(90px, auto) minmax(0, 1fr); gap: 6px 12px; }
    dd { margin: 0; overflow-wrap: anywhere; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; }
    button { padding: 8px 12px; cursor: pointer; }
    button:disabled { cursor: default; opacity: .5; }
    p { overflow-wrap: anywhere; }
  `]
})
export class CodexAuthComponent implements OnInit, OnDestroy {
  private http = inject(HttpClient);
  @Output() authChanged = new EventEmitter<void>();
  status = signal<CodexAuthStatus | null>(null);
  busy = signal(false);
  message = signal('尚未检查后台认证状态');
  authUrl = signal<string | null>(null);
  private timer?: ReturnType<typeof setTimeout>;
  private request?: Subscription;
  private expiresAt = 0;
  private readonly base = '/api/system/codex/auth';
  private readonly options = { headers: { 'X-Artemis-Auth-Intent': 'login' } };
  authenticated() { return ['authenticated', 'not_required'].includes(this.status()?.state || ''); }
  ngOnInit() { this.refresh(); }
  ngOnDestroy() { clearTimeout(this.timer); this.request?.unsubscribe(); }
  refresh() {
    if (this.busy()) return;
    clearTimeout(this.timer);
    this.busy.set(true);
    this.request = this.http.get<CodexAuthStatus>(this.base).pipe(timeout(55000)).subscribe({
      next: info => {
        this.status.set(info);
        const messages: Record<string, string> = {
          authenticated: '后台独立进程已确认登录；尚未验证实际推理。',
          not_required: '当前模型供应商不要求 OpenAI 认证；尚未验证实际推理。',
          not_logged_in: '后台进程未登录。请在此登录，或检查运行用户、CODEX_HOME 和凭据库权限。',
          login_pending: '等待你在 OpenAI 页面完成授权；不要关闭登录服务。',
          login_failed: '授权失败或连接中断。可重新发起登录；不会继续等待旧授权。',
          login_expired: '授权已超时，旧链接已清理，请重新登录。',
          login_cancelled: '登录已取消，可重新发起。',
          login_not_persisted: '登录已完成，但新进程无法读取凭据。请检查凭据存储与运行环境。',
          binary_missing: '未找到 Codex 程序，请先安装或配置程序路径。',
          credential_check_failed: '无法检查凭据：App Server 启动、协议或凭据库访问失败；这不等于已确认退出登录。'
        };
        this.message.set(messages[info.state] || '认证状态未确认');
        this.busy.set(false);
        if (info.state === 'login_pending') {
          // Restore only an officially validated URL, held server-side in RAM.
          if (info.auth_url !== undefined) this.authUrl.set(trustedCodexLoginUrl(info.auth_url));
          this.expiresAt = Date.now() + Math.max(0, info.expires_in ?? 600) * 1000;
          this.pollPending();
        } else { this.authUrl.set(null); this.authChanged.emit(); }
      },
      error: () => {
        this.failed('认证诊断暂时失败。待授权时会在有效期内自动重试；也可手动刷新。请通过本机 localhost 后台访问。');
        this.pollPending();
      }
    });
  }
  login() {
    if (this.busy()) return;
    this.busy.set(true);
    this.request = this.http.post<CodexAuthStatus & { auth_url?: string }>(this.base + '/start', {}, this.options).pipe(timeout(60000)).subscribe({
      next: info => {
        this.busy.set(false);
        this.authUrl.set(trustedCodexLoginUrl(info.auth_url));
        this.refresh();
      },
      error: () => this.failed('登录无法启动；请刷新状态，检查是否已有待完成登录或凭据库不可用。')
    });
  }
  cancel() {
    if (this.busy()) return;
    clearTimeout(this.timer);
    this.busy.set(true);
    this.request = this.http.post(this.base + '/cancel', {}, this.options).pipe(timeout(20000)).subscribe({
      next: () => { this.authUrl.set(null); this.busy.set(false); this.refresh(); },
      error: () => this.failed('取消登录失败，请刷新认证状态。')
    });
  }
  verify() {
    if (this.busy() || !this.authenticated()) return;
    this.busy.set(true);
    this.message.set('正在发送最小 READY 请求验证实际模型权限，不发送设备数据…');
    this.request = this.http.post<{ ok: boolean; inference_tested: boolean }>(this.base + '/verify', {}, this.options).pipe(timeout(120000)).subscribe({
      next: result => {
        this.busy.set(false);
        this.message.set(result.ok && result.inference_tested ? '实际推理验证通过。可继续使用 Run Task 验证设备操作。' : '实际推理未通过或未执行，请检查模型配置、账号权限及诊断结果。');
      },
      error: () => this.failed('实际推理验证失败，请刷新认证状态并检查账号或模型权限。')
    });
  }
  private failed(message: string) { this.busy.set(false); this.message.set(message); }
  private pollPending() {
    if (this.status()?.state !== 'login_pending') return;
    if (this.expiresAt <= Date.now()) {
      this.status.set({...this.status()!, state: 'login_expired'});
      this.authUrl.set(null);
      this.message.set('授权已超时，请刷新状态后重新登录。');
      return;
    }
    this.timer = setTimeout(() => this.refresh(), Math.min(3000, this.expiresAt - Date.now()));
  }
}
