# agentcad (fork)

> **This is a personal fork**, maintained by Matt ([`MattBetancourt`](https://github.com/MattBetancourt)),
> of the canonical project: **[jdilla1277/agentcad](https://github.com/jdilla1277/agentcad)**.
> It exists to fix one specific Linux crash (below). For anything else —
> general use, issues, contributions — go to the upstream repo.

**CAD tool for AI agents.** Give your coding agent the ability to design 3D models.

Your agent writes build123d Python scripts by default. agentcad handles execution, STEP export, PNG rendering, mesh export (STL/GLB/OBJ), geometric metrics, validation, diffing, and browser preview. CadQuery remains available as an explicit compatibility mode. Each command's final response is structured JSON on stdout.

> **Reading the output:** the JSON response is written to **stdout**; human-readable progress and diagnostics go to **stderr**. Parse stdout as JSON and treat stderr as plain text — don't merge the streams with `2>&1` before a JSON parser, or the progress lines will break parsing. If you need both, capture them separately.

## Why this fork exists

On Linux, `agentcad run --render`/`--preview` crashes with `X Error: BadWindow
(invalid Window parameter)`. The root cause is that OCCT's GLX offscreen
context creation fails, and Xlib's default X11 error handler calls C `exit()`
on that error — so it doesn't just fail the render, it hard-kills the whole
`agentcad run` process partway through, silently desyncing agentcad's version
registry (everything after the render call, including saving the run
manifest, never executes). This fork adds a non-fatal X11 error handler
(scoped to just the render call) plus a VTK-based offscreen rendering
fallback for when the OCCT/GLX path fails.

## What's different from upstream

Everything lives on branch [`fix/linux-glx-offscreen-render-fallback`](https://github.com/MattBetancourt/agentcad/tree/fix/linux-glx-offscreen-render-fallback),
three commits, all touching only `src/agentcad/render.py`:

- [`80193b8`](https://github.com/MattBetancourt/agentcad/commit/80193b8) — non-fatal X11 error handler + VTK offscreen fallback
- [`0f6dd20`](https://github.com/MattBetancourt/agentcad/commit/0f6dd20) — scope the error handler to just the render call; log the fallback reason instead of swallowing it
- [`a61bdfa`](https://github.com/MattBetancourt/agentcad/commit/a61bdfa) — guard the fallback's (currently transitive, not declared) `vtk` import

Full diff: `git diff main fix/linux-glx-offscreen-render-fallback -- src/agentcad/render.py`.
See `CLAUDE.md` in this repo for the fuller technical writeup.

## Status

**Works** for the case it was built for: Linux/WSL, `agentcad run --render`/`--preview`.

**Open questions, stated plainly:**
- Not yet submitted upstream as a PR or issue — reported so far only via
  agentcad's own `agentcad feedback` command.
- `commands/run.py` is untouched. An exception that escapes *both* the OCCT
  path and the new VTK fallback still desyncs a run, just for a narrower set
  of triggers than before this fix.
- Untested on macOS and Windows. Genuinely unknown whether either is
  affected — agentcad's CI has no macOS runner, and its Windows job doesn't
  exercise the render code path either.

## Quick start

Install agentcad, then paste this into Claude Code, Cursor, or any coding agent:

```
Create a Python 3.12 virtual environment, then:

pip install agentcad
agentcad skill install
agentcad instructions install
agentcad --help
agentcad init --name phone-stand

Read the --help output — it's your guide to creating, checking, and sharing a model.
Use the default build123d runtime unless the task explicitly requires
CadQuery compatibility.

Then design me a phone stand: a simple angled cradle that holds a phone
at 60 degrees. About 80mm wide, 50mm deep, with a 5mm lip at the bottom
to keep the phone from sliding. Show me a preview when you're done.
```

`pip install agentcad` installs the **upstream** release from PyPI, not this
fork's fix. To use this fork's fix before it's merged upstream, install from
this branch instead:

```bash
pip install "git+https://github.com/MattBetancourt/agentcad.git@fix/linux-glx-offscreen-render-fallback"
```

## What it does

- **`agentcad run script.py --output label`** — execute a build123d script, producing a versioned STEP file + geometric metrics (volume, dimensions, validity, face/edge counts)
- **Automatic review viewer** — successful runs open `viewer.html`; from v2,
  A=previous and B=current are preloaded for A/B, side-by-side, overlay, and
  Parts-tab change review (`--no-view` opts out)
- **`agentcad run ... --preview`** — four-view PNG for visual verification; the browser viewer can export an on-demand turntable GIF
- **`agentcad run ... --render iso,front`** — high-quality PNG views
- **`agentcad run ... --export stl,glb`** — mesh export for 3D printing or web viewers
- **`agentcad measure output.step`** — dimensional report (overall metrics, edge lengths, face areas, circular/cylindrical diameters)
- **`agentcad check-spec output.step spec.json`** — compare measured cylindrical features against an explicit checklist
- **`agentcad inspect output.step`** — topology deep-dive (shells, free edges, validity)
- **`agentcad parts list REF`** — list named/captured parts for a version
- **`agentcad parts show REF ID`** — show one versioned part by stable id
- **`agentcad parts view REF`** — hand off an isolated, focused, or grouped part review viewer
- **`agentcad diff 1 2`** — compare versions, including actual shared/reference-only/candidate-only source-frame volume for valid closed solids
- **`agentcad view old.step new.step`** — open a synchronized A/B comparison with separate centered projection and source-frame 3D volume artifacts
- **`agentcad docs [section]`** — runtime-aware built-in documentation and worked examples

## No boilerplate

Scripts need zero imports. By default, build123d primitives, `show_object`, and agentcad edit helpers are pre-injected:

```python
box = Box(10, 20, 5)
show_object(box)
```

`agentcad init` records build123d as the project runtime. That keeps the
script API, built-in docs, and subsequent runs on one clear default.

## CadQuery compatibility

CadQuery remains supported for existing scripts and projects, but it is not
the default authoring path.

For a CadQuery project:

```bash
agentcad init --name legacy-model --runtime cadquery
agentcad docs quickstart --runtime cadquery
agentcad run script.py --output first
```

For a one-off CadQuery script inside a build123d project:

```bash
agentcad docs preamble --runtime cadquery
agentcad run legacy.py --output legacy --runtime cadquery
```

Keep each script on one CAD API. If a script clearly targets the other engine,
agentcad reports the mismatch and the exact one-off override. Run
`agentcad docs runtimes` for the complete dispatch contract.

## MCP integration

For native tool integration with Claude Code, Cursor, or Windsurf:

```bash
pip install agentcad[mcp]
```

Add to `.mcp.json`:

```json
{"agentcad": {"command": "python", "args": ["-m", "agentcad.mcp"]}}
```

## Requirements

- Python 3.10–3.12 (OpenCascade bindings do not support 3.13+)

## License

Apache License 2.0. See [LICENSE](LICENSE). Same license as upstream; this
fork changes nothing about it.

## Feedback

For this fork's fix specifically, open an issue against
[`MattBetancourt/agentcad`](https://github.com/MattBetancourt/agentcad). For
everything else, use upstream: [`jdilla1277/agentcad`](https://github.com/jdilla1277/agentcad).
If your agent struggles, run `agentcad feedback "what happened"` to capture a friction log.
