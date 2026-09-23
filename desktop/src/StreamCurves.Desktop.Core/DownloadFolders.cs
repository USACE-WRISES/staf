namespace StreamCurves.Desktop.Core;

/// <summary>
/// Where the native "Save from StreamCurves" dialog opens for a download from the app. The
/// app reports its open project folder (setProjectFolder); exports land in that project's
/// exports\ folder when it exists, else in the project folder itself, else wherever the user
/// saved last. Null leaves the choice to Windows.
/// </summary>
public static class DownloadFolders
{
    public static string? InitialDirectory(
        string? projectFolder,
        string? lastUsedFolder,
        Func<string, bool>? directoryExists = null)
    {
        directoryExists ??= Directory.Exists;
        if (!string.IsNullOrWhiteSpace(projectFolder))
        {
            var exports = Path.Combine(projectFolder, "exports");
            if (directoryExists(exports))
            {
                return exports;
            }
            if (directoryExists(projectFolder))
            {
                return projectFolder;
            }
        }
        return !string.IsNullOrWhiteSpace(lastUsedFolder) && directoryExists(lastUsedFolder)
            ? lastUsedFolder
            : null;
    }
}
