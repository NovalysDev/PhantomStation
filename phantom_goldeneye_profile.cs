using System;
using System.IO;

// GoldenEye's standalone port keeps its EEPROM beside its data instead of
// accepting a save-directory argument. Give each profile its own game data.
internal static class PhantomGoldenEyeProfile {
    static void CopyAsset(string source, string target) {
        var info = new FileInfo(source);
        if ((info.Attributes & FileAttributes.ReparsePoint) != 0) throw new IOException("Lien dans les données GoldenEye.");
        if (File.Exists(target)) {
            var current = new FileInfo(target);
            if ((current.Attributes & FileAttributes.ReparsePoint) != 0) throw new IOException("Lien dans le profil GoldenEye.");
            if (current.Length == info.Length && current.LastWriteTimeUtc == info.LastWriteTimeUtc) return;
        }
        Directory.CreateDirectory(Path.GetDirectoryName(target));
        string temporary = target + "." + Guid.NewGuid().ToString("N") + ".part";
        try {
            File.Copy(source, temporary, false);
            File.SetLastWriteTimeUtc(temporary, info.LastWriteTimeUtc);
            if (File.Exists(target)) File.Replace(temporary, target, null);
            else File.Move(temporary, target);
        } finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    static void CopyData(string source, string target) {
        if ((File.GetAttributes(source) & FileAttributes.ReparsePoint) != 0) throw new IOException("Lien dans les données GoldenEye.");
        if (Directory.Exists(target) && (File.GetAttributes(target) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("Lien dans le profil GoldenEye.");
        Directory.CreateDirectory(target);
        foreach (string file in Directory.GetFiles(source)) {
            string ext = Path.GetExtension(file);
            // The profile owns all saves and configuration; never copy them
            // from the existing game or replace them during a later update.
            string name = Path.GetFileName(file);
            if (ext.Equals(".eep", StringComparison.OrdinalIgnoreCase) || ext.Equals(".ini", StringComparison.OrdinalIgnoreCase) ||
                ext.Equals(".sav", StringComparison.OrdinalIgnoreCase) || ext.Equals(".bak", StringComparison.OrdinalIgnoreCase) ||
                name.IndexOf(".eep.", StringComparison.OrdinalIgnoreCase) >= 0) continue;
            CopyAsset(file, Path.Combine(target, Path.GetFileName(file)));
        }
        foreach (string dir in Directory.GetDirectories(source))
            CopyData(dir, Path.Combine(target, Path.GetFileName(dir)));
    }

    public static string Prepare(string executable, string profile) {
        string sourceRoot = Path.GetDirectoryName(executable);
        string targetRoot = Path.Combine(profile, "portage-goldeneye");
        Directory.CreateDirectory(targetRoot);
        CopyAsset(executable, Path.Combine(targetRoot, Path.GetFileName(executable)));
        foreach (string dll in Directory.GetFiles(sourceRoot, "*.dll"))
            CopyAsset(dll, Path.Combine(targetRoot, Path.GetFileName(dll)));
        CopyData(Path.Combine(sourceRoot, "data"), Path.Combine(targetRoot, "data"));
        return Path.Combine(targetRoot, Path.GetFileName(executable));
    }
}
