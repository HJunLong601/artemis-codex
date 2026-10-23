import { Component, EventEmitter, OnDestroy, OnInit, Output, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Subscription, timeout } from 'rxjs';
import { CodexAuthComponent } from '../codex-auth/codex-auth.component';
import { ClaudeCodeAuthComponent } from '../claude-code-auth/claude-code-auth.component';

interface ClientSettings { active_provider: string; automatic_client_fallback: false; }

@Component({
  selector: 'app-native-client-selector', standalone: true,
  imports: [CodexAuthComponent, ClaudeCodeAuthComponent],
  template: `
    <section class="client-selector" aria-label="模型客户端选择">
      <h3>模型客户端</h3>
      <p>Codex 与 Claude Code 是两套独立配置。只使用你选择的一套，不在两者之间自动回退。
        切换保留另一套设置和登录状态，不影响正在执行的任务。</p>
      <div role="radiogroup" aria-label="选择模型客户端" class="switch">
        <button type="button" role="radio" [attr.aria-checked]="active() === 'codex'"
          [disabled]="busy() || !loaded()" (click)="select('codex')">Codex</button>
        <button type="button" role="radio" [attr.aria-checked]="active() === 'claude_code'"
          [disabled]="busy() || !loaded()" (click)="select('claude_code')">Claude Code</button>
      </div>
      <p role="status">{{ message() }}</p>
      @if (!loaded()) { <button type="button" (click)="refresh()" [disabled]="busy()">重新读取选择</button> }
      @if (loaded() && active() === 'codex') {
        <app-codex-auth (authChanged)="selectionChanged.emit()" />
      }
      @if (loaded() && active() === 'claude_code') {
        <app-claude-code-auth (authChanged)="selectionChanged.emit()" />
      }
    </section>
  `,
  styles: [`
    .client-selector { margin-bottom: 16px; }
    .switch { display: flex; flex-wrap: wrap; gap: 8px; }
    button { padding: 8px 16px; border: 1px solid var(--border-color, #ccc); border-radius: 8px; cursor: pointer; }
    button[aria-checked=true] { outline: 2px solid var(--accent-color, #60a5fa); font-weight: bold; }
    button:disabled { opacity: .5; cursor: default; }
    p { overflow-wrap: anywhere; }
  `]
})
export class NativeClientSelectorComponent implements OnInit, OnDestroy {
  private http = inject(HttpClient);
  @Output() selectionChanged = new EventEmitter<string | void>();
  active = signal<string | null>(null);
  loaded = signal(false);
  busy = signal(false);
  message = signal('正在读取当前选择…');
  private request?: Subscription;
  private readonly base = '/api/system/model-clients';
  ngOnInit() { this.refresh(); }
  ngOnDestroy() { this.request?.unsubscribe(); }
  refresh() {
    if (this.busy()) return;
    this.busy.set(true);
    this.request = this.http.get<ClientSettings>(this.base).pipe(timeout(15000)).subscribe({
      next: info => {
        this.active.set(info.active_provider); this.loaded.set(true); this.busy.set(false);
        this.selectionChanged.emit(info.active_provider);
        this.message.set(info.active_provider === 'claude_code' ? '当前选用 Claude Code。' :
          info.active_provider === 'codex' ? '当前选用 Codex。' : '当前使用其他 API 配置；可显式切换为本机客户端。');
      },
      error: () => { this.loaded.set(false); this.busy.set(false); this.message.set('读取失败，请检查本机配置后重试；不会自动选择其他客户端。'); }
    });
  }
  select(provider: 'codex' | 'claude_code') {
    if (this.busy() || !this.loaded() || this.active() === provider) return;
    this.busy.set(true);
    this.request = this.http.post<{active_provider: string}>(this.base + '/select', {provider}, {
      headers: {'X-Artemis-Auth-Intent': 'login'}
    }).pipe(timeout(15000)).subscribe({
      next: info => {
        this.active.set(info.active_provider); this.busy.set(false);
        this.message.set(info.active_provider === 'claude_code' ? '已切换为 Claude Code，Codex 配置保留。' : '已切换为 Codex，Claude Code 配置保留。');
        this.selectionChanged.emit(info.active_provider);
      },
      error: () => { this.busy.set(false); this.message.set('切换失败，原选择保持不变；不会自动回退。'); }
    });
  }
}
