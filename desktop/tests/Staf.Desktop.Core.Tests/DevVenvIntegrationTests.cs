using Staf.Desktop.Core;
using Staf.Desktop.Core.Logging;
using Staf.Desktop.Core.Manifest;
using Staf.Desktop.Core.Processes;

namespace Staf.Desktop.Core.Tests;

/// <summary>
/// Opt-in end-to-end proof against the real repo .venv and the real SFARI app:
///   set STAF_ITEST=1, build the solution, then `dotnet test`.
/// Asserts the whole M1 story: spawn → HTTP-healthy → graceful Ctrl+C stop with exit code 0.
/// </summary>
public sealed class DevVenvIntegrationTests
{
    private static bool Enabled => Environment.GetEnvironmentVariable("STAF_ITEST") == "1";

    [Fact]
    public async Task Sfari_Boots_AnswersHttp_And_StopsGracefully()
    {
        if (!Enabled)
        {
            return;
        }

        var repoRoot = FindRepoRoot();
        var stopHelperExe = Path.Combine(
            repoRoot, "desktop", "src", "Staf.Desktop", "bin", "Debug", "net10.0-windows", "StafDesktop.exe");
        Assert.True(File.Exists(stopHelperExe), $"Build the solution first — missing {stopHelperExe}");

        var dataRoot = Path.Combine(Path.GetTempPath(), "staf-desktop-itest", Guid.NewGuid().ToString("N"));
        var config = new ShellConfig { DataRoot = dataRoot, SelfExePath = stopHelperExe, DevRepoRoot = repoRoot };
        config.EnsureDirectories();

        var sfari = new AppDescriptor { Id = "sfari", Dir = "sfari", Entry = "app.py", Name = "SFARI" };
        using var logs = new LogFactory(config.LogsDir);
        using var job = KillOnCloseJob.TryCreate();
        using var probe = new HttpHealthProbe();
        var supervisor = new AppSupervisor(
            config,
            new DevPayloadLocator(config),
            [sfari],
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

        await supervisor.StartAsync("sfari");
        var state = await running.Task.WaitAsync(TimeSpan.FromMinutes(3));
        Assert.NotNull(state.Port);

        await supervisor.StopAsync("sfari");
        var final = await stopped.Task.WaitAsync(TimeSpan.FromSeconds(30));

        // Exit code 0 proves the Ctrl+C landed and uvicorn drained; a forced kill reports -1.
        Assert.Contains("exit code 0", final.Detail);
        Assert.NotNull(runningState);
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
