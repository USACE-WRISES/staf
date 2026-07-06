using System.Net;
using System.Net.Sockets;
using Staf.Desktop.Core;
using Staf.Desktop.Core.Logging;
using Staf.Desktop.Core.Manifest;
using Staf.Desktop.Core.Processes;

namespace Staf.Desktop.Core.Tests;

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
          "version": "apps-2026.07.06-abc1234",
          "builtFromCommit": "abc1234",
          "requiresEnv": "env-cp312-deadbeef",
          "apps": [
            { "id": "easi", "dir": "easi", "entry": "app.py", "name": "EASI",
              "fullName": "Ecosystem Assessment Screening Index", "tier": "Screening", "tierNum": 1 },
            { "id": "curves", "dir": "stream-curves", "entry": "app.py", "name": "stream-curves" }
          ]
        }
        """;

    [Fact]
    public void ParsesValidManifest()
    {
        var manifest = DesktopManifest.Parse(Valid);
        Assert.Equal(2, manifest.Apps.Count);
        Assert.Equal("stream-curves", manifest.Apps[1].Dir);
        Assert.Equal("env-cp312-deadbeef", manifest.RequiresEnv);
        Assert.Equal(1, manifest.Apps[0].TierNum);
    }

    [Fact]
    public void RejectsUnknownSchemaVersion()
    {
        var ex = Assert.Throws<ShellException>(() => DesktopManifest.Parse(Valid.Replace("\"schemaVersion\": 1", "\"schemaVersion\": 2")));
        Assert.Contains("schema version 2", ex.Message);
    }

    [Fact]
    public void RejectsDuplicateAppIds()
    {
        var json = Valid.Replace("\"id\": \"curves\"", "\"id\": \"EASI\"");
        Assert.Throws<ShellException>(() => DesktopManifest.Parse(json));
    }

    [Theory]
    [InlineData("..")]
    [InlineData("easi/../../etc")]
    [InlineData("C:\\evil")]
    [InlineData("")]
    public void RejectsUnsafeDirs(string dir)
    {
        var json = Valid.Replace("\"dir\": \"stream-curves\"", $"\"dir\": {System.Text.Json.JsonSerializer.Serialize(dir)}");
        Assert.Throws<ShellException>(() => DesktopManifest.Parse(json));
    }

    [Fact]
    public void RejectsGarbage()
    {
        Assert.Throws<ShellException>(() => DesktopManifest.Parse("not json"));
        Assert.Throws<ShellException>(() => DesktopManifest.Parse("null"));
    }
}

public sealed class RollingLogWriterTests
{
    [Fact]
    public void RotatesWhenMaxSizeExceeded()
    {
        var dir = Path.Combine(Path.GetTempPath(), "staf-desktop-tests", Guid.NewGuid().ToString("N"));
        var path = Path.Combine(dir, "app.log");
        using (var log = new RollingLogWriter(path, maxBytes: 300, keep: 3))
        {
            for (var i = 0; i < 30; i++)
            {
                log.WriteLine($"line {i} — padding padding padding");
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
        var dir = Path.Combine(Path.GetTempPath(), "staf-desktop-tests", Guid.NewGuid().ToString("N"));
        var path = Path.Combine(dir, "shell.log");
        using (var log = new RollingLogWriter(path))
        {
            log.WriteLine("shell line");
            // A sibling handle (--stop-helper, a log viewer opened for read/write) must not be
            // locked out while the shell holds the log — with FileShare.Read this open threw.
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
        var path = Path.Combine(Path.GetTempPath(), "staf-desktop-tests", Guid.NewGuid().ToString("N"), "state.json");
        var store = new StateStore(path);
        Assert.False(store.HasStartedOk("easi"));

        store.MarkStartedOk("easi");
        var reloaded = new StateStore(path);
        Assert.True(reloaded.HasStartedOk("easi"));
        Assert.False(reloaded.HasStartedOk("sfari"));
        Directory.Delete(Path.GetDirectoryName(path)!, recursive: true);
    }

    [Fact]
    public void CorruptFileResetsToEmpty()
    {
        var dir = Path.Combine(Path.GetTempPath(), "staf-desktop-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        var path = Path.Combine(dir, "state.json");
        File.WriteAllText(path, "{{{ definitely not json");

        var store = new StateStore(path);
        Assert.False(store.HasStartedOk("easi"));
        Directory.Delete(dir, recursive: true);
    }
}

public sealed class AppEnvironmentTests
{
    private static readonly ShellConfig Config = new()
    {
        DataRoot = @"C:\Users\test\AppData\Local\STAF",
        SelfExePath = @"C:\apps\StafDesktop.exe",
    };

    private static readonly PayloadPaths Payload = new(
        PythonExe: @"C:\Users\test\AppData\Local\STAF\payloads\env-x\python\python.exe",
        AppsRoot: @"C:\Users\test\AppData\Local\STAF\payloads\apps-x",
        ManifestFile: @"C:\Users\test\AppData\Local\STAF\payloads\apps-x\desktop-manifest.json");

    private static readonly AppDescriptor App = new()
    {
        Id = "sfari", Dir = "sfari", Entry = "app.py", Name = "SFARI",
    };

    [Fact]
    public void SetsCacheAndIsolationVariables()
    {
        var env = AppEnvironment.Build(Config, Payload, App, getEnv: _ => null, proxyResolver: _ => null);

        Assert.Equal(Path.Combine(Config.CacheDir, "sfari_hyriver.sqlite"), env["HYRIVER_CACHE_NAME"]);
        Assert.Equal(Path.Combine(Config.CacheDir, "matplotlib"), env["MPLCONFIGDIR"]);
        Assert.Equal("1", env["PYTHONDONTWRITEBYTECODE"]);
        Assert.Equal("1", env["PYTHONNOUSERSITE"]);
        Assert.Equal("1", env["STAF_DESKTOP"]);
        Assert.StartsWith(@"C:\Users\test\AppData\Local\STAF\payloads\env-x\python;", env["PATH"]);
        Assert.Contains("staf-desktop://app/easi", env["STAF_LINKS_OVERRIDES"]);
    }

    [Fact]
    public void InjectsSystemProxy_OnlyWhenUnsetAndDistinct()
    {
        var proxied = AppEnvironment.Build(Config, Payload, App,
            getEnv: _ => null,
            proxyResolver: _ => new Uri("http://proxy.corp:8080"));
        Assert.Equal("http://proxy.corp:8080", proxied["HTTPS_PROXY"]);
        Assert.Equal("http://proxy.corp:8080", proxied["HTTP_PROXY"]);

        var alreadySet = AppEnvironment.Build(Config, Payload, App,
            getEnv: name => name is "HTTPS_PROXY" ? "http://existing:1" : null,
            proxyResolver: _ => new Uri("http://proxy.corp:8080"));
        Assert.False(alreadySet.ContainsKey("HTTPS_PROXY"));

        // IWebProxy convention: GetProxy returns the destination itself when direct.
        var direct = AppEnvironment.Build(Config, Payload, App,
            getEnv: _ => null,
            proxyResolver: uri => uri);
        Assert.False(direct.ContainsKey("HTTPS_PROXY"));
    }
}
