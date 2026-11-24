import { DecimalPipe } from '@angular/common';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { afterNextRender, ChangeDetectionStrategy, Component, computed, DestroyRef, ElementRef, inject, Injector, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';

// 与后台绑定接口的 Android 包名格式和长度限制保持一致。
const ANDROID_PACKAGE_PATTERN = /^[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+$/;
const MAX_PACKAGE_LENGTH = 255;

/** 后台持久化的一套构建源码；同一包名可对应多个独立源码组。 */
interface SourceGroupInput {
  /** 可手动输入或由真机清单提供的 Android 应用包名。 */
  package: string;
  /** 用来区分同一 App 各组源码的名称。 */
  name: string;
  /** ARTEMIS 后台所在电脑上的主工程绝对路径。 */
  project_dir: string;
  /** 同组的附加绝对路径，空数组表示只绑定主工程。 */
  additional_dirs: string[];
}

/** 已保存配置，组 ID 用于更新、移除和提交真机对照。 */
interface SourceGroup extends SourceGroupInput {
  /** 后台生成的不透明标识，与包名及组名分离。 */
  id: string;
}

/** 表单保留用户输入的换行，保存时才拆分附加目录。 */
interface GroupDraft extends Omit<SourceGroupInput, 'additional_dirs'> {
  /** 空值表示新建，否则编辑这个已保存的组。 */
  id: string | null;
  /** 每行一个可选附加目录，空白行在保存时忽略。 */
  additional: string;
  /** 从页面顶部发起时允许输入包名，并与首组源码一起保存。 */
  newBinding: boolean;
  /** 应用卡片的自定义名称，空白时采用设备名称或包名。 */
  appName: string;
}

/** 设备清单中的字段；对照只使用 ready 且不忙碌的 Android 设备。 */
interface Device {
  /** 带平台前缀的设备标识，用于接口查询。 */
  canonical_id: string;
  /** 当设备名称为空时展示的序列号。 */
  serial: string;
  /** 设备可读名称，可能为空。 */
  name: string | null;
  /** 后端识别的平台。 */
  platform: string;
  /** 后端连接状态；ready 表示已授权并可访问。 */
  state: string;
  /** 后端是否观察到设备当前被任务占用。 */
  is_busy: boolean;
}

/** 已安装 App 入口提供的包名和展示名称。 */
interface InstalledApp {
  /** Android 应用在设备上的实际包名。 */
  package: string;
  /** 可读名称，现有接口可能直接返回包名。 */
  name: string;
}

/** 浏览列表中的同一个 Android 包，展示已保存的源码组。 */
interface AppSourceGroups {
  /** 用于分组及新建绑定的准确包名。 */
  package: string;
  /** 优先使用已保存的应用名称，其次使用设备名称，均缺失时由模板显示包名。 */
  name?: string;
  /** 同一个包名下已保存的各组源码。 */
  groups: SourceGroup[];
}

/** 用已转换为小写的 query 按包名或展示名称做部分匹配。空搜索匹配所有应用。 */
function matchesApp(packageName: string, name: string | undefined, query: string): boolean {
  return packageName.toLowerCase().includes(query) || !!name?.toLowerCase().includes(query);
}

/** 普通任务中实际进入源码辅助定位的累计数据。 */
interface SourceLocatingStats {
  /** 每批去重后实际检查的目标总数。 */
  attempts: number;
  /** 经实时 UI 树确认唯一、可点击的目标数。 */
  hits: number;
  /** 未确认后交给已有定位流程的目标数。 */
  fallbacks: number;
  /** 实际发生过源码检查的批次数。 */
  batches: number;
  /** 每批检查的平均耗时，单位毫秒；无样本时为 null。 */
  average_ms: number | null;
  /** 只用可比较的视觉历史估算的节省耗时，单位毫秒；无样本时为 null。 */
  estimated_saved_ms: number | null;
  /** 拥有可比视觉耗时、实际纳入估算的命中目标数。 */
  estimated_hits: number;
}

/** 定位实际命中的路径；unresolved 表示最终没有采纳坐标。 */
type LocalizationSource = 'hierarchy' | 'project' | 'visual' | 'unresolved';

/** 同一设备观察下单个对照分支的测量值。 */
interface ComparisonArm {
  /** 这次实际采用的定位路径。 */
  source: LocalizationSource;
  /** 归一化的 [横坐标, 纵坐标]，范围为 0–1000。无结果时为 null。 */
  coords: [number, number] | null;
  /** 定位结果是否吻合独立填写的预期控件。 */
  correct: boolean;
  /** 定位期间实际调用视觉模型的次数。 */
  vision_calls: number;
  /** 分支定位耗时，单位为毫秒。 */
  elapsed_ms: number;
}

/** 一次只读对照；两条分支使用同一帧。 */
interface Comparison {
  /** A 跑现有定位链路。 */
  a: ComparisonArm;
  /** B 在同样条件下增加已选源码组的线索。 */
  b: ComparisonArm;
  /** 两条分支共用的设备采集耗时，单位为毫秒。 */
  observation_ms: number;
  /** 用户是否为本次对照开启视觉兜底。 */
  vision_enabled: boolean;
}

/** 管理本机 App 工程绑定，并展示用户明确选组后发起的只读真机对照。 */
@Component({
  selector: 'app-app-projects',
  standalone: true,
  imports: [DecimalPipe, FormsModule, TranslatePipe],
  templateUrl: './app-projects.component.html',
  styleUrl: './app-projects.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppProjectsComponent {
  /** 控制台语言，用于按用户 locale 显示耗时。 */
  readonly i18n = inject(I18nService);
  /** 本机后台的 HTTP 客户端。 */
  private readonly http = inject(HttpClient);
  /** 页面卸载后取消回调和请求订阅。 */
  private readonly destroyRef = inject(DestroyRef);
  /** 打开组表单后，将上方表单滚动到可见位置。 */
  private readonly editor = viewChild<ElementRef<HTMLFormElement>>('groupForm');
  /** 将打开表单后的滚动注册到当前组件的渲染周期。 */
  private readonly injector = inject(Injector);
  /** 源码组 CRUD 和定位对照共享的本机 API 前缀。 */
  private readonly api = '/api/system/app-projects';
  /** 配置写请求使用的显式动作标识。 */
  private readonly configure = { headers: { 'X-Artemis-App-Project-Intent': 'configure' } };

  /** 后台已经落盘的全部源码组。 */
  readonly groups = signal<SourceGroup[]>([]);
  /** 已保存的应用名称，以包名为键，同一应用的各组源码共用。 */
  readonly appNames = signal<Record<string, string>>({});
  /** 新建或编辑中的表单，null 表示未打开。 */
  readonly draft = signal<GroupDraft | null>(null);
  /** 已要求确认移除的组 ID，空字符串表示未请求移除。 */
  readonly removing = signal('');
  /** 当前连接并已授权的 Android 设备。 */
  readonly devices = signal<Device[]>([]);
  /** 用户明确选择的设备标识，空字符串表示未选。 */
  readonly serial = signal('');
  /** 当前设备上安装的 App 清单。 */
  readonly apps = signal<InstalledApp[]>([]);
  /** 当前所选设备的清单已成功返回；读取失败或没有设备时不能按清单筛选。 */
  readonly appsReady = signal(false);
  /** 浏览已有配置的关键字，和新建时填写的包名互不影响。 */
  readonly browseQuery = signal('');
  /** 是否由用户明确开启仅显示当前设备已安装应用。 */
  readonly installedOnly = signal(false);
  /** 新建表单中搜索设备建议的独立输入。 */
  readonly appSearch = signal('');
  /** 已安装应用以准确包名查找，支持浏览筛选和验证共用。 */
  private readonly installedByPackage = computed(() => new Map(this.apps().map(app => [app.package, app])));
  /** 没有搜索或主动启用的设备筛选时，按包名分组展示全部绑定。 */
  readonly visibleApps = computed(() => {
    const query = this.browseQuery().trim().toLowerCase();
    const installed = this.installedByPackage();
    // 离线时依然可以依靠本机绑定中保存的名称筛选应用。
    const appNames = this.appNames();
    const filterInstalled = this.installedOnly() && this.appsReady();
    const grouped = new Map<string, AppSourceGroups>();
    for (const group of this.groups()) {
      const app = installed.get(group.package);
      // 卡片与搜索使用相同的展示名称。
      const name = appNames[group.package] || app?.name;
      if ((filterInstalled && !app) || !matchesApp(group.package, name, query)) continue;
      const existing = grouped.get(group.package);
      if (existing) {
        existing.groups.push(group);
      } else {
        grouped.set(group.package, { package: group.package, name, groups: [group] });
      }
    }
    return [...grouped.values()];
  });
  /** 当前草稿中可提交的准确包名，空值表示未填或不符合接口格式。 */
  readonly validDraftPackage = computed(() => {
    const name = this.draft()?.package.trim() ?? '';
    return name.length <= MAX_PACKAGE_LENGTH && ANDROID_PACKAGE_PATTERN.test(name) ? name : null;
  });
  /** 新建表单中符合独立搜索条件的已安装应用。 */
  readonly filteredDeviceApps = computed(() => {
    const query = this.appSearch().trim().toLowerCase();
    return this.apps().filter(app => matchesApp(app.package, app.name, query));
  });
  /** 草稿包名在当前建议列表中时显示该选项，否则保留手动选择的占位项。 */
  readonly selectedDeviceSuggestion = computed(() => {
    const packageName = this.validDraftPackage();
    return packageName && this.filteredDeviceApps().some(app => app.package === packageName) ? packageName : '';
  });
  /** 所选设备确认安装了哪些已保存源码组对应的 App。 */
  readonly availableGroups = computed(() =>
    this.groups().filter(group => this.installedByPackage().has(group.package)),
  );

  /** 用户手动选定用于对照的组 ID，空字符串表示未选。 */
  readonly verifyGroup = signal('');
  /** A 和 B 共同收到的原始定位描述。 */
  readonly query = signal('');
  /** 用来判分的当前目标文字或完整 ID，仅用于独立核对。 */
  readonly expectedTarget = signal('');
  /** 这次验证是否获得用户允许发送当前截图。 */
  readonly confirmed = signal(false);
  /** 最近一次对照返回值；入参变动时清空。 */
  readonly comparison = signal<Comparison | null>(null);
  /** 定位来源在页面中的显示文案。 */
  readonly sourceLabels: Record<LocalizationSource, string> = {
    hierarchy: '当前 UI 树',
    project: '源码辅助定位',
    visual: '视觉模型',
    unresolved: '未定位',
  };

  /** 是否正在读取已保存的配置。 */
  readonly readingGroups = signal(false);
  /** 是否正在读取设备或设备上的已安装 App。 */
  readonly readingDevice = signal(false);
  /** 最近成功读取的普通任务源码辅助统计，首次成功前为空。 */
  readonly locatingStats = signal<SourceLocatingStats | null>(null);
  /** 是否正在读取源码辅助统计，与绑定和真机操作状态分开。 */
  readonly readingStats = signal(false);
  /** 当前读取统计的可翻译错误提示。 */
  readonly statsError = signal('');
  /** 是否尚有保存或移除操作在途。 */
  readonly saving = signal(false);
  /** 是否有一组 A/B 请求在途。 */
  readonly verifying = signal(false);
  /** 失败提示。后台的明确错误语义保留给操作者判断。 */
  readonly error = signal('');
  /** 单次成功反馈；进行新操作时清空。 */
  readonly message = signal('');
  /** 当前状态是否满足发起一次新的 A/B 请求。 */
  readonly canVerify = computed(() => !this.readingGroups() && !this.readingDevice() && !this.saving() && !this.verifying()
    && this.availableGroups().some(group => group.id === this.verifyGroup())
    && this.serial() !== '' && this.query().trim() !== '' && this.expectedTarget().trim() !== '');

  /** 打开页面时读取配置和设备。每路请求独立结束，任一路失败可单独重试。 */
  constructor() {
    this.loadGroups();
    this.loadDevices();
    this.loadStats();
  }

  /** 读取实际定位记录及估算范围；失败时保留上次取得的统计。 */
  loadStats(): void {
    if (this.readingStats()) return;
    this.readingStats.set(true);
    this.statsError.set('');
    this.http.get<SourceLocatingStats>(`${this.api}/stats`).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.readingStats.set(false)),
    ).subscribe({
      next: stats => this.locatingStats.set(stats),
      error: (err: HttpErrorResponse) => this.statsError.set(err.status === 404
        ? '当前后台未提供源码辅助定位统计。'
        : '无法读取源码辅助定位统计。'),
    });
  }

  /** 读取后端配置。加载失败保留当前的已读部分。 */
  loadGroups(): void {
    if (this.readingGroups() || this.saving() || this.verifying()) return;
    this.error.set('');
    this.readingGroups.set(true);
    this.http.get<{ groups: SourceGroup[]; app_names?: Record<string, string> }>(this.api).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.readingGroups.set(false)),
    ).subscribe({
      next: response => {
        this.groups.set(response.groups);
        this.appNames.set(response.app_names ?? {});
        if (!response.groups.some(group => group.id === this.verifyGroup())) this.verifyGroup.set('');
        this.resetComparison();
      },
      error: err => this.showError(err, '无法读取已保存的源码组。'),
    });
  }

  /** 刷新在线 Android 设备。旧选择仍可用时重新读取该设备的 App。 */
  loadDevices(): void {
    if (this.readingDevice() || this.saving() || this.verifying()) return;
    this.error.set('');
    this.readingDevice.set(true);
    this.http.get<{ devices: Device[] }>('/api/devices').pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: response => {
        this.devices.set(response.devices.filter(device => device.platform === 'android' && device.state === 'ready'));
        // 保留仍可用的选择；设备或占用状态变化时要求重新选择。
        const selected = this.devices().some(device => device.canonical_id === this.serial() && !device.is_busy)
          ? this.serial()
          : '';
        this.readingDevice.set(false);
        this.selectDevice(selected);
      },
      error: err => {
        this.readingDevice.set(false);
        this.selectDevice('');
        this.showError(err, '无法读取设备，请检查连接后重试。');
      },
    });
  }

  /** 切换设备时清除上一个设备的 App 清单、验证组和截图确认。 */
  selectDevice(serial: string): void {
    if (this.readingDevice() || this.saving() || this.verifying()) return;
    this.serial.set(serial);
    this.apps.set([]);
    this.appsReady.set(false);
    this.verifyGroup.set('');
    this.resetComparison();
    if (!serial) {
      this.installedOnly.set(false);
      return;
    }
    this.error.set('');
    this.readingDevice.set(true);
    this.http.get<{ items: InstalledApp[] }>('/api/system/visual-cache/exploration/apps', {
      params: { device_serial: serial },
    }).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.readingDevice.set(false)),
    ).subscribe({
      next: response => {
        this.apps.set(response.items);
        // 空清单也可能来自设备查询失败，不能用它隐藏全部本机绑定。
        const hasApps = response.items.length > 0;
        this.appsReady.set(hasApps);
        if (!hasApps) this.installedOnly.set(false);
      },
      error: err => {
        this.installedOnly.set(false);
        this.showError(err, '应用清单读取失败；设备忙碌时请先暂停任务。');
      },
    });
  }

  /** 打开指定组的编辑器，或创建新组；从 App 下添加时可预填 packageName。 */
  edit(group?: SourceGroup, packageName = ''): void {
    if (this.saving() || this.verifying()) return;
    this.appSearch.set('');
    this.removing.set('');
    this.error.set('');
    this.message.set('');
    this.draft.set(group ? {
      id: group.id, package: group.package, name: group.name,
      project_dir: group.project_dir, additional: group.additional_dirs.join('\n'),
      newBinding: false, appName: this.appNames()[group.package] ?? '',
    } : {
      // 新组预填当前界面语言的默认名称，用户可直接保存或改名。
      id: null, package: packageName, name: this.i18n.t('主工程'), project_dir: '', additional: '',
      newBinding: !packageName, appName: '',
    });
    // 从较下方的 App 卡片操作时，也能看到实际打开的表单。
    afterNextRender(() => this.editor()?.nativeElement.scrollIntoView({ block: 'nearest' }), { injector: this.injector });
  }

  /** 更新一个表单字段，值使用用户输入原文，保存后由后台校验路径。 */
  updateDraft(field: 'package' | 'name' | 'project_dir' | 'additional' | 'appName', value: string): void {
    this.draft.update(draft => draft && (field !== 'package' || draft.newBinding) ? { ...draft, [field]: value } : draft);
  }

  /** 用户明确选择当前设备建议时，将包名填写到新建草稿，不改变浏览状态。 */
  chooseInstalledApp(packageName: string): void {
    if (this.appsReady() && this.installedByPackage().has(packageName)) {
      this.updateDraft('package', packageName);
    }
  }

  /** 保存当前源码组。请求失败时保留表单，避免丢失路径和组名。 */
  save(): void {
    const draft = this.draft();
    const packageName = this.validDraftPackage();
    if (!draft || !packageName || !draft.name.trim() || !draft.project_dir.trim()
      || this.readingGroups() || this.saving() || this.verifying()) return;
    // 每行都是独立路径。绝对性、规范化、去重和可读性由后台判断。
    const body: SourceGroupInput & { app_name?: string } = {
      package: packageName,
      name: draft.name.trim(),
      project_dir: draft.project_dir.trim(),
      additional_dirs: draft.additional.split(/\r?\n/).map(line => line.trim()).filter(Boolean),
    };
    // 只提交已改动的名称；编辑时清空输入可恢复默认展示。
    const appName = draft.appName.trim();
    if ((draft.id && appName !== (this.appNames()[packageName] ?? '')) || (draft.newBinding && appName)) {
      body.app_name = appName;
    }
    this.error.set('');
    this.message.set('');
    this.saving.set(true);
    // 新组使用 POST 取得后台 ID，编辑通过原 ID 更新。
    const request = draft.id
      ? this.http.put<SourceGroup>(`${this.api}/${encodeURIComponent(draft.id)}`, body, this.configure)
      : this.http.post<SourceGroup>(this.api, body, this.configure);
    request.pipe(takeUntilDestroyed(this.destroyRef), finalize(() => this.saving.set(false))).subscribe({
      next: group => {
        this.groups.update(groups => draft.id ? groups.map(item => item.id === group.id ? group : item) : [...groups, group]);
        if (body.app_name !== undefined) {
          this.appNames.update(names => ({ ...names, [group.package]: appName }));
        }
        this.message.set(draft.newBinding ? '应用绑定已保存。' : '源码组已保存。');
        this.draft.set(null);
        this.resetComparison();
      },
      error: err => this.showError(err, '保存源码组失败，编辑内容已保留。'),
    });
  }

  /** 删除已确认的绑定配置，并清空与该组关联的验证状态。 */
  remove(group: SourceGroup): void {
    if (this.removing() !== group.id || this.readingGroups() || this.saving() || this.verifying()) return;
    this.saving.set(true);
    this.error.set('');
    this.message.set('');
    this.http.delete(`${this.api}/${encodeURIComponent(group.id)}`, this.configure).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.saving.set(false)),
    ).subscribe({
      next: () => {
        this.groups.update(groups => groups.filter(item => item.id !== group.id));
        if (!this.groups().some(item => item.package === group.package)) {
          this.appNames.update(names => {
            // 其余应用的展示名称继续保留在当前列表快照中。
            const remaining = { ...names };
            delete remaining[group.package];
            return remaining;
          });
        }
        if (this.verifyGroup() === group.id) {
          this.verifyGroup.set('');
          this.resetComparison();
        }
        if (this.draft()?.id === group.id) this.draft.set(null);
        this.removing.set('');
        this.message.set('已移除源码组。');
      },
      error: err => this.showError(err, '无法移除源码组。'),
    });
  }

  /** 清除与旧输入关联的结果和截图确认。 */
  resetComparison(): void {
    this.comparison.set(null);
    this.confirmed.set(false);
  }

  /** 以当前已选条件发起同帧对照，只有本次勾选才开启视觉兜底。 */
  verify(): void {
    if (!this.canVerify()) return;
    const allowScreenshot = this.confirmed();
    this.error.set('');
    this.message.set('');
    this.resetComparison();
    this.verifying.set(true);
    this.http.post<Comparison>(`${this.api}/verify`, {
      group_id: this.verifyGroup(),
      device_serial: this.serial(),
      query: this.query().trim(),
      expected_target: this.expectedTarget().trim(),
      confirm_model_call: allowScreenshot,
    }, { headers: { 'X-Artemis-App-Project-Intent': 'verify' } }).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.verifying.set(false)),
    ).subscribe({
      next: result => this.comparison.set(result),
      error: err => this.showError(err, '真机定位对照失败。'),
    });
  }

  /** 展示后台提供的动作失败原因，缺少原因时使用可翻译的提示。 */
  private showError(error: HttpErrorResponse, fallback: string): void {
    // Pydantic 等结构化错误不直接拼到用户界面上。
    const detail: unknown = error.error?.detail;
    this.error.set(typeof detail === 'string' && detail ? detail : fallback);
  }
}
