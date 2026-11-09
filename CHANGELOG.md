# Changelog

## 0.1.0 — unpublished beta candidate

- Complete child reaping, active-worker removal and temporary-directory cleanup
  before returning from an AnyIO-cancelled tool call.
- Package only tracked files from clean MCP and synth checkouts, record both
  revisions and include the decoder used by the bundled CLI.
- `doctor` reports the synth API version (`synth_api`) apart from the native
  renderer. When this server does not support the version, or the synth declares
  none, `ok` is false and `synth_api.error` says why; a working renderer is still
  reported as working.
- `config` and `doctor` probe the selected Python with the module search path the
  server uses, so modules in the caller's current directory cannot change the result.
- Link the related repositories from the README.
- Check out text files with LF endings on every platform, so the shebang scripts
  run from any checkout; `tests/line_endings.test.mjs` checks it.
- Confine MCP tool paths to the workspace and refuse destructive output reuse.
- Isolate and bound tool execution; cancel and reap native descendants.
- Bound default replies and use interoperable object-root tool schemas.
- Guard the source checkout against publishing without its assembled synth SDK.
- Provide the JotTune stdio MCP tool surface and local Node launcher.
- Resolve an explicit, bundled or sibling synth SDK without a separate engine fork.
- Stage self-contained npm packages from a committed synth source revision.
- Advertise the package version consistently during MCP discovery.
- Validate the packaged core workflow on Node 22/Python 3.10 and newer runtimes.
