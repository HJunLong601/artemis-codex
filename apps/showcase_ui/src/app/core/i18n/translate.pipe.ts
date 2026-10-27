import { inject, Pipe, PipeTransform } from '@angular/core';
import { I18nService } from './i18n.service';

/** Impure so existing labels react immediately, including in OnPush views. */
@Pipe({ name: 't', standalone: true, pure: false })
export class TranslatePipe implements PipeTransform {
  private readonly i18n = inject(I18nService);
  transform(value: unknown, parameters: readonly unknown[] = []): string {
    return this.i18n.t(value, parameters);
  }
}
