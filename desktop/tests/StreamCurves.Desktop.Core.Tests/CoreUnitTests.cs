using System.Net;
using System.Net.Sockets;
using System.Text.Json;
using StreamCurves.Desktop.Core;
using StreamCurves.Desktop.Core.Logging;
using StreamCurves.Desktop.Core.Manifest;
using StreamCurves.Desktop.Core.Processes;

namespace StreamCurves.Desktop.Core.Tests;

public sealed class PortAllocatorTests
{
    [Fact]
    public void ReturnsAnImmediatelyBindableLoopbackPort()
    {
        var port = PortAllocator.GetFreeLoopbackPort();
        Assert.InRange(port, 1024, 65535);

        var listener = new TcpListener(IPAddress.Loopback, port);
        listener.Start();
        listener.Stop();
    }
}

public sealed class DesktopManifestTests
{
    private const string Valid = """
        {
          "schemaVersion": 1,
          "version": "apps-2026.09.23-abc1234",
          "builtFromCommit": "abc1234",
          "requiresEnv": "env-cp312-deadbeef",
          "apps": [
            { "id": "streamcurves", "dir": "stream-curves", "entry": "app.py", "name": "StreamCurves",
              "fullName": "Reference & Regional Curve Development", "tier": "", "tierNum": 0 },
            { "id": "other", "dir": "other-app", "entry": "app.py", "name": "Other" }
          ]
        }
        """;

    [Fact]
    public void ParsesValidManifest()
    {
        var manifest = DesktopManifest.Parse(Valid);
        Assert.Equal(2, manifest.Apps.Count);
        Assert.Equal("stream-curves", manifest.Apps[0].Dir);
        Assert.Equal("env-cp312-deadbeef", manifest.RequiresEnv);
        Assert.Equal(0, manifest.Apps[0].TierNum);
    }

    [Fact]
    public void RejectsUnknownSchemaVersion()
    {
        var ex = Assert.Throws<ShellException>(() => DesktopManifest.Parse(Valid.Replace("\"schemaVersion\": 1", "\"schemaVersion\": 2")));
        Assert.Contains("schema version 2", ex.Message);
        Assert.Contains("StreamCurves Desktop", ex.Message);
    }

    [Fact]
    public void RejectsDuplicateAppIds()
    {
        var json = Valid.Replace("\"id\": \"other\"", "\"id\": \"STREAMCURVES\"");
        Assert.Throws<ShellException>(() => DesktopManifest.Parse(json));
    }

    [Theory]
    [InlineData(".")]
    [InlineData("..")]
    [InlineData("other/../../etc")]
    [InlineData("C:\\evil")]
    [InlineData("")]
    [InlineData("./other-app")]
    public void RejectsUnsafeDirs(string dir)
    {
        // StreamCurves lives in a subfolder of the apps root, so even "." (the payload root)
        // is refused: a dir must name a real child folder.
        var json = Valid.Replace("\"dir\": \"other-app\"", $"\"dir\": {JsonSerializer.Serialize(dir)}");
        Assert.Throws<ShellException>(() => DesktopManifest.Parse(json));
    }

    [Fact]
    public void DotEntryIsRejected()
    {
        var json = Valid.Replace("\"entry\": \"app.py\", \"name\": \"Other\"",
                                 "\"entry\": \".\", \"name\": \"Other\"");
        Assert.Throws<ShellException>(() => DesktopManifest.Parse(json));
    }

    [Fact]
    public void RejectsGarbage()
    {
        Assert.Throws<ShellException>(() => DesktopManifest.Parse("not json"));
        Assert.Throws<ShellException>(() => DesktopManifest.Parse("null"));
    }

    [Fact]
    public void DevManifest_DescribesTheOneStreamCurvesApp()
    {
        // desktop/dev/dev-manifest.json is what dev mode launches; it must stay the single
        // StreamCurves entry the payload's generated manifest also carries.
        var repo = ShellConfig.ResolveDevRepoRoot(_ => null, AppContext.BaseDirectory);
        Assert.NotNull(repo);
        var manifest = DesktopManifest.Load(Path.Combine(repo!, "desktop", "dev", "dev-manifest.json"));

        var app = Assert.Single(manifest.Apps);
        Assert.Equal("streamcurves", app.Id);
        Assert.Equal("stream-curves", app.Dir);
        Assert.Equal("app.py", app.Entry);
        Assert.Equal("StreamCurves", app.Name);
        Assert.True(File.Exists(Path.Combine(repo!, "apps", app.Dir, app.Entry)));
    }
}

