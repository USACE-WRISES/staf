namespace StreamCurves.Desktop.Core;

/// <summary>
/// One-shot startup cleanup for portable installs. vpk 1.2.0 writes the portable zip's
/// launcher under the pack TITLE ("StreamCurves Desktop.exe") while the updater writes the
/// pack-ID name ("StreamCurvesDesktop.exe"), so a portable install's first update would leave
/// the title-named stub behind as a stale duplicate (see desktop/RELEASING.md, "Prerequisites
/// &amp; recovery"). The shell workflow normalizes the zip to the ID name; this removes the
/// leftover from any zip that slipped through un-normalized.
/// </summary>
public static class PortableJanitor
{
    // Historical filenames: the whole point is that these two can disagree.
    private const string IdLauncher = "StreamCurvesDesktop.exe";
    private const string StaleTitleLauncher = "StreamCurves Desktop.exe";

    /// <summary>
    /// Deletes a stale title-named launcher one level above <paramref name="appDir"/>
    /// (the running app's directory, i.e. the Velopack "current" dir). Acts only when
    /// this is a portable install (".portable" marker) AND the id-named launcher exists;
    /// the only launcher is never deleted. Returns true only when the stale file was
    /// actually removed; never throws.
    /// </summary>
    public static bool CleanStaleLauncher(string appDir, Action<string>? log = null)
    {
        try
        {
            var root = Directory.GetParent(Path.TrimEndingDirectorySeparator(appDir))?.FullName;
            if (root is null)
            {
                return false;
            }
            if (!File.Exists(Path.Combine(root, ".portable"))
                || !File.Exists(Path.Combine(root, IdLauncher)))
            {
                return false;
            }
            var stale = Path.Combine(root, StaleTitleLauncher);
            if (!File.Exists(stale))
            {
                return false;
            }
            File.Delete(stale);
            log?.Invoke($"[janitor] removed stale portable launcher: {stale}");
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Locked or protected (e.g. the user launched THROUGH the stale stub this very
            // session): leave it for the next start; the app must never fail over this.
            log?.Invoke($"[janitor] could not remove stale portable launcher: {ex.Message}");
            return false;
        }
    }
}
