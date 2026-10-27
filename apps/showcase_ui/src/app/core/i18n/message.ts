/** Structured messages retain their source and parameters across language changes. */
export interface UiMessage { readonly key: string; readonly parameters: readonly unknown[]; }
export type UiText = string | UiMessage;
export type Translate = (key: string, parameters?: readonly unknown[]) => string;
export function uiMessage(key: string, parameters: readonly unknown[] = []): UiMessage {
  return { key, parameters };
}
export const sourceText: Translate = (key, parameters = []) => key.replace(/\{(\d+)\}/g,
  (placeholder, index: string) => Number(index) < parameters.length ? String(parameters[Number(index)] ?? '') : placeholder);