public sealed class RollingLogWriterTests
{
    [Fact]
    public void RotatesWhenMaxSizeExceeded()
    {
        var dir = Path.Combine(Path.GetTempPath(), "streamcurves-desktop-tests", Guid.NewGuid().ToString("N"));
        var path = Path.Combine(dir, "app.log");
        using (var log = new RollingLogWriter(path, maxBytes: 300, keep: 3))
        {
            for (var i = 0; i < 30; i++)
            {
                log.WriteLine($"line {i} - padding padding padding");
            }
        }

        Assert.True(File.Exists(path));
        Assert.True(File.Exists(path + ".1"));
        Assert.True(new FileInfo(path).Length <= 400);
        Directory.Delete(dir, recursive: true);
    }

    [Fact]
    public void SharesTheLiveLogWithSiblings()
    {
        var dir = Path.Combine(Path.GetTempPath(), "streamcurves-desktop-tests", Guid.NewGuid().ToString("N"));
        var path = Path.Combine(dir, "shell.log");
        using (var log = new RollingLogWriter(path))
        {
            log.WriteLine("shell line");
            // A sibling handle (--stop-helper, a log viewer opened for read/write) must not be
            // locked out while the shell holds the log; with FileShare.Read this open threw.
            // No byte-interleaving guarantee: only that the open succeeds and the shell's own
            // lines keep landing.
            using (var sibling = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
            {
                sibling.Write("sibling line\n"u8);
            }
            log.WriteLine("shell line after sibling");
        }

        var content = File.ReadAllText(path);
        Assert.Contains("shell line", content);
        Assert.Contains("shell line after sibling", content);
        Directory.Delete(dir, recursive: true);
    }
}

public sealed class StateStoreTests
{
    [Fact]
    public void PersistsAcrossInstances()
    {
        var path = Path.Combine(Path.GetTempPath(), "streamcurves-desktop-tests", Guid.NewGuid().ToString("N"), "state.json");
        var store = new StateStore(path);
        Assert.False(store.HasStartedOk("streamcurves"));

        store.MarkStartedOk("streamcurves");
        var reloaded = new StateStore(path);
        Assert.True(reloaded.HasStartedOk("streamcurves"));
        Assert.False(reloaded.HasStartedOk("other"));
        Directory.Delete(Path.GetDirectoryName(path)!, recursive: true);
    }

    [Fact]
    public void CorruptFileResetsToEmpty()
    {
        var dir = Path.Combine(Path.GetTempPath(), "streamcurves-desktop-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        var path = Path.Combine(dir, "state.json");
        File.WriteAllText(path, "{{{ definitely not json");

        var store = new StateStore(path);
        Assert.False(store.HasStartedOk("streamcurves"));
        Directory.Delete(dir, recursive: true);
    }
}

public sealed class AppEnvironmentTests
{
    private static readonly ShellConfig Config = new()
    {
        DataRoot = @"C:\Users\test\AppData\Local\StreamCurves",
        SelfExePath = @"C:\apps\StreamCurvesDesktop.exe",
    };

    private static readonly PayloadPaths InstalledPayload = new(
        PythonExe: @"C:\Users\test\AppData\Local\StreamCurves\payloads\env-x\python\python.exe",
        AppsRoot: @"C:\Users\test\AppData\Local\StreamCurves\payloads\apps-x",
        ManifestFile: @"C:\Users\test\AppData\Local\StreamCurves\payloads\apps-x\desktop-manifest.json")
    {
        Installed = true,
    };

    private static readonly PayloadPaths DevPayload = new(
        PythonExe: @"D:\src\staf\.venv\Scripts\python.exe",
        AppsRoot: @"D:\src\staf\apps",
        ManifestFile: @"D:\src\staf\desktop\dev\dev-manifest.json");

    private static readonly AppDescriptor App = new()
    {
        Id = "streamcurves", Dir = "stream-curves", Entry = "app.py", Name = "StreamCurves",
    };

