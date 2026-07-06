using System.Text.Json;
using Staf.Desktop.Core.Manifest;
using Staf.Desktop.Core.Processes;

namespace Staf.Desktop.Core;

/// <summary>
/// The JSON contract between the shell and the launcher page (WebView2 postMessage).
/// Shell → page: full snapshots only — no deltas, so the page can always render from scratch.
/// Page → shell: small command messages ({ type, appId? }).
/// </summary>
public static class LauncherProtocol
{
    public sealed record ShellInfo(string Version, string Mode, string DataRoot);

    public sealed record AppCard(
        string Id,
        string Name,
        string FullName,
        string Tier,
        int TierNum,
        string Description,
        string WebUrl,
        string Status,
        int? Port,
        string? Detail);

    public sealed record Snapshot(string Type, ShellInfo Shell, IReadOnlyList<AppCard> Apps);

    public sealed record Command(string Type, string? AppId);

    public static string BuildSnapshotJson(
        ShellInfo shell,
        IEnumerable<AppDescriptor> apps,
        Func<string, AppRuntimeState> stateOf)
    {
        var cards = apps.Select(app =>
        {
            var state = stateOf(app.Id);
            return new AppCard(
                app.Id,
                app.Name,
                app.FullName,
                app.Tier,
                app.TierNum,
                app.Description,
                app.WebUrl,
                state.Status.ToString().ToLowerInvariant(),
                state.Port,
                state.Detail);
        }).ToList();

        return JsonSerializer.Serialize(new Snapshot("snapshot", shell, cards), DesktopJson.Options);
    }

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
