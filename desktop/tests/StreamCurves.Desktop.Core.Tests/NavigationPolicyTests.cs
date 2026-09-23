using StreamCurves.Desktop.Core.Navigation;

namespace StreamCurves.Desktop.Core.Tests;

public sealed class NavigationPolicyTests
{
    private static readonly Dictionary<int, string> Ports = new()
    {
        [8100] = "streamcurves",
    };

    [Fact]
    public void SameOriginLoopback_IsAllowed()
    {
        var decision = NavigationPolicy.Decide("http://127.0.0.1:8100/session/abc/download/report.pdf", 8100, Ports);
        Assert.Equal(NavAction.Allow, decision.Action);
    }

    [Fact]
    public void LocalhostName_OnTheOwnPort_IsAllowed()
    {
        var decision = NavigationPolicy.Decide("http://localhost:8100/?step=3", 8100, Ports);
        Assert.Equal(NavAction.Allow, decision.Action);
    }

    [Fact]
    public void KnownLoopbackPort_WithoutAnOwnPort_RoutesToThatApp()
    {
        // The launcher page (no own port yet) following a link to the running app.
        var decision = NavigationPolicy.Decide("http://127.0.0.1:8100/?region=neh", ownPort: null, Ports);
        Assert.Equal(NavAction.OpenApp, decision.Action);
        Assert.Equal("streamcurves", decision.AppId);
        Assert.Equal("?region=neh", decision.Query);
    }

    [Fact]
    public void UnknownLoopbackPort_IsSuppressed()
    {
        var decision = NavigationPolicy.Decide("http://127.0.0.1:9999/", 8100, Ports);
        Assert.Equal(NavAction.Suppress, decision.Action);
    }

    [Theory]
    [InlineData("https://usace-wrises.github.io/staf/")]
    [InlineData("https://github.com/USACE-WRISES/staf")]
    [InlineData("https://basemap.nationalmap.gov/arcgis/rest/services")]
    [InlineData("http://example.com")]
    public void ExternalHttp_GoesToSystemBrowser(string url)
    {
        var decision = NavigationPolicy.Decide(url, 8100, Ports);
        Assert.Equal(NavAction.OpenExternal, decision.Action);
        Assert.Equal(new Uri(url).ToString(), decision.Url);
    }

    [Theory]
    [InlineData("file:///C:/Windows/system32/calc.exe")]
    [InlineData("ftp://example.com/x")]
    [InlineData("not a url")]
    // The four-app shell's cross-app scheme is not routed any more: like any unknown scheme,
    // it is cancelled instead of navigating the window or reaching the OS protocol handler.
    [InlineData("staf-desktop://home")]
    [InlineData("staf-desktop://app/deep/?assessment=x")]
    public void EverythingElse_IsSuppressed(string url)
    {
        var decision = NavigationPolicy.Decide(url, 8100, Ports);
        Assert.Equal(NavAction.Suppress, decision.Action);
    }

    [Theory]
    [InlineData("about:blank")]
    [InlineData("data:text/plain,hello")]
    public void InertSchemes_AreAllowed(string url)
    {
        Assert.Equal(NavAction.Allow, NavigationPolicy.Decide(url, 8100, Ports).Action);
    }
}
