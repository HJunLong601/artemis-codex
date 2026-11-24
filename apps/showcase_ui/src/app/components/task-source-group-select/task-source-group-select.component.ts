import { HttpClient } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, DestroyRef, ElementRef, computed, effect, inject, input, signal, untracked, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import { TranslatePipe } from '../../core/i18n/translate.pipe';
import type { TaskSourceGroupSelection } from '../../services/agent.service';
import { SystemService } from '../../services/system.service';

/** 发起任务时显示源码组，不保留绑定目录。 */
interface SourceGroupOption {
  /** 后台保存的源码组标识。 */
  id: string;
  /** 已绑定的 Android 应用包名。 */
  package: string;
  /** 用户给这组目录填写的名称。 */
  name: string;
}

/** 在发起任务前手动选择一组源码，选择只对当前 Android 设备和本次表单有效。 */
@Component({
  selector: 'app-task-source-group-select',
  standalone: true,
  imports: [TranslatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (deviceSerial() && groups().length) {
      <select
        #picker
        [attr.aria-label]="'任务源码组（可选）' | t"
        [title]="'前台 App 只有一个源码组时自动匹配，有多个时请手动选择。' | t"
        [disabled]="disabled()"
        [value]="selection()?.id ?? ''"
        (change)="choose($event)">
        <option value="">{{ '自动匹配源码组' | t }}</option>
        @for (group of groups(); track group.id) {
          <option [value]="group.id">{{ group.name }} · {{ group.package }}</option>
        }
      </select>
    }
  `,
  styles: [`
    :host { display: contents; }
    select {
      box-sizing: border-box;
      min-width: 0;
      max-width: min(15rem, 100%);
      padding: 4px 8px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.75);
      color: #334155;
      font: inherit;
      font-size: 0.75rem;
      text-overflow: ellipsis;
    }
    select:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
  `],
})
export class TaskSourceGroupSelectComponent {
  /** 父表单正在提交时冻结此控件，避免修改已提交的选择。 */
  readonly disabled = input(false);
  /** 仅保存列表渲染需要的字段，旧后台或非本机控制台读取失败时保留原有发起入口。 */
  protected readonly groups = signal<SourceGroupOption[]>([]);
  /** 用户明确选择的源码组及选择时的设备，空值交给后台按前台 App 自动匹配。 */
  private readonly picked = signal<TaskSourceGroupSelection | undefined>(undefined);
  /** 设备切换时直接复位原生控件，覆盖同一渲染帧内尚未同步的选择。 */
  private readonly picker = viewChild<ElementRef<HTMLSelectElement>>('picker');
  /** 组件卸载时取消尚未结束的配置列表读取。 */
  private readonly destroyRef = inject(DestroyRef);
  /** 现有诊断选择的 Android 设备。 */
  private readonly system = inject(SystemService);
  /** 当前已连接的 Android 序列号，无设备时不呈现选择器。 */
  protected readonly deviceSerial = computed(() => {
    const device = this.system.activeDevice();
    return device?.state === 'device' ? device.serial : undefined;
  });
  /** 任务提交读取的快照，设备此刻已切换时立即返回空值。 */
  readonly selection = computed(() => {
    const picked = this.picked();
    return picked?.deviceSerial === this.deviceSerial() ? picked : undefined;
  });

  constructor() {
    const http = inject(HttpClient);
    http.get<{ groups: SourceGroupOption[] }>('/api/system/app-projects').pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: ({ groups }) => this.groups.set(groups.map(({ id, name, package: app }) => ({ id, name, package: app }))),
      error: () => this.groups.set([]),
    });

    // 切换回来也需要重新选组，避免旧设备状态恢复时复用上次选择。
    effect(() => {
      const serial = this.deviceSerial();
      const picked = untracked(this.picked);
      if (picked && picked.deviceSerial !== serial) this.reset();
    });
  }

  /** 只接受当前列表中用户选中的组；选择空项或设备断开时清除。 */
  protected choose(event: Event): void {
    const id = (event.target as HTMLSelectElement).value;
    const deviceSerial = this.deviceSerial();
    this.picked.set(deviceSerial && this.groups().some(group => group.id === id)
      ? { id, deviceSerial }
      : undefined);
  }

  /** 提交成功后清空，新任务需要手动选组。 */
  reset(): void {
    this.picked.set(undefined);
    const select = this.picker()?.nativeElement;
    if (select) select.value = '';
  }
}
