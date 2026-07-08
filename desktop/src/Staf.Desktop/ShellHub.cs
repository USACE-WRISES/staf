using Staf.Desktop.Core;

namespace Staf.Desktop;

/// <summary>
/// What app windows need from the shell. Implemented by <see cref="LauncherForm"/>; all methods
/// are safe to call from any thread (implementations marshal to the UI thread).
/// </summary>
internal interface IShellHub
{
    ShellConfig Config { get; }

    /// <summary>Loopback port → app id for every currently running app (navigation policy input).</summary>
    IReadOnlyDictionary<int, string> GetPortMap();

    /// <summary>
    /// Launch the app if stopped, then open/focus its window. When <paramref name="deepLinkQuery"/>
    /// is set (the query string incl. leading '?' from a cross-app deep link), the window navigates
    /// to <c>http://127.0.0.1:&lt;port&gt;/&lt;query&gt;</c> so the app's URL-param handler runs.
    /// </summary>
    void RequestOpenApp(string appId, string? deepLinkQuery = null);

    void FocusLauncher();

    void OpenExternal(string url);

    /// <summary>An app window closed. When <paramref name="stopServer"/> the user closed it directly.</summary>
    void NotifyAppWindowClosed(string appId, bool stopServer);
}
