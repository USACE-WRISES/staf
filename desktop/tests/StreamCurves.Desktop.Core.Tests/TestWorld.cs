using StreamCurves.Desktop.Core;
using StreamCurves.Desktop.Core.Logging;
using StreamCurves.Desktop.Core.Manifest;
using StreamCurves.Desktop.Core.Processes;

namespace StreamCurves.Desktop.Core.Tests;

/// <summary>Disposable sandbox wiring a supervisor to fakes over a temp data root + apps tree.</summary>
internal sealed class TestWorld : IDisposable
{
    public string Root { get; }
    public ShellConfig Config { get; }
    public FakeRunner Runner { get; } = new();
    public FakeProbe Probe { get; } = new();
    public FakeLocator Locator { get; } = new();
    public LogFactory Logs { get; }
    public StateStore StateStore { get; }
    public AppSupervisor Supervisor { get; }

    public static readonly AppDescriptor App = new()
    {
        Id = "streamcurves",
        Dir = "stream-curves",
        Entry = "app.py",
        Name = "StreamCurves",
    };

    public TestWorld(SupervisorOptions? options = null)
    {
        Root = Path.Combine(Path.GetTempPath(), "streamcurves-desktop-tests", Guid.NewGuid().ToString("N"));
        var appsRoot = Path.Combine(Root, "apps");
        Directory.CreateDirectory(Path.Combine(appsRoot, "stream-curves"));

        Config = new ShellConfig
        {
            DataRoot = Path.Combine(Root, "data"),
            SelfExePath = Path.Combine(Root, "StreamCurvesDesktop.exe"),
        };
        Config.EnsureDirectories();

        Locator.Paths = new PayloadPaths(
            PythonExe: Path.Combine(Root, "python", "python.exe"),
            AppsRoot: appsRoot,
            ManifestFile: Path.Combine(Root, "manifest.json"));

        Logs = new LogFactory(Config.LogsDir);
        StateStore = new StateStore(Config.StateFile);
        Supervisor = new AppSupervisor(
            Config, Locator, [App], Runner, Probe, Logs, StateStore, Logs.For("shell"),
            options ?? FastOptions);
    }

    public static SupervisorOptions FastOptions { get; } = new()
    {
        PollInterval = TimeSpan.FromMilliseconds(20),
        FirstStartSoftTimeout = TimeSpan.FromMilliseconds(250),
        SoftTimeout = TimeSpan.FromMilliseconds(150),
        StartHardCap = TimeSpan.FromSeconds(2),
        StopHelperTimeout = TimeSpan.FromMilliseconds(200),
        GracefulStopWait = TimeSpan.FromMilliseconds(400),
        KillWait = TimeSpan.FromMilliseconds(200),
        ExitPollInterval = TimeSpan.FromMilliseconds(20),
        MaxSpawnAttempts = 3,
    };

    public async Task<AppRuntimeState> WaitForStatusAsync(AppStatus status, TimeSpan? timeout = null)
    {
        var tcs = new TaskCompletionSource<AppRuntimeState>(TaskCreationOptions.RunContinuationsAsynchronously);
        void Handler(string appId, AppRuntimeState state)
        {
            if (appId == App.Id && state.Status == status)
            {
                tcs.TrySetResult(state);
            }
        }

        Supervisor.StateChanged += Handler;
        try
        {
            var current = Supervisor.GetState(App.Id);
            if (current.Status == status)
            {
                return current;
            }
            var winner = await Task.WhenAny(tcs.Task, Task.Delay(timeout ?? TimeSpan.FromSeconds(10)));
            if (winner != tcs.Task)
            {
                var last = Supervisor.GetState(App.Id);
                throw new TimeoutException($"Never reached {status}; last: {last.Status} ({last.Detail})");
            }
            return await tcs.Task;
        }
        finally
        {
            Supervisor.StateChanged -= Handler;
        }
    }

    public void Dispose()
    {
        Logs.Dispose();
        try
        {
            Directory.Delete(Root, recursive: true);
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }
}
