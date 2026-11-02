using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Compression;
using System.Media;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

// The appended ZIP contains the game files. The per-package cache retains
// runtime settings; default saves are stored in the user's Documents folder.
class UniqueLauncher : Form {
    public static string PackagePath {
        get {
            string external = Environment.GetEnvironmentVariable("PHANTOM_PACKAGE_PATH");
            return String.IsNullOrWhiteSpace(external) ? Application.ExecutablePath : Path.GetFullPath(external);
        }
    }
    readonly Image art = Image.FromStream(Assembly.GetExecutingAssembly().GetManifestResourceStream("art"));
    readonly Image logo = Image.FromStream(Assembly.GetExecutingAssembly().GetManifestResourceStream("logo"));
    readonly SoundPlayer sound = new SoundPlayer(Assembly.GetExecutingAssembly().GetManifestResourceStream("sound"));
    readonly Stopwatch clock = Stopwatch.StartNew();
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    readonly Label status = new Label();
    readonly string gameTitle;
    readonly IntPtr parentWindow;
    public static string PreparedGameTitle;
    public static bool PreparedDocumentsSaves;
    bool started;

    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr window);
    [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr window, out NativeRect rect);
    [DllImport("user32.dll", SetLastError=true)] static extern IntPtr SetParent(IntPtr child, IntPtr parent);
    [DllImport("user32.dll", EntryPoint="GetWindowLongW")] static extern int GetWindowStyle(IntPtr window, int index);
    [DllImport("user32.dll", EntryPoint="SetWindowLongW")] static extern int SetWindowStyle(IntPtr window, int index, int value);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
    [StructLayout(LayoutKind.Sequential)] struct NativeRect { public int Left, Top, Right, Bottom; }

    UniqueLauncher(int width, int height, bool fullScreen, IntPtr parent) {
        parentWindow = parent != IntPtr.Zero && IsWindow(parent) ? parent : IntPtr.Zero;
        gameTitle = DisplayTitle(Environment.GetEnvironmentVariable("PHANTOM_GAME_TITLE"));
        Text = "PhantomStation"; ClientSize = new Size(width, height); StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(3,8,20); DoubleBuffered = true; FormBorderStyle = FormBorderStyle.FixedSingle; MaximizeBox = false;
        ControlBox = false;
        FormClosing += (s,e) => { if (!started && e.CloseReason == CloseReason.UserClosing) e.Cancel = true; };
        if (parentWindow != IntPtr.Zero) {
            FormBorderStyle = FormBorderStyle.None; StartPosition = FormStartPosition.Manual;
            Location = Point.Empty; ShowInTaskbar = false; SyncParentSize();
        } else {
            if (fullScreen) { FormBorderStyle = FormBorderStyle.None; WindowState = FormWindowState.Maximized; }
            TopMost = true; ShowInTaskbar = true;
        }
        status.Text = "Chargement du jeu…"; status.BackColor = Color.FromArgb(2,5,14); status.ForeColor = Color.LightCyan;
        status.AutoSize = true; Controls.Add(status);
        Resize += (s,e) => { status.Location = new Point(Math.Max(0, (ClientSize.Width-status.Width)/2), Math.Max(0,ClientSize.Height-45)); };
        status.Location = new Point(Math.Max(0,(ClientSize.Width-status.PreferredWidth)/2), Math.Max(0,ClientSize.Height-45));
        Shown += (s,e) => { EmbedInParent(); BringToFront(); Activate(); SetForegroundWindow(parentWindow != IntPtr.Zero ? parentWindow : Handle); clock.Restart(); try { sound.Play(); } catch {} timer.Start(); };
        timer.Interval = 30; timer.Tick += Tick; FormClosed += (s,e) => { timer.Stop(); sound.Stop(); };
    }
    void EmbedInParent() {
        if (parentWindow == IntPtr.Zero) return;
        int style = GetWindowStyle(Handle, -16);
        SetWindowStyle(Handle, -16, (style | 0x40000000) & unchecked((int)0x7fffffff));
        SetParent(Handle, parentWindow);
        NativeRect rect;
        if (GetClientRect(parentWindow, out rect))
            SetWindowPos(Handle, IntPtr.Zero, 0, 0, rect.Right - rect.Left, rect.Bottom - rect.Top, 0x0024);
    }
    protected override CreateParams CreateParams {
        get {
            var cp = base.CreateParams;
            if (parentWindow != IntPtr.Zero) {
                cp.Parent = parentWindow;
                cp.Style = (cp.Style | 0x40000000) & unchecked((int)0x7fffffff); // WS_CHILD, no WS_POPUP
                cp.ExStyle &= ~0x00040000; // no separate taskbar entry
                cp.X = 0; cp.Y = 0;
            }
            return cp;
        }
    }
    void SyncParentSize() {
        NativeRect rect;
        if (parentWindow != IntPtr.Zero && GetClientRect(parentWindow, out rect)) {
            int width = Math.Max(1, rect.Right - rect.Left), height = Math.Max(1, rect.Bottom - rect.Top);
            if (ClientSize.Width != width || ClientSize.Height != height) ClientSize = new Size(width, height);
        }
    }
    public static string DisplayTitle(string value) {
        string title = value ?? "";
        string previous;
        do {
            previous = title;
            title = System.Text.RegularExpressions.Regex.Replace(title, @"\([^()]*\)", " ");
        } while (title != previous);
        title = System.Text.RegularExpressions.Regex.Replace(title, @"\s+", " ").Trim();
        title = System.Text.RegularExpressions.Regex.Replace(title, @"^(Parasite\s+Eve\s+II)\s+France$", "$1", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
        return title.Length == 0 ? "Jeu PS1" : title;
    }
    void Tick(object sender, EventArgs e) {
        SyncParentSize();
        if (clock.Elapsed.TotalSeconds >= 7.7) status.Visible = false;
        Invalidate();
        if (started) return;
        if (clock.Elapsed.TotalSeconds < 8.4) return;
        started = true; timer.Stop(); sound.Stop();
        Close();
    }
    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e); var g = e.Graphics; g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        float t = (float)clock.Elapsed.TotalSeconds;
        float scale = Math.Max(ClientSize.Width/(float)art.Width, ClientSize.Height/(float)art.Height);
        float w = art.Width*scale, h = art.Height*scale;
        g.DrawImage(art, (ClientSize.Width-w)/2, -(h-ClientSize.Height)*.42f, w, h);
        int gradientTop = ClientSize.Height * 45 / 100;
        using (var brush = new LinearGradientBrush(new Rectangle(0,gradientTop,ClientSize.Width,ClientSize.Height-gradientTop), Color.FromArgb(10,2,5,14), Color.FromArgb(235,2,5,14), 90))
            g.FillRectangle(brush,0,gradientTop,ClientSize.Width,ClientSize.Height-gradientTop);
        float layoutScale = Math.Min(ClientSize.Width / 1100f, ClientSize.Height / 650f);
        g.TranslateTransform((ClientSize.Width-1100*layoutScale)/2, (ClientSize.Height-650*layoutScale)/2);
        g.ScaleTransform(layoutScale, layoutScale);
        // Reveal the wordmark later in the startup sound, after the logo.
        float alpha = Math.Min(1,t/2f); var matrix = new System.Drawing.Imaging.ColorMatrix(); matrix.Matrix33 = alpha;
        using (var attrs = new System.Drawing.Imaging.ImageAttributes()) {
            attrs.SetColorMatrix(matrix);
            float logoScale = Math.Min(440f / logo.Width, 175f / logo.Height);
            int logoWidth = Math.Max(1, (int)(logo.Width * logoScale));
            int logoHeight = Math.Max(1, (int)(logo.Height * logoScale));
            g.DrawImage(logo, new Rectangle(550 - logoWidth / 2, 224 - logoHeight / 2, logoWidth, logoHeight),
                0,0,logo.Width,logo.Height,GraphicsUnit.Pixel,attrs);
        }
        using (var sf = new StringFormat { Alignment=StringAlignment.Center }) {
            int wordAlpha = (int)(255 * Math.Max(0, Math.Min(1, (t-3.5f)/1.2f)));
            using (var wordBrush = new SolidBrush(Color.FromArgb(wordAlpha,243,244,250)))
            {
                float size = 36;
                using (var f = new Font("Segoe UI", size, FontStyle.Bold)) {
                    float width = g.MeasureString(gameTitle, f).Width;
                    if (width > 1000) size = Math.Max(14, size * 1000 / width);
                }
                using (var f = new Font("Segoe UI", size, FontStyle.Bold))
                using (var wordFormat = new StringFormat { Alignment=StringAlignment.Center, FormatFlags=StringFormatFlags.NoWrap, Trimming=StringTrimming.EllipsisCharacter })
                    g.DrawString(gameTitle,f,wordBrush,new RectangleF(50,320,1000,66),wordFormat);
            }
            using (var f = new Font("Segoe UI",11,FontStyle.Bold)) g.DrawString("B I E N V E N U E   D A N S",f,Brushes.LightCyan,new RectangleF(0,454,1100,25),sf);
            using (var f = new Font("Segoe UI",34,FontStyle.Bold)) g.DrawString("PHANTOMSTATION",f,Brushes.White,new RectangleF(0,490,1100,58),sf);
            using (var p = new Pen(Color.FromArgb(149,244,228))) g.DrawLine(p,375,544,725,544);
            using (var f = new Font("Segoe UI",9)) g.DrawString("U N E   N O U V E L L E   V I E   P O U R   T E S   J E U X",f,Brushes.LightCyan,new RectangleF(0,559,1100,22),sf);
            using (var f = new Font("Segoe UI",10)) g.DrawString("DEV BY NOVALYS",f,Brushes.Aquamarine,new RectangleF(0,581,1100,22),sf);
        }
        // Fade the whole intro to black without fading the game window itself.
        float fade = Math.Max(0, Math.Min(1, (t - 7.7f) / 0.7f));
        if (fade > 0) {
            g.ResetTransform();
            using (var shade = new SolidBrush(Color.FromArgb((int)(255 * fade), 0, 0, 0)))
                g.FillRectangle(shade, ClientRectangle);
        }
    }
    static void CopySaveTree(string source, string destination) {
        foreach (string file in Directory.GetFiles(source)) {
            if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Lien dans les anciennes sauvegardes : " + file);
            string target = Path.Combine(destination, Path.GetFileName(file));
            if (!File.Exists(target)) File.Copy(file, target, false);
        }
        foreach (string sub in Directory.GetDirectories(source)) {
            if ((File.GetAttributes(sub) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Lien dans les anciennes sauvegardes : " + sub);
            string target = Path.Combine(destination, Path.GetFileName(sub));
            Directory.CreateDirectory(target);
            CopySaveTree(sub, target);
        }
    }
    public static string PrepareSaveDirectory(string game) {
        return PrepareSaveDirectory(game, Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments));
    }
    public static string PrepareSaveDirectory(string game, string documents) {
        string gameDir = Path.GetDirectoryName(game);
        string id = Path.GetFileName(gameDir);
        Guid parsed;
        if (!Guid.TryParseExact(id, "N", out parsed)) throw new Exception("Identité du jeu invalide pour les sauvegardes.");
        if (String.IsNullOrWhiteSpace(documents)) throw new IOException("Dossier Documents introuvable.");
        string name = Path.GetFileNameWithoutExtension(game).Replace('_', ' ').Trim();
        if (name.Length > 60) name = name.Substring(0, 60).Trim();
        if (name.Length == 0) name = "Jeu PS1";
        string destination = Path.Combine(documents, "PhantomStation", "Sauvegardes", name + " - " + id);
        using (var mutex = new Mutex(false, "Local\\PhantomStation-" + id)) {
            mutex.WaitOne();
            try {
                string savesRoot = Path.Combine(documents, "PhantomStation", "Sauvegardes");
                Directory.CreateDirectory(savesRoot);
                string[] existing = Directory.GetDirectories(savesRoot, "* - " + id);
                if (existing.Length > 1) throw new IOException("Plusieurs dossiers de sauvegarde correspondent à ce jeu.");
                if (existing.Length == 1) {
                    if ((File.GetAttributes(existing[0]) & FileAttributes.ReparsePoint) != 0)
                        throw new IOException("Le dossier des sauvegardes est un lien.");
                    destination = existing[0];
                }
                Directory.CreateDirectory(destination);
                string marker = Path.Combine(destination, ".migration-phantomstation");
                if (!File.Exists(marker)) {
                    string old = Path.Combine(gameDir, "saves");
                    if (Directory.Exists(old)) {
                        if ((File.GetAttributes(old) & FileAttributes.ReparsePoint) != 0)
                            throw new IOException("Lien dans les anciennes sauvegardes : " + old);
                        CopySaveTree(old, destination);
                    }
                    File.WriteAllText(marker, "Migration terminée ; fichiers d'origine conservés.");
                }
            } finally { mutex.ReleaseMutex(); }
        }
        return destination;
    }
    public static string Prepare() {
        using (var archive = ZipFile.OpenRead(PackagePath)) {
            var manifest = archive.GetEntry("phantom-package.txt");
            if (manifest == null) throw new Exception("Archive du jeu absente.");
            string[] lines; using (var r = new StreamReader(manifest.Open(), Encoding.UTF8)) lines = r.ReadToEnd().Split('\n');
            if (lines.Length < 2) throw new Exception("Archive incomplète.");
            var id = lines[0].Trim(); var exe = lines[1].Trim();
            PreparedGameTitle = Path.GetFileNameWithoutExtension(PackagePath);
            if (lines.Length > 3 && lines[3].Trim().Length > 0) {
                try { PreparedGameTitle = Encoding.UTF8.GetString(Convert.FromBase64String(lines[3].Trim())); }
                catch (FormatException) { throw new Exception("Nom du jeu invalide dans l'archive."); }
            }
            PreparedDocumentsSaves = lines.Length > 4 && lines[4].Trim() == "documents-saves-v1";
            var expectedHash = lines.Length > 2 ? lines[2].Trim() : "";
            Guid parsed; if (!Guid.TryParseExact(id, "N", out parsed) || Path.GetFileName(exe) != exe || (expectedHash.Length != 0 && (expectedHash.Length != 64 || !System.Text.RegularExpressions.Regex.IsMatch(expectedHash, "^[0-9a-f]+$")))) throw new Exception("Identité du jeu invalide.");
            string titleFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PhantomStation", "Titres", id + ".txt");
            if (File.Exists(titleFile)) {
                var info = new FileInfo(titleFile);
                if (info.Length <= 1024) {
                    string savedTitle = File.ReadAllText(titleFile, Encoding.UTF8).Trim();
                    if (savedTitle.Length > 0 && savedTitle.Length <= 120) PreparedGameTitle = savedTitle;
                }
            }
            string basePath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PhantomStation", "Jeux");
            Directory.CreateDirectory(basePath);
            string root = Path.Combine(basePath, id);
            using (var mutex = new Mutex(false, "Local\\PhantomStation-" + id)) {
                mutex.WaitOne();
                try {
                    if (!File.Exists(Path.Combine(root, ".pret"))) {
                        string tmp = root + "-preparation-" + Guid.NewGuid().ToString("N"); Directory.CreateDirectory(tmp);
                        foreach (var item in archive.Entries) {
                            if (item.FullName == "phantom-package.txt") continue;
                            string destination = Path.GetFullPath(Path.Combine(tmp, item.FullName.Replace('/', Path.DirectorySeparatorChar)));
                            string prefix = Path.GetFullPath(tmp) + Path.DirectorySeparatorChar;
                            if (!destination.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) throw new Exception("Chemin invalide dans l'archive.");
                            if (item.FullName.EndsWith("/")) { Directory.CreateDirectory(destination); continue; }
                            Directory.CreateDirectory(Path.GetDirectoryName(destination));
                            using (var input = item.Open()) using (var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write)) input.CopyTo(output);
                        }
                        File.WriteAllText(Path.Combine(tmp, ".pret"), id);
                        if (Directory.Exists(root)) throw new Exception("Un ancien dossier de travail incomplet existe : " + root);
                        Directory.Move(tmp, root);
                    }
                    string cachedExe = Path.Combine(root, exe);
                    if (expectedHash.Length != 0 && File.Exists(cachedExe)) {
                        string actual;
                        using (var sha = SHA256.Create()) using (var stream = File.OpenRead(cachedExe))
                            actual = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                        if (actual != expectedHash) {
                            string update = cachedExe + ".mise-a-jour-" + Guid.NewGuid().ToString("N");
                            var entry = archive.GetEntry(exe);
                            if (entry == null) throw new Exception("Exécutable absent de l'archive.");
                            using (var input = entry.Open()) using (var output = new FileStream(update, FileMode.CreateNew, FileAccess.Write)) input.CopyTo(output);
                            string updatedHash;
                            using (var sha = SHA256.Create()) using (var stream = File.OpenRead(update))
                                updatedHash = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                            if (updatedHash != expectedHash) throw new Exception("Mise à jour du programme endommagée.");
                            File.Replace(update, cachedExe, null);
                        }
                    }
                    // The intro has its own small executable in the cache, so it
                    // still works if the packaged game is moved or renamed.
                    var introEntry = archive.GetEntry("phantom-intro.exe");
                    string introPath = Path.Combine(root, "phantom-intro.exe");
                    if (introEntry != null) {
                        byte[] incoming;
                        using (var input = introEntry.Open()) using (var buffer = new MemoryStream()) {
                            input.CopyTo(buffer); incoming = buffer.ToArray();
                        }
                        bool updateIntro = true;
                        if (File.Exists(introPath)) using (var sha = SHA256.Create()) {
                            byte[] incomingHash = sha.ComputeHash(incoming), existingHash;
                            using (var stream = File.OpenRead(introPath)) existingHash = sha.ComputeHash(stream);
                            updateIntro = !System.Linq.Enumerable.SequenceEqual(incomingHash, existingHash);
                        }
                        if (updateIntro) {
                            string update = introPath + ".mise-a-jour-" + Guid.NewGuid().ToString("N");
                            File.WriteAllBytes(update, incoming);
                            if (File.Exists(introPath)) File.Replace(update, introPath, null);
                            else File.Move(update, introPath);
                        }
                    }
                    // New launcher art is installed once in an existing cache;
                    // preserve any artwork the player has subsequently edited.
                    foreach (string artPath in new[] {"assets/img/boxart.tga", "assets/img/phantom-background.tga", "assets/phantom-codes-default.tsv", "assets/gamecontrollerdb.txt", "licences/LICENSE-SDL-GameControllerDB.txt"}) {
                        string artDestination = Path.Combine(root, artPath.Replace('/', Path.DirectorySeparatorChar));
                        var artEntry = archive.GetEntry(artPath);
                        if (artEntry != null && !File.Exists(artDestination)) {
                            Directory.CreateDirectory(Path.GetDirectoryName(artDestination));
                            using (var input = artEntry.Open()) using (var output = new FileStream(artDestination, FileMode.CreateNew, FileAccess.Write)) input.CopyTo(output);
                        }
                    }
                    // The window icon is a managed application asset, unlike
                    // the player's editable cover or background.
                    var windowIconEntry = archive.GetEntry("assets/psxrecomp.png");
                    if (windowIconEntry != null) {
                        string windowIcon = Path.Combine(root, "assets", "psxrecomp.png");
                        byte[] incoming;
                        using (var input = windowIconEntry.Open()) using (var buffer = new MemoryStream()) {
                            input.CopyTo(buffer); incoming = buffer.ToArray();
                        }
                        bool update = true;
                        if (File.Exists(windowIcon)) using (var sha = SHA256.Create()) {
                            byte[] expected = sha.ComputeHash(incoming), actual;
                            using (var stream = File.OpenRead(windowIcon)) actual = sha.ComputeHash(stream);
                            update = !System.Linq.Enumerable.SequenceEqual(expected, actual);
                        }
                        if (update) {
                            Directory.CreateDirectory(Path.GetDirectoryName(windowIcon));
                            string tempIcon = windowIcon + ".mise-a-jour-" + Guid.NewGuid().ToString("N");
                            File.WriteAllBytes(tempIcon, incoming);
                            if (File.Exists(windowIcon)) File.Replace(tempIcon, windowIcon, null);
                            else File.Move(tempIcon, windowIcon);
                        }
                    }
                    foreach (var item in archive.Entries) {
                        if (!item.FullName.StartsWith("media/cd", StringComparison.OrdinalIgnoreCase) ||
                            !item.FullName.EndsWith(".sbi", StringComparison.OrdinalIgnoreCase)) continue;
                        string target = Path.GetFullPath(Path.Combine(root, item.FullName.Replace('/', Path.DirectorySeparatorChar)));
                        if (!target.StartsWith(Path.GetFullPath(root) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                            throw new Exception("Chemin SBI invalide dans l'archive.");
                        if (!File.Exists(target)) {
                            Directory.CreateDirectory(Path.GetDirectoryName(target));
                            using (var input = item.Open()) using (var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write)) input.CopyTo(output);
                        }
                    }
                } finally { mutex.ReleaseMutex(); }
            }
            string game = Path.Combine(root, exe);
            if (!File.Exists(game)) throw new Exception("Exécutable du jeu introuvable : " + game);
            return game;
        }
    }
    [STAThread] static void Main(string[] args) {
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        if (args.Length >= 1 && args[0] == "--intro") {
            int width=1100, height=650, parsedWidth, parsedHeight;
            if (args.Length >= 3 && int.TryParse(args[1], out parsedWidth) && int.TryParse(args[2], out parsedHeight) &&
                parsedWidth >= 320 && parsedWidth <= 7680 && parsedHeight >= 240 && parsedHeight <= 4320) {
                width=parsedWidth; height=parsedHeight;
            }
            long parentValue;
            IntPtr parent = args.Length >= 5 && long.TryParse(args[4], out parentValue) && parentValue > 0
                ? new IntPtr(parentValue) : IntPtr.Zero;
            Application.Run(new UniqueLauncher(width, height, args.Length >= 4 && args[3] == "fullscreen", parent));
        }
        else Application.Run(new PreparingForm());
    }
}

