import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { afterNextRender, ChangeDetectionStrategy, Component, DestroyRef, ElementRef, Injector, computed, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';

export interface CacheGroup {
  device_key: string;
  device_name: string;
  platform: string;
  category: string;
  application: string;
  activity: string | null;
  page_name?: string | null;
  fingerprints: { page_key: string; page_name?: string | null; positions: number; hits: number; prefetched?: number }[];
  pages: number;
  positions: number;
  hits: number;
}

interface PageSelection { device_key: string; application: string; page_key: string; name: string }
interface CacheElement {
  target_key: string; name: string; aliases: string[]; kind: string; coords: number[]; bounds: number[] | null;
  reuse_scope?: string; region_key?: string | null; region_bounds?: number[];
  hits: number; prefetched: boolean; probation: boolean; last_used: number | null; failures: number;
}
interface ElementPage { total: number; offset: number; limit: number; items: CacheElement[] }
interface FailureCase {
  case_key: string; device_key: string; application: string; page_key: string; source_page_key: string;
  activity: string; target: string; reason: string; reuse_scope: string; region_key: string;
  first_seen: number; last_seen: number; occurrences: number;
}
interface FailurePage { total: number; offset: number; limit: number; items: FailureCase[] }
interface InventoryState {
  device_key: string; application: string; page_key: string; status: string; expected: number;
  retained: number; retry_after: number; updated_at: number; failures: number;
}
export interface CacheStats {
  probation_limit?: number;
  history?: {
    since: number | null; queries: number; hits: number; hit_rate: number | null;
    estimated_saved_ms?: number | null; estimated_hits?: number;
    metrics: Record<string, { count: number; total_ms: number }>;
    misses: Record<string, number>; inventory: InventoryState[]; inventory_counts: Record<string, number>;
    bypasses?: Record<string, number>;
  };
  enabled: boolean;
  capacity_per_device: number;
  batch_prefetch?: boolean;
  max_elements_per_page?: number;
  path: string;
  exists: boolean;
  size_bytes: number;
  updated_at: number | null;
  checked_at: number;
  totals: { devices: number; applications: number; pages: number; positions: number; hits: number };
  groups: CacheGroup[];
}

@Component({
  selector: 'app-visual-cache',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './visual-cache.component.html',
  styleUrl: './visual-cache.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class VisualCacheComponent {
  private http = inject(HttpClient);
  private destroyRef = inject(DestroyRef);
  private injector = inject(Injector);
  private detailPanel = viewChild<ElementRef<HTMLElement>>('detailPanel');
  stats = signal<CacheStats | null>(null);
  loading = signal(false);
  error = signal<string | null>(null);
  device = signal('');
  category = signal('');
  search = signal('');
  selection = signal<PageSelection | null>(null);
  elements = signal<ElementPage | null>(null);
  elementsLoading = signal(false);
  elementsError = signal('');
  confirmingClear = signal(false);
  clearing = signal(false);
  notice = signal('');
  private elementsRequest = 0;
  failureCases = signal<FailurePage | null>(null);
  failureReason = signal('');
  failureApplication = signal('');
  failuresLoading = signal(false);
  failuresError = signal('');
  showFailures = signal(false);
  private failureRequest = 0;
  readonly reuseNames: Record<string, string> = { page_exact: '整页精确匹配', page_local: '整页结构 + 局部校验',
    bottom_navigation: '固定区域 · 底部导航栏', bottom_controls: '固定区域 · 底部固定控件区', top_toolbar: '固定区域 · 顶部工具栏', side_navigation: '固定区域 · 侧边导航' };
  readonly missNames: Record<string, string> = {
    page_missing: '页面尚未学习', target_missing: '页面中无此目标', local_guard_failed: '局部校验不一致',
    ambiguous: '目标不唯一', invalid_location: '无效坐标', ignored_region: '位于忽略区域',
    stale_generation: '识别期间缓存已失效', region_guard_failed: '区域校验不一致', region_nodes_changed: '区域节点或控件状态变化', region_pixels_changed: '区域像素变化',
    region_layout_changed: '固定区域布局变化', region_unavailable: '无法确认固定区域',
    region_occluded: '弹窗、键盘或其他窗口遮挡', region_overlap: '其他节点跨入固定区域',
    region_not_fixed: '区域位于可滚动容器内', region_activity_changed: 'Activity 变化',
    activity_changed: 'Activity 变化', structure_changed: '页面结构变化', structure_unavailable: '缺少可用页面结构',
    action_failed: '缓存坐标对应的动作执行失败', feedback_correction: '收到纠错并清理缓存'
  };
  readonly bypassNames: Record<string, string> = {
    device_unavailable: '未获取到设备', device_unknown: '设备身份未知', historical_frame: '历史截图',
    foreground_timeout: '前台应用查询超时', application_unknown: '应用包名未知', activity_unknown: 'Activity 未知',
    hierarchy_invalid: 'UI 层级格式异常', scope_unavailable: '截图或缓存范围不可用'
  };
  readonly stateNames: Record<string, string> = {
    complete: '已完成', partial: '部分淘汰，待补齐', empty: '未发现常用元素', failed: '识别失败',
    learning: '学习中', invalidated: '已失效', legacy: '旧版记录', new: '尚未学习'
  };

  readonly categories = [
    { key: 'system', label: '系统' },
    { key: 'launcher', label: '桌面' },
    { key: 'app', label: '应用' }
  ];

  devices = computed(() => {
    const devices = new Map<string, { key: string; name: string; platform: string; positions: number; hits: number }>();
    for (const group of this.stats()?.groups ?? []) {
      const device = devices.get(group.device_key) ?? {
        key: group.device_key, name: group.device_name, platform: group.platform, positions: 0, hits: 0
      };
      device.positions += group.positions;
      device.hits += group.hits;
      devices.set(device.key, device);
    }
    return [...devices.values()].sort((a, b) => b.positions - a.positions);
  });

  distribution = computed(() => this.categories.map(category => {
    const groups = (this.stats()?.groups ?? []).filter(group => group.category === category.key);
    return {
      ...category,
      positions: groups.reduce((sum, group) => sum + group.positions, 0),
      hits: groups.reduce((sum, group) => sum + group.hits, 0)
    };
  }));

  filteredGroups = computed(() => {
    const search = this.search().trim().toLowerCase();
    return (this.stats()?.groups ?? []).filter(group =>
      (!this.device() || group.device_key === this.device()) &&
      (!this.category() || group.category === this.category()) &&
      (!search || `${group.application} ${group.device_name} ${group.activity ?? ''} ${group.page_name ?? ''}`.toLowerCase().includes(search) ||
        group.fingerprints.some(fingerprint => `${fingerprint.page_key} ${fingerprint.page_name ?? ''}`.toLowerCase().includes(search)))
    );
  });

  constructor() { this.refresh(); }

  refresh(): void {
    if (this.loading()) return;
    this.loading.set(true);
    this.error.set(null);
    this.http.get<CacheStats>('/api/system/visual-cache').pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: stats => {
        this.stats.set(stats);
        if (this.device() && !stats.groups.some(group => group.device_key === this.device())) this.device.set('');
        this.loading.set(false);
        if (this.selection()) this.loadElements(this.elements()?.offset ?? 0);
        if (this.showFailures()) this.loadFailures(0);
      },
      error: () => {
        this.error.set('无法读取缓存统计，请确认后台服务可用后重试。');
        this.loading.set(false);
      }
    });
  }

  count(metric: string): number { return this.stats()?.history?.metrics[metric]?.count ?? 0; }
  savedTime(value: number | null | undefined): string {
    if (value == null || !Number.isFinite(value)) return '暂无估算';
    if (value <= 0) return '约 0 秒';
    if (value < 1000) return '约不到 1 秒';
    const seconds = Math.round(value / 1000);
    if (seconds < 60) return `约 ${seconds} 秒`;
    const minutes = Math.floor(seconds / 60);
    const remainder = seconds % 60;
    if (minutes < 60) return `约 ${minutes} 分${remainder ? ` ${remainder} 秒` : ''}`;
    const hours = Math.floor(minutes / 60);
    return `约 ${hours} 小时${minutes % 60 ? ` ${minutes % 60} 分` : ''}`;
  }
  average(metric: string): number | null {
    const value = this.stats()?.history?.metrics[metric];
    return value?.count ? value.total_ms / value.count : null;
  }
  stateName(state: string): string { return this.stateNames[state] ?? state; }
  pageName(row: CacheGroup, page_key?: string): string | null {
    const fingerprint = page_key ? row.fingerprints.find(item => item.page_key === page_key) : undefined;
    const value = fingerprint && 'page_name' in fingerprint ? fingerprint.page_name : row.page_name;
    const name = value?.trim();
    if (!name || /^(unknown|unknow)([\s_-]+page)?$|^(未识别|未知)(页面)?$|^(unnamed|unidentified|不确定|n\/a)$/i.test(name)) return null;
    return name;
  }
  pageLabel(row: CacheGroup, page_key?: string): string {
    const name = this.pageName(row, page_key);
    if (name) return name;
    const activity = row.activity?.split('.').pop();
    const base = activity || row.application;
    const key = page_key ?? (row.fingerprints.length === 1 ? row.fingerprints[0].page_key : null);
    return key ? `${base} · ${key.slice(0, 8)}` : `${base} · ${row.pages} 个页面`;
  }
  inspect(row: CacheGroup, page_key: string): void {
    this.selection.set({ device_key: row.device_key, application: row.application, page_key,
      name: this.pageLabel(row, page_key) });
    this.confirmingClear.set(false);
    this.notice.set('');
    this.loadElements(0);
    afterNextRender(() => this.detailPanel()?.nativeElement.scrollIntoView({ block: 'start', behavior: 'smooth' }), { injector: this.injector });
  }
  inspectInventory(item: InventoryState): void {
    this.selection.set({ ...item, name: this.stateName(item.status) });
    this.confirmingClear.set(false);
    this.notice.set('');
    this.loadElements(0);
    afterNextRender(() => this.detailPanel()?.nativeElement.scrollIntoView({ block: 'start', behavior: 'smooth' }), { injector: this.injector });
  }
  loadElements(offset: number): void {
    const selection = this.selection();
    if (!selection) return;
    const request = ++this.elementsRequest;
    this.elementsLoading.set(true);
    this.elementsError.set('');
    this.elements.set(null);
    this.http.get<ElementPage>('/api/system/visual-cache/elements', { params: {
      device_key: selection.device_key, application: selection.application, page_key: selection.page_key,
      offset, limit: 25
    }}).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => { if (request === this.elementsRequest) { this.elements.set(result); this.elementsLoading.set(false); } },
      error: () => { if (request === this.elementsRequest) { this.elementsError.set('元素明细读取失败，请重试。'); this.elementsLoading.set(false); } }
    });
  }
  closeElements(): void {
    ++this.elementsRequest;
    this.selection.set(null);
    this.elements.set(null);
    this.confirmingClear.set(false);
  }
  clearPage(): void {
    const selection = this.selection();
    if (!selection || !this.confirmingClear() || this.clearing()) return;
    this.clearing.set(true);
    this.http.post<{ deleted: number }>('/api/system/visual-cache/clear-page', {
      device_key: selection.device_key, application: selection.application, page_key: selection.page_key
    }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => {
        this.clearing.set(false);
        this.confirmingClear.set(false);
        this.notice.set(`已清理 ${result.deleted} 个元素，累计统计已保留。`);
        this.refresh();
      },
      error: () => { this.clearing.set(false); this.elementsError.set('清理失败，缓存未确认删除，请重试。'); }
    });
  }

  reuseName(scope?: string): string { return this.reuseNames[scope ?? 'page_exact'] ?? scope ?? '整页精确匹配'; }
  loadFailures(offset = 0): void {
    this.showFailures.set(true);
    const request = ++this.failureRequest;
    this.failuresLoading.set(true);
    this.failuresError.set('');
    this.failureCases.set(null);
    this.http.get<FailurePage>('/api/system/visual-cache/failures', { params: {
      device_key: this.device(), application: this.failureApplication().trim(), reason: this.failureReason(), offset, limit: 25
    }}).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => { if (request === this.failureRequest) { this.failureCases.set(result); this.failuresLoading.set(false); } },
      error: () => { if (request === this.failureRequest) { this.failuresError.set('失败案例读取失败，请重试。'); this.failuresLoading.set(false); } }
    });
  }

  categoryName(key: string): string { return this.categories.find(category => category.key === key)?.label ?? key; }

  percent(value: number, total: number): number { return total > 0 ? Math.min(100, value / total * 100) : 0; }

  size(bytes: number): string {
    return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${(bytes / 1024).toFixed(1)} KB`;
  }
}