    [Fact]
    public void SetsCacheAndIsolationVariables()
    {
        var env = AppEnvironment.Build(Config, InstalledPayload, App, getEnv: _ => null, proxyResolver: _ => null);

        Assert.Equal(@"C:\Users\test\AppData\Local\StreamCurves\cache\hyriver.sqlite", env["HYRIVER_CACHE_NAME"]);
        Assert.Equal(Path.Combine(Config.CacheDir, "matplotlib"), env["MPLCONFIGDIR"]);
        Assert.Equal("1", env["PYTHONDONTWRITEBYTECODE"]);
        Assert.Equal("1", env["PYTHONNOUSERSITE"]);
        Assert.Equal("1", env["PYTHONUTF8"]);
        Assert.Equal("1", env["STREAMCURVES_DESKTOP"]);
        Assert.Equal(Config.DataRoot, env["STREAMCURVES_DATA_ROOT"]);
        Assert.StartsWith(
            @"C:\Users\test\AppData\Local\StreamCurves\payloads\env-x\python;C:\Users\test\AppData\Local\StreamCurves\payloads\env-x\python\DLLs;",
            env["PATH"]);
        // The four-app shell's cross-app link injection is gone with the four-app shell.
        Assert.False(env.ContainsKey("STAF_LINKS_OVERRIDES"));
    }

    [Fact]
    public void InstalledMode_RemovesTheLibraryVariables()
    {
        var env = AppEnvironment.Build(Config, InstalledPayload, App,
            getEnv: name => name.StartsWith("STAF_LIBRARY_", StringComparison.Ordinal) ? "from-machine" : null,
            proxyResolver: _ => null);

        // Present with a null value = "remove from the child" (WindowsProcessRunner).
        foreach (var name in new[] { "STAF_LIBRARY_ROOT", "STAF_LIBRARY_PUBLISH", "STAF_LIBRARY_MAINTAINER" })
        {
            Assert.True(env.ContainsKey(name), $"{name} must be listed for removal");
            Assert.Null(env[name]);
        }
    }

    [Fact]
    public void DevMode_PassesTheLibraryVariablesThrough()
    {
        var env = AppEnvironment.Build(Config, DevPayload, App, getEnv: _ => null, proxyResolver: _ => null);

        // Absent from the overrides = inherited unchanged (canonical publishing is a checkout workflow).
        foreach (var name in AppEnvironment.LibraryVariables)
        {
            Assert.False(env.ContainsKey(name), $"{name} must pass through in dev mode");
        }
        Assert.Equal("1", env["STREAMCURVES_DESKTOP"]);
    }

    [Fact]
    public async Task InstalledMode_ChildProcessDoesNotInheritLibraryVariables()
    {
        // End to end through the real runner: the parent has the variables set, the installed
        // environment removes them, the dev environment passes them through.
        var saved = AppEnvironment.LibraryVariables.ToDictionary(n => n, Environment.GetEnvironmentVariable);
        try
        {
            foreach (var name in AppEnvironment.LibraryVariables)
            {
                Environment.SetEnvironmentVariable(name, "from-parent");
            }

            var installed = await ChildEnvironmentAsync(
                AppEnvironment.Build(Config, InstalledPayload, App, proxyResolver: _ => null));
            Assert.DoesNotContain(installed, line => line.StartsWith("STAF_LIBRARY_", StringComparison.OrdinalIgnoreCase));
            Assert.Contains("STREAMCURVES_DESKTOP=1", installed);

            var dev = await ChildEnvironmentAsync(
                AppEnvironment.Build(Config, DevPayload, App, proxyResolver: _ => null));
            Assert.Contains("STAF_LIBRARY_ROOT=from-parent", dev);
            Assert.Contains("STAF_LIBRARY_PUBLISH=from-parent", dev);
            Assert.Contains("STAF_LIBRARY_MAINTAINER=from-parent", dev);
        }
        finally
        {
            foreach (var (name, value) in saved)
            {
                Environment.SetEnvironmentVariable(name, value);
            }
        }
    }

    [Fact]
    public void InjectsSystemProxy_OnlyWhenUnsetAndDistinct()
    {
        var proxied = AppEnvironment.Build(Config, InstalledPayload, App,
            getEnv: _ => null,
            proxyResolver: _ => new Uri("http://proxy.corp:8080"));
        Assert.Equal("http://proxy.corp:8080", proxied["HTTPS_PROXY"]);
        Assert.Equal("http://proxy.corp:8080", proxied["HTTP_PROXY"]);

        var alreadySet = AppEnvironment.Build(Config, InstalledPayload, App,
            getEnv: name => name is "HTTPS_PROXY" ? "http://existing:1" : null,
            proxyResolver: _ => new Uri("http://proxy.corp:8080"));
        Assert.False(alreadySet.ContainsKey("HTTPS_PROXY"));

        // IWebProxy convention: GetProxy returns the destination itself when direct.
        var direct = AppEnvironment.Build(Config, InstalledPayload, App,
            getEnv: _ => null,
            proxyResolver: uri => uri);
        Assert.False(direct.ContainsKey("HTTPS_PROXY"));
    }

