using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Diagnostics;
using System.Threading.Tasks;
using System.Windows.Forms;

// Dreamcast launcher; the generic profile remains experimental.
class DreamcastGameForm : Form {
    readonly string game, saves, settings;
    readonly PhantomEmbeddedGame session;
    readonly ComboBox scale;
    readonly CheckBox fullscreen, audio;
    readonly Button play;
    static string L(string fr, string en) { return PhantomLanguage.English ? en : fr; }
    static Color ColorCard = Color.FromArgb(11, 25, 41);
    static Label LabelText(string text, int size, bool bold = false) {
        return new Label { Text=text, ForeColor=Color.White, AutoSize=true,
            BackColor=Color.Transparent, Font=new Font("Segoe UI", size, bold ? FontStyle.Bold : FontStyle.Regular) };
    }
    public DreamcastGameForm(string executable) {
        game=executable;
        saves=UniqueLauncher.PrepareSaveDirectory(game);
        settings=Path.Combine(saves,"dreamcast-reglages.ini");
        Text=UniqueLauncher.PreparedGameTitle + " — PhantomStation";
        ClientSize=new Size(900,600); MinimumSize=new Size(820,560);
        StartPosition=FormStartPosition.CenterScreen; DoubleBuffered=true;
        try { Icon=Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch {}
        using (var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("art"))
            if (stream!=null) using (var source=Image.FromStream(stream)) BackgroundImage=new Bitmap(source);
        BackgroundImageLayout=ImageLayout.Stretch; BackColor=Color.FromArgb(4,10,22);
        var title=LabelText(UniqueLauncher.PreparedGameTitle,24,true); title.Location=new Point(28,24); Controls.Add(title);
        var caption=LabelText(L("DREAMCAST  ·  VERSION EXPÉRIMENTALE", "DREAMCAST  ·  EXPERIMENTAL VERSION"),10,true);
        caption.ForeColor=Color.FromArgb(110,220,235); caption.Location=new Point(31,70); Controls.Add(caption);
        var panel=new Panel { BackColor=ColorCard, Location=new Point(26,112), Size=new Size(515,365) };
        Controls.Add(panel);
        var heading=LabelText(L("VIDÉO", "VIDEO"),11,true);heading.ForeColor=Color.Aquamarine;heading.Location=new Point(20,19);panel.Controls.Add(heading);
        var sizeLabel=LabelText(L("Taille du rendu", "Render size"),10);sizeLabel.Location=new Point(20,58);panel.Controls.Add(sizeLabel);
        scale=new ComboBox { DropDownStyle=ComboBoxStyle.DropDownList, Location=new Point(190,54), Width=220 };
        scale.Items.AddRange(new object[]{"640 × 480  (1×)","1280 × 960  (2×)","1920 × 1440  (3×)","2560 × 1920  (4×)"});
#if PHANTOM_DC_GENERIC
        scale.SelectedIndex=0;
#else
        scale.SelectedIndex=3;
#endif
        panel.Controls.Add(scale);
        fullscreen=new CheckBox { Text=L("Plein écran", "Fullscreen"), ForeColor=Color.White, BackColor=Color.Transparent,
                                  AutoSize=true, Location=new Point(23,102) };panel.Controls.Add(fullscreen);
        var soundHead=LabelText(L("SON", "AUDIO"),11,true);soundHead.ForeColor=Color.Aquamarine;soundHead.Location=new Point(20,148);panel.Controls.Add(soundHead);
        audio=new CheckBox { Text=L("Activer le son expérimental", "Enable experimental audio"), ForeColor=Color.White,
                             BackColor=Color.Transparent, AutoSize=true, Checked=true, Location=new Point(23,185) };panel.Controls.Add(audio);
        var info=LabelText(L("Commandes : flèches, Z / X, A / S, Entrée ; manette détectée automatiquement.",
                             "Controls: arrows, Z / X, A / S, Enter; gamepads are detected automatically."),9);
        info.AutoSize=false;info.Size=new Size(465,50);info.Location=new Point(20,236);panel.Controls.Add(info);
#if PHANTOM_DC_GENERIC
        var notice=LabelText(L("Essai expérimental : ce jeu peut ne pas démarrer ou présenter des problèmes.",
                               "Experimental build: this game may not start or may have problems."),9);
#else
        var notice=LabelText(L("Son et graphismes encore en cours de mise au point.",
                               "Audio and graphics are still under development."),9);
#endif
        notice.AutoSize=false;notice.Size=new Size(465,48);notice.ForeColor=Color.FromArgb(169,186,204);notice.Location=new Point(20,290);panel.Controls.Add(notice);
        string cover=Path.Combine(Path.GetDirectoryName(game),"assets","img","boxart.png");
        if(File.Exists(cover)) {
            var picture=new PictureBox { Location=new Point(590,115), Size=new Size(240,325), SizeMode=PictureBoxSizeMode.Zoom, BackColor=Color.FromArgb(15,28,42) };
            using(var source=Image.FromFile(cover)) picture.Image=new Bitmap(source); Controls.Add(picture);
        }
        var open=new Button { Text=L("Ouvrir les sauvegardes", "Open saves"), Location=new Point(29,510), Size=new Size(200,42), FlatStyle=FlatStyle.Flat,
                              BackColor=Color.FromArgb(31,55,78), ForeColor=Color.White, Cursor=Cursors.Hand };
        open.Click+=(s,e)=>Process.Start("explorer.exe",saves);Controls.Add(open);
        play=new Button { Text=L("▶  JOUER", "▶  PLAY"), Location=new Point(660,500), Size=new Size(195,52), FlatStyle=FlatStyle.Flat,
                          BackColor=Color.FromArgb(29,105,220), ForeColor=Color.White, Font=new Font("Segoe UI",12,FontStyle.Bold), Cursor=Cursors.Hand,
                          Anchor=AnchorStyles.Right|AnchorStyles.Bottom };
        play.Click+=StartGame;Controls.Add(play);
        LoadSettings(); session=new PhantomEmbeddedGame(this);
        FormClosed+=(s,e)=> { try { SaveSettings(); } catch(IOException) {} catch(UnauthorizedAccessException) {} };
    }
    void LoadSettings() {
        if(!File.Exists(settings)) return;
        try { foreach(var line in File.ReadAllLines(settings)) {
            string[] pair=line.Split(new[]{'='},2);if(pair.Length!=2)continue;
            int n;if(pair[0]=="scale" && Int32.TryParse(pair[1],out n) && n>=1 && n<=4) scale.SelectedIndex=n-1;
            if(pair[0]=="fullscreen") fullscreen.Checked=pair[1]=="1";
            if(pair[0]=="audio") audio.Checked=pair[1]=="1";
        }} catch(IOException) {}
    }
    void SaveSettings() {
        string tmp=settings+"."+Guid.NewGuid().ToString("N")+".tmp";
        try {
            File.WriteAllLines(tmp,new[]{"scale="+(scale.SelectedIndex+1),"fullscreen="+(fullscreen.Checked?"1":"0"),"audio="+(audio.Checked?"1":"0")});
            if(File.Exists(settings)) File.Replace(tmp,settings,null); else File.Move(tmp,settings);
        } finally { if(File.Exists(tmp)) File.Delete(tmp); }
    }
    // Optional local repair for this exact cached runtime. The original game
    // package is untouched; a repair is never used for a different build.
    static string CorrectedRuntime(string original) {
        string folder=Path.GetDirectoryName(original);
        string marker=Path.Combine(folder,"dreamcast-correction.sha256");
        if(!File.Exists(marker)) return original;
        if(new FileInfo(marker).Length>256) throw new IOException("La vérification de la correction Dreamcast est invalide.");
        string[] lines=File.ReadAllLines(marker);
        if(lines.Length!=2 || !System.Text.RegularExpressions.Regex.IsMatch(lines[0],"^[0-9a-f]{64}$") ||
           !System.Text.RegularExpressions.Regex.IsMatch(lines[1],"^[0-9a-f]{64}$"))
            throw new IOException("La vérification de la correction Dreamcast est invalide.");
        string corrected=Path.Combine(folder,"dreamcast-correction.exe");
        if(!File.Exists(corrected)) throw new IOException("Le moteur Dreamcast corrigé est introuvable.");
        using(var sha=System.Security.Cryptography.SHA256.Create()) {
            string originalHash, correctedHash;
            using(var f=File.OpenRead(original)) originalHash=BitConverter.ToString(sha.ComputeHash(f)).Replace("-","").ToLowerInvariant();
            using(var f=File.OpenRead(corrected)) correctedHash=BitConverter.ToString(sha.ComputeHash(f)).Replace("-","").ToLowerInvariant();
            if(originalHash!=lines[0]) return original;
            if(correctedHash!=lines[1])
                throw new IOException("Le moteur Dreamcast corrigé est endommagé ; il n’a pas été lancé.");
        }
        return corrected;
    }
    static string TrialRuntime(string original) {
        string baseline=CorrectedRuntime(original);
        if(Environment.GetEnvironmentVariable("PHANTOM_DC_TRIAL")!="1") return baseline;
        string folder=Path.GetDirectoryName(original), marker=Path.Combine(folder,"dreamcast-candidat.sha256");
        string trial=Path.Combine(folder,"dreamcast-candidat.exe");
        if(!File.Exists(marker)||!File.Exists(trial)||new FileInfo(marker).Length>256) return baseline;
        string[] lines=File.ReadAllLines(marker);
        if(lines.Length!=3) return baseline;
        foreach(string line in lines) if(!System.Text.RegularExpressions.Regex.IsMatch(line,"^[0-9a-f]{64}$")) return baseline;
        using(var sha=System.Security.Cryptography.SHA256.Create()) {
            string first, current, candidate;
            using(var f=File.OpenRead(original)) first=BitConverter.ToString(sha.ComputeHash(f)).Replace("-","").ToLowerInvariant();
            using(var f=File.OpenRead(baseline)) current=BitConverter.ToString(sha.ComputeHash(f)).Replace("-","").ToLowerInvariant();
            using(var f=File.OpenRead(trial)) candidate=BitConverter.ToString(sha.ComputeHash(f)).Replace("-","").ToLowerInvariant();
            if(first!=lines[0]||current!=lines[1]||candidate!=lines[2]) return baseline;
        }
        return trial;
    }
    async void StartGame(object sender,EventArgs e) {
        play.Enabled=false;
        try {
            SaveSettings();
            string folder=Path.GetDirectoryName(game);
            string boot=Path.Combine(folder,"1ST_READ.BIN"),disc=Path.Combine(folder,"disque.chd");
            if(!File.Exists(boot)||!File.Exists(disc)) throw new FileNotFoundException(L("Données du jeu manquantes.","Game data is missing."));
            string runtime=TrialRuntime(game);
            string token=Environment.GetEnvironmentVariable("PHANTOM_DC_TRIAL_TOKEN");
            if(runtime==Path.Combine(folder,"dreamcast-candidat.exe") &&
               !String.IsNullOrEmpty(token) && System.Text.RegularExpressions.Regex.IsMatch(token,"^[0-9a-f]{32}$")) {
                try { File.WriteAllText(Path.Combine(folder,"dreamcast-candidat-essai-"+token+".txt"),"ok"); }
                catch(IOException) {} catch(UnauthorizedAccessException) {}
            }
            var start=new ProcessStartInfo(runtime) { WorkingDirectory=folder, UseShellExecute=false,
                Arguments="\""+boot+"\" \""+disc+"\"" };
            string selectedCard=Environment.GetEnvironmentVariable("PHANTOM_SELECTED_DC_CARD");
            if(!String.IsNullOrWhiteSpace(selectedCard) && !File.Exists(selectedCard))
                throw new IOException(L("La carte Dreamcast sélectionnée est introuvable.","The selected Dreamcast memory card is missing."));
            start.EnvironmentVariables["PHANTOM_DC_VMU"]=String.IsNullOrWhiteSpace(selectedCard) ? PhantomDreamcastVmu.Prepare(saves) : selectedCard;
            if(!String.IsNullOrWhiteSpace(selectedCard)) start.EnvironmentVariables["PHANTOM_DC_VMU_EXACT"]="1";
            // Code the game reaches that was not translated yet is noted here,
            // per disc, and translated by the next compilation of it. Every
            // game learns: what differs between profiles is only the starting
            // knowledge, never whether the disc can teach the engine.
            try {
                string id;
                using(var sha=System.Security.Cryptography.SHA1.Create()) using(var f=File.OpenRead(boot))
                    id=BitConverter.ToString(sha.ComputeHash(f)).Replace("-","").ToLowerInvariant();
                start.EnvironmentVariables["PHANTOM_DC_LEARN_DIR"]=Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"PhantomStation",
                    "Dreamcast-apprentissage",id,"partie-"+DateTime.Now.ToString("yyyyMMdd-HHmmss"));
            } catch(IOException) {} catch(UnauthorizedAccessException) {}
#if PHANTOM_DC_GENERIC
            start.EnvironmentVariables["DCRECOMP_TEST_CONTROLLER_DESCRIPTOR"]="1";
#if PHANTOM_DC_HOUSE
            // Addresses and disk programs were verified against this exact disc.
            start.EnvironmentVariables["DCRECOMP_TEST_IRQ_ADDR"]="0x8c0bbb90";
            start.EnvironmentVariables["DCRECOMP_TEST_GD_SERVER"]="0x8c0afaa4";
            start.EnvironmentVariables["DCRECOMP_TEST_ADX_SERVER"]="0x8c0ad252";
            start.EnvironmentVariables["DCRECOMP_TEST_NATIVE_JMP"]="1";
            start.EnvironmentVariables["DCRECOMP_TEST_HOUSE_OVERLAY2"]="1";
#endif
#else
            start.EnvironmentVariables["PHANTOM_DC_FIBERS"]="1";
            start.EnvironmentVariables["PHANTOM_DC_TEXTURE_RESERVE"]="1";
#endif
            start.EnvironmentVariables["PHANTOM_DC_SCALE"]=(scale.SelectedIndex+1).ToString();
            start.EnvironmentVariables["DCRECOMP_NO_PRESENT"]="1";
            // Continuous WAV captures used hundreds of megabytes per play.
            // Ordinary diagnostics go to the one bounded game log instead.
            if (String.IsNullOrEmpty(Environment.GetEnvironmentVariable("PHANTOM_DC_SON_ANALYSE")))
                start.EnvironmentVariables["PHANTOM_DC_SON_ANALYSE"]="0";
            if(audio.Checked) start.EnvironmentVariables["PHANTOM_DC_AUDIO"]="1";
            await session.Run(start,true,fullscreen.Checked);
        } catch(Exception ex) { PhantomLaunchReport.Show(this,ex,game,ex is PhantomGameCrashException); }
        finally { if(!IsDisposed) play.Enabled=true; }
    }
}
