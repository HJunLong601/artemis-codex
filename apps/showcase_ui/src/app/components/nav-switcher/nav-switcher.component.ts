/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { inject } from '@angular/core';
import { TranslatePipe } from '../../core/i18n/translate.pipe';
import { I18nService } from '../../core/i18n/i18n.service';

import { Component, ChangeDetectionStrategy } from '@angular/core';

import { RouterLink, RouterLinkActive } from '@angular/router';

@Component({
  selector: 'app-nav-switcher',
  standalone: true,
  imports: [TranslatePipe, RouterLink, RouterLinkActive],
  template: `
    <nav class="floating-nav-switcher" [attr.aria-label]="'Main Navigation' | t">
      <a 
        routerLink="/" 
        routerLinkActive="active" 
        [routerLinkActiveOptions]="{exact: true}"
        class="nav-tab-btn"
        [title]="'Return to Home Launcher to start a new task' | t"
      >
        <span class="material-symbols-outlined tab-icon">add_task</span>
        <span class="tab-label">{{ 'New / Home' | t }}</span>
      </a>
      <a 
        routerLink="/workspace" 
        routerLinkActive="active" 
        class="nav-tab-btn"
        [title]="'Open Workspace' | t"
      >
        <span class="material-symbols-outlined tab-icon">space_dashboard</span>
        <span class="tab-label">{{ 'Workspace' | t }}</span>
      </a>
      <a routerLink="/cache" routerLinkActive="active" class="nav-tab-btn" [title]="'查看页面缓存和复用统计' | t">
        <span class="material-symbols-outlined tab-icon">database</span>
        <span class="tab-label">{{ '页面缓存' | t }}</span>
      </a>
      <a routerLink="/settings/models" routerLinkActive="active" class="nav-tab-btn" [title]="'可视化配置默认模型与角色模型' | t">
        <span class="material-symbols-outlined tab-icon">tune</span>
        <span class="tab-label">{{ '模型配置' | t }}</span>
      </a>
      <label class="language-picker">
        <span class="material-symbols-outlined" aria-hidden="true">language</span>
        <select #language [attr.aria-label]="'Language' | t" [value]="i18n.preference()"
          (change)="i18n.setPreference(language.value)">
          <option value="system">{{ 'System default' | t }}</option>
          <option value="en" lang="en">English</option>
          <option value="zh" lang="zh-CN">中文</option>
        </select>
        <span class="material-symbols-outlined language-chevron" aria-hidden="true">expand_more</span>
      </label>
    </nav>
  `,
  changeDetection: ChangeDetectionStrategy.Eager,
  styleUrls: ['./nav-switcher.component.scss']
})
export class NavSwitcherComponent {
  readonly i18n = inject(I18nService);
}
