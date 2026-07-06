using System.Text.Json;
using Staf.Desktop.Core.Manifest;

namespace Staf.Desktop.Core.Processes;

/// <summary>
/// Builds the environment for an app server process. The apps read only HYRIVER_* (via
/// setdefault — parent wins) and STAF_LINKS_OVERRIDES; everything else here hardens the bundled
/// interpreter against the host machine (user site-packages, profile-root caches, proxies).
/// </summary>
public static class AppEnvironment
{
    /// <summary>
    /// Cross-app links injected into every app's STAF_LINKS dict. The staf-desktop:// scheme never
    /// resolves anywhere — the shell's WebView2 navigation policy intercepts and routes it.
    /// </summary>
    public static readonly string LinksOverridesJson = JsonSerializer.Serialize(new Dictionary<string, string>
    {
        ["home"] = "staf-desktop://home",
        ["easi"] = "staf-desktop://app/easi",
        ["sfari"] = "staf-desktop://app/sfari",
        ["curves"] = "staf-desktop://app/curves",
        ["deep"] = "staf-desktop://app/deep",
    });

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
            ["STAF_DESKTOP"] = "1",
            ["STAF_LINKS_OVERRIDES"] = LinksOverridesJson,
            ["HYRIVER_CACHE_NAME"] = Path.Combine(config.CacheDir, $"{app.Id}_hyriver.sqlite"),
            ["MPLCONFIGDIR"] = Path.Combine(config.CacheDir, "matplotlib"),
            ["PYTHONDONTWRITEBYTECODE"] = "1",
            ["PYTHONNOUSERSITE"] = "1",
            ["PYTHONUTF8"] = "1",
            ["PATH"] = $"{pythonDir};{Path.Combine(pythonDir, "DLLs")};{getEnv("PATH")}",
        };

        // The apps' server-side USGS/EPA calls (requests/aiohttp) only honor proxies via env vars.
        // WebView2 traffic follows system settings automatically; without this, map tiles would
        // work while delineation silently failed on proxied networks.
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
