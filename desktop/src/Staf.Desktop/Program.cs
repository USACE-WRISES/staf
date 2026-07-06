using Staf.Desktop.Core;
using Staf.Desktop.Core.Logging;
using Staf.Desktop.Core.Manifest;
using Staf.Desktop.Core.Processes;

namespace Staf.Desktop;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        // Helper mode: `StafDesktop.exe --stop-helper <pid>` delivers Ctrl+C to a child server.
        // Must run before any WinForms/UI initialization.
        if (args is ["--stop-helper", var pidArg] && int.TryParse(pidArg, out var pid))
        {
            return StopHelper.Run(pid);
        }

        using var instanceLock = new Mutex(initiallyOwned: true, @"Local\StafDesktopShell", out var isFirstInstance);
        if (!isFirstInstance)
        {
            MessageBox.Show("STAF Desktop is already running.", "STAF Desktop",
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
            MessageBox.Show($"STAF Desktop could not create its data folder at {config.DataRoot}:\n{ex.Message}",
                "STAF Desktop", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        using var logs = new LogFactory(config.LogsDir);
        var shellLog = logs.For("shell");
        shellLog.WriteLine($"[shell] === STAF Desktop starting (pid {Environment.ProcessId}) ===");
        shellLog.WriteLine($"[shell] data root: {config.DataRoot}");
        shellLog.WriteLine($"[shell] mode: {(config.IsDevMode ? $"dev ({config.DevRepoRoot})" : "installed")}");

        // M1: dev mode only — the installed-payload locator arrives with the payload manager (M3).
        IPayloadLocator locator = new DevPayloadLocator(config);
        DesktopManifest manifest;
        try
        {
            manifest = DesktopManifest.Load(locator.Resolve().ManifestFile);
        }
        catch (ShellException ex)
        {
            shellLog.WriteLine($"[shell] fatal: {ex.Message}");
            MessageBox.Show(ex.Message, "STAF Desktop", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        var reaped = OrphanReaper.ReapPayloadOrphans(config.PayloadsDir, shellLog.WriteLine);
        if (reaped > 0)
        {
            shellLog.WriteLine($"[shell] reaped {reaped} orphaned payload process(es) from a previous session");
        }

        using var job = KillOnCloseJob.TryCreate(shellLog.WriteLine);
        if (job is null)
        {
            shellLog.WriteLine("[shell] warning: job object unavailable — orphan cleanup degraded");
        }
        var runner = new WindowsProcessRunner(job, shellLog.WriteLine);
        using var probe = new HttpHealthProbe();
        var stateStore = new StateStore(config.StateFile);
        var supervisor = new AppSupervisor(config, locator, manifest.Apps, runner, probe, logs, stateStore, shellLog);

        Application.Run(new LauncherForm(config, supervisor, shellLog));

        shellLog.WriteLine("[shell] === STAF Desktop exiting ===");
        return 0;
    }
}
