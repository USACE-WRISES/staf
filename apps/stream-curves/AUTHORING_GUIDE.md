# Authoring a method in StreamCurves: author and maintainer guide

How to create, revise, review, package, download, activate and roll back a DEEP or EASI
method. The contracts behind these steps are in `AUTHORING.md`. Commands run from the
repository root with the shared interpreter (`.venv\Scripts\python.exe`, Python 3.12).

Two roles:

- An **author** works in an installed StreamCurves (or any checkout without the publish switch).
  They download a version, revise it as their own project, and send the project file back.
- The **maintainer** works in a checkout with `STAF_LIBRARY_PUBLISH=1` and
  `STAF_LIBRARY_MAINTAINER=<initials>`. Only they publish to the canonical library. When
  approvals carried from earlier versions are recorded under another name of theirs (their login,
  before 2026-09-24), `STAF_LIBRARY_MAINTAINER_ALIASES=<that name>` lets `promote` take them as
  theirs.

StreamCurves records initials, never a name or the Windows login: `STAF_LIBRARY_MAINTAINER`
when it is set, else the open project's Prepared by initials (Project panel, **Project
properties**), else `n/a`. A missing name never blocks a decision or a publish.

Nothing below publishes externally. Uploading feeds or evidence, redeploying an app and moving
EASI's active method are the owner's decisions (see the adoption package in the notes).

## Author: revise a method

1. **Open.** Start page, **Assessment library**, pick a version, **Open a copy**. The copy is
   your project (Documents\StreamCurves Projects by default). An EASI method opens on its five
   stages: Method, Development data, Curves and criteria, Final selection, Review and publish.
