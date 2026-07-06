using System.ComponentModel;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Staf.Desktop.Core.Manifest;
using Staf.Desktop.Core.Navigation;

namespace Staf.Desktop;

/// <summary>
/// One window per running app: a WebView2 pointed at the app's loopback server. The window is
/// deliberately dumb — every navigation/new-window/download event defers to
/// <see cref="NavigationPolicy"/> or a save dialog, and closing it asks the hub to stop the server.
/// </summary>
internal sealed class AppWindowForm : Form
{
    private readonly IShellHub _hub;
    private readonly AppDescriptor _app;
    private readonly int _port;
    private readonly WebView2 _webView;

    /// <summary>Set before Close() when the shell is closing this window itself (stop command / shutdown).</summary>
    [Browsable(false)]
    [DesignerSerializationVisibility(DesignerSerializationVisibility.Hidden)]
    public bool SuppressStopOnClose { get; set; }

    public AppWindowForm(IShellHub hub, AppDescriptor app, int port, CoreWebView2Environment environment)
    {
        _hub = hub;
        _app = app;
        _port = port;

        Text = $"{app.Name} — STAF Desktop";
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(1280, 860);
        MinimumSize = new Size(720, 480);
        ShellIcon.Apply(this);

        _webView = new WebView2 { Dock = DockStyle.Fill };
        Controls.Add(_webView);

        Load += async (_, _) => await InitializeAsync(environment);
        FormClosed += (_, _) => _hub.NotifyAppWindowClosed(_app.Id, stopServer: !SuppressStopOnClose);
    }

    private async Task InitializeAsync(CoreWebView2Environment environment)
    {
        try
        {
            await _webView.EnsureCoreWebView2Async(environment);
        }
        catch (Exception ex) when (ex is WebView2RuntimeNotFoundException or InvalidOperationException or System.Runtime.InteropServices.COMException)
        {
            MessageBox.Show(this, $"Could not initialize the embedded browser:\n{ex.Message}",
                "STAF Desktop", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
            return;
        }

        var core = _webView.CoreWebView2;
        core.Settings.AreDevToolsEnabled = _hub.Config.IsDevMode;
        core.Settings.IsStatusBarEnabled = false;

        core.NewWindowRequested += OnNewWindowRequested;
        core.NavigationStarting += OnNavigationStarting;
        core.DownloadStarting += OnDownloadStarting;
        core.DocumentTitleChanged += (_, _) =>
        {
            var title = core.DocumentTitle;
            Text = string.IsNullOrWhiteSpace(title) ? $"{_app.Name} — STAF Desktop" : $"{title} — STAF Desktop";
        };

        core.Navigate($"http://127.0.0.1:{_port}/");
    }

    private void OnNewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs e)
    {
        e.Handled = true; // never spawn an uncontrolled WebView2 popup
        Apply(NavigationPolicy.Decide(e.Uri, _port, _hub.GetPortMap()));
    }

    private void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        var decision = NavigationPolicy.Decide(e.Uri, _port, _hub.GetPortMap());
        if (decision.Action == NavAction.Allow)
        {
            return;
        }
        e.Cancel = true;
        Apply(decision);
    }

    private void Apply(NavDecision decision)
    {
        switch (decision.Action)
        {
            case NavAction.OpenApp when decision.AppId is { } appId:
                _hub.RequestOpenApp(appId);
                break;
            case NavAction.FocusLauncher:
                _hub.FocusLauncher();
                break;
            case NavAction.OpenExternal when decision.Url is { } url:
                _hub.OpenExternal(url);
                break;
        }
    }

    /// <summary>
    /// Shiny's @render.download responses land here. A real save dialog replaces WebView2's
    /// default silent-download bar; cancel in the dialog cancels the download.
    /// </summary>
    private void OnDownloadStarting(object? sender, CoreWebView2DownloadStartingEventArgs e)
    {
        var deferral = e.GetDeferral();
        try
        {
            using var dialog = new SaveFileDialog
            {
                FileName = Path.GetFileName(e.ResultFilePath),
                Title = $"Save from {_app.Name}",
            };
            if (dialog.ShowDialog(this) == DialogResult.OK)
            {
                e.ResultFilePath = dialog.FileName;
                e.Handled = true; // suppress the default download UI
            }
            else
            {
                e.Cancel = true;
            }
        }
        finally
        {
            deferral.Complete();
        }
    }
}