class PreparingForm : Form {
    [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int processId);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    Task<string> task;
    bool finished;
    public PreparingForm() {
        Text = "PhantomStation"; ClientSize = new Size(440,110); StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
        Controls.Add(new Label { Text = "Préparation du jeu…\nLe premier lancement peut prendre quelques instants.",
                                 AutoSize = true, Location = new Point(24,25), Font = new Font("Segoe UI",10) });
        Shown += (s,e) => { task = Task.Run(() => UniqueLauncher.Prepare()); timer.Start(); };
        timer.Interval = 100; timer.Tick += Tick;
        FormClosed += (s,e) => timer.Stop();
    }
    void Tick(object sender, EventArgs args) {
        if (finished || task == null || !task.IsCompleted) return;
        finished = true; timer.Stop();
        if (task.IsFaulted) {
            PhantomLaunchReport.Show(this, task.Exception.GetBaseException(), UniqueLauncher.PackagePath); Close(); return;
        }
        try {
            string game = task.Result;
            var start = new ProcessStartInfo(game) { WorkingDirectory = Path.GetDirectoryName(game), UseShellExecute = false };
            string intro = Path.Combine(Path.GetDirectoryName(game), "phantom-intro.exe");
            start.EnvironmentVariables["PHANTOM_SPLASH_EXE"] = File.Exists(intro) ? intro : Application.ExecutablePath;
            start.EnvironmentVariables["PHANTOM_GAME_TITLE"] = UniqueLauncher.PreparedGameTitle;
            if (UniqueLauncher.PreparedDocumentsSaves) start.EnvironmentVariables["PHANTOM_SAVES_DIR"] = UniqueLauncher.PrepareSaveDirectory(game);
            var process = Process.Start(start);
            if (process != null) {
                AllowSetForegroundWindow(process.Id);
                var foregroundThread = new Thread(() => {
                    try {
                        for (int i = 0; i < 60 && !process.HasExited; i++) {
                            process.Refresh();
                            if (process.MainWindowHandle != IntPtr.Zero) { SetForegroundWindow(process.MainWindowHandle); break; }
                            Thread.Sleep(100);
                        }
                    } catch (InvalidOperationException) { }
                });
                foregroundThread.IsBackground = false;
                foregroundThread.Start();
            }
        } catch (Exception ex) { PhantomLaunchReport.Show(this, ex, UniqueLauncher.PackagePath); }
        Close();
    }
}

