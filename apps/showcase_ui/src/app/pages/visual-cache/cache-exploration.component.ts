import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { afterNextRender, ChangeDetectionStrategy, Component, DestroyRef, ElementRef, Injector, computed, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';

interface Device { canonical_id: string; serial: string; name: string; platform: string; state: string; is_busy: boolean }
interface Capabilities { enabled: boolean; routes: Record<string, {provider: string; model: string}>; limits: Record<string, number> }
export interface ExplorationTask {
  id: string; status: string; reason: string; phase: string; version: number; updated_at: number; created_at: number;
  config: { device_serial: string; package: string; duration_minutes: number; max_steps: number | null; max_tokens: number | null };
  elapsed_ms: number; steps: number; tokens: number; unsettled: number; reserved_tokens: number;
  blocked_actions: number; page_counts: Record<string, number>; token_sources: Record<string, number>;
}
interface PageResult { page_key: string; activity: string; name: string; status: string; reason: string; attempts: number }

@Component({
  selector: 'app-cache-exploration', standalone: true, imports: [CommonModule, FormsModule],
  templateUrl: './cache-exploration.component.html', styleUrl: './cache-exploration.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class CacheExplorationComponent {
  private http = inject(HttpClient);
  private destroy = inject(DestroyRef);
  private injector = inject(Injector);
  private stopReturn = viewChild<ElementRef<HTMLButtonElement>>('stopReturn');
  private stopTrigger: HTMLButtonElement | null = null;
  readonly base = '/api/system/visual-cache/exploration';
  tasks = signal<ExplorationTask[]>([]);
  selected = signal('');
  task = computed(() => this.tasks().find(t => t.id === this.selected()) ?? null);
  pages = signal<PageResult[]>([]);
  devices = signal<Device[]>([]);
  apps = signal<{package: string; name: string}[]>([]);
  capabilities = signal<Capabilities | null>(null);
  open = signal(false);
  pending = signal(false);
  reading = signal(false);
  loadingApps = signal(false);
  message = signal('');
  stale = signal(false);
  confirmStop = signal(false);
  pageOffset = signal(0);
  serial = '';
  package = '';
  duration: number | null = null;
  steps: number | null = null;
  tokens: number | null = null;
  unlimitedSteps = true;
  unlimitedTokens = true;
  confirmed = false;
  private requestKey = '';
  private timer: ReturnType<typeof setTimeout> | undefined;
  private appRequest = 0;
  readonly states: Record<string, string> = { starting: '启动校验中', running: '运行中', pausing: '暂停处理中',
    paused: '已暂停', resuming: '恢复校验中', stopping: '停止处理中', stopped: '已停止',
    ended: '已结束', interrupted: '已中断', interrupting: '中断处理中' };
  readonly phases: Record<string, string> = {checking: '校验设备与模型配置', learning: '准备页面学习', exploration: '准备探索推理',
    learning_running: '模型正在学习当前页面', exploration_running: '模型正在选择安全导航', idle: '无在途操作'};
  readonly reasons: Record<string, string> = {
    user_pause: '用户暂停', user_resume: '正在重新校验设备、页面和累计预算', user_stop: '用户停止，终态不可继续', duration_budget: '有效时长预算用尽',
    step_budget: '操作步数预算用尽', token_budget: 'Token 预算用尽', token_budget_insufficient: '剩余 Token 不足以发起下一请求',
    usage_unknown: '用量不可确认，核验前不能继续', safe_branches_exhausted: '暂无可继续的安全分支，不代表已遍历整个 App',
    device_busy: '设备忙碌，请稍后继续', target_app_not_foreground: '请在设备上打开目标 App，再继续',
    sensitive_or_unobservable_window: '出现敏感或不可观察窗口，请在设备上自行处理',
    model_configuration_changed: '模型配置已变化，请停止并重新配置任务', cache_disabled: '缓存已关闭，请先启用缓存',
    page_unstable: '画面不稳定，尚未继续操作', unsafe_or_unknown_action: '动作语义无法安全确认',
    page_changed_during_learning: '学习时页面发生变化，旧结果未写入', page_changed_before_action: '执行前页面变化，未点击旧位置',
    target_changed_before_action: '执行前目标变化', unexpected_back_destination: '返回后的页面与预期不符',
    backend_shutdown: '后台已退出，未自动重启任务', backend_exited: '后台连接已退出', worker_missing: '执行进程已中断',
    worker_exited: '执行进程已退出', worker_launch_failed: '执行进程启动失败', invalid_or_unstored_inventory: '学习结果无效或写入失败',
    learning_busy_or_cooldown: '学习占用或冷却中，等待再次访问', existing_inventory: '已有完整缓存',
    page_capacity: '本次页面记录达到容量上限', local_storage_capacity: '本地任务容量已满，请保留报告后清理数据',
    execution_segment_cancelled: '执行段已取消，未写入旧结果，可在继续后有限补学', accounting_unknown: '累计计量无法确认，不能继续消费',
    model_authentication: '模型认证失败，请到主页 Setup Guide 检查运行环境登录态', model_rate_limit: '模型额度或速率受限',
    model_timeout: '模型请求超时', model_connection: '模型连接失败', model_provider_unavailable: '模型服务暂不可用',
    model_bad_request: '模型配置或请求不受支持', model_unknown: '模型调用失败，请查看诊断', control_timeout: '控制收敛超时，执行进程已清理'
  };
  readonly pageStates: Record<string, string> = { pending: '待学习', learning: '学习中', learned: '本次学习成功',
    cached: '已有缓存跳过', empty: '无可信常用控件', retry: '失败待重试', failed: '学习失败', unstable: '不稳定跳过' };

  constructor() {
    this.refresh();
    this.destroy.onDestroy(() => { if (this.timer) clearTimeout(this.timer); });
  }

  refresh() {
    if (this.reading()) return;
    if (this.timer) clearTimeout(this.timer);
    this.reading.set(true);
    this.http.get<{items: ExplorationTask[]}>(`${this.base}/tasks`).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: data => {
        if (this.stale()) this.message.set('');
        this.tasks.set(data.items); this.stale.set(false); this.reading.set(false);
        if (!data.items.some(t => t.id === this.selected())) this.selected.set(data.items[0]?.id ?? '');
        if (this.task()) this.readPages();
        this.schedule();
      },
      error: () => { this.reading.set(false); this.stale.set(true); this.message.set('任务状态读取失败，保留最后结果。恢复连接不会自动继续。'); this.schedule(); }
    });
  }

  private schedule() { this.timer = setTimeout(() => this.refresh(), this.tasks().some(t => !['stopped', 'ended', 'paused', 'interrupted'].includes(t.status)) ? 2000 : 10000); }
  changed() { this.confirmed = false; this.requestKey = ''; }
  configure() {
    this.open.set(true); this.changed(); this.message.set('');
    this.http.get<Capabilities>(`${this.base}/capabilities`).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: data => this.capabilities.set(data), error: () => { this.capabilities.set(null); this.message.set('无法读取模型配置，请检查设置。'); }
    });
    this.http.get<{devices: Device[]}>('/api/devices').pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: data => this.devices.set(data.devices.filter(d => d.platform === 'android' && d.state === 'ready')),
      error: () => this.message.set('无法读取设备，请检查连接后重试。')
    });
  }
  chooseDevice() {
    this.changed(); this.package = ''; this.apps.set([]);
    const request = ++this.appRequest;
    if (!this.serial) return;
    this.loadingApps.set(true);
    this.http.get<{items: {package: string; name: string}[]}>(`${this.base}/apps`, {params: {device_serial: this.serial}}).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: data => { if (request === this.appRequest) { this.apps.set(data.items); this.loadingApps.set(false); } },
      error: () => { if (request === this.appRequest) { this.loadingApps.set(false); this.message.set('应用清单读取失败；设备忙碌时请先暂停任务。'); } }
    });
  }
  valid(): boolean {
    return !!this.serial && this.apps().some(a => a.package === this.package) && this.confirmed && !!this.capabilities()?.enabled &&
      this.finite(this.duration, 1440) && (this.unlimitedSteps || this.finite(this.steps, 20000)) &&
      (this.unlimitedTokens || this.finite(this.tokens, 10000000));
  }
  private finite(n: number | null, max: number) { return Number.isInteger(n) && n! >= 1 && n! <= max; }
  start() {
    if (!this.valid() || this.pending()) return;
    this.pending.set(true); this.message.set('');
    this.requestKey ||= crypto.randomUUID();
    this.http.post<ExplorationTask>(`${this.base}/tasks`, {
      request_key: this.requestKey, device_serial: this.serial, package: this.package,
      duration_minutes: this.duration, max_steps: this.unlimitedSteps ? null : this.steps,
      max_tokens: this.unlimitedTokens ? null : this.tokens,
      confirm_model_call: true, route_snapshot: this.capabilities()!.routes
    }, {headers: {'X-Artemis-Cache-Intent': 'exploration'}}).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: task => { this.pending.set(false); this.confirmed = false; this.open.set(false); this.selected.set(task.id); this.refresh(); },
      error: error => { this.pending.set(false); this.confirmed = false; this.message.set(this.errorMessage(error)); this.refresh(); }
    });
  }
  control(action: 'pause' | 'resume' | 'stop') {
    const task = this.task();
    if (!task || this.pending()) return;
    this.pending.set(true);
    this.http.post<ExplorationTask>(`${this.base}/tasks/${task.id}/${action}`, {}, {headers: {'X-Artemis-Cache-Intent': 'exploration'}}).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: () => { this.pending.set(false); this.confirmStop.set(false); this.refresh(); },
      error: error => { this.pending.set(false); this.confirmStop.set(false); this.message.set(this.errorMessage(error)); this.refresh(); }
    });
  }
  private errorMessage(error: {status?: number; error?: {detail?: string}}) {
    const detail = error.error?.detail;
    if (detail && this.reasons[detail]) return this.reasons[detail];
    if (detail === 'unfinished_task_on_device') return '该设备已有未结束任务，请选择原任务继续或停止。';
    return error.status === 409 ? '任务状态、配置或设备条件已变化，请刷新并核对后重试。' : '请求结果待确认，请刷新状态；同一次开始请求可安全重试，不会重复创建。';
  }
  selectTask() { this.confirmStop.set(false); this.pageOffset.set(0); this.readPages(); }
  openStop(event: Event) {
    this.stopTrigger = event.currentTarget as HTMLButtonElement;
    this.confirmStop.set(true);
    afterNextRender(() => this.stopReturn()?.nativeElement.focus(), {injector: this.injector});
  }
  cancelStop() { this.confirmStop.set(false); this.stopTrigger?.focus(); }
  readPages() {
    const id = this.selected(); if (!id) return;
    const offset = this.pageOffset();
    this.http.get<{items: PageResult[]}>(`${this.base}/tasks/${id}/pages`, {params: {offset, limit: 25}}).pipe(takeUntilDestroyed(this.destroy)).subscribe({
      next: data => { if (id === this.selected() && offset === this.pageOffset()) this.pages.set(data.items); },
      error: () => this.message.set('页面明细读取失败；任务控制和已有统计仍然保留。')
    });
  }
  page(delta: number) { this.pageOffset.update(n => Math.max(0, n + delta)); this.readPages(); }
  resumable(task: ExplorationTask) { return ['paused', 'interrupted'].includes(task.status) && !task.unsettled && !['model_configuration_changed', 'accounting_unknown'].includes(task.reason); }
  terminal(task: ExplorationTask) { return ['stopped', 'ended'].includes(task.status); }
  label(page: PageResult) { return page.name || `${page.activity?.split('.').pop() || this.task()?.config.package || '页面'} · ${page.page_key.slice(0, 8)}`; }
  reason(value: string) { return this.reasons[value] || (value.startsWith('worker_error_') ? '执行失败，请查看本地诊断并检查模型/设备' : value); }
}
