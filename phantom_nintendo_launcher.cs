using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Collections.Generic;
using System.IO.Compression;
using System.Threading.Tasks;
using System.Windows.Forms;

class NintendoGameForm : Form {
    readonly string game;
    readonly string saves;
    readonly bool genericN64;
    readonly bool n64;
    readonly int maxPlayers = 4;
    readonly Button play;
    Button controllersButton;
    Button settingsButton;
    Button codesButton;
    Label controllerList;
    Timer controllerTimer;
    IntPtr sdlLibrary;
    bool sdlReady;
    readonly Image background;
    readonly PhantomEmbeddedGame session;

    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern IntPtr LoadLibrary(string file);
    [DllImport("kernel32.dll")] static extern bool FreeLibrary(IntPtr module);
    [DllImport("SDL2.dll", CallingConvention=CallingConvention.Cdecl)] static extern int SDL_InitSubSystem(uint flags);
    [DllImport("SDL2.dll", CallingConvention=CallingConvention.Cdecl)] static extern void SDL_QuitSubSystem(uint flags);
    [DllImport("SDL2.dll", CallingConvention=CallingConvention.Cdecl)] static extern int SDL_NumJoysticks();
    [DllImport("SDL2.dll", CallingConvention=CallingConvention.Cdecl)] static extern void SDL_JoystickUpdate();
    [DllImport("SDL2.dll", CallingConvention=CallingConvention.Cdecl)] static extern int SDL_IsGameController(int index);
    [DllImport("SDL2.dll", CallingConvention=CallingConvention.Cdecl)] static extern IntPtr SDL_GameControllerNameForIndex(int index);

    static string Utf8(IntPtr value) {
        if (value == IntPtr.Zero) return "Manette";
        int length = 0; while (Marshal.ReadByte(value, length) != 0 && length < 1024) length++;
        byte[] data = new byte[length]; Marshal.Copy(value, data, 0, length);
        return Encoding.UTF8.GetString(data);
    }

    void RefreshControllers() {
        if (controllerList == null) return;
        if (!sdlReady) { controllerList.Text = PhantomLanguage.English ? "Controller detection unavailable." : "Détection des manettes indisponible."; return; }
        try {
            SDL_JoystickUpdate();
            int player = 0; var text = new StringBuilder();
            for (int i = 0; i < SDL_NumJoysticks() && player < maxPlayers; i++) {
                if (SDL_IsGameController(i) == 0) continue;
                player++; text.Append(PhantomLanguage.English ? "Player " : "Joueur ").Append(player).Append(" : ").Append(Utf8(SDL_GameControllerNameForIndex(i))).Append('\n');
            }
            for (int i = player + 1; i <= maxPlayers; i++)
                text.Append(PhantomLanguage.English ? "Player " : "Joueur ").Append(i).Append(PhantomLanguage.English ? (i == 1 ? " : keyboard available" : " : no controller") : (i == 1 ? " : clavier disponible" : " : aucune manette")).Append('\n');
            controllerList.Text = text.ToString().TrimEnd();
        } catch { controllerList.Text = PhantomLanguage.English ? "Controller detection unavailable." : "Détection des manettes indisponible."; }
    }

    static void Rounded(Control control, int radius) {
        Action update = () => {
            if (control.Width < 2 || control.Height < 2) return;
            using (var path = new GraphicsPath()) {
                int d = radius * 2, w = control.Width - 1, h = control.Height - 1;
                path.AddArc(0, 0, d, d, 180, 90); path.AddArc(w-d, 0, d, d, 270, 90);
                path.AddArc(w-d, h-d, d, d, 0, 90); path.AddArc(0, h-d, d, d, 90, 90);
                path.CloseFigure(); control.Region = new Region(path);
            }
        };
        control.Resize += (s, e) => update(); update();
    }

