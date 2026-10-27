# ✨ Artemis Showcase & User Workspace UI

A modern, highly aesthetic presentation and interaction frontend built with **Angular 19** and **SCSS** for showcasing the Artemis mobile autonomous agent.

## 🎨 Visual Design Highlights

* **Aurora Glow Dynamics**: Ambient background lighting with smooth gradients.
* **Glassmorphism Aesthetic**: Translucent frosted-glass panels with subtle borders.
* **Real-time Dual-Pane Workspace**:
  - **Left Pane (`app-agent-stream`)**: Live streaming of Agent thought steps, plan breakdowns, tool actions, and status.
  - **Right Pane (`app-chat-interface`)**: Natural language chat input sidebar with interactive feedback and status pills.

## 🚀 How to Run

### Prerequisites
- Node.js (>= 18)
- Backend API running on `http://localhost:8000` (via `apps/admin_console` or `artemis ui`)

### Development Server
```bash
cd apps/showcase_ui
npm install
npm start
```
This runs `ng serve --proxy-config proxy.conf.json` on **`http://localhost:4200/`**.

### Production Build
```bash
npm run build
```
Build artifacts will be emitted to `dist/`, which can be served statically by `apps.admin_console` or `artemis ui`.

## Console languages

The console supports English and Simplified Chinese. On the first visit it uses
the browser's primary language, which normally follows the operating system:
`zh`, including regional/script variants, selects Chinese; unsupported or
missing locale information defaults to English.

The navigation bar offers English, 中文, and System default. Manual choices are
saved in this browser under `artemis.console.language`, persist across reloads
and synchronize between tabs on the same origin. System default clears the
override. If storage is blocked, the selector still works for the current page.
Switching does not reload the page or discard unsaved edits.

Console copy lives in `src/app/core/i18n/messages.ts`. Add the same source key to
both catalogs and preserve numbered placeholders (`{0}`, `{1}`, …). Use the `t`
pipe for templates and `I18nService.t` in display methods. Deferred feedback with
parameters uses `uiMessage(key, parameters)` so it also changes language while
visible. Dates and numbers use `i18n.locale()`.

User goals, model output, notes, raw logs, device/app names, package names and
model identifiers retain their original content. Pure formatters accept an
optional translator and keep their original English output for other consumers.

Run `npm test -- --watch=false --browsers=ChromeHeadless` for the front-end
regression suite, including detection, persistence, live switching, catalog
completeness and placeholder/data preservation checks.