    /// <summary>Runs `cmd /c set` under the given overrides and returns the child's variables.</summary>
    private static async Task<List<string>> ChildEnvironmentAsync(IReadOnlyDictionary<string, string?> overrides)
    {
        var lines = new List<string>();
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var runner = new WindowsProcessRunner(job: null);
        using var process = runner.Start(
            new ProcessSpec
            {
                ExePath = Path.Combine(Environment.SystemDirectory, "cmd.exe"),
                Arguments = ["/d", "/c", "set & echo __end__"],
                Environment = overrides,
            },
            line =>
            {
                lock (lines)
                {
                    if (line.Trim() == "__end__")
                    {
                        done.TrySetResult();
                    }
                    else
                    {
                        lines.Add(line);
                    }
                }
            });
        await done.Task.WaitAsync(TimeSpan.FromSeconds(30));
        lock (lines)
        {
            return [.. lines];
        }
    }
}

public sealed class LauncherProtocolCommandTests
{
    [Fact]
    public void ParsesProjectPickerCommandFields()
    {
        var cmd = LauncherProtocol.ParseCommand(
            """{ "type": "pickProjectSave", "purpose": "save_as", "fileName": "NEH region.streamcurves" }""");
        Assert.NotNull(cmd);
        Assert.Equal("pickProjectSave", cmd!.Type);
        Assert.Equal("save_as", cmd.Purpose);
        Assert.Equal("NEH region.streamcurves", cmd.FileName);
        Assert.Null(cmd.AppId);
        Assert.Null(cmd.Title);
        Assert.Null(cmd.Path);
    }

    [Fact]
    public void ParsesSetTitleAndIgnoresUnknownProperties()
    {
        // Older/newer bridge payloads may carry extra fields; they must never break parsing.
        var cmd = LauncherProtocol.ParseCommand(
            """{ "type": "setTitle", "title": "NEH region", "nonce": 12345, "future": {"x": 1} }""");
        Assert.NotNull(cmd);
        Assert.Equal("setTitle", cmd!.Type);
        Assert.Equal("NEH region", cmd.Title);
    }

    [Fact]
    public void ParsesPickFolder()
    {
        var cmd = LauncherProtocol.ParseCommand("""{ "type": "pickFolder", "purpose": "export_dir" }""");
        Assert.NotNull(cmd);
        Assert.Equal("pickFolder", cmd!.Type);
        Assert.Equal("export_dir", cmd.Purpose);
        Assert.Null(cmd.Path);
        Assert.Null(cmd.FileName);
    }

    [Fact]
    public void ParsesSetProjectFolderPath()
    {
        var json = JsonSerializer.Serialize(new { type = "setProjectFolder", path = @"D:\Projects\NEH region" });
        var cmd = LauncherProtocol.ParseCommand(json);
        Assert.NotNull(cmd);
        Assert.Equal("setProjectFolder", cmd!.Type);
        Assert.Equal(@"D:\Projects\NEH region", cmd.Path);
    }

    [Fact]
    public void SetProjectFolderWithoutAPath_ParsesAsAClear()
    {
        var cleared = LauncherProtocol.ParseCommand("""{ "type": "setProjectFolder", "path": null }""");
        Assert.NotNull(cleared);
        Assert.Null(cleared!.Path);

        var missing = LauncherProtocol.ParseCommand("""{ "type": "setProjectFolder" }""");
        Assert.NotNull(missing);
        Assert.Null(missing!.Path);
    }

    [Theory]
    [InlineData("ready")]
    [InlineData("setupRetry")]
    [InlineData("installFromFile")]
    [InlineData("openLogsFolder")]
    [InlineData("pickProjectOpen")]
    [InlineData("pickProjectSave")]
    [InlineData("pickFolder")]
    [InlineData("setTitle")]
    [InlineData("setProjectFolder")]
    [InlineData("someFutureCommand")]
    public void EveryCommandTypeParses_UnknownOnesToo(string type)
    {
        // The shell ignores types it does not handle; parsing must still succeed so an
        // unknown command is dropped quietly instead of logged as garbage.
        var cmd = LauncherProtocol.ParseCommand(JsonSerializer.Serialize(new { type }));
        Assert.NotNull(cmd);
        Assert.Equal(type, cmd!.Type);
    }

