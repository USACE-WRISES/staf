using StreamCurves.Desktop.Core;
using StreamCurves.Desktop.Core.Logging;
using StreamCurves.Desktop.Core.Payload;
using StreamCurves.Desktop.Core.Processes;

namespace StreamCurves.Desktop;

internal static class Program
{
    /// <summary>The one URL every installed shell polls for payload updates (rolling prerelease asset).</summary>
    private const string OfficialManifestUrl =
        "https://github.com/USACE-WRISES/staf/releases/download/streamcurves-current/latest-desktop.json";

    private const string OfficialDownloadPrefix = "https://github.com/USACE-WRISES/staf/releases/download/";

    [STAThread]
    private static int Main(string[] args)
    {
        // Velopack lifecycle hooks (install/update/uninstall) must run first: the call exits the
        // process for those invocations and is a no-op otherwise.
        Velopack.VelopackApp.Build().Run();

        // Helper mode: `StreamCurvesDesktop.exe --stop-helper <pid>` delivers Ctrl+C to a child
        // server. Must run before any WinForms/UI initialization.
        if (args is ["--stop-helper", var pidArg] && int.TryParse(pidArg, out var pid))
        {
            return StopHelper.Run(pid);
        }

        using var instanceLock = new Mutex(initiallyOwned: true, @"Local\StreamCurvesDesktopShell", out var isFirstInstance);
        if (!isFirstInstance)
        {
            MessageBox.Show("StreamCurves Desktop is already running.", "StreamCurves Desktop",
                MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 0;
        }

        ApplicationConfiguration.Initialize();

        var config = ShellConfig.Create();
        try
        {
            config.EnsureDirectories();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            MessageBox.Show($"StreamCurves Desktop could not create its data folder at {config.DataRoot}:\n{ex.Message}",
                "StreamCurves Desktop", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        using var logs = new LogFactory(config.LogsDir);
        var shellLog = logs.For("shell");
        shellLog.WriteLine($"[shell] === StreamCurves Desktop starting (pid {Environment.ProcessId}) ===");
        shellLog.WriteLine($"[shell] data root: {config.DataRoot}");

        var reaped = OrphanReaper.ReapPayloadOrphans(config.PayloadsDir, shellLog.WriteLine);
        if (reaped > 0)
        {
            shellLog.WriteLine($"[shell] reaped {reaped} orphaned payload process(es) from a previous session");
        }

        // A portable install whose zip escaped the launcher-name normalization carries a stale
        // title-named launcher after its first update (see PortableJanitor): quietly remove it.
        PortableJanitor.CleanStaleLauncher(AppContext.BaseDirectory, shellLog.WriteLine);

        using var job = KillOnCloseJob.TryCreate(shellLog.WriteLine);
        if (job is null)
        {
            shellLog.WriteLine("[shell] warning: job object unavailable - orphan cleanup degraded");
        }
        var runner = new WindowsProcessRunner(job, shellLog.WriteLine);
        using var probe = new HttpHealthProbe();
        var stateStore = new StateStore(config.StateFile);

        // Mode: dev (repo .venv, payload machinery off) unless disabled or not in a checkout.
        // STREAMCURVES_FORCE_PAYLOAD=1 lets a developer exercise the installed-payload path in a checkout.
        var usePayload = !config.IsDevMode || Environment.GetEnvironmentVariable("STREAMCURVES_FORCE_PAYLOAD") == "1";
        shellLog.WriteLine($"[shell] mode: {(usePayload ? "installed payload" : $"dev ({config.DevRepoRoot})")}");

        IPayloadLocator locator;
        PayloadManager? payloadManager = null;
        string? manifestUrl = null;

        if (usePayload)
        {
            locator = new InstalledPayloadLocator(config);

            manifestUrl = Environment.GetEnvironmentVariable("STREAMCURVES_MANIFEST_URL");
            var allowedPrefixes = new List<string> { OfficialDownloadPrefix };
            if (string.IsNullOrWhiteSpace(manifestUrl))
            {
                manifestUrl = OfficialManifestUrl;
            }
            else if (Uri.TryCreate(manifestUrl, UriKind.Absolute, out var overrideUri))
            {
                // QA/dev override: also trust that origin for component downloads.
                allowedPrefixes.Add(overrideUri.GetLeftPart(UriPartial.Authority));
                shellLog.WriteLine($"[shell] manifest override: {manifestUrl}");
            }

            var localState = new LocalState(config.PayloadsDir);
            payloadManager = new PayloadManager(
                localState,
                config.DownloadsDir,
                typeof(Program).Assembly.GetName().Version ?? new Version(0, 0, 0),
                allowedPrefixes,
                inUsePayloadDirs: () => _supervisorRef?.GetInUsePayloadDirs() ?? [],
                shellLog);
        }
        else
        {
            locator = new DevPayloadLocator(config);
        }

        var services = new ShellServices
        {
            Config = config,
            ShellLog = shellLog,
            Locator = locator,
            PayloadManager = payloadManager,
            ManifestUrl = manifestUrl,
            SourceFactory = () => new HttpPayloadSource(),
            SupervisorFactory = apps =>
            {
                var supervisor = new AppSupervisor(config, locator, apps, runner, probe, logs, stateStore, shellLog);
                _supervisorRef = supervisor;
                return supervisor;
            },
        };

        Application.Run(new MainForm(services));

        shellLog.WriteLine("[shell] === StreamCurves Desktop exiting ===");
        return 0;
    }

    // The payload manager needs live in-use dirs, but the supervisor is created after first-run
    // setup completes; this reference bridges that ordering.
    private static AppSupervisor? _supervisorRef;
}
