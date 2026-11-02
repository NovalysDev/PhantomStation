// Lanceur PS2 autonome, compilé avec unique_launcher.cs.
using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Threading.Tasks;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Windows.Forms;

class Ps2GameForm : Form {
    readonly string game;
    readonly string root;
    readonly string saves;
    readonly Ps2Options options;
    readonly Image backdrop;
    readonly ComboBox scale = new ComboBox();
    readonly CheckBox fullscreen = new CheckBox();
    readonly Button play = new Button();
    readonly Label status = new Label();
    readonly Button codes = new Button();
    readonly PhantomEmbeddedGame session;
    bool syncingOptions;
    public Ps2GameForm(string runtime) {
        game = runtime;
        root = Path.GetDirectoryName(runtime);
        saves = UniqueLauncher.PrepareSaveDirectory(runtime);
        options = new Ps2Options(root);
        backdrop = Image.FromStream(Assembly.GetExecutingAssembly().GetManifestResourceStream("art"));
        Text = UniqueLauncher.PreparedGameTitle + " — PhantomStation";
        ClientSize = new Size(920, 590); MinimumSize = new Size(800, 540);
        StartPosition = FormStartPosition.CenterScreen; BackColor = Color.FromArgb(5, 13, 27);
        DoubleBuffered = true; Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        var title = new Label { Text = UniqueLauncher.PreparedGameTitle, ForeColor = Color.White,
            Font = new Font("Segoe UI", 24, FontStyle.Bold), AutoSize = false, BackColor = Color.Transparent,
            Location = new Point(38, 42), Size = new Size(800, 48) };
        Controls.Add(title);
        var subtitle = new Label { Text = "PLAYSTATION 2  ·  RECOMPILATION EXPÉRIMENTALE",
            ForeColor = Color.FromArgb(102, 220, 231), Font = new Font("Segoe UI", 10, FontStyle.Bold),
            AutoSize = true, BackColor = Color.Transparent, Location = new Point(42, 98) };
        Controls.Add(subtitle);
        var panel = new Panel { BackColor = Color.FromArgb(12, 26, 43), Location = new Point(38, 150), Size = new Size(480, 230), Anchor = AnchorStyles.Left | AnchorStyles.Top };
        Ps2Style.Rounded(panel, 18);
        Controls.Add(panel);
        panel.Controls.Add(new Label { Text = "Qualité de l’image", ForeColor = Color.White, BackColor = Color.Transparent,
            Font = new Font("Segoe UI", 12, FontStyle.Bold), Location = new Point(22, 20), AutoSize = true });
        scale.DropDownStyle = ComboBoxStyle.DropDownList; scale.Items.AddRange(new object[] { "Résolution normale (1x)", "Image plus nette (2x)" });
        scale.Location = new Point(24, 52); scale.Size = new Size(300, 30); scale.Font = new Font("Segoe UI", 11);
        Ps2Style.Dropdown(scale);
        panel.Controls.Add(scale);
        fullscreen.Text = "Plein écran"; fullscreen.ForeColor = Color.White; fullscreen.BackColor = Color.Transparent;
        fullscreen.Location = new Point(24, 102); fullscreen.AutoSize = true; fullscreen.Font = new Font("Segoe UI", 11);
        panel.Controls.Add(fullscreen);
        panel.Controls.Add(new Label { Text = "F1 : commandes   ·   F5 / F8 : sauvegarde rapide", ForeColor = Color.LightSteelBlue,
            BackColor = Color.Transparent, Font = new Font("Segoe UI", 10), AutoSize = true, Location = new Point(22, 150) });
        codes.Text = "Codes PS2"; codes.ForeColor = Color.White; codes.BackColor = Color.FromArgb(32, 51, 75);
        codes.FlatStyle = FlatStyle.Flat; codes.Location = new Point(24, 182); codes.Size = new Size(150, 32);
        Ps2Style.Button(codes);
        codes.Click += delegate { PhantomInlinePage.Show(this, new Ps2CodesForm(root), null); };
        panel.Controls.Add(codes);
        var settings = new Button { Text = "Réglages", ForeColor = Color.White, BackColor = Color.FromArgb(32, 51, 75),
            FlatStyle = FlatStyle.Flat, Location = new Point(185, 182), Size = new Size(150, 32) };
        Ps2Style.Button(settings);
        settings.Click += delegate {
            PhantomInlinePage.Show(this, new Ps2SettingsForm(root, saves, options), result => {
                if (result == DialogResult.OK) {
                    syncingOptions = true;
                    try { scale.SelectedIndex = options.Get("scale") == "1" ? 0 : 1; fullscreen.Checked = options.Yes("fullscreen"); }
                    finally { syncingOptions = false; }
                }
            });
        };
        panel.Controls.Add(settings);
        using (var archive = ZipFile.OpenRead(UniqueLauncher.PackagePath)) {
            var cover = archive.GetEntry("assets/img/boxart.png");
            if (cover != null) using (var input = cover.Open()) using (var buffer = new MemoryStream()) {
                input.CopyTo(buffer); buffer.Position = 0;
                using (var image = Image.FromStream(buffer)) {
                var picture = new PictureBox { Image = new Bitmap(image), SizeMode = PictureBoxSizeMode.Zoom,
                    Location = new Point(570, 148), Size = new Size(235, 300), BackColor = Color.Transparent,
                    Anchor = AnchorStyles.Top | AnchorStyles.Right };
                Controls.Add(picture);
                }
            }
        }
        status.ForeColor = Color.White; status.BackColor = Color.Transparent; status.AutoSize = true;
        status.Location = new Point(42, 414); status.Font = new Font("Segoe UI", 10); Controls.Add(status);
        play.Text = "▶   JOUER"; play.ForeColor = Color.White; play.BackColor = Color.FromArgb(33, 112, 222);
        play.FlatStyle = FlatStyle.Flat; play.FlatAppearance.BorderColor = Color.FromArgb(80, 225, 241);
        play.Font = new Font("Segoe UI", 13, FontStyle.Bold); play.Size = new Size(205, 52);
        Ps2Style.Button(play, true);
        play.Location = new Point(675, 494); play.Anchor = AnchorStyles.Right | AnchorStyles.Bottom; play.Click += StartGame; Controls.Add(play);
        session = new PhantomEmbeddedGame(this);
        scale.SelectedIndex = options.Get("scale") == "1" ? 0 : 1;
        fullscreen.Checked = options.Yes("fullscreen");
        scale.SelectedIndexChanged += delegate { SaveOptions(); }; fullscreen.CheckedChanged += delegate { SaveOptions(); };
    }
    protected override void OnPaintBackground(PaintEventArgs e) {
        e.Graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
        float s = Math.Max((float)ClientSize.Width / backdrop.Width, (float)ClientSize.Height / backdrop.Height);
        int w = (int)(backdrop.Width*s), h = (int)(backdrop.Height*s);
        e.Graphics.DrawImage(backdrop, (ClientSize.Width-w)/2, (ClientSize.Height-h)/2, w, h);
        using (var shade = new SolidBrush(Color.FromArgb(150, 0, 7, 18))) e.Graphics.FillRectangle(shade, ClientRectangle);
    }
    void SaveOptions() {
        if (syncingOptions) return;
        try { options.Set("scale", scale.SelectedIndex == 0 ? "1" : "2"); options.Set("fullscreen", fullscreen.Checked); options.Save(); }
        catch (IOException) { status.Text = "Réglages non enregistrés."; } catch (UnauthorizedAccessException) { status.Text = "Réglages non enregistrés."; }
    }
    static string Quote(string path) { return "\"" + path.Replace("\"", "\\\"") + "\""; }
    void PrepareCard(string card) {
        if (File.Exists(card)) return;
        string temp = card + "." + Guid.NewGuid().ToString("N") + ".part";
        try {
            using (var archive = ZipFile.OpenRead(UniqueLauncher.PackagePath)) {
                var blank = archive.GetEntry("ps2-carte-vierge.ps2");
                if (blank == null) throw new IOException("Carte mémoire PS2 initiale absente.");
                using (var input = blank.Open()) using (var output = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None)) input.CopyTo(output);
            }
            try { File.Move(temp, card); }
            catch (IOException) { if (!File.Exists(card)) throw; }
        } finally { if (File.Exists(temp)) File.Delete(temp); }
    }
    async void StartGame(object sender, EventArgs e) {
        play.Enabled = false; scale.Enabled = false; fullscreen.Enabled = false; status.Text = "Lancement du jeu…";
        bool full = fullscreen.Checked; string res = scale.SelectedIndex == 0 ? "1" : "2";
        try {
            string card = Path.Combine(saves, "carte-memoire.ps2"), quick = Path.Combine(saves, "sauvegardes-rapides");
            PrepareCard(card);
            string elf = Path.Combine(root, "ps2-boot.elf"), disc = Path.Combine(root, "media", "disque.iso");
            Directory.CreateDirectory(quick);
            string codeFile = Ps2CodesForm.WriteActivePatches(root);
            var start = new ProcessStartInfo(game, Quote(elf) + " --disc " + Quote(disc) + " --memcard " + Quote(card) + " --quick-dir " + Quote(quick) + " --window" + (options.Yes("wide") ? " --widescreen" : "") + (codeFile == null ? "" : " --patches " + Quote(codeFile)));
            start.WorkingDirectory = root; start.UseShellExecute = false;
            start.EnvironmentVariables["PS2_SCALE"] = res; start.EnvironmentVariables["PS2_WINDOW_TITLE"] = UniqueLauncher.PreparedGameTitle;
            string playerFile = Path.Combine(root, "phantom-players.txt");
            string playerLimit = File.Exists(playerFile) ? File.ReadAllText(playerFile).Trim() : "2";
            start.EnvironmentVariables["PHANTOM_MAX_PLAYERS"] = playerLimit == "1" ? "1" : "2";
            start.EnvironmentVariables["PS2_LOOK"] = options.Get("look");
            start.EnvironmentVariables["PS2_PRESENT_MODE"] = options.Yes("vsync") ? "fifo" : "immediate";
            if (options.Get("gpu") != "auto") start.EnvironmentVariables["PS2_GPU_BACKEND"] = options.Get("gpu");
            if (!options.Yes("audio")) start.EnvironmentVariables["PS2_AUDIO_STRETCH"] = "0";
            if (!options.Yes("fps")) start.EnvironmentVariables["PHANTOM_PS2_HIDE_FPS"] = "1";
            if (!options.Yes("deinterlace")) start.EnvironmentVariables["PS2_NO_DEINTERLACE"] = "1";
            await session.Run(start, true, full);
            status.Text = "";
        } catch (Exception ex) {
            PhantomLaunchReport.Show(this, ex, game);
            status.Text = "Lancement impossible.";
        } finally { play.Enabled = true; scale.Enabled = true; fullscreen.Enabled = true; }
    }
}