    [Theory]
    [InlineData("not json")]
    [InlineData("null")]
    [InlineData("{}")]
    [InlineData("""{ "type": "" }""")]
    public void RejectsMessagesWithoutAType(string json)
    {
        Assert.Null(LauncherProtocol.ParseCommand(json));
    }
}

public sealed class ShellConfigDevModeTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), "streamcurves-devmode-" + Guid.NewGuid().ToString("N"));

    public void Dispose()
    {
        try
        {
            Directory.Delete(_root, recursive: true);
        }
        catch (IOException)
        {
        }
    }

    /// <summary>A checkout skeleton: apps\stream-curves\app.py + desktop\, and the exe's bin dir.</summary>
    private (string Repo, string BinDir) MakeCheckout(string name, bool withStreamCurves = true)
    {
        var repo = Path.Combine(_root, name);
        var bin = Path.Combine(repo, "desktop", "src", "StreamCurves.Desktop", "bin", "Debug", "net10.0-windows");
        Directory.CreateDirectory(bin);
        Directory.CreateDirectory(Path.Combine(repo, "apps", "library"));
        if (withStreamCurves)
        {
            Directory.CreateDirectory(Path.Combine(repo, "apps", "stream-curves"));
            File.WriteAllText(Path.Combine(repo, "apps", "stream-curves", "app.py"), "app = None");
        }
        return (repo, bin);
    }

    [Fact]
    public void DetectsTheCheckoutByWalkingUpFromTheExe()
    {
        var (repo, bin) = MakeCheckout("staf");
        Assert.Equal(repo, ShellConfig.ResolveDevRepoRoot(_ => null, bin));
    }

    [Fact]
    public void DevModeZeroDisablesDetection()
    {
        var (_, bin) = MakeCheckout("staf");
        Assert.Null(ShellConfig.ResolveDevRepoRoot(
            name => name == "STREAMCURVES_DESKTOP_DEV" ? "0" : null, bin));
    }

    [Fact]
    public void RepoRootOverrideWins()
    {
        var (_, bin) = MakeCheckout("staf");
        var (other, _) = MakeCheckout("other-checkout");
        Assert.Equal(other, ShellConfig.ResolveDevRepoRoot(
            name => name == "STREAMCURVES_REPO_ROOT" ? other : null, bin));
    }

    [Fact]
    public void RepoRootOverrideThatIsNotACheckoutFallsBackToTheWalk()
    {
        var (repo, bin) = MakeCheckout("staf");
        var notARepo = Path.Combine(_root, "elsewhere");
        Directory.CreateDirectory(notARepo);
        Assert.Equal(repo, ShellConfig.ResolveDevRepoRoot(
            name => name == "STREAMCURVES_REPO_ROOT" ? notARepo : null, bin));
    }

    [Fact]
    public void ACheckoutWithoutTheStreamCurvesAppIsNotDevMode()
    {
        // apps\ + desktop\ alone was the four-app shell's test; StreamCurves needs its app.
        var (repo, bin) = MakeCheckout("staf-without-streamcurves", withStreamCurves: false);
        Assert.False(ShellConfig.LooksLikeRepoRoot(repo));
        Assert.Null(ShellConfig.ResolveDevRepoRoot(_ => null, bin));
    }

    [Fact]
    public void ARepoRootAppPyLayoutIsNotDevMode()
    {
        // A single-app repo with app.py at its root (the HYPE layout) is not a STAF checkout.
        var repo = Path.Combine(_root, "single-app");
        Directory.CreateDirectory(Path.Combine(repo, "desktop"));
        File.WriteAllText(Path.Combine(repo, "app.py"), "app = None");
        Assert.False(ShellConfig.LooksLikeRepoRoot(repo));
    }

    [Fact]
    public void ThisTestRunSitsInsideARealCheckout()
    {
        var repo = ShellConfig.ResolveDevRepoRoot(_ => null, AppContext.BaseDirectory);
        Assert.NotNull(repo);
        Assert.True(File.Exists(Path.Combine(repo!, "apps", "stream-curves", "app.py")));
    }
}

