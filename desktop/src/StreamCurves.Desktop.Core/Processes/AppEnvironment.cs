using StreamCurves.Desktop.Core.Manifest;

namespace StreamCurves.Desktop.Core.Processes;

/// <summary>
/// Builds the environment for the app server process. STREAMCURVES_DESKTOP tags the process as
/// shell-launched and STREAMCURVES_DATA_ROOT names the shell's data root; the app reads
/// HYRIVER_CACHE_NAME (via setdefault, so the parent wins); everything else here hardens the
/// bundled interpreter against the host machine (user site-packages, profile-root caches,
/// proxies, and a developer's library-publishing variables).
/// </summary>
public static class AppEnvironment
{
    /// <summary>
    /// The assessment-library variables StreamCurves reads (streamcurves/library.py). An INSTALLED
    /// copy strips them: the payload's library sits beside the app and is never a canonical
    /// publish target, so a value inherited from the machine (a developer who once exported
    /// STAF_LIBRARY_PUBLISH=1, a STAF_LIBRARY_ROOT pointing into a checkout) must not reach it.
    /// Dev mode passes them through, because canonical publishing is a checkout workflow.
    /// </summary>
    public static readonly IReadOnlyList<string> LibraryVariables =
    [
        "STAF_LIBRARY_ROOT",
        "STAF_LIBRARY_PUBLISH",
        "STAF_LIBRARY_MAINTAINER",
    ];

    public static Dictionary<string, string?> Build(
        ShellConfig config,
        PayloadPaths payload,
        AppDescriptor app,
        Func<string, string?>? getEnv = null,
        Func<Uri, Uri?>? proxyResolver = null)
    {
        getEnv ??= Environment.GetEnvironmentVariable;
        proxyResolver ??= ResolveSystemProxy;

        var pythonDir = Path.GetDirectoryName(payload.PythonExe)!;
        var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase)
        {
            ["STREAMCURVES_DESKTOP"] = "1",
            // Explicit so the app can never diverge from ShellConfig's resolution (both default
            // to %LOCALAPPDATA%\StreamCurves, but only one source of truth should decide).
            ["STREAMCURVES_DATA_ROOT"] = config.DataRoot,
            ["HYRIVER_CACHE_NAME"] = Path.Combine(config.CacheDir, "hyriver.sqlite"),
            ["MPLCONFIGDIR"] = Path.Combine(config.CacheDir, "matplotlib"),
            ["PYTHONDONTWRITEBYTECODE"] = "1",
            ["PYTHONNOUSERSITE"] = "1",
            ["PYTHONUTF8"] = "1",
            ["PATH"] = $"{pythonDir};{Path.Combine(pythonDir, "DLLs")};{getEnv("PATH")}",
        };

        if (payload.Installed)
        {
            // A null value removes the variable from the child (WindowsProcessRunner).
            foreach (var name in LibraryVariables)
            {
                env[name] = null;
            }
        }

        // The app's server-side USGS/EPA/NHD calls (requests/aiohttp) only honor proxies via env
        // vars. WebView2 traffic follows system settings automatically; without this, map tiles
        // would work while screening and delineation calls silently failed on proxied networks.
        if (string.IsNullOrEmpty(getEnv("HTTPS_PROXY")) && string.IsNullOrEmpty(getEnv("https_proxy")))
        {
            var probe = new Uri("https://api.water.usgs.gov/");
            var proxy = proxyResolver(probe);
            if (proxy is not null && proxy != probe)
            {
                var value = proxy.GetLeftPart(UriPartial.Authority);
                env["HTTPS_PROXY"] = value;
                env["HTTP_PROXY"] = value;
            }
        }

        return env;
    }

    private static Uri? ResolveSystemProxy(Uri target)
    {
        try
        {
            var proxy = System.Net.Http.HttpClient.DefaultProxy;
            if (proxy.IsBypassed(target))
            {
                return null;
            }
            return proxy.GetProxy(target);
        }
        catch (PlatformNotSupportedException)
        {
            return null;
        }
    }
}
