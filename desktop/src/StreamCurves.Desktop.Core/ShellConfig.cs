namespace StreamCurves.Desktop.Core;

/// <summary>
/// Resolved filesystem layout and runtime mode for the shell. All shell data lives under
/// %LOCALAPPDATA%\StreamCurves (never Roaming — government roaming-profile quotas), separate from
/// the Velopack-managed application directory so shell updates never disturb payloads or caches.
/// </summary>
public sealed record ShellConfig
{
    public required string DataRoot { get; init; }
    public required string SelfExePath { get; init; }

    /// <summary>Repo root when running against a checkout's .venv instead of an installed payload.</summary>
    public string? DevRepoRoot { get; init; }

    public string PayloadsDir => Path.Combine(DataRoot, "payloads");
    public string DownloadsDir => Path.Combine(DataRoot, "downloads");
    public string CacheDir => Path.Combine(DataRoot, "cache");
    public string LogsDir => Path.Combine(DataRoot, "logs");
    public string WebViewDataDir => Path.Combine(DataRoot, "webview-data");
    public string TmpDir => Path.Combine(DataRoot, "tmp");
    public string StateFile => Path.Combine(DataRoot, "state.json");

    public bool IsDevMode => DevRepoRoot is not null;

    public static ShellConfig Create()
    {
        var dataRoot = Environment.GetEnvironmentVariable("STREAMCURVES_DATA_ROOT");
        if (string.IsNullOrWhiteSpace(dataRoot))
        {
            dataRoot = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StreamCurves");
        }

        return new ShellConfig
        {
            DataRoot = dataRoot,
            SelfExePath = Environment.ProcessPath
                ?? Path.Combine(AppContext.BaseDirectory, "StreamCurvesDesktop.exe"),
            DevRepoRoot = ResolveDevRepoRoot(Environment.GetEnvironmentVariable, AppContext.BaseDirectory),
        };
    }

    public void EnsureDirectories()
    {
        foreach (var dir in new[] { DataRoot, PayloadsDir, DownloadsDir, CacheDir, LogsDir, WebViewDataDir, TmpDir })
        {
            Directory.CreateDirectory(dir);
        }
    }

    /// <summary>
    /// Dev mode activates when the shell runs from inside a STAF checkout (the bin dir sits under
    /// desktop/) or when STREAMCURVES_REPO_ROOT points at one. STREAMCURVES_DESKTOP_DEV=0 forces
    /// it off. Parameterized over the environment and the start folder so tests can drive it.
    /// </summary>
    public static string? ResolveDevRepoRoot(Func<string, string?> getEnv, string startDirectory)
    {
        if (getEnv("STREAMCURVES_DESKTOP_DEV") == "0")
        {
            return null;
        }

        var explicitRoot = getEnv("STREAMCURVES_REPO_ROOT");
        if (!string.IsNullOrWhiteSpace(explicitRoot) && LooksLikeRepoRoot(explicitRoot))
        {
            return Path.GetFullPath(explicitRoot);
        }

        var dir = new DirectoryInfo(startDirectory);
        while (dir is not null)
        {
            if (LooksLikeRepoRoot(dir.FullName))
            {
                return dir.FullName;
            }
            dir = dir.Parent;
        }
        return null;
    }

    /// <summary>
    /// A STAF checkout that carries the StreamCurves app: apps\stream-curves\app.py AND desktop\.
    /// Requiring the app file (not just an apps\ folder) keeps a checkout without StreamCurves,
    /// or any other repo that happens to have those folder names, from switching dev mode on.
    /// </summary>
    public static bool LooksLikeRepoRoot(string path) =>
        File.Exists(Path.Combine(path, "apps", "stream-curves", "app.py"))
        && Directory.Exists(Path.Combine(path, "desktop"));
}
