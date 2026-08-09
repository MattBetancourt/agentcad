# CLAUDE.md

## What this fork is

A personal fork of [`jdilla1277/agentcad`](https://github.com/jdilla1277/agentcad)
(owner: Matt, GitHub `MattBetancourt`), created to fix a real Linux rendering
crash. Goal is to eventually contribute the fix upstream (to agentcad and/or
its `CadQuery/OCP` dependency) once better understood — this is not intended
as a permanent divergent fork.

Remotes: `origin` = `jdilla1277/agentcad` (canonical upstream), `fork` =
`MattBetancourt/agentcad` (this fork's push target).

## The bug

On Linux, `agentcad run --render`/`--preview` crashes with `X Error:
BadWindow (invalid Window parameter)` from `X_GetWindowAttributes`. Root
cause: OCCT's `OpenGl_GraphicDriver`/`Aspect_NeutralWindow` GLX context
creation fails (`Aspect_GraphicDeviceDefinitionError:
OpenGl_Window::CreateWindow: XGetVisualInfo is unable to choose needed
configuration in existing OpenGL context`). Xlib's default error handler
calls C `exit()` on `BadWindow`, so this doesn't just fail the render — it
hard-kills the whole process, silently desyncing agentcad's version registry
(everything after the render call in `_run_impl`, including
`save_manifest()`, never runs).

Not Wayland-specific — reproduces identically under a real Xvfb X11 server.
Not version-specific either: confirmed by direct reproduction (a minimal
6-call script, no agentcad/build123d involved) that this crash is present in
the *current* `cadquery-ocp` release (`7.9.3.1.1`), not just the older
`7.8.1.1` this fork's upstream is pinned to — so upgrading the pin alone
would not fix it. `cadquery-ocp` is built from `CadQuery/OCP`, not
`tpaviot/pythonocc-core` (a separate, unrelated set of OCCT bindings —
don't cite pythonocc-core issues as precedent for this, that was an earlier
mistake corrected after checking). No matching issue exists in `CadQuery/OCP`
either way, so there's no documented upstream maintainer stance on this bug
at all yet.

## The fix

Branch `fix/linux-glx-offscreen-render-fallback`, 3 commits, all touching
only `src/agentcad/render.py`:

1. `404092e` — non-fatal `XSetErrorHandler` (via ctypes) so `BadWindow`
   raises a catchable Python exception instead of killing the process; a
   VTK-based offscreen fallback (`vtkRenderWindow` +
   `SetOffScreenRendering(1)`) used when the OCCT/GLX path raises, wrapping
   `render_shape`/`render_shape_batch`/`render_shape_custom`.
2. `6a9f4b9` — scoped the error handler to just the render call via a
   context manager (not global at import time, so agentcad-as-a-library
   doesn't permanently swallow a host application's own X11 errors); logs
   the OCCT exception to stderr before falling back instead of swallowing
   it silently.
3. `773c99e` — guards the fallback's `import vtk`. vtk isn't a declared
   agentcad dependency — it's present today only transitively via
   `cadquery-ocp==7.8.1.1`'s own dependency on `vtk==9.3.1` (see the
   `build123d<0.11` cap in `pyproject.toml`). If agentcad ever moves to
   `cadquery-ocp-novtk`, vtk could vanish; the guard raises a clear
   `RuntimeError` instead of a bare `ModuleNotFoundError`.

The VTK fallback also needed explicit lighting (key+fill lights plus an
ambient brightness floor) — an earlier version relied on VTK's automatic
default light, which rendered axis-aligned views with solid-black unlit
faces.

## Verified vs. still open

Verified: reproduces under a real Xvfb X11 server (not Wayland-specific);
still present on the current `cadquery-ocp` release (7.9.3.1.1), not just
the pinned 7.8.1.1, via a minimal OCP-only repro independent of agentcad;
agentcad's own CI (`.github/workflows/smoke.yml`) never exercises
`tests/test_render.py` / `tests_b3d/test_render_cmd.py` on either runner it
has (`ubuntu-22.04`, `windows-latest`) — so this exact code path isn't
covered there either.

Open / not yet done:
- `commands/run.py` itself is untouched — an exception that escapes *both*
  the OCCT path and the VTK fallback still desyncs a run, just for a
  narrower set of triggers than before.
- Not yet opened as a PR or issue upstream; reported so far only via
  agentcad's own `agentcad feedback` command.
- Untested on macOS/Windows — genuinely unknown whether either is affected,
  since CI has no macOS runner and Windows CI doesn't run the render tests.

## Upstream dev conventions (kept from original CLAUDE.md)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[mcp,dev]"
pytest
```

- Python 3.10–3.12 only (CadQuery/OpenCascade doesn't support 3.13+).
- Commands return structured JSON on stdout; human-readable diagnostics go
  to stderr — don't merge the streams before parsing.
- Keep error messages concise and actionable for coding agents.