    public NintendoGameForm(string executable) {
        game = executable;
        genericN64 = File.Exists(Path.Combine(Path.GetDirectoryName(game), "n64-generic.flag"));
        if (genericN64) {
            string[] marker = File.ReadAllLines(Path.Combine(Path.GetDirectoryName(game), "n64-generic.flag"));
            int configured;
            if (marker.Length > 1 && Int32.TryParse(marker[1].Trim(), out configured) && configured >= 1 && configured <= 4)
                maxPlayers = configured;
        }
        else {
            string marker = Path.Combine(Path.GetDirectoryName(game), "phantom-players.txt");
            int configured;
            if (File.Exists(marker) && Int32.TryParse(File.ReadAllText(marker).Trim(), out configured) && configured >= 1 && configured <= 5)
                maxPlayers = configured;
        }
        n64 = genericN64 || File.Exists(Path.Combine(Path.GetDirectoryName(game), "data", "ge007.pal-final.z64"));
        saves = UniqueLauncher.PrepareSaveDirectory(game);
        Text = UniqueLauncher.PreparedGameTitle + " — PhantomStation";
        ClientSize = new Size(860, 550); MinimumSize = new Size(760, 500);
        StartPosition = FormStartPosition.CenterScreen; DoubleBuffered = true;
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("art"))
        using (var image = Image.FromStream(stream)) background = new Bitmap(image);
        BackgroundImage = background; BackgroundImageLayout = ImageLayout.Stretch;
        var title = new Label { Text = UniqueLauncher.PreparedGameTitle, Tag = "PhantomKeepText", ForeColor = Color.White,
            Font = new Font("Segoe UI", 22, FontStyle.Bold), BackColor = Color.Transparent,
            Location = new Point(35, 32), Size = new Size(770, 47) };
        Controls.Add(title);
        var subtitle = new Label { Text = genericN64 ? "NINTENDO 64  ·  RECOMPILATION HYBRIDE EXPÉRIMENTALE" : n64 ? "NINTENDO 64  ·  PORTAGE NATIF EXPÉRIMENTAL" : "NINTENDO  ·  RECOMPILATION EXPÉRIMENTALE", AutoSize = true,
            ForeColor = Color.FromArgb(105, 220, 235), Font = new Font("Segoe UI", 10, FontStyle.Bold),
            BackColor = Color.Transparent, Location = new Point(38, 86) };
        Controls.Add(subtitle);
        var panel = new Panel { BackColor = Color.FromArgb(14, 29, 46), Location = new Point(35, 135), Size = new Size(485, 230) };
        Rounded(panel, 16); Controls.Add(panel);
        panel.Controls.Add(new Label { Text = genericN64 ? (PhantomLanguage.English ? "CONTROLLERS  ·  UP TO " + maxPlayers + " PLAYERS" : "MANETTES  ·  JUSQU’À " + maxPlayers + " JOUEUR" + (maxPlayers > 1 ? "S" : "")) : "TON JEU EST PRÊT", ForeColor = Color.FromArgb(104, 225, 238),
            Font = new Font("Segoe UI", 10, FontStyle.Bold), Location = new Point(22, 20), AutoSize = true });
        if (genericN64) {
            controllerList = new Label { ForeColor = Color.White, Font = new Font("Segoe UI", 10),
                Location = new Point(22, 50), Size = new Size(440, 88) };
            panel.Controls.Add(controllerList);
            controllersButton = new Button { Text = "CONFIGURER LES MANETTES", ForeColor = Color.White,
                Font = new Font("Segoe UI", 9, FontStyle.Bold), BackColor = Color.FromArgb(32, 59, 87),
                FlatStyle = FlatStyle.Flat, Location = new Point(22, 147), Size = new Size(175, 36), Cursor = Cursors.Hand };
            controllersButton.FlatAppearance.BorderSize = 0; Rounded(controllersButton, 8);
            controllersButton.Click += StartGame; panel.Controls.Add(controllersButton);
            settingsButton = new Button { Text = "VIDÉO ET SON", ForeColor = Color.White,
                Font = new Font("Segoe UI", 9, FontStyle.Bold), BackColor = Color.FromArgb(32, 59, 87),
                FlatStyle = FlatStyle.Flat, Location = new Point(204, 147), Size = new Size(135, 36), Cursor = Cursors.Hand };
            settingsButton.FlatAppearance.BorderSize = 0; Rounded(settingsButton, 8);
            settingsButton.Click += StartGame; panel.Controls.Add(settingsButton);
            codesButton = new Button { Text = "CODES", ForeColor = Color.White,
                Font = new Font("Segoe UI", 9, FontStyle.Bold), BackColor = Color.FromArgb(32, 59, 87),
                FlatStyle = FlatStyle.Flat, Location = new Point(346, 147), Size = new Size(105, 36), Cursor = Cursors.Hand };
            codesButton.FlatAppearance.BorderSize = 0; Rounded(codesButton, 8);
            codesButton.Click += delegate {
                var page = new NintendoCodesPanel(saves);
                page.Done += delegate { Controls.Remove(page); page.Dispose(); };
                Controls.Add(page); PhantomLanguage.Apply(page); page.BringToFront();
            };
            panel.Controls.Add(codesButton);
            panel.Controls.Add(new Label { Text = "Commandes communes · F10 en jeu · compatibilité à vérifier", ForeColor = Color.FromArgb(154, 180, 196),
                Font = new Font("Segoe UI", 9), Location = new Point(22, 196), Size = new Size(445, 25) });
            try {
                sdlLibrary = LoadLibrary(Path.Combine(Path.GetDirectoryName(game), "SDL2.dll"));
                sdlReady = sdlLibrary != IntPtr.Zero && SDL_InitSubSystem(0x00002000) == 0;
            } catch { sdlReady = false; }
            RefreshControllers(); controllerTimer = new Timer { Interval = 2000 };
            controllerTimer.Tick += (s, e) => RefreshControllers(); controllerTimer.Start();
        } else {
            panel.Controls.Add(new Label { Text = n64 ? "Dans le jeu :\nF10  ·  réglages vidéo et commandes\nSauvegardes dans Documents\nManettes configurables dans le jeu" : "Dans le jeu :\nF2  ·  réglages et commandes\nF3  ·  filtres d’image\nF8 / F9  ·  sauvegarde rapide", ForeColor = Color.White,
                Font = new Font("Segoe UI", 12), Location = new Point(22, 60), Size = new Size(430, 125) });
            panel.Controls.Add(new Label { Text = n64 ? "Options et codes du jeu dans son menu." : "Codes de triche : pas encore disponibles.", ForeColor = Color.FromArgb(154, 180, 196),
                Font = new Font("Segoe UI", 9), Location = new Point(22, 196), Size = new Size(440, 25) });
            if (!n64) {
                controllersButton = new Button { Text = "MANETTES", ForeColor = Color.White, BackColor = Color.FromArgb(32, 59, 87),
                    FlatStyle = FlatStyle.Flat, Location = new Point(22, 160), Size = new Size(145, 30), Cursor = Cursors.Hand };
                controllersButton.Click += StartGame; Rounded(controllersButton, 7); panel.Controls.Add(controllersButton);
                settingsButton = new Button { Text = "VIDÉO ET SON", ForeColor = Color.White, BackColor = Color.FromArgb(32, 59, 87),
                    FlatStyle = FlatStyle.Flat, Location = new Point(177, 160), Size = new Size(160, 30), Cursor = Cursors.Hand };
                settingsButton.Click += StartGame; Rounded(settingsButton, 7); panel.Controls.Add(settingsButton);
            }
        }
        string cover = Path.Combine(Path.GetDirectoryName(game), "assets", "img", "boxart.png");
        if (File.Exists(cover)) {
            var picture = new PictureBox { Location = new Point(570, 135), Size = new Size(220, 245), SizeMode = PictureBoxSizeMode.Zoom,
                BackColor = Color.FromArgb(14, 29, 46), Image = Image.FromFile(cover) };
            Rounded(picture, 14); Controls.Add(picture);
        }
        play = new Button { Text = "▶   JOUER", Font = new Font("Segoe UI", 14, FontStyle.Bold), ForeColor = Color.White,
            BackColor = Color.FromArgb(36, 114, 219), FlatStyle = FlatStyle.Flat, Size = new Size(205, 56),
            Location = new Point(620, 464), Anchor = AnchorStyles.Right | AnchorStyles.Bottom, Cursor = Cursors.Hand };
        play.FlatAppearance.BorderSize = 0; Rounded(play, 12); play.Click += StartGame; Controls.Add(play);
        session = new PhantomEmbeddedGame(this);
        PhantomLanguage.Apply(this);
    }

    async void StartGame(object sender, EventArgs e) {
        play.Enabled = false;
        if (controllersButton != null) controllersButton.Enabled = false;
        if (settingsButton != null) settingsButton.Enabled = false;
        if (codesButton != null) codesButton.Enabled = false;
        try {
            var start = new ProcessStartInfo(game) { WorkingDirectory = Path.GetDirectoryName(game), UseShellExecute = false };
            start.EnvironmentVariables["PHANTOM_LANGUAGE"] = PhantomLanguage.English ? "en" : "fr";
            string profile = Environment.GetEnvironmentVariable("PHANTOM_SELECTED_PROFILE_DIR");
            string activeSaves = String.IsNullOrWhiteSpace(profile) ? saves : profile;
            if (!Directory.Exists(activeSaves)) throw new IOException("Le profil de sauvegarde sélectionné est introuvable.");
            if (n64 && !genericN64 && !String.IsNullOrWhiteSpace(profile)) {
                string isolated = PhantomGoldenEyeProfile.Prepare(game, activeSaves);
                start.FileName = isolated;
                start.WorkingDirectory = Path.GetDirectoryName(isolated);
            }
            start.EnvironmentVariables["PHANTOM_RUNTIME_DATA_ROOT"] = activeSaves;
            start.EnvironmentVariables["PHANTOM_MAX_PLAYERS"] = maxPlayers.ToString();
            if (!n64 && maxPlayers > 2) start.EnvironmentVariables["SNES_MULTITAP"] = "port2";
            if (genericN64) {
                string codePath = Path.Combine(saves, "codes-n64.tsv");
                start.Arguments = "--save-dir \"" + activeSaves + "\" --prefs \"" + Path.Combine(saves, "n64_prefs.ini") + "\" --max-players " + maxPlayers +
                    (File.Exists(codePath) ? " --codes \"" + codePath + "\"" : "") +
                    (sender == controllersButton ? " --open-controls" : sender == settingsButton ? " --open-settings" : "");
            }
            if (!n64 && sender == controllersButton) start.EnvironmentVariables["PHANTOM_OPEN_MENU"] = "controls";
            if (!n64 && sender == settingsButton) start.EnvironmentVariables["PHANTOM_OPEN_MENU"] = "settings";
            await session.Run(start, sender == play);
        } catch (Exception ex) {
            PhantomLaunchReport.Show(this, ex, game, ex is PhantomGameCrashException);
        } finally { play.Enabled = true; if (controllersButton != null) controllersButton.Enabled = true; if (settingsButton != null) settingsButton.Enabled = true; if (codesButton != null) codesButton.Enabled = true; }
    }

    protected override void OnFormClosed(FormClosedEventArgs e) {
        if (controllerTimer != null) { controllerTimer.Stop(); controllerTimer.Dispose(); }
        if (sdlReady) SDL_QuitSubSystem(0x00002000);
        if (sdlLibrary != IntPtr.Zero) FreeLibrary(sdlLibrary);
        base.OnFormClosed(e);
    }
}

