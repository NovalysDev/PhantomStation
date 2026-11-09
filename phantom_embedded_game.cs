using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;

class PhantomGameCrashException : Exception {
    public PhantomGameCrashException(string message) : base(message) {}
}

// Keep the launcher window alive while an independent game process is running.
class PhantomEmbeddedGame {
    [DllImport("user32.dll", SetLastError=true)] static extern IntPtr SetParent(IntPtr child, IntPtr parent);
    [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr child);
    [DllImport("user32.dll", EntryPoint="GetWindowLongW")] static extern int GetWindowLong(IntPtr window, int index);
    [DllImport("user32.dll", EntryPoint="SetWindowLongW")] static extern int SetWindowLong(IntPtr window, int index, int value);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int w, int h, uint flags);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr window, uint message, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr SetFocus(IntPtr window);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint current, uint target, bool attach);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();

    readonly Form owner;
    readonly List<Control> shown = new List<Control>();
    readonly Label status;
    Process running;
    IntPtr child;
    bool active, introduction, closeRequested;

    static void LogLaunch(string message) {
        try {
            string directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PhantomStation", "Journaux");
            Directory.CreateDirectory(directory);
            string path = Path.Combine(directory, "demarrage-jeux.log");
            if (File.Exists(path) && new FileInfo(path).Length > 1048576) return;
            File.AppendAllText(path, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + message + Environment.NewLine);
        } catch (IOException) {} catch (UnauthorizedAccessException) {}
    }

    public PhantomEmbeddedGame(Form form) {
        owner = form;
        status = new Label { Text = PhantomLanguage.Text("Chargement du jeu…"), AutoSize = true,
            BackColor = Color.FromArgb(4, 10, 22), ForeColor = Color.White,
            Font = new Font("Segoe UI", 11), Visible = false };
        owner.Controls.Add(status);
        owner.Resize += (s,e) => ResizeChild();
        owner.FormClosing += (s,e) => {
            if (!active) return;
            e.Cancel = true;
            closeRequested = true;
            if (!introduction && child != IntPtr.Zero) PostMessage(child, 0x0010, IntPtr.Zero, IntPtr.Zero);
            else if (!introduction && running != null && !running.HasExited) running.CloseMainWindow();
            // A stalled experimental engine may never process the close message.
            // Stop only the process started by this session after a grace period.
            if (!introduction && running != null) {
                var toStop = running;
                Task.Run(async () => {
                    await Task.Delay(3000);
                    try { if (!toStop.HasExited) toStop.Kill(); }
                    catch (InvalidOperationException) { }
                    catch (System.ComponentModel.Win32Exception) { }
                });
            }
        };
    }

    void ResizeChild() {
        if (child != IntPtr.Zero && owner.ClientSize.Width > 0 && owner.ClientSize.Height > 0)
            SetWindowPos(child, IntPtr.Zero, 0, 0, owner.ClientSize.Width, owner.ClientSize.Height, 0x0014);
        status.Location = new Point(Math.Max(0, (owner.ClientSize.Width-status.Width)/2), Math.Max(0, owner.ClientSize.Height-35));
    }

    void Begin() {
        active = true; closeRequested = false; shown.Clear();
        status.Text = PhantomLanguage.Text("Chargement du jeu…");
        foreach (Control control in owner.Controls) if (control != status && control.Visible) { shown.Add(control); control.Visible = false; }
        status.Visible = true; ResizeChild(); status.BringToFront();
    }

    void Restore() {
        child = IntPtr.Zero; running = null; introduction = false; active = false;
        if (owner.IsDisposed) return;
        status.Visible = false;
        foreach (Control control in shown) if (!control.IsDisposed) control.Visible = true;
        shown.Clear();
        if (!owner.Visible) owner.Show();
        owner.Activate(); SetForegroundWindow(owner.Handle);
    }

    async Task<bool> Attach(Process process) {
        var timer = Stopwatch.StartNew();
        while (!process.HasExited && timer.ElapsedMilliseconds < 15000) {
            process.Refresh();
            IntPtr candidate = process.MainWindowHandle;
            if (candidate != IntPtr.Zero) {
                int style = GetWindowLong(candidate, -16);
                SetWindowLong(candidate, -16, (style & unchecked((int)~0x80CF0000)) | 0x40000000 | 0x04000000);
                SetParent(candidate, owner.Handle);
                if (GetParent(candidate) == owner.Handle) {
                    child = candidate; status.Visible = false; ResizeChild();
                    SetWindowPos(child, IntPtr.Zero, 0, 0, owner.ClientSize.Width, owner.ClientSize.Height, 0x0054);
                    owner.Activate(); SetForegroundWindow(owner.Handle);
                    uint processId, targetThread = GetWindowThreadProcessId(child, out processId), currentThread = GetCurrentThreadId();
                    bool attachedInput = targetThread != 0 && targetThread != currentThread && AttachThreadInput(currentThread, targetThread, true);
                    try { SetFocus(child); } finally { if (attachedInput) AttachThreadInput(currentThread, targetThread, false); }
                    return true;
                }
                SetWindowLong(candidate, -16, style);
                return false;
            }
            await Task.Delay(100);
        }
        return false;
    }

    public async Task Run(ProcessStartInfo game, bool showIntro, bool fullscreen = false) {
        if (active) return;
        var previousBorder = owner.FormBorderStyle;
        var previousState = owner.WindowState;
        var previousBounds = owner.Bounds;
        if (fullscreen) { owner.FormBorderStyle = FormBorderStyle.None; owner.WindowState = FormWindowState.Maximized; }
        Begin();
        try {
            if (showIntro) {
                introduction = true;
                string args = "--intro " + owner.ClientSize.Width + " " + owner.ClientSize.Height + " window " + owner.Handle.ToInt64();
                var intro = new ProcessStartInfo(Application.ExecutablePath, args) { UseShellExecute = false };
                intro.EnvironmentVariables["PHANTOM_GAME_TITLE"] = UniqueLauncher.PreparedGameTitle;
                using (var animation = Process.Start(intro)) if (animation != null) await Task.Run(() => animation.WaitForExit());
                introduction = false;
            }
            game.RedirectStandardError = true;
            game.RedirectStandardOutput = true;
            for (int attempt = 1; attempt <= 2; attempt++) {
                var elapsed = Stopwatch.StartNew();
                LogLaunch(owner.Text + " : démarrage, essai " + attempt);
                using (running = Process.Start(game)) {
                    if (running == null) throw new InvalidOperationException("Le jeu n’a pas démarré.");
                    using (var journal = PhantomGameJournal.Session(attempt)) {
                    var errorStream = running.StandardError;
                    var errorOutput = Task.Run(() => journal.Drain(errorStream, true));
                    var standardOutput = running.StandardOutput;
                    var pumpOutput = Task.Run(() => journal.Drain(standardOutput, false));
                    bool attached = await Attach(running);
                    // Some graphics backends refuse Windows parenting. Keep just one
                    // visible window in that case, and restore the launcher on exit.
                    if (!attached && !running.HasExited) owner.Hide();
                    await Task.Run(() => running.WaitForExit());
                    await pumpOutput;
                    string error = (await errorOutput).Trim();
                    int exitCode = running.ExitCode;
                    LogLaunch(owner.Text + " : fin, code " + exitCode + ", durée " +
                              elapsed.ElapsedMilliseconds + " ms, fenêtre intégrée " + attached +
                              (error.Length == 0 ? "" : ", détail " + error.Substring(0, Math.Min(500, error.Length)).Replace('\r',' ').Replace('\n',' ')));
                    if (exitCode == 0 || closeRequested) break;
                    if (attempt == 1 && !attached && elapsed.ElapsedMilliseconds < 8000 && !closeRequested) {
                        child = IntPtr.Zero;
                        running = null;
                        if (!owner.Visible) owner.Show();
                        status.Text = PhantomLanguage.Text("Nouvel essai de lancement…");
                        status.Visible = true; status.BringToFront();
                        await Task.Delay(350);
                        if (closeRequested) break;
                        continue;
                    }
                    if (error.Length > 1500) error = error.Substring(error.Length - 1500);
                    throw new PhantomGameCrashException("Le jeu s’est arrêté (code 0x" + unchecked((uint)exitCode).ToString("X8") + ")." +
                        (error.Length == 0 ? "" : "\n\n" + error));
                    }
                }
            }
        } catch (Exception ex) {
            LogLaunch(owner.Text + " : erreur " + ex.GetType().Name + " : " + ex.Message.Replace('\r',' ').Replace('\n',' '));
            throw;
        } finally {
            if (fullscreen && !owner.IsDisposed) {
                owner.WindowState = FormWindowState.Normal; owner.FormBorderStyle = previousBorder;
                owner.Bounds = previousBounds; owner.WindowState = previousState;
            }
            Restore();
        }
    }
}
