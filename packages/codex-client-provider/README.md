# Codex Client Provider

Reusable Python and LangChain adapter for the official `codex app-server`
protocol. It reuses the login managed by the installed Codex client and never
reads or copies Codex authentication files.

The package currently provides:

- a persistent JSONL/stdio App Server client;
- a LangChain `BaseChatModel` implementation;
- text, local image, data URL image, tool-call, and structured-output support;
- bounded image resizing/compression and temporary-file cleanup;
- configurable read-only sandbox, client identity, model, reasoning effort,
  and request timeout.

```python
from codex_client_provider import CodexAppServerChatModel

model = CodexAppServerChatModel(model_name="default")
response = model.invoke("Return a concise status message.")
print(response.content)
```

Prerequisites:

```text
codex login
codex login status
```

`CODEX_CLIENT_BIN` can point to the Codex executable when it is not on `PATH`.
Image limits can be changed with `CODEX_CLIENT_IMAGE_MAX_EDGE`,
`CODEX_CLIENT_IMAGE_MAX_BYTES`, and `CODEX_CLIENT_IMAGE_JPEG_QUALITY`.

This directory is intentionally self-contained so it can later move to its own
repository without importing Artemis.
