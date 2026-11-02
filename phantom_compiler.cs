using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class PhantomCompiler {
    [STAThread]
    private static void Main() {
        string directory = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(directory, "lanceur.py");
        if (!File.Exists(script)) {
            MessageBox.Show("Le fichier lanceur.py est introuvable à côté de PhantomStation.exe.",
                            "PhantomStation", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        string portable = Path.Combine(directory, @"runtime-python\pythonw.exe");
        string bundled = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            @".cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe");
        string python = File.Exists(portable) ? portable : (File.Exists(bundled) ? bundled : "pythonw.exe");
        try {
            Process.Start(new ProcessStartInfo(python, "\"" + script + "\"") {
                WorkingDirectory = directory, UseShellExecute = false, CreateNoWindow = true
            });
        } catch (Exception ex) {
            MessageBox.Show("Impossible d'ouvrir PhantomStation. Python avec Tkinter et Pillow est nécessaire.\n\n" + ex.Message,
                            "PhantomStation", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