static class PhantomInlinePage {
    public static void Show(Form host, Form page, Action<DialogResult> finished) {
        page.TopLevel = false; page.FormBorderStyle = FormBorderStyle.None;
        page.Dock = DockStyle.Fill; host.Controls.Add(page);
        page.FormClosed += delegate {
            DialogResult result = page.DialogResult;
            host.Controls.Remove(page);
            if (finished != null) finished(result);
            page.Dispose();
        };
        page.Show(); page.BringToFront();
    }
}

class Ps2CodesForm : Form {
    readonly string root;
    readonly CheckedListBox list = new CheckedListBox();
    readonly TextBox label = new TextBox(), code = new TextBox();
    readonly List<string[]> items = new List<string[]>();
    string UserFile { get { return Path.Combine(root, "phantom-ps2-codes.tsv"); } }
    public Ps2CodesForm(string gameRoot) {
        root = gameRoot; Text = "Codes PS2 — PhantomStation"; ClientSize = new Size(620, 460);
        StartPosition = FormStartPosition.CenterParent; BackColor = Color.FromArgb(7, 15, 28);
        ForeColor = Color.White; Font = new Font("Segoe UI", 10); FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false; MinimizeBox = false; Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        Controls.Add(new Label { Text = "Codes PS2", Font = new Font("Segoe UI", 19, FontStyle.Bold), Location = new Point(20, 15), AutoSize = true });
        Controls.Add(new Label { Text = "Coche les codes à activer au prochain lancement. Leur effet dépend du jeu.", Location = new Point(23, 58), AutoSize = true, ForeColor = Color.LightSteelBlue });
        list.Location = new Point(24, 92); list.Size = new Size(572, 190); list.CheckOnClick = true; Controls.Add(list);
        list.BackColor = Ps2Style.Surface; list.ForeColor = Color.White; list.BorderStyle = BorderStyle.FixedSingle;
        Controls.Add(new Label { Text = "Nom du code", Location = new Point(24, 294), AutoSize = true });
        Controls.Add(new Label { Text = "Code direct : 0, 1 ou 2 (ex. 20448F2C 000000C8)", Location = new Point(215, 294), AutoSize = true });
        label.Location = new Point(24, 320); label.Size = new Size(180, 28); label.BackColor=Ps2Style.Field; label.ForeColor=Color.White; label.BorderStyle=BorderStyle.FixedSingle; Controls.Add(label);
        code.Location = new Point(215, 320); code.Size = new Size(260, 28); code.BackColor=Ps2Style.Field; code.ForeColor=Color.White; code.BorderStyle=BorderStyle.FixedSingle; Controls.Add(code);
        var add = new Button { Text = "Ajouter", Location = new Point(484, 318), Size = new Size(112, 32), ForeColor = Color.White, BackColor = Color.FromArgb(32, 91, 155), FlatStyle = FlatStyle.Flat };
        Ps2Style.Button(add);
        add.Click += AddCode; Controls.Add(add);
        Controls.Add(new Label { Text = "Codes de données simples uniquement ; codes conditionnels et chiffrés non pris en charge.", Location = new Point(24, 365), ForeColor = Color.LightSteelBlue, AutoSize = true });
        var save = new Button { Text = "Enregistrer", Location = new Point(460, 408), Size = new Size(136, 34), ForeColor = Color.White, BackColor = Color.FromArgb(33, 112, 222), FlatStyle = FlatStyle.Flat };
        Ps2Style.Button(save,true);
        save.Click += Save; Controls.Add(save);
        var back = new Button { Text = "Retour", Location = new Point(330, 408), Size = new Size(115, 34) };
        Ps2Style.Button(back); back.Click += delegate { Close(); }; Controls.Add(back);
        var remove = new Button { Text = "Retirer le code sélectionné", Location = new Point(24, 408), Size = new Size(220, 34), ForeColor = Color.White, BackColor = Color.FromArgb(32, 51, 75), FlatStyle = FlatStyle.Flat };
        Ps2Style.Button(remove);
        remove.Click += delegate { int i = list.SelectedIndex; if (i >= 0) { items.RemoveAt(i); list.Items.RemoveAt(i); } }; Controls.Add(remove);
        LoadItems();
    }
    static bool ParseCode(string value, out uint address, out int bytes, out uint data) {
        address = 0; data = 0; bytes = 0;
        var m = Regex.Match(value ?? "", @"^\s*([0-2][0-9a-fA-F]{7})\s+([0-9a-fA-F]{8})\s*$");
        if (!m.Success) return false;
        uint first = Convert.ToUInt32(m.Groups[1].Value, 16); data = Convert.ToUInt32(m.Groups[2].Value, 16);
        bytes = first >> 28 == 0 ? 1 : first >> 28 == 1 ? 2 : 4; address = first & 0x0fffffff;
        return address + bytes <= 0x02000000 && (address % bytes) == 0 && (bytes == 4 || data < (1u << (bytes * 8)));
    }
    void LoadItems() {
        string[] lines;
        if (File.Exists(UserFile)) lines = File.ReadAllLines(UserFile);
        else {
            var defaults = new List<string>();
            using (var archive = ZipFile.OpenRead(UniqueLauncher.PackagePath)) {
                var entry = archive.GetEntry("ps2-codes-defaut.tsv");
                if (entry != null) using (var reader = new StreamReader(entry.Open())) {
                    string line; while ((line = reader.ReadLine()) != null) defaults.Add(line);
                }
            }
            lines = defaults.ToArray();
        }
        foreach (string line in lines) {
            string[] parts = line.Split('\t'); uint address, data; int bytes;
            if (parts.Length == 3 && ParseCode(parts[2], out address, out bytes, out data)) {
                items.Add(parts); list.Items.Add(parts[1], parts[0] == "1");
            }
        }
    }
    void AddCode(object sender, EventArgs args) {
        uint address, value; int bytes; string name = label.Text.Trim();
        if (name.Length == 0 || name.Length > 100 || name.IndexOfAny(new[] {'\t','\r','\n'}) >= 0 || !ParseCode(code.Text, out address, out bytes, out value)) {
            MessageBox.Show("Indique un nom et un code de données direct compatible (type 0, 1 ou 2).", "Code invalide", MessageBoxButtons.OK, MessageBoxIcon.Warning); return;
        }
        string[] entry = {"0", name, code.Text.Trim().ToUpperInvariant()}; items.Add(entry); list.Items.Add(name, false); label.Clear(); code.Clear();
    }
    void Save(object sender, EventArgs args) {
        try {
            var lines = new List<string>();
            for (int i = 0; i < items.Count; i++) lines.Add((list.GetItemChecked(i) ? "1" : "0") + "\t" + items[i][1] + "\t" + items[i][2]);
            string temp = UserFile + "." + Guid.NewGuid().ToString("N") + ".part";
            try { File.WriteAllLines(temp, lines.ToArray()); if (File.Exists(UserFile)) File.Replace(temp, UserFile, null); else File.Move(temp, UserFile); }
            finally { if (File.Exists(temp)) File.Delete(temp); }
            Close();
        } catch (Exception ex) { MessageBox.Show("Impossible d’enregistrer : " + ex.Message, "PhantomStation", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }
    public static string WriteActivePatches(string root) {
        string source = Path.Combine(root, "phantom-ps2-codes.tsv");
        if (!File.Exists(source)) return null;
        var lines = new List<string>();
        foreach (string line in File.ReadAllLines(source)) {
            string[] p = line.Split('\t'); uint address, value; int bytes;
            if (p.Length == 3 && p[0] == "1" && ParseCode(p[2], out address, out bytes, out value))
                lines.Add("1 " + address.ToString("X8") + " " + bytes + " " + value.ToString("X"));
        }
        if (lines.Count == 0) return null;
        string path = Path.Combine(root, "phantom-ps2-codes-actifs.txt");
        File.WriteAllLines(path, lines.ToArray()); return path;
    }
}