public sealed class PayloadLocatorTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), "streamcurves-locator-" + Guid.NewGuid().ToString("N"));

    public void Dispose()
    {
        try
        {
            Directory.Delete(_root, recursive: true);
        }
        catch (IOException)
        {
        }
    }

    [Fact]
    public void InstalledLocator_ResolvesTheCommittedPayload_AndMarksItInstalled()
    {
        var config = new ShellConfig { DataRoot = Path.Combine(_root, "data"), SelfExePath = "x.exe" };
        config.EnsureDirectories();
        var state = new StreamCurves.Desktop.Core.Payload.LocalState(config.PayloadsDir);
        var envDir = state.DirFor("env-cp312-abcd1234");
        var appsDir = state.DirFor("apps-2026.09.23-abc1234");
        Directory.CreateDirectory(Path.Combine(envDir, "python"));
        File.WriteAllText(Path.Combine(envDir, "python", "python.exe"), "");
        Directory.CreateDirectory(Path.Combine(appsDir, "stream-curves"));
        Directory.CreateDirectory(Path.Combine(appsDir, "library"));
        File.WriteAllText(Path.Combine(appsDir, "desktop-manifest.json"), "{}");
        state.Commit(
            new StreamCurves.Desktop.Core.Payload.InstalledComponent("env-cp312-abcd1234", "env-cp312-abcd1234"),
            new StreamCurves.Desktop.Core.Payload.InstalledComponent("apps-2026.09.23-abc1234", "apps-2026.09.23-abc1234"),
            DateTimeOffset.UtcNow);

        var paths = new InstalledPayloadLocator(config).Resolve();

        Assert.True(paths.Installed);
        Assert.Equal(Path.Combine(envDir, "python", "python.exe"), paths.PythonExe);
        Assert.Equal(appsDir, paths.AppsRoot);
    }

    [Fact]
    public void InstalledLocator_ExplainsAMissingInstall()
    {
        var config = new ShellConfig { DataRoot = Path.Combine(_root, "empty"), SelfExePath = "x.exe" };
        var ex = Assert.Throws<ShellException>(() => new InstalledPayloadLocator(config).Resolve());
        Assert.Contains("StreamCurves runtime is not installed", ex.Message);
    }

    [Fact]
    public void DevLocator_RunsTheCheckoutsAppsTree_NotInstalled()
    {
        var repo = Path.Combine(_root, "staf");
        Directory.CreateDirectory(Path.Combine(repo, ".venv", "Scripts"));
        File.WriteAllText(Path.Combine(repo, ".venv", "Scripts", "python.exe"), "");
        var config = new ShellConfig { DataRoot = Path.Combine(_root, "data"), SelfExePath = "x.exe", DevRepoRoot = repo };

        var paths = new DevPayloadLocator(config).Resolve();

        Assert.False(paths.Installed);
        Assert.Equal(Path.Combine(repo, "apps"), paths.AppsRoot);
        Assert.Equal(Path.Combine(repo, "desktop", "dev", "dev-manifest.json"), paths.ManifestFile);
    }
}

public sealed class DownloadFoldersTests
{
    private static Func<string, bool> Existing(params string[] dirs) =>
        path => dirs.Contains(path, StringComparer.OrdinalIgnoreCase);

    [Fact]
    public void PrefersTheProjectsExportsFolder()
    {
        var exports = Path.Combine(@"D:\Projects\NEH", "exports");
        Assert.Equal(exports, DownloadFolders.InitialDirectory(
            @"D:\Projects\NEH", @"C:\Users\test\Downloads", Existing(@"D:\Projects\NEH", exports, @"C:\Users\test\Downloads")));
    }

    [Fact]
    public void FallsBackToTheProjectFolderWithoutAnExportsFolder()
    {
        Assert.Equal(@"D:\Projects\NEH", DownloadFolders.InitialDirectory(
            @"D:\Projects\NEH", @"C:\Users\test\Downloads", Existing(@"D:\Projects\NEH", @"C:\Users\test\Downloads")));
    }

    [Fact]
    public void FallsBackToTheLastFolderUsed()
    {
        // Project folder reported but gone (moved, unplugged drive), or none reported.
        Assert.Equal(@"C:\Users\test\Downloads", DownloadFolders.InitialDirectory(
            @"E:\Gone", @"C:\Users\test\Downloads", Existing(@"C:\Users\test\Downloads")));
        Assert.Equal(@"C:\Users\test\Downloads", DownloadFolders.InitialDirectory(
            null, @"C:\Users\test\Downloads", Existing(@"C:\Users\test\Downloads")));
    }

    [Fact]
    public void NothingKnown_LeavesItToWindows()
    {
        Assert.Null(DownloadFolders.InitialDirectory(null, null, Existing()));
        Assert.Null(DownloadFolders.InitialDirectory("  ", @"E:\Gone", Existing()));
    }
}
