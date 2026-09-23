using StreamCurves.Desktop.Core;
using StreamCurves.Desktop.Core.Logging;
using StreamCurves.Desktop.Core.Manifest;
using StreamCurves.Desktop.Core.Payload;
using StreamCurves.Desktop.Core.Processes;

namespace StreamCurves.Desktop.Core.Tests;

/// <summary>
/// Opt-in full-pipeline proof using a locally built payload release served over HTTP:
/// check → download (HttpPayloadSource) → verify → extract → commit → InstalledPayloadLocator
/// → real supervisor boots StreamCurves from the RELOCATED python-build-standalone interpreter
/// → graceful stop. Run with:
///   STREAMCURVES_ITEST_PAYLOAD=1, a release in desktop/build/release (build-env-payload.ps1 +
///   build-apps-payload.ps1 + gen_latest_manifest.py with http://127.0.0.1:8020/build/release/
///   URLs), an HTTP server on :8020 serving the desktop/ folder, and the solution built.
///   STREAMCURVES_ITEST_MANIFEST_URL points elsewhere.
/// </summary>
public sealed class PayloadE2ETests
{
    private static bool Enabled => Environment.GetEnvironmentVariable("STREAMCURVES_ITEST_PAYLOAD") == "1";

    [Fact]
    public async Task FirstRun_OverHttp_ThenTheAppBoots_FromInstalledPayload()
    {
        if (!Enabled)
        {
            return;
        }

        var manifestUrl = Environment.GetEnvironmentVariable("STREAMCURVES_ITEST_MANIFEST_URL")
            ?? "http://127.0.0.1:8020/build/release/latest-desktop.json";
        var origin = new Uri(manifestUrl).GetLeftPart(UriPartial.Authority);

        var repoRoot = ShellConfig.ResolveDevRepoRoot(_ => null, AppContext.BaseDirectory)
            ?? throw new InvalidOperationException("repo root not found above test bin dir");
        var stopHelperExe = Path.Combine(
            repoRoot, "desktop", "src", "StreamCurves.Desktop", "bin", "Debug", "net10.0-windows", "StreamCurvesDesktop.exe");
        Assert.True(File.Exists(stopHelperExe), $"Build the solution first - missing {stopHelperExe}");

        var dataRoot = Path.Combine(Path.GetTempPath(), "streamcurves-desktop-payload-e2e", Guid.NewGuid().ToString("N"));
        var config = new ShellConfig { DataRoot = dataRoot, SelfExePath = stopHelperExe };
        config.EnsureDirectories();

        using var logs = new LogFactory(config.LogsDir);
        var shellLog = logs.For("shell");

        // ── Phase 1: first-run install over HTTP ──
        var manager = new PayloadManager(
            new LocalState(config.PayloadsDir),
            config.DownloadsDir,
            new Version(1, 0, 0),
            allowedUrlPrefixes: [origin],
            inUsePayloadDirs: () => [],
            shellLog);

        using (var source = new HttpPayloadSource())
        {
            var check = await manager.CheckAsync(source, manifestUrl, CancellationToken.None);
            var update = Assert.IsType<CheckResult.UpdateAvailable>(check);
            Assert.NotNull(update.Plan.Env);
            Assert.NotNull(update.Plan.Apps);

            await manager.ApplyAsync(source, update.Manifest, update.Plan, CancellationToken.None);
        }

        // ── Phase 2: resolve the installed payload and boot its app for real ──
        var locator = new InstalledPayloadLocator(config);
        var payload = locator.Resolve();
        Assert.True(payload.Installed);
        Assert.StartsWith(config.PayloadsDir, payload.PythonExe); // truly running from the payload
        Assert.True(Directory.Exists(Path.Combine(payload.AppsRoot, "library")), "library\\ must sit beside stream-curves\\");
        var manifest = DesktopManifest.Load(payload.ManifestFile);
        var app = Assert.Single(manifest.Apps);

        using var job = KillOnCloseJob.TryCreate();
        using var probe = new HttpHealthProbe();
        var supervisor = new AppSupervisor(
            config, locator, [app],
            new WindowsProcessRunner(job), probe, logs,
            new StateStore(config.StateFile), shellLog);

        var running = new TaskCompletionSource<AppRuntimeState>(TaskCreationOptions.RunContinuationsAsynchronously);
        var stopped = new TaskCompletionSource<AppRuntimeState>(TaskCreationOptions.RunContinuationsAsynchronously);
        supervisor.StateChanged += (_, state) =>
        {
            switch (state.Status)
            {
                case AppStatus.Running:
                    running.TrySetResult(state);
                    break;
                case AppStatus.Stopped:
                    stopped.TrySetResult(state);
                    break;
                case AppStatus.Crashed:
                    var failure = new Exception($"crashed: {state.Detail}");
                    running.TrySetException(failure);
                    stopped.TrySetException(failure);
                    break;
            }
        };

        await supervisor.StartAsync(app.Id);
        var runState = await running.Task.WaitAsync(TimeSpan.FromMinutes(4));
        Assert.NotNull(runState.Port);

        // The page must be a real document served by the payload's app.
        using (var http = new HttpClient(new SocketsHttpHandler { UseProxy = false }))
        {
            var html = await http.GetStringAsync($"http://127.0.0.1:{runState.Port}/");
            Assert.Contains("<html", html, StringComparison.OrdinalIgnoreCase);
            Assert.True(html.Length > 500, "suspiciously small response");
        }

        await supervisor.StopAsync(app.Id);
        var final = await stopped.Task.WaitAsync(TimeSpan.FromSeconds(30));
        Assert.Contains("exit code 0", final.Detail); // graceful Ctrl+C against the pbs interpreter

        try
        {
            Directory.Delete(dataRoot, recursive: true);
        }
        catch (IOException)
        {
        }
    }
}
