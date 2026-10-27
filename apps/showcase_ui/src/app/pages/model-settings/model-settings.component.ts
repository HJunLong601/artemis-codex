import { Component, OnInit, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { CodexAuthComponent } from '../../components/codex-auth/codex-auth.component';

interface ModelRoute { [key: string]: any; provider?: string; model?: string; fallback?: ModelRoute; }
export interface ModelSettings {
  version: string; filename: string; default: ModelRoute; nodes: Record<string, ModelRoute>;
  providers: string[]; roles: string[]; credentials: Record<string, boolean>;
}
const fields = ['provider', 'model', 'reasoning_effort', 'temperature', 'timeout', 'thinking_level', 'thinking_budget', 'include_thoughts', 'enable_grounding'];
export interface ModelEdit { path: string; value?: unknown; remove?: boolean; }

export function modelEdits(before: ModelSettings, after: ModelSettings): ModelEdit[] {
  const edits: ModelEdit[] = [];
  const compare = (a: ModelRoute, b: ModelRoute, prefix: string) => {
    for (const field of fields) {
      const value = b[field];
      if (value === a[field]) continue;
      edits.push(value === '' || value === undefined || value === null ?
        {path: `${prefix}.${field}`, remove: true} : {path: `${prefix}.${field}`, value});
    }
  };
  for (const role of ['default', ...before.roles]) {
    const a = role === 'default' ? before.default : before.nodes[role] || {};
    const b = role === 'default' ? after.default : after.nodes[role] || {};
    const prefix = role === 'default' ? 'default' : `nodes.${role}`;
    compare(a, b, prefix);
    compare(a.fallback || {}, b.fallback || {}, `${prefix}.fallback`);
  }
  return edits;
}

@Component({
  selector: 'app-model-settings', standalone: true, imports: [FormsModule, CodexAuthComponent],
  templateUrl: './model-settings.component.html', styleUrl: './model-settings.component.scss'
})
export class ModelSettingsComponent implements OnInit {
  private http = inject(HttpClient);
  private base = '/api/system/model-settings';
  original = signal<ModelSettings | null>(null);
  draft: ModelSettings | null = null;
  busy = signal(false);
  message = signal('');
  error = signal('');
  showLogin = signal(false);
  credentialProvider = 'google';
  credentialKey = '';
  credentialBusy = signal(false);
  credentialMessage = signal('');
  efforts = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'];
  thoughts = ['minimal', 'low', 'medium', 'high'];
  labels: Record<string, string> = {default: '默认模型', planner: '规划 Planner', operator: '操作 / Monkey 探索',
    explorer: '视觉定位 Explorer', checker: '验证 Checker', object_detector: '页面学习 / 目标检测',
    summarizer: '摘要', operator_summarizer: '操作摘要', outputter: '报告输出', video_analyzer: '视频分析',
    history_analyzer: '历史分析', validator_pixel_safety_net: '动作安全检查', planner_validation: '计划校验'};

  ngOnInit() { this.load(); }
  load() {
    if (this.busy()) return;
    this.busy.set(true); this.error.set('');
    this.http.get<ModelSettings>(this.base).subscribe({next: data => {
      this.original.set(data); this.draft = structuredClone(data);
      for (const role of data.roles) this.draft.nodes[role] ||= {};
      this.busy.set(false);
    }, error: () => { this.busy.set(false); this.error.set('无法读取模型配置，请检查本地后台或配置文件格式。当前编辑内容已保留。'); }});
  }
  routes() { return this.draft ? ['default', ...this.draft.roles] : []; }
  route(role: string): ModelRoute { return role === 'default' ? this.draft!.default : this.draft!.nodes[role]; }
  endpoint(role: string, fallback: boolean): ModelRoute {
    const route = this.route(role);
    return fallback ? (route.fallback ||= {}) : route;
  }
  value(role: string, fallback: boolean, field: string) { return this.endpoint(role, fallback)[field] ?? ''; }
  set(role: string, fallback: boolean, field: string, value: any) {
    this.endpoint(role, fallback)[field] = value;
    this.message.set(''); this.error.set('');
  }
  inherited(role: string, fallback: boolean, field: string) {
    if (role === 'default') return '使用运行时默认值';
    const value = fallback ? this.draft?.default.fallback?.[field] : this.draft?.default[field];
    return `继承默认${value === undefined ? '' : `：${value}`}`;
  }
  summary(role: string) {
    const row = this.route(role), defaults = this.draft!.default;
    return `${row.provider || defaults.provider} / ${row.model || defaults.model}`;
  }
  dirty() { return !!this.original() && !!this.draft && modelEdits(this.original()!, this.draft).length > 0; }
  save() {
    if (!this.draft || !this.original() || this.busy()) return;
    this.busy.set(true); this.error.set(''); this.message.set('');
    this.http.put<ModelSettings>(this.base, {version: this.original()!.version, edits: modelEdits(this.original()!, this.draft)},
      {headers: {'X-Artemis-Config-Intent': 'save'}}).subscribe({next: data => {
      this.original.set(data); this.draft = structuredClone(data);
      for (const role of data.roles) this.draft.nodes[role] ||= {};
      this.busy.set(false); this.message.set('已保存，新任务会读取新配置。已运行任务保持原模型；外部 MCP 会话请重启后使用。');
    }, error: response => {
      this.busy.set(false);
      this.error.set(response.status === 409 ? '文件已被其他操作修改，未覆盖。请先记下本次编辑，再点“重新加载”读取最新配置。' :
        '保存失败，编辑内容已保留。请检查模型参数、备用模型完整性和配置文件写入权限。');
    }});
  }
  saveCredential() {
    if (!this.credentialKey.trim() || this.credentialBusy()) return;
    this.credentialBusy.set(true); this.credentialMessage.set('');
    // Existing credential service validates against the selected provider before persisting.
    const provider = this.credentialProvider;
    this.http.post(`${this.base}/credentials`, {provider,
      api_key: this.credentialKey.trim()}, {headers: {'X-Artemis-Config-Intent': 'save'}}).subscribe({next: () => {
      this.credentialKey = ''; this.credentialBusy.set(false);
      this.credentialMessage.set('凭证已验证并保存到本机 .env，新任务可使用。');
      if (this.original()) {
        this.original.update(data => data ? {...data, credentials: {...data.credentials, [provider]: true}} : data);
      }
    }, error: () => { this.credentialBusy.set(false); this.credentialKey = '';
      this.credentialMessage.set('凭证验证或保存失败。请检查所选提供商、网络和写入权限，再重新输入。'); }});
  }
}