2. **Inspect the evidence.** EASI: the **Development data** stage lists each evidence package
   with its role, coverage, size and reproducibility (reviewable, refittable, regenerable) and
   whether your store holds it. **Download** fetches one (a stopped download resumes; nothing is
   marked ready until every file's size and SHA-256 match); **Import local package** installs a
   zip or folder you were sent, and asks before the project names another version of a package.
   A copy damaged on disk reads **Damaged** and is never read until it is fetched again. DEEP: the
   Region & data stage shows the in-app NRSA archive the same way.
3. **Start a revision.** A published version never changes. EASI: **Start v<N>** on the Method
   stage makes a draft that keeps its origin's bytes, so every change can be reverted exactly.
   DEEP: rebuild or edit in the workspace as before.
4. **Change what the method supports.** EASI: band edges and which side owns them, regional
   TN/TP edges, curve knots and wording, on Curves and criteria. Operators, inputs, routes and
   weights are evaluator behavior and change only through reviewed EASI development.
5. **Compare and select.** DEEP: Reference curves, **Select final curves**. Each function lists
   its selected curves and, when opened, every alternative with its status, reason and who
   decided; **Compare** puts up to three side by side; **Use in this function** and **Undo** are
   REF-15 decisions; **Add a state SQT curve** searches the SQT registry and checks each curve
   against this region and the function's own curves; a curve its source leaves open at an end
   is completed first (**Complete the curve**: points past the end to the index limit, keeping its
   direction, with your initials and a reason); **Record why not** keeps your reason for
   leaving a considered curve out. A decision records the curve it was made on: when a rebuild
   moves that curve, the function asks you to look again. EASI:
   **Final selection** lists the alternatives the method was chosen from; **Select** adopts one
   in the draft (functions that read the same curves move with it); selecting the method the
   draft started from restores it exactly. Alternatives 3 and 4, where the 2026-09-15 study's
   rule excluded them, offer **Select against the study** instead: it asks for your initials,
   your reason, and why you select it against the rule (at least 20 characters), and records all
   three in the selection and the history.
6. **Preview the consequences.** EASI: **Preview consequences** scores the project's cases with
   the draft and its origin in separate processes and lists every rating that moves. A function
   whose method changed asks you to **Confirm** the selection with a reason.
7. **Save and send.** **Save** writes the project file; **Method package** exports what EASI
   would load. Send the project file to the maintainer. Nothing you did reached the library.

## Maintainer: publish a method

- **Import the current EASI method** (once, from the checkout, byte for byte):

      .venv\Scripts\python.exe apps\stream-curves\scripts\import_easi_method.py --out "EASI screening method.streamcurves" ^
          --by <initials> --evidence D:\Data\staf-authoring\evidence ^
          --alternatives D:\Data\easi-national\review\alternative-studies\2026-09-15-controlled-alternatives

- **Publish** an author's project: open it with `STAF_LIBRARY_PUBLISH=1`, review, **Publish**
  (EASI: Review and publish; DEEP: Publish). The library refuses an unconfirmed selection and
  keeps the candidate register in the version's provenance, never in the bundle or package.
- **Build and check the feeds** (`library.json` stays DEEP-only; `library-v2.json` is typed):

      .venv\Scripts\python.exe apps\stream-curves\scripts\library_release.py build --out build\library
      .venv\Scripts\python.exe apps\stream-curves\scripts\library_release.py check --dir build\library

- **Evidence packages** (from the frozen baseline, read only):

      cd tools\easi-national
      ..\..\.venv\Scripts\python.exe -m builder.evidence_export --snapshot D:\Data\easi-national\review\2026-09-15-regional\baseline --out D:\Data\staf-authoring\evidence

  Authors download the four public ones from the rolling `easi-evidence` prerelease, the
  StreamCurves default; `STREAMCURVES_EVIDENCE_BASE_URL` (a folder or an http(s) base holding the
  zips and `index.json`) points elsewhere. The two internal-review packages are not hosted. After
  a re-export, upload the new archives to `easi-evidence` first and its `index.json` last.
- **Refit from packages alone** (developer paths refused for the whole run):

      .venv\Scripts\python.exe apps\stream-curves\scripts\refit_easi_curves.py --evidence <store> --operational --block-dev-paths

- **Activate a method in EASI** (a separate process; the built-in method is the default):

      set EASI_METHOD_PACKAGE=<path>\easi-screening-v2.easi-method.zip
      .venv\Scripts\python.exe -m shiny run apps\easi\app.py

  EASI verifies the package before it starts and reports its method identity in every report
  and export. **Roll back** by unsetting `EASI_METHOD_PACKAGE` and restarting: EASI loads
  `apps/easi/data` again (`b2e3033116e3`).
- **Explore many curves at once** (fit only; nothing is built or published):

      .venv\Scripts\python.exe apps\stream-curves\scripts\explore_curves.py deep --out <campaign> --l3 50 --l3 58 --workers 3
      .venv\Scripts\python.exe apps\stream-curves\scripts\explore_curves.py easi --out <campaign> --evidence <store> --variant as-built --variant relaxed-first

  A campaign folder resumes where it stopped; `candidates.jsonl` and `grid.csv` hold the results.
- **Stage many regions in parallel** (never promotes):

      .venv\Scripts\python.exe apps\stream-curves\scripts\run_region_batch.py stage-many --l3 55 --l3 65 --l3 71 --out-root <folder> --workers 3

  Each region is bound to its inputs (code, configuration, data and the version it carries
  forward from) and to what it wrote; a finished region whose outputs are intact is skipped on
  rerun, also when the region list changed, and a region staged again replaces its staged
  library. One run uses a folder at a time: a second batch on the same `--out-root`, or a stage
  into a region folder that is being staged, stops and says another run is using it. `--isolated` stages one region at
  a time, each in its own process. Promote stays one region at a time
  (`run_region_batch.py promote`). Three workers are a reasonable start on a 12-core machine;
  keep it low when the screen calls live services.
- **SQT registry.** Rebuild after the owner adds originals under
  `D:\Data\staf-authoring\sqt-originals\<STATE>\` with a `sources.json` (see `data/sqt/README.md`):

      .venv\Scripts\python.exe apps\stream-curves\scripts\build_sqt_registry.py
      .venv\Scripts\python.exe apps\stream-curves\scripts\build_sqt_registry.py --check

  A rebuilt registry never changes a saved project: an adopted SQT curve is frozen in it.
- **Selecting an SQT curve over a fitted one** needs the REF-15 extension, off until the owner
  adopts it (`owner_decisions.alternatives_over_fitted` in the methodology config). With it off,
  such a decision is refused and a held one applies nothing.
