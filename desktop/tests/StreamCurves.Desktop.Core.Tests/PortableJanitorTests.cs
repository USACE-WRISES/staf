using StreamCurves.Desktop.Core;

namespace StreamCurves.Desktop.Core.Tests;

/// <summary>
/// The stale-portable-launcher cleanup (PortableJanitor): delete the title-named
/// "StreamCurves Desktop.exe" that an un-normalized portable zip leaves behind after its first
/// update, and ONLY then. Layouts are built in temp dirs mirroring Velopack's portable root
/// (launcher + ".portable" marker + current\ with the running app).
/// </summary>
public sealed class PortableJanitorTests : IDisposable
{
    private readonly string _root;
    private readonly string _current;

    public PortableJanitorTests()
    {
        _root = Path.Combine(Path.GetTempPath(), "streamcurves-janitor-" + Guid.NewGuid().ToString("N"));
        _current = Path.Combine(_root, "current");
        Directory.CreateDirectory(_current);
    }

    public void Dispose() => Directory.Delete(_root, recursive: true);

    private void Touch(params string[] names)
    {
        foreach (var n in names)
        {
            File.WriteAllBytes(Path.Combine(_root, n), []);
        }
    }

    [Fact]
    public void DeletesTheStaleLauncherFromAPortableInstall()
    {
        Touch(".portable", "StreamCurvesDesktop.exe", "StreamCurves Desktop.exe");
        var messages = new List<string>();

        Assert.True(PortableJanitor.CleanStaleLauncher(_current, messages.Add));
        Assert.False(File.Exists(Path.Combine(_root, "StreamCurves Desktop.exe")));
        Assert.True(File.Exists(Path.Combine(_root, "StreamCurvesDesktop.exe")));   // untouched
        Assert.Contains(messages, m => m.Contains("removed stale portable launcher"));
    }

    [Fact]
    public void LeavesInstalledLayoutsAlone()
    {
        // No .portable marker = a Setup install; those never had the title-named file,
        // and if one somehow appears there it is not ours to judge.
        Touch("StreamCurvesDesktop.exe", "StreamCurves Desktop.exe");

        Assert.False(PortableJanitor.CleanStaleLauncher(_current));
        Assert.True(File.Exists(Path.Combine(_root, "StreamCurves Desktop.exe")));
    }

    [Fact]
    public void NeverDeletesTheOnlyLauncher()
    {
        // Pre-update portable install: the title-named stub is the ONLY launcher.
        Touch(".portable", "StreamCurves Desktop.exe");

        Assert.False(PortableJanitor.CleanStaleLauncher(_current));
        Assert.True(File.Exists(Path.Combine(_root, "StreamCurves Desktop.exe")));
    }

    [Fact]
    public void NoOpsWhenNothingIsStale()
    {
        Touch(".portable", "StreamCurvesDesktop.exe");

        Assert.False(PortableJanitor.CleanStaleLauncher(_current));
    }

    [Fact]
    public void NoOpsAtTheFilesystemRoot()
    {
        // Defensive: a caller handing us a drive root must not throw.
        Assert.False(PortableJanitor.CleanStaleLauncher(Path.GetPathRoot(_root)!));
    }
}
