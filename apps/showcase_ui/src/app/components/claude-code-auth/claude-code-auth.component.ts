import { Component, EventEmitter, OnDestroy, OnInit, Output, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Subscription, timeout } from 'rxjs';

export interface ClaudeCodeStatus {
  state: string;
  binary?: string | null;
  version?: string;
  auth_method?: string;
  required?: boolean;
  configured_models?: string[];
  routing_error?: string;
  model_settings?: {model: string; fallback_model: string; reasoning_effort?: string};
}

@Component({
  selector: 'app-claude-code-auth',
  standalone: true,
  template: `
    <section aria-label="Claude Code authentication" class="auth-panel">
      <h3>Claude Code 本机调用</h3>
      <p role="status">{{ message() }}</p>
      <p>使用原版 CLI 和你自己的账号；不复用当前聊天，也不向后台提交登录凭据。</p>
      <dl>
        <dt>CLI 程序</dt><dd>{{ status()?.binary || '未找到' }}</dd>
        <dt>CLI 版本</dt><dd>{{ status()?.version || '未确认' }}</dd>
        <dt>认证方式</dt><dd>{{ status()?.auth_method || '未确认' }}</dd>
        <dt>主路由</dt><dd>{{ status()?.configured_models?.join(', ') || '尚未配置 claude_code' }}</dd>
      </dl>
      <p>在运行 Artemis 的同一用户和环境中执行 <code>claude auth login</code>，然后刷新。
        <a href="https://code.claude.com/docs/en/overview" target="_blank" rel="noopener noreferrer">官方安装与登录说明</a></p>
      <p>下方只编辑 Claude Code 的独立模型配置，不修改 Codex 配置，也不把 Codex 作为备用。</p>
      <div class="configure">
        <label>主模型 <input #primary [value]="primaryModel()" maxlength="200" [disabled]="busy()" /></label>
        <label>同客户端备用模型 <input #fallback [value]="fallbackModel()" maxlength="200" [disabled]="busy()" /></label>
        <label>推理强度 <select [value]="effort()" (change)="effort.set($any($event.target).value)" [disabled]="busy()">
          <option value="low">low</option><option value="medium">medium</option><option value="high">high</option>
          <option value="xhigh">xhigh</option><option value="max">max</option>
        </select></label>
        <label><input type="checkbox" [checked]="confirmReplace()"
          (change)="confirmReplace.set($any($event.target).checked)" />
          我确认保存 Claude Code 模型设置（保留 Codex 和原文件备份）</label>
        <button type="button" (click)="configure(primary.value, fallback.value)"
          [disabled]="busy() || !confirmReplace()">保存 Claude Code 模型配置</button>
      </div>
      @if (status()?.routing_error) { <p role="alert">模型配置无法读取，请先修正配置文件。</p> }
      <div class="actions">
        <button type="button" (click)="refresh()" [disabled]="busy()">刷新认证状态</button>
        <button type="button" (click)="verify()" [disabled]="busy() || !authenticated() || !status()?.required">验证实际推理</button>
      </div>
      <p>验证会向已配置的 Claude Code 模型发送最小 READY 请求，可能消耗额度，不发送设备数据。
        验证通过后，再使用 Run Task 验证手机操作。</p>
    </section>
  `,
  styles: [`
    .auth-panel { padding: 16px; margin-bottom: 16px; border: 1px solid var(--border-color, #ccc); border-radius: 12px; }
    dl { display: grid; grid-template-columns: minmax(80px, auto) minmax(0, 1fr); gap: 6px 12px; }
    dd { margin: 0; overflow-wrap: anywhere; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; }
    .configure { display: grid; gap: 8px; margin-bottom: 12px; }
    label { overflow-wrap: anywhere; }
    input:not([type=checkbox]) { max-width: 100%; box-sizing: border-box; }
    button { padding: 8px 12px; cursor: pointer; }
    button:disabled { cursor: default; opacity: .5; }
    p { overflow-wrap: anywhere; }
  `]
})
export class ClaudeCodeAuthComponent implements OnInit, OnDestroy {
  private http = inject(HttpClient);
  @Output() authChanged = new EventEmitter<void>();
  status = signal<ClaudeCodeStatus | null>(null);
  busy = signal(false);
  confirmReplace = signal(false);
  primaryModel = signal('sonnet');
  fallbackModel = signal('haiku');
  effort = signal('low');
  message = signal('尚未检查 Claude Code');
  private request?: Subscription;
  private readonly base = '/api/system/claude-code/auth';

  authenticated() { return this.status()?.state === 'authenticated'; }
  ngOnInit() { this.refresh(); }
  ngOnDestroy() { this.request?.unsubscribe(); }

  refresh() {
    if (this.busy()) return;
    this.busy.set(true);
    this.request = this.http.get<ClaudeCodeStatus>(this.base).pipe(timeout(45000)).subscribe({
      next: info => {
        this.status.set(info);
        if (info.model_settings) {
          this.primaryModel.set(info.model_settings.model);
          this.fallbackModel.set(info.model_settings.fallback_model);
          this.effort.set(info.model_settings.reasoning_effort || 'low');
        }
        const messages: Record<string, string> = {
          authenticated: '后台独立进程已确认认证；尚未验证实际推理。',
          binary_missing: '未找到官方 Claude Code CLI，请先安装，或设置 ARTEMIS_CLAUDE_CODE_BIN 后重启后台。',
          not_logged_in: 'Claude Code 尚未登录，请通过官方 CLI 登录后刷新。',
          unsupported_client: 'CLI 缺少必要的隔离或结构化输出能力，请更新官方 CLI。',
          credential_check_failed: '认证检查失败，不等于已确认退出登录。请检查后台环境和 CLI。'
        };
        this.message.set(messages[info.state] || 'Claude Code 状态未确认');
        this.busy.set(false);
        this.authChanged.emit();
      },
      error: () => this.failed('认证诊断失败，请使用本机同源后台，检查 CLI 后重试。')
    });
  }

  verify() {
    if (this.busy() || !this.authenticated() || !this.status()?.required) return;
    this.busy.set(true);
    this.message.set('正在验证实际模型推理，不发送手机内容…');
    this.request = this.http.post<{ok: boolean; inference_tested: boolean}>(
      this.base + '/verify', {}, {headers: {'X-Artemis-Auth-Intent': 'login'}}
    ).pipe(timeout(100000)).subscribe({
      next: result => {
        this.busy.set(false);
        this.message.set(result.ok && result.inference_tested
          ? '实际推理验证通过。请继续通过 Run Task 验证手机操作。'
          : '实际推理未通过或未执行，请检查认证、模型配置和账号额度。');
      },
      error: () => this.failed('实际推理验证失败或超时，请刷新状态并检查模型配置与账号权限。')
    });
  }

  configure(model: string, fallback: string) {
    if (this.busy() || !this.confirmReplace()) return;
    this.busy.set(true);
    this.request = this.http.post<{ok: boolean}>(this.base + '/configure', {
      confirm_save_claude_settings: true, model: model.trim(), fallback: fallback.trim(), effort: this.effort()
    }, {headers: {'X-Artemis-Auth-Intent': 'login'}}).pipe(timeout(15000)).subscribe({
      next: () => { this.confirmReplace.set(false); this.busy.set(false); this.refresh(); },
      error: () => this.failed('配置未保存。请检查可写的统一配置文件、模型标识；原配置和备份均保留。')
    });
  }
  private failed(message: string) { this.busy.set(false); this.message.set(message); }
}
