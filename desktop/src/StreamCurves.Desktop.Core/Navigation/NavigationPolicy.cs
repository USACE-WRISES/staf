namespace StreamCurves.Desktop.Core.Navigation;

public enum NavAction
{
    /// <summary>Let the WebView handle it (the app's own loopback origin, or about/blob/data).</summary>
    Allow,

    /// <summary>Cancel and bring the named app's window forward (single window: this one).</summary>
    OpenApp,

    /// <summary>Cancel and hand the URL to the system browser.</summary>
    OpenExternal,

    /// <summary>Cancel and do nothing.</summary>
    Suppress,
}

public sealed record NavDecision(NavAction Action, string? AppId = null, string? Url = null, string? Query = null);

/// <summary>
/// The one place that decides what any URL does inside the shell window. Pure logic: WebView2
/// event handlers translate their args into a call here and act on the answer. The app's own
/// loopback port stays in the window; every other http/https link opens in the system browser.
/// There is no custom URI scheme: StreamCurves Desktop hosts one app, so the cross-app
/// desktop links of the old four-app shell (staf-desktop://) are gone and, like any other
/// unknown scheme, are suppressed.
/// </summary>
public static class NavigationPolicy
{
    /// <param name="url">Target URL of the navigation or new-window request.</param>
    /// <param name="ownPort">Loopback port of the window's own app, if it is running.</param>
    /// <param name="portToAppId">Live map of loopback port to app id for running apps.</param>
    public static NavDecision Decide(string url, int? ownPort, IReadOnlyDictionary<int, string> portToAppId)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri))
        {
            return new NavDecision(NavAction.Suppress);
        }

        if (uri.Scheme is "http" or "https")
        {
            if (uri.IsLoopback)
            {
                if (ownPort is { } own && uri.Port == own)
                {
                    return new NavDecision(NavAction.Allow);
                }
                if (portToAppId.TryGetValue(uri.Port, out var appId))
                {
                    return new NavDecision(NavAction.OpenApp, AppId: appId, Query: NormalizeQuery(uri.Query));
                }
                // Unknown local server: likely a stale port from a previous session. Don't
                // navigate the app window away; don't open a browser to a dead port either.
                return new NavDecision(NavAction.Suppress);
            }
            return new NavDecision(NavAction.OpenExternal, Url: uri.ToString());
        }

        if (uri.Scheme is "about" or "blob" or "data")
        {
            return new NavDecision(NavAction.Allow);
        }

        return new NavDecision(NavAction.Suppress);
    }

    /// <summary>The query (including the leading '?') carried by a loopback link, or null.</summary>
    private static string? NormalizeQuery(string query) =>
        string.IsNullOrEmpty(query) || query == "?" ? null : query;
}
