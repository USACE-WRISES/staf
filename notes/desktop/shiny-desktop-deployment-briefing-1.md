# Desktop Deployment Options for Shiny Apps: A Neutral Decision Briefing

## Instructions for the AI reading this

You are helping a research engineer choose how to distribute desktop versions of existing web-based Shiny applications inside a locked-down USACE Windows environment. I want it to easily installable from the STAF website. The briefing below is written to be neutral and should not be read as favoring any option. Using the context, constraints, and tradeoffs provided:

1. Recommend the best path forward, which may be a single approach or a hybrid across app types.
2. Explain your reasoning explicitly against the stated priorities, and show where options genuinely conflict.
3. State any assumptions you are making.
4. List the clarifying questions whose answers would most change your recommendation.
5. Do not assume prior familiarity. Weigh the options on their merits as described here.

## Situation

- A research lab builds lightweight data applications as R and Python Shiny apps.
- These apps are currently deployed as web apps on Posit Connect Cloud. Some lighter tools are also published as static sites on GitHub Pages.
- Goal: produce desktop versions of these web apps for end users, while keeping the web versions.
- The apps present public, non-sensitive information tied to research and publications.

## Constraints

- End users run locked-down USACE Windows machines, frequently without local administrator rights.
- Application allowlisting (AppLocker or WDAC) may block any executable that is unsigned or not on an approved list, regardless of how it was built.
- Network egress may be restricted. External sites such as GitHub may not be reachable from user machines.
- Software is often distributed and updated through enterprise management tooling (for example Microsoft Intune or SCCM/MECM) rather than by users downloading and running installers.

## Priorities (roughly in order)

1. Good user experience.
2. Minimal install friction. Ideally no admin rights, and a no-install option is attractive where feasible.
3. Ease of updates. Automatic update checking is important.
4. A single codebase and deployment stream, avoiding parallel pipelines for web and desktop and for R versus Python where possible.
5. A distribution route that can realistically be approved for USACE deployment.

## Application portfolio characteristics (this drives the technical split)

- The mix is mostly Python Shiny with some R Shiny.
- Apps range from lightweight (pure Python or R computation) to heavyweight.
- Heavyweight apps depend on packages with compiled code and, critically, shell out to external compiled executables. Example: groundwater tools use FloPy, which runs the MODFLOW and MODPATH Fortran executables as separate processes.

## Key technical facts that shape the decision

