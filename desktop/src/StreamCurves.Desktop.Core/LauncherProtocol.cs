using System.Text.Json;

namespace StreamCurves.Desktop.Core;

/// <summary>
/// The JSON contract between the shell and the pages it hosts (WebView2 postMessage).
/// Page → shell: small command messages. The launcher page sends { type } (ready, setupRetry,
/// installFromFile, openLogsFolder); the app page relays its desktop commands through
/// www/desktop_bridge.js: pickProjectOpen / pickProjectSave { purpose, fileName? },
/// pickFolder { purpose }, setTitle { title } and setProjectFolder { path }. Shell → page
/// messages are anonymous objects serialized in MainForm. Unknown JSON properties are
/// ignored, so the record can grow fields without breaking older payloads, and unknown
/// command types are ignored by the shell.
/// </summary>
public static class LauncherProtocol
{
    public sealed record Command(string Type, string? AppId = null, string? Purpose = null,
                                 string? FileName = null, string? Title = null, string? Path = null);

    public static Command? ParseCommand(string json)
    {
        try
        {
            var command = JsonSerializer.Deserialize<Command>(json, DesktopJson.Options);
            return command is { Type.Length: > 0 } ? command : null;
        }
        catch (JsonException)
        {
            return null;
        }
    }
}
