using StreamCurves.Desktop.Core;
using StreamCurves.Desktop.Core.Logging;
using StreamCurves.Desktop.Core.Manifest;
using StreamCurves.Desktop.Core.Processes;

namespace StreamCurves.Desktop.Core.Tests;

/// <summary>
/// Opt-in end-to-end proof against the real repo .venv and the real StreamCurves app:
///   set STREAMCURVES_ITEST=1, build the solution (the shell exe is the stop helper), then
///   `dotnet test`.
/// Asserts the whole story: spawn → HTTP-healthy → graceful Ctrl+C stop with exit code 0.
/// </summary>
public sealed class DevVenvIntegrationTests
{
    private static bool Enabled => Environment.GetEnvironmentVariable("STREAMCURVES_ITEST") == "1";

    [Theory]
    [InlineData("streamcurves", "stream-curves")]
    public async Task App_Boots_AnswersHttp_And_StopsGracefully(string id, string dir)
    {
        if (!Enabled)
        {
            return;
        }

        var repoRoot = ShellConfig.ResolveDevRepoRoot(_ => null, AppContext.BaseDirectory)
            ?? throw new InvalidOperationException("repo root not found above test bin dir");
        var stopHelperExe = Path.Combine(
            repoRoot, "desktop", "src", "StreamCurves.Desktop", "bin", "Debug", "net10.0-windows", "StreamCurvesDesktop.exe");
        Assert.True(File.Exists(stopHelperExe), $"Build the solution first - missing {stopHelperExe}");

        var dataRoot = Path.Combine(Path.GetTempPath(), "streamcurves-desktop-itest", Guid.NewGuid().ToString("N"));
        var config = new ShellConfig { DataRoot = dataRoot, SelfExePath = stopHelperExe, DevRepoRoot = repoRoot };
        config.EnsureDirectories();

        var app = new AppDescriptor { Id = id, Dir = dir, Entry = "app.py", Name = "StreamCurves" };
        using var logs = new LogFactory(config.LogsDir);
        using var job = KillOnCloseJob.TryCreate();
        using var probe = new HttpHealthProbe();
        var supervisor = new AppSupervisor(
            config,
            new DevPayloadLocator(config),
            [app],
            new WindowsProcessRunner(job),
            probe,
            logs,
            new StateStore(config.StateFile),
            logs.For("shell"));

        AppRuntimeState? runningState = null;
        var running = new TaskCompletionSource<AppRuntimeState>(TaskCreationOptions.RunContinuationsAsynchronously);
        var stopped = new TaskCompletionSource<AppRuntimeState>(TaskCreationOptions.RunContinuationsAsynchronously);
        supervisor.StateChanged += (_, state) =>
        {
            if (state.Status == AppStatus.Running)
            {
                runningState = state;
                running.TrySetResult(state);
            }
            if (state.Status is AppStatus.Stopped)
            {
                stopped.TrySetResult(state);
            }
            if (state.Status is AppStatus.Crashed)
            {
                running.TrySetException(new Exception($"crashed: {state.Detail}"));
                stopped.TrySetException(new Exception($"crashed: {state.Detail}"));
            }
        };

        await supervisor.StartAsync(id);
        var state = await running.Task.WaitAsync(TimeSpan.FromMinutes(3));
        Assert.NotNull(state.Port);

        await supervisor.StopAsync(id);
        var final = await stopped.Task.WaitAsync(TimeSpan.FromSeconds(30));

        // Exit code 0 proves the Ctrl+C landed and uvicorn drained; a forced kill reports -1.
        Assert.Contains("exit code 0", final.Detail);
        Assert.NotNull(runningState);
    }
}