// A game can be started without the compiler, so failures must also be saved here.
static class PhantomLaunchReport {
    public static string Save(string folder, string report) {
        Directory.CreateDirectory(folder);
        string path = Path.Combine(folder, "rapport-lancement-" + DateTime.Now.ToString("yyyy-MM-dd_HH-mm-ss") + "-" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".txt");
        File.WriteAllText(path, report, new UTF8Encoding(false));
        return path;
    }
    static string Json(string text) {
        var b = new StringBuilder("\"");
        foreach (char c in text) {
            if (c == '"' || c == '\\') b.Append('\\').Append(c);
            else if (c < 32) b.AppendFormat("\\u{0:x4}", (int)c);
            else b.Append(c);
        }
        return b.Append('"').ToString();
    }
    static void Send(string title, string content) {
        if (content.Length > 1900) content = content.Substring(0, 1850) + "\n[Rapport complet enregistré sur le PC]";
        string subject = "Rapport de lancement - " + title;
        if (subject.Length > 110) subject = subject.Substring(0, 110);
        string body = "{\"name\":\"PhantomStation\",\"email\":\"noreply@phantomstation.invalid\",\"subject\":" + Json(subject) +
                      ",\"message\":" + Json(content) + ",\"website\":\"\"}";
        var request = (System.Net.HttpWebRequest)System.Net.WebRequest.Create("https://gears.novalysdev.fr/api/public/contact");
        request.Method = "POST"; request.ContentType = "application/json; charset=utf-8";
        request.Accept = "application/json"; request.UserAgent = "PhantomStation/1.0";
        request.AllowAutoRedirect = false; request.Timeout = 15000; request.ReadWriteTimeout = 15000;
        byte[] data = Encoding.UTF8.GetBytes(body); request.ContentLength = data.Length;
        using (var stream = request.GetRequestStream()) stream.Write(data, 0, data.Length);
        using (var response = (System.Net.HttpWebResponse)request.GetResponse()) {
            if ((int)response.StatusCode != 200) throw new IOException("Le service n’a pas confirmé la réception.");
            using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8)) {
                char[] buffer = new char[8193]; int length = reader.ReadBlock(buffer, 0, buffer.Length);
                if (length > 8192) throw new IOException("Réponse du service invalide.");
                string reply = System.Text.RegularExpressions.Regex.Unescape(new string(buffer, 0, length));
                if (!System.Text.RegularExpressions.Regex.IsMatch(reply, "\\\"message\\\"\\s*:\\s*\\\"Message envoy(?:é|e)\\.\\\""))
                    throw new IOException("Le service n’a pas confirmé la réception.");
            }
        }
    }
    public static void Show(IWin32Window owner, Exception error, string executable) {
        string title = String.IsNullOrWhiteSpace(UniqueLauncher.PreparedGameTitle) ? Path.GetFileNameWithoutExtension(executable) : UniqueLauncher.PreparedGameTitle;
        string report = "Rapport de lancement PhantomStation\r\nDate : " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss zzz") +
                        "\r\nJeu : " + title + "\r\nFichier : " + executable + "\r\nSystème : " + Environment.OSVersion +
                        "\r\nWindows 64 bits : " + Environment.Is64BitOperatingSystem + "\r\n\r\n--- Erreur de lancement ---\r\n" + error;
        string path = null;
        try {
            string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "PhantomStation", "Rapports");
            path = Save(folder, report);
        } catch (Exception) { path = null; }
        using (var dialog = new Form { Text = "Erreur de lancement — PhantomStation", Width = 760, Height = 500,
                                      StartPosition = FormStartPosition.CenterParent, MinimizeBox = false, MaximizeBox = false }) {
            var caption = new Label { Dock = DockStyle.Top, Height = 58, Padding = new Padding(12, 8, 12, 0),
                Text = "Impossible de lancer le jeu. " + (path == null ? "Le rapport n’a pas pu être enregistré." : "Rapport enregistré : " + path) };
            var preview = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Both,
                                        Dock = DockStyle.Fill, Text = report, WordWrap = false };
            var bar = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 50, FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(6) };
            var close = new Button { Text = "Fermer", AutoSize = true, DialogResult = DialogResult.Cancel };
            var send = new Button { Text = "Envoyer le rapport", AutoSize = true };
            var open = new Button { Text = "Ouvrir le dossier", AutoSize = true, Enabled = path != null };
            var copy = new Button { Text = "Copier", AutoSize = true };
            bar.Controls.Add(close); bar.Controls.Add(send); bar.Controls.Add(open); bar.Controls.Add(copy);
            dialog.Controls.Add(preview); dialog.Controls.Add(caption); dialog.Controls.Add(bar); dialog.CancelButton = close;
            copy.Click += (s, e) => { try { Clipboard.SetText(report); } catch (Exception) {} };
            open.Click += (s, e) => { try { Process.Start("explorer.exe", "\"" + Path.GetDirectoryName(path) + "\""); } catch (Exception) {} };
            send.Click += async (s, e) => {
                send.Enabled = false; send.Text = "Envoi…";
                try { await Task.Run(() => Send(title, report)); send.Text = "Rapport transmis"; }
                catch (Exception ex) { send.Enabled = true; send.Text = "Envoyer le rapport"; MessageBox.Show(dialog, "Envoi impossible : " + ex.Message, "PhantomStation", MessageBoxButtons.OK, MessageBoxIcon.Error); }
            };
            dialog.ShowDialog(owner);
        }
    }
}
