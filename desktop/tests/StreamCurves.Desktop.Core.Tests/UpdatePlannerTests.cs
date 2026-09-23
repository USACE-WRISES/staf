using StreamCurves.Desktop.Core;

namespace StreamCurves.Desktop.Core.Tests;

/// <summary>
/// The single-banner composition rules (UpdatePlanner) and every user-facing update
/// string. Two pending streams must yield ONE coherent banner (never last-writer-wins),
/// with labels that say what the click actually does.
/// </summary>
public sealed class UpdatePlannerTests
{
    [Fact]
    public void BothPending_ComposeTheCombinedOneRestartBanner()
    {
        var b = UpdatePlanner.Compose(payloadMb: 3, shellVersion: "1.0.3");

        Assert.NotNull(b);
        Assert.Equal(UpdateKind.Combined, b!.Kind);
        Assert.Equal("A StreamCurves update is ready: the app (3 MB) and the desktop shell (1.0.3).", b.Text);
        Assert.Equal("Update and restart", b.Button);
    }

    [Fact]
    public void ShellOnly_NamesTheDownloadHonestly()
    {
        var b = UpdatePlanner.Compose(payloadMb: null, shellVersion: "1.0.3");

        Assert.Equal(UpdateKind.Shell, b!.Kind);
        Assert.Equal("A new version of StreamCurves Desktop (1.0.3) is ready to download.", b.Text);
        Assert.Equal("Download and restart", b.Button);
    }

    [Fact]
    public void PayloadOnly_SaysItInstallsInPlace()
    {
        var b = UpdatePlanner.Compose(payloadMb: 5, shellVersion: null);

        Assert.Equal(UpdateKind.Payload, b!.Kind);
        Assert.Equal("A StreamCurves app update is ready (5 MB). It installs in this window.", b.Text);
        Assert.Equal("Install update", b.Button);
    }

    [Fact]
    public void ShellTooOld_RoutesThroughTheShellUpdateFirst()
    {
        // The manifest withholds the payload plan when the shell is too old, so the shell
        // update leads and the text promises the app follows after the restart.
        var b = UpdatePlanner.Compose(payloadMb: null, shellVersion: "2.0.0", shellTooOld: true);

        Assert.Equal(UpdateKind.Shell, b!.Kind);
        Assert.Equal(
            "A new version of StreamCurves Desktop (2.0.0) is ready to download. The app update installs after the restart.",
            b.Text);
        Assert.Equal("Download and restart", b.Button);
    }

    [Fact]
    public void NothingPending_NoBanner()
    {
        Assert.Null(UpdatePlanner.Compose(null, null));
    }

    [Fact]
    public void ShellTooOldWithoutAVisibleShellUpdate_StaysQuiet()
    {
        // Transient release-ordering skew (payload published before the shell tag):
        // the next check resolves it; no banner the user cannot act on.
        Assert.Null(UpdatePlanner.Compose(payloadMb: null, shellVersion: null, shellTooOld: true));
        Assert.Null(UpdatePlanner.Compose(payloadMb: 4, shellVersion: null, shellTooOld: true));
    }

    [Fact]
    public void NoBannerTextOrButtonCarriesAnEmDash()
    {
        // House style for user-visible copy: " - " or a rephrase, never U+2014.
        var emDash = ((char)0x2014).ToString();
        UpdateBanner?[] banners =
        [
            UpdatePlanner.Compose(payloadMb: 3, shellVersion: null),
            UpdatePlanner.Compose(payloadMb: 3, shellVersion: "1.0.1"),
            UpdatePlanner.Compose(payloadMb: null, shellVersion: "1.0.1"),
            UpdatePlanner.Compose(payloadMb: null, shellVersion: "1.0.1", shellTooOld: true),
            UpdatePlanner.Compose(payloadMb: 3, shellVersion: "1.0.1", shellTooOld: true),
        ];
        foreach (var banner in banners)
        {
            Assert.NotNull(banner);
            Assert.DoesNotContain(emDash, banner!.Text, StringComparison.Ordinal);
            Assert.DoesNotContain(emDash, banner.Button, StringComparison.Ordinal);
        }
    }
}
