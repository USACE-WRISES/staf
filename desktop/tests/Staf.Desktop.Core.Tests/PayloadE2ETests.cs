using Staf.Desktop.Core;
using Staf.Desktop.Core.Logging;
using Staf.Desktop.Core.Manifest;
using Staf.Desktop.Core.Payload;
using Staf.Desktop.Core.Processes;

namespace Staf.Desktop.Core.Tests;

/// <summary>
/// Opt-in full-pipeline proof using the mini payload release (build-mini-payload.ps1) served over
/// HTTP: check → download (HttpPayloadSource) → verify → extract → commit → InstalledPayloadLocator
/// → real supervisor boots the stub app from the RELOCATED python-build-standalone interpreter →
/// graceful stop. Run with:
///   STAF_ITEST_PAYLOAD=1, mini release at desktop/build/mini-release, an HTTP server on :8020
///   serving the desktop/ folder (the launcher-preview config), and the solution built.
/// </summary>
public sealed class PayloadE2ETests
{
    private static bool Enabled => Environment.GetEnvironmentVariable("STAF_ITEST_PAYLOAD") == "1";

    [Fact]
    public async Task FirstRun_OverHttp_ThenStubAppBoots_FromInstalledPayload()
    {
        if (!Enabled)
        {
            return;
        }

        var manifestUrl = Environment.GetEnvironmentVariable("STAF_MINI_MANIFEST_URL")
            ?? "http://127.0.0.1:8020/build/mini-release/latest-desktop.json";
        var origin = new Uri(manifestUrl).GetLeftPart(UriPartial.Authority);

        var repoRoot = FindRepoRoot();
        var stopHelperExe = Path.Combine(
            repoRoot, "desktop", "src", "Staf.Desktop", "bin", "Debug", "net10.0-windows", "StafDesktop.exe");
        Assert.True(File.Exists(stopHelperExe), $"Build the solution first — missing {stopHelperExe}");

        var dataRoot = Path.Combine(Path.GetTempPath(), "staf-desktop-payload-e2e", Guid.NewGuid().ToString("N"));
        var config = new ShellConfig { DataRoot = dataRoot, SelfExePath = stopHelperExe };
        config.EnsureDirectories();

        using var logs = new LogFactory(config.LogsDir);
        var shellLog = logs.For("shell");

        // ── Phase 1: first-run install over HTTP ──
        var manager = new PayloadManager(
            new LocalState(config.PayloadsDir),
            config.DownloadsDir,
            new Version(0, 1, 0),
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

        // ── Phase 2: resolve the installed payload and boot the stub app for real ──
        var locator = new InstalledPayloadLocator(config);
        var payload = locator.Resolve();
        Assert.StartsWith(config.PayloadsDir, payload.PythonExe); // truly running from the payload
        var manifest = DesktopManifest.Load(payload.ManifestFile);
        var stub = Assert.Single(manifest.Apps);

        using var job = KillOnCloseJob.TryCreate();
        using var probe = new HttpHealthProbe();
        var supervisor = new AppSupervisor(
            config, locator, [stub],
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

        await supervisor.StartAsync(stub.Id);
        var runState = await running.Task.WaitAsync(TimeSpan.FromMinutes(2));
        Assert.NotNull(runState.Port);

        // The page itself must come from the payload's stub app.
        using (var http = new HttpClient(new SocketsHttpHandler { UseProxy = false }))
        {
            var html = await http.GetStringAsync($"http://127.0.0.1:{runState.Port}/");
            Assert.Contains("payload pipeline", html, StringComparison.OrdinalIgnoreCase);
        }

        await supervisor.StopAsync(stub.Id);
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

    private static string FindRepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            if (Directory.Exists(Path.Combine(dir.FullName, "apps"))
                && Directory.Exists(Path.Combine(dir.FullName, "desktop")))
            {
                return dir.FullName;
            }
            dir = dir.Parent!;
        }
        throw new InvalidOperationException("repo root not found above test bin dir");
    }
}