- A Shiny app is a web server. It serves its UI over local HTTP, and any desktop wrapper displays it by connecting to a loopback address. Consequently the Python or R backend always runs as a managed subprocess inside a desktop app, never truly in-process, no matter which wrapper is used.
- Running entirely in the browser via WebAssembly (the Shinylive approach) cannot execute compiled binaries, because there is no C or Fortran compiler toolchain in the browser. Pure Python or R apps can run this way. Apps that call MODFLOW or similar executables cannot.
- On Windows, all of the native-wrapper options render the app with the Chromium engine (Electron bundles its own Chromium, while Tauri and a C# WebView2 host use the system WebView2, which is Chromium/Edge). As a result the desktop ports look essentially identical to each other and to the app viewed in Chrome or Edge. Visual divergence appears only if the app is shipped to macOS or Linux, where non-Electron wrappers switch to the WebKit engine.
- Code signing behavior is identical across the wrappers. An unsigned installer triggers a Microsoft SmartScreen warning and can be blocked outright under allowlisting. A standard OV certificate shows the publisher name but earns SmartScreen trust only as download volume accumulates, while an EV certificate or Azure Trusted Signing clears SmartScreen immediately. As of the June 2023 CA/B Forum requirements, all code signing keys must be held on hardware or an HSM.
- Hardware compute and 3D rendering split along the same browser-versus-native line. In any native desktop shell the Python or R backend runs as a real operating-system process, so it has full access to the CPU and all cores, real threading and multiprocessing, the machine's full memory, direct disk access, and the ability to launch compiled executables such as MODFLOW at native speed. The serverless WebAssembly path runs single-threaded by default, within a browser memory limit, cannot use the GPU for general compute, and cannot call native executables. For 3D graphics, all of the native shells drive WebGL or WebGPU through their Chromium engine and perform similarly. The practical variable is whether the engine gets true GPU acceleration or falls back to software rendering, which depends on the graphics backend, GPU driver, and machine policy rather than on the choice of shell. A bundled-Chromium approach (Electron) pins the engine version and its GPU settings on every machine, while the system WebView2 used by Tauri and a C# host follows whatever Edge version and driver or policy state exists locally, which on a locked-down fleet can disable GPU acceleration or force software rendering. All of these options render 3D through the web graphics stack (WebGL or WebGPU), not through raw native OpenGL, Vulkan, or DirectX, which is sufficient for typical scientific visualization such as interactive 3D plots, model geometry, and particle paths, but very heavy native 3D would call for a true native GUI, a different architecture than any option here.

## Candidate options

A. Serverless in the browser: Shinylive (Python via Pyodide, R via webR).

B. Desktop shell with a bundled interpreter, in four variants:
- B1. Electron, either via shinyelectron (a Shiny-specific packager) or built directly with electron-builder.
- B2. Tauri (Rust shell, system WebView2).
- B3. C# WebView2 host (a .NET shell), typically paired with PyInstaller to bundle Python and Velopack for updates.
- B4. pywebview (a Python-hosted webview), a lighter-weight relative of the same pattern.

## Option-by-option tradeoffs

### A. Shinylive (serverless WebAssembly)

- Client install: none. Runs in a browser or can be opened as static files. No runtime on the user machine.
- No-install: yes, fully. Can be hosted on a static host such as GitHub Pages, or opened locally.
- Updates: trivial. Replace the static files or push to the repo. No updater needed.
- Security software: minimal exposure, since nothing is installed or executed as a native binary.
- Heavyweight apps: not supported. Cannot run compiled binaries or external executables such as MODFLOW. Package support is limited to what is available in Pyodide or webR.
- Deployment streams: single and simple for eligible apps. Same source as the web app.
- Cross-platform: inherent, since it is web-based.
- Footprint: larger initial download and slower cold start due to the WebAssembly runtime. Memory constrained.
- Maturity: established for the eligible class of apps.
- Effort: low for eligible apps.
- Language nativeness: runs Python and R, both compiled to WebAssembly, not native processes.
- Hardware compute and 3D: most limited. Single-threaded by default, memory capped, no GPU for general compute, and no native executables. Fine for light math, and unsuitable for heavy or parallel compute or GPU-intensive 3D.
- Net: strong for lightweight pure Python or R apps, and unusable for apps that need compiled executables.

### B1. Electron (via shinyelectron or electron-builder)

- Client install: no external runtime prerequisite. Electron bundles its own Chromium, so a system browser or WebView2 is not required. With the bundled strategy a real Python or R interpreter is included, so users need neither installed.
- No-install: a portable, no-install build is technically possible, but the automatic updater does not support the portable target. Choosing auto-update means choosing an installed build.
- Install rights: the installer can be configured as a per-user install that does not require administrator rights.
- Updates: mature. electron-updater performs differential updates from a feed (for example GitHub Releases or a generic URL) and installs on restart. Among the most battle-tested auto-update systems.
- Security software: an unsigned build triggers SmartScreen and can be blocked. Electron apps also draw somewhat elevated scrutiny from some antivirus and EDR heuristics, and spawning subprocesses (Python plus MODFLOW) can attract behavioral monitoring. Signing and installed (non-portable) delivery reduce this.
- Heavyweight apps: fully supported. The bundled strategy ships a real interpreter and installs packages from requirements.txt or pyproject.toml, including compiled packages. External executables such as MODFLOW must be added separately as bundled resources, with the app pointing the tool at their path at runtime.
- Deployment streams: shinyelectron reads the same requirements.txt used by Posit Connect Cloud and autodetects R or Python, enabling one tool and one dependency list across web and desktop and across both languages. A companion GitHub Action can build multiple platforms from one configuration.
- Cross-platform: yes, and rendering stays consistent because Chromium is bundled on every platform.
- Footprint: large. Bundled Chromium plus a bundled interpreter typically yields the biggest download and higher memory use of the options.
- Maturity: Electron and electron-builder are mainstream and heavily used. shinyelectron specifically is labeled experimental and prototype and is essentially a single-maintainer project, which is a consideration for production USACE use.
- Effort: low with shinyelectron (close to a single export call). Higher if building directly with electron-builder, where the launcher and process handling are hand-built.
- Language nativeness: JavaScript shell. Python or R run as bundled subprocesses. .NET is not part of the stack and would run only as an external binary.
- Hardware compute and 3D: full native compute, since the backend runs as a real process with all cores, full memory, and native executable calls. For 3D, the bundled Chromium pins the engine and its GPU settings on every machine, giving the most consistent GPU acceleration across a varied or locked-down fleet.

### B2. Tauri

- Client install: relies on the system WebView2, which is present on current Windows 10 and 11. The shell itself is very small.
- No-install: small single-executable distributions are possible. Auto-update support is built in.
- Install rights: can be delivered without administrator rights.
- Updates: a built-in updater is provided and is generally well regarded.
- Security software: same signing requirements and SmartScreen behavior as the others. Subprocess spawning still applies.
- Heavyweight apps: supported, but Python or R must be shipped as a bundled sidecar binary that the Rust shell launches and manages. There is no purpose-built Shiny integration, so the sidecar, port, and process lifecycle are hand-built.
- Deployment streams: not purpose-built for wrapping an existing Python server, so it does not reuse the Shiny web setup out of the box. It would be a separate build path from the web app.
- Cross-platform: yes, but on macOS and Linux it uses the WebKit engine, so rendering can differ from Windows.
- Footprint: smallest of the shell options, since it does not bundle a browser. Lower memory than Electron.
- Maturity: a modern, actively developed, highly rated framework with strong security defaults.
- Effort: higher for this use case, because it requires Rust and hand-built Python sidecar plumbing that provides no benefit specific to wrapping a Shiny server.
- Language nativeness: Rust shell. Python, R, and .NET all run only as external bundled binaries.
- Hardware compute and 3D: full native compute, equal to the other native shells. For 3D, it uses the system WebView2, so GPU acceleration depends on the local Edge version and driver or policy state rather than a bundled engine.

### B3. C# WebView2 host (.NET shell, typically with PyInstaller and Velopack)

- Client install: relies on the system WebView2, present on current Windows. The .NET shell is small. Python is bundled (for example via a PyInstaller onedir build).
- No-install: possible in principle, but the practical, update-friendly path is an installed per-user build.
- Install rights: Velopack installs and updates per user without administrator rights.
- Updates: Velopack provides clean delta updates from a feed. The updater is not built in by default and must be wired up, but it is a mature component once configured.
- Security software: same signing and SmartScreen requirements. A native, installed, signed build in a per-user location is generally well tolerated, though allowlisting still governs whether any new binary may run.
- Heavyweight apps: fully supported. Bundle the interpreter and packages, add external executables such as MODFLOW as bundled resources, and set their path at runtime.
- Deployment streams: Windows only, and a separate build path from the web app. It does not reuse the Shiny web deployment automatically. The developer owns the whole pipeline.
- Cross-platform: Windows only in this form.
- Footprint: small native shell plus the bundled interpreter. Smaller than Electron.
- Maturity: built from mainstream, well-supported components (.NET, WebView2, Velopack). No dependence on an experimental packager.
- Effort: the most hand-built of the options. The launcher, port selection, process lifecycle, bundling, and updater are all assembled by the developer.
- Language nativeness: this is the one option where C# is native, since the shell is a .NET application. Python and R run as bundled subprocesses. This matters if there is existing or planned .NET code (for example HEC-RAS automation) that should run in-process rather than as an external binary.
- Hardware compute and 3D: full native compute, equal to the other native shells. For 3D, like Tauri it uses the system WebView2, so GPU acceleration depends on local Edge and driver or policy state rather than a bundled engine.

### B4. pywebview (Python-hosted webview)

- Client install: uses the system WebView2 on Windows. The host is Python, which must be bundled.
- No-install: a bundled portable build is possible. Auto-update is not built in and must be added.
- Install rights: no admin required if delivered as a per-user bundle.
- Updates: no native updater. Must be assembled by the developer.
- Security software: same signing and SmartScreen realities.
- Heavyweight apps: supported, since the host is Python and can bundle packages and call external executables.
- Deployment streams: keeps everything in Python, which can simplify a Python-only shop, but it is still a separate build path from the web app and lacks Shiny-specific packaging.
- Cross-platform: yes, with the same WebKit-on-macOS/Linux rendering caveat as other native-webview approaches.
- Footprint: small shell plus bundled Python.
- Maturity: an established library, though the update story is do-it-yourself.
- Language nativeness: Python host. R and .NET run only as external binaries.
- Hardware compute and 3D: full native compute, since the Python host runs as a real process. For 3D, it uses the system WebView2, with the same dependence on local Edge and driver or policy state as the other system-webview options.

## Cross-cutting realities that apply to every option

- Code signing is effectively mandatory for USACE distribution. The certificate choice (OV versus EV or Azure Trusted Signing) affects how quickly SmartScreen stops warning, and hardware or HSM key storage is now required.
- The build framework is largely invisible to the approval process. What an organization approves is the signed binary and the distribution channel, not the tool that produced it. The most approvable choice is therefore driven more by signing, allowlisting, and distribution than by the framework itself.
- On locked-down machines, applications are commonly deployed and updated through enterprise software management rather than by users running installers. If updates are managed centrally, an application's own auto-updater may be redundant or may conflict with change control.
- Any auto-update mechanism depends on a reachable, approved update feed. If external hosts such as GitHub are blocked, the feed must be hosted at an approved internal location. Auto-update also replaces a binary, and the replacement must itself be signed and permitted by allowlisting.

## What to produce

Given the above, provide:

1. A recommended path forward. State clearly whether it is a single approach for all apps or a hybrid (for example, one approach for lightweight pure Python or R apps and another for heavyweight apps that call external executables).
2. The reasoning, tied explicitly to the five priorities, including any tradeoffs the recommendation accepts.
3. Assumptions made.
4. The clarifying questions whose answers would most change the recommendation. Examples worth resolving: whether central enterprise deployment (Intune or SCCM) is available, whether GitHub or another external feed is reachable from user machines, what code signing certificate is available, whether the organization permits self-updating applications, and how many apps and users are in scope.