class NintendoCodesPanel : Panel {
    readonly string path;
    readonly CheckedListBox list = new CheckedListBox();
    readonly TextBox label = new TextBox(), value = new TextBox();
    readonly List<string[]> entries = new List<string[]>();
    public event Action Done;
    public NintendoCodesPanel(string saves) {
        path = Path.Combine(saves, "codes-n64.tsv"); Dock = DockStyle.Fill;
        BackColor = Color.FromArgb(7, 15, 28); ForeColor = Color.White;
        Controls.Add(new Label { Text = "Codes Nintendo 64", Font = new Font("Segoe UI", 19, FontStyle.Bold), Location = new Point(24, 20), AutoSize = true });
        Controls.Add(new Label { Text = "Coche les codes pour les activer au prochain lancement.", Location = new Point(27, 65), AutoSize = true });
        Controls.Add(new Label { Text = "Saisie manuelle : codes directs 80 et 81, adaptés à la version exacte du jeu.", Location = new Point(27, 90), AutoSize = true, ForeColor = Color.LightSteelBlue });
        list.Location = new Point(27, 122); list.Size = new Size(660, 205); list.CheckOnClick = true;
        list.BackColor = Color.FromArgb(20, 35, 53); list.ForeColor = Color.White; Controls.Add(list);
        Controls.Add(new Label { Text = "Nom", Location = new Point(27, 342), AutoSize = true });
        Controls.Add(new Label { Text = "Code : 80XXXXXX 00YY ou 81XXXXXX YYYY", Location = new Point(240, 342), AutoSize = true });
        label.Location = new Point(27, 366); label.Size = new Size(200, 28); Controls.Add(label);
        value.Location = new Point(240, 366); value.Size = new Size(270, 28); Controls.Add(value);
        var add = new Button { Text = "Ajouter", Location = new Point(522, 364), Size = new Size(130, 32) };
        add.Click += delegate { Add(); }; Controls.Add(add);
        var remove = new Button { Text = "Retirer", Location = new Point(27, 420), Size = new Size(130, 34) };
        remove.Click += delegate { int i = list.SelectedIndex; if (i >= 0) { entries.RemoveAt(i); list.Items.RemoveAt(i); } }; Controls.Add(remove);
        var back = new Button { Text = "Retour", Location = new Point(370, 420), Size = new Size(130, 34) };
        back.Click += delegate { if (Done != null) Done(); }; Controls.Add(back);
        var save = new Button { Text = "Enregistrer", Location = new Point(515, 420), Size = new Size(150, 34) };
        save.Click += delegate { Save(); }; Controls.Add(save);
        string[] saved = new string[0];
        if (File.Exists(path)) saved = File.ReadAllLines(path);
        else {
            using (var archive = ZipFile.OpenRead(UniqueLauncher.PackagePath)) {
                var entry = archive.GetEntry("codes-n64-defaut.tsv");
                if (entry != null) using (var reader = new StreamReader(entry.Open(), Encoding.UTF8)) {
                    var lines = new List<string>(); string line;
                    while ((line = reader.ReadLine()) != null) lines.Add(line);
                    saved = lines.ToArray();
                }
            }
        }
        foreach (string line in saved) {
            string[] parts = line.Split('\t'); string normalized;
            if (parts.Length == 3 && Valid(parts[2], out normalized)) {
                entries.Add(new[] { parts[1], normalized }); list.Items.Add(parts[1] + "  ·  " + normalized, parts[0] == "1");
            }
        }
    }
    static bool Valid(string text, out string normalized) {
        normalized = "";
        var m = Regex.Match(text ?? "", @"^\s*(80|81)([0-9a-fA-F]{6})\s+([0-9a-fA-F]{4})\s*$", RegexOptions.IgnoreCase);
        if (!m.Success) return false;
        uint address = Convert.ToUInt32(m.Groups[2].Value, 16), data = Convert.ToUInt32(m.Groups[3].Value, 16);
        bool wide = m.Groups[1].Value == "81";
        if (address >= 0x800000 || (!wide && data > 255) || (wide && (address & 1) != 0)) return false;
        normalized = (m.Groups[1].Value + m.Groups[2].Value + " " + m.Groups[3].Value).ToUpperInvariant();
        return true;
    }
    void Add() {
        string name = label.Text.Trim(), code;
        if (name.Length == 0 || name.Length > 100 || name.IndexOfAny(new[] {'\t','\r','\n'}) >= 0 || !Valid(value.Text, out code)) {
            MessageBox.Show("Indique un nom et un code direct N64 valide (80 ou 81).", "Code invalide", MessageBoxButtons.OK, MessageBoxIcon.Warning); return;
        }
        entries.Add(new[] {name, code}); list.Items.Add(name + "  ·  " + code, false); label.Clear(); value.Clear();
    }
    void Save() {
        try {
            var lines = new List<string>();
            for (int i = 0; i < entries.Count; i++) lines.Add((list.GetItemChecked(i) ? "1" : "0") + "\t" + entries[i][0] + "\t" + entries[i][1]);
            string temp = path + "." + Guid.NewGuid().ToString("N") + ".part";
            try { File.WriteAllLines(temp, lines.ToArray(), new UTF8Encoding(false)); if (File.Exists(path)) File.Replace(temp, path, null); else File.Move(temp, path); }
            finally { if (File.Exists(temp)) File.Delete(temp); }
            if (Done != null) Done();
        } catch (Exception ex) { MessageBox.Show("Impossible d’enregistrer les codes : " + ex.Message, "PhantomStation", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }
}
