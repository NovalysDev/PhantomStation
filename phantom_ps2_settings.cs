// Réglages enregistrés par jeu, sans modifier les sauvegardes.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Windows.Forms;

static class Ps2Style {
    public static readonly Color Background=Color.FromArgb(7,15,28), Surface=Color.FromArgb(16,29,49), Field=Color.FromArgb(27,44,66), Accent=Color.FromArgb(28,168,217);
    static readonly HashSet<Control> rounded=new HashSet<Control>();
    public static void Rounded(Control control,int radius) {
        if (!rounded.Add(control)) return;
        control.Disposed+=delegate {rounded.Remove(control);};
        Action update=delegate {
            if(control.Width<2 || control.Height<2) return;
            int r=Math.Min(radius,Math.Min(control.Width,control.Height)/2);
            using(var path=new GraphicsPath()) {
                int d=r*2, w=control.Width, h=control.Height;
                path.AddArc(0,0,d,d,180,90); path.AddArc(w-d,0,d,d,270,90);
                path.AddArc(w-d,h-d,d,d,0,90); path.AddArc(0,h-d,d,d,90,90); path.CloseFigure();
                var old=control.Region; control.Region=new Region(path); if(old!=null) old.Dispose();
            }
        };
        control.SizeChanged+=delegate {update();}; update();
    }
    public static void Button(Button button, bool primary=false) {
        button.FlatStyle=FlatStyle.Flat; button.FlatAppearance.BorderSize=0;
        button.BackColor=primary?Color.FromArgb(27,133,200):Field; button.ForeColor=Color.White;
        button.FlatAppearance.MouseOverBackColor=primary?Color.FromArgb(42,163,218):Color.FromArgb(44,72,101);
        button.FlatAppearance.MouseDownBackColor=primary?Color.FromArgb(17,98,158):Color.FromArgb(21,39,59);
        button.Cursor=Cursors.Hand; Rounded(button,10);
    }
    public static void Dropdown(ComboBox box) {
        box.FlatStyle=FlatStyle.Flat; box.BackColor=Field; box.ForeColor=Color.White;
        box.DrawMode=DrawMode.OwnerDrawFixed; box.ItemHeight=25;
        box.DrawItem+=delegate(object sender,DrawItemEventArgs e) {
            if(e.Index<0) return;
            bool selected=(e.State&DrawItemState.Selected)!=0;
            using(var brush=new SolidBrush(selected?Color.FromArgb(35,102,145):Field)) e.Graphics.FillRectangle(brush,e.Bounds);
            TextRenderer.DrawText(e.Graphics,box.GetItemText(box.Items[e.Index]),box.Font,e.Bounds,Color.White,
                TextFormatFlags.Left|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);
        };
    }
}

class Ps2Options {
    readonly string path;
    readonly Dictionary<string,string> data = new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
    public Ps2Options(string root) {
        path = Path.Combine(root, "phantom-ps2-options.txt");
        data["scale"]="2"; data["fullscreen"]="0"; data["look"]="sharp_edges"; data["vsync"]="1";
        data["wide"]="0"; data["gpu"]="auto"; data["audio"]="1"; data["fps"]="1"; data["deinterlace"]="1";
        try {
            if (File.Exists(path)) {
                string[] lines = File.ReadAllLines(path);
                if (lines.Length > 0 && (lines[0] == "1" || lines[0] == "2")) data["scale"] = lines[0];
                if (lines.Length > 1 && (lines[1] == "0" || lines[1] == "1")) data["fullscreen"] = lines[1];
                foreach (string line in lines) { int i=line.IndexOf('='); if(i>0 && data.ContainsKey(line.Substring(0,i))) data[line.Substring(0,i)] = line.Substring(i+1); }
            }
        } catch (IOException) {} catch (UnauthorizedAccessException) {}
    }
    public string Get(string key) { string value; return data.TryGetValue(key, out value) ? value : ""; }
    public bool Yes(string key) { return Get(key) == "1"; }
    public void Set(string key,string value) { if (data.ContainsKey(key)) data[key]=value; }
    public void Set(string key,bool value) { Set(key,value ? "1" : "0"); }
    public void Save() {
        string temp=path+"."+Guid.NewGuid().ToString("N")+".part";
        try { File.WriteAllLines(temp,data.Select(x=>x.Key+"="+x.Value).ToArray()); if(File.Exists(path)) File.Replace(temp,path,null); else File.Move(temp,path); }
        finally { if(File.Exists(temp)) File.Delete(temp); }
    }
}

class Ps2SettingsForm : Form {
    readonly Ps2Options options; readonly string root, saves;
    readonly ComboBox resolution=new ComboBox(), look=new ComboBox(), gpu=new ComboBox();
    readonly CheckBox fullscreen=new CheckBox(), wide=new CheckBox(), vsync=new CheckBox(), fps=new CheckBox(), deinterlace=new CheckBox(), audio=new CheckBox();
    static readonly string[] lookWords={"crisp","crisp_edges","soft","soft_edges","sharp","sharp_edges"};
    static readonly string[] gpuWords={"auto","dx12","vulkan"};
    static void Caption(Control parent,string text,int x,int y) { parent.Controls.Add(new Label {Text=text,Location=new Point(x,y),AutoSize=true,ForeColor=Color.FromArgb(220,232,245)}); }
    static void Dropdown(Control parent,ComboBox c,int x,int y,int width,string[] values,int selected) {
        c.DropDownStyle=ComboBoxStyle.DropDownList; c.Location=new Point(x,y); c.Width=width; c.Items.AddRange(values); c.SelectedIndex=Math.Max(0,Math.Min(selected,values.Length-1)); Ps2Style.Dropdown(c); parent.Controls.Add(c);
    }
    static void Check(Control parent,CheckBox box,string text,int x,int y,bool value) {
        box.Text=text; box.Location=new Point(x,y); box.AutoSize=true; box.ForeColor=Color.White; box.Checked=value; parent.Controls.Add(box);
    }
    static int Index(string[] values,string value,int fallback) { int i=Array.IndexOf(values,value); return i<0?fallback:i; }
    public Ps2SettingsForm(string gameRoot,string savePath,Ps2Options values) {
        root=gameRoot; saves=savePath; options=values; Text="Réglages PS2 — PhantomStation"; ClientSize=new Size(670,540);
        StartPosition=FormStartPosition.CenterParent; FormBorderStyle=FormBorderStyle.FixedDialog; MaximizeBox=false; MinimizeBox=false;
        BackColor=Color.FromArgb(7,15,28); ForeColor=Color.White; Font=new Font("Segoe UI",10); Icon=Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        Controls.Add(new Label {Text="Réglages du jeu",Font=new Font("Segoe UI",20,FontStyle.Bold),Location=new Point(22,15),AutoSize=true});
        var navigation=new Panel {Location=new Point(24,64),Size=new Size(622,38),BackColor=Color.Transparent}; Controls.Add(navigation);
        var content=new Panel {Location=new Point(24,110),Size=new Size(622,360),BackColor=Ps2Style.Surface}; Ps2Style.Rounded(content,16); Controls.Add(content);
        var video=Page(content); var input=Page(content); var sound=Page(content); var save=Page(content);
        var pages=new[]{video,input,sound,save}; var buttons=new Button[4];
        string[] titles={"Vidéo","Manettes et clavier","Audio","Sauvegardes"};
        for(int i=0;i<titles.Length;i++) {
            int index=i; buttons[i]=ActionButton(titles[i],i*156,0,150); navigation.Controls.Add(buttons[i]);
            buttons[i].Click+=delegate { for(int j=0;j<pages.Length;j++) { pages[j].Visible=j==index; Ps2Style.Button(buttons[j],j==index); } };
        }
        for(int i=0;i<pages.Length;i++) pages[i].Visible=i==0;
        Ps2Style.Button(buttons[0],true);
        Caption(video,"Résolution interne",18,18); Dropdown(video,resolution,245,14,300,new[]{"Normale (1x)","Plus nette (2x)"},options.Get("scale")=="1"?0:1);
        Caption(video,"Lissage et netteté",18,61); Dropdown(video,look,245,57,300,new[]{"Pixels nets","Pixels nets et bords lissés","Image douce","Image douce et bords lissés","Netteté renforcée","Netteté et bords lissés"},Index(lookWords,options.Get("look"),5));
        Caption(video,"Rendu graphique",18,104); Dropdown(video,gpu,245,100,300,new[]{"Automatique","DirectX 12","Vulkan"},Index(gpuWords,options.Get("gpu"),0));
        Check(video,fullscreen,"Plein écran",18,153,options.Yes("fullscreen"));
        Check(video,wide,"Format large 16:9 (selon le jeu)",18,188,options.Yes("wide"));
        Check(video,vsync,"Synchronisation verticale (si disponible)",18,223,options.Yes("vsync"));
        Check(video,deinterlace,"Désentrelacement de l’image",18,258,options.Yes("deinterlace"));
        Check(video,fps,"Afficher les FPS dans le titre de la fenêtre",18,293,options.Yes("fps"));
        Caption(video,"Certaines options dépendent du jeu et de la carte graphique.",18,336);
        Caption(input,"Les manettes compatibles sont reconnues automatiquement.",18,20);
        Caption(input,"Le moteur PS2 gère actuellement deux ports de manette.",18,53);
        Caption(input,"F1 affiche les commandes en jeu. Le clavier est personnalisable.",18,86);
        var keys=ActionButton("Configurer le clavier",18,133,220); keys.Click+=delegate { PhantomInlinePage.Show(this,new Ps2KeysForm(saves),null); }; input.Controls.Add(keys);
        Caption(input,"F2 : style d’image  ·  F5 : sauvegarde rapide  ·  F8 : chargement",18,190);
        Check(sound,audio,"Adapter le son quand le jeu ralentit",18,25,options.Yes("audio"));
        Caption(sound,"Quand cette option est désactivée, le son peut se couper si le jeu ralentit.",18,64);
        Caption(save,"Carte mémoire PS2 et sauvegardes rapides dans Documents.",18,25);
        Caption(save,"La carte est créée formatée au premier lancement et conservée ensuite.",18,58);
        var folder=ActionButton("Ouvrir le dossier des sauvegardes",18,108,285); folder.Click+=delegate { try { Process.Start("explorer.exe", "\""+saves+"\""); } catch(Exception ex){MessageBox.Show(ex.Message,"PhantomStation");} }; save.Controls.Add(folder);
        var cancel=ActionButton("Annuler",338,489,135); cancel.Click+=delegate { Close(); }; Controls.Add(cancel);
        var apply=ActionButton("Enregistrer",486,489,160); Ps2Style.Button(apply,true);
        apply.Click+=delegate {
            try {
                options.Set("scale",resolution.SelectedIndex==0?"1":"2"); options.Set("look",lookWords[look.SelectedIndex]); options.Set("gpu",gpuWords[gpu.SelectedIndex]);
                options.Set("fullscreen",fullscreen.Checked); options.Set("wide",wide.Checked); options.Set("vsync",vsync.Checked);
                options.Set("fps",fps.Checked); options.Set("deinterlace",deinterlace.Checked); options.Set("audio",audio.Checked); options.Save();
                DialogResult=DialogResult.OK; Close();
            } catch(Exception ex) { MessageBox.Show("Impossible d’enregistrer les réglages :\n"+ex.Message,"PhantomStation",MessageBoxButtons.OK,MessageBoxIcon.Error); }
        }; Controls.Add(apply);
    }
    static Panel Page(Panel parent) { var page=new Panel {Dock=DockStyle.Fill,BackColor=Ps2Style.Surface,ForeColor=Color.White}; parent.Controls.Add(page); return page; }
    static Button ActionButton(string label,int x,int y,int width) { var b=new Button {Text=label,Location=new Point(x,y),Size=new Size(width,36)}; Ps2Style.Button(b); return b; }
}

class Ps2KeysForm : Form {
    static readonly string[,] actions={
        {"Croix directionnelle : haut","dpad_up","Up"},{"Croix directionnelle : bas","dpad_down","Down"},{"Croix directionnelle : gauche","dpad_left","Left"},{"Croix directionnelle : droite","dpad_right","Right"},
        {"Stick gauche : haut","left_up","W"},{"Stick gauche : bas","left_down","S"},{"Stick gauche : gauche","left_left","A"},{"Stick gauche : droite","left_right","D"},
        {"Stick droit : haut","right_up","I"},{"Stick droit : bas","right_down","K"},{"Stick droit : gauche","right_left","J"},{"Stick droit : droite","right_right","L"},
        {"Croix","cross","Space, Enter, KPEnter"},{"Rond","circle","E, Middle Mouse"},{"Carré","square","Left Mouse"},{"Triangle","triangle","Right Mouse"},
        {"L1","l1","LShift, Mouse X2"},{"R1","r1","F"},{"L2","l2","Q"},{"R2","r2","R"},{"L3","l3","X"},{"R3","r3","C"},{"Start","start","Esc"},{"Select","select","Tab, Backspace"}};
    readonly string path; readonly DataGridView grid=new DataGridView();
    static readonly string[] names={"None","Up","Down","Left","Right","W","A","S","D","Z","Q","E","R","F","I","J","K","L","X","C","V","Space","Enter","Esc","Tab","Backspace","LShift","RShift","LCtrl","RCtrl","Left Mouse","Right Mouse","Middle Mouse","Mouse X1","Mouse X2"};
    public Ps2KeysForm(string saves) {
        path=Path.Combine(saves,"sauvegardes-rapides","keys.ini"); Text="Clavier — PhantomStation PS2"; ClientSize=new Size(610,570); StartPosition=FormStartPosition.CenterParent;
        BackColor=Color.FromArgb(7,15,28); ForeColor=Color.White; Font=new Font("Segoe UI",10); MinimizeBox=false; MaximizeBox=false;
        Controls.Add(new Label {Text="Commandes du clavier",Location=new Point(20,12),Font=new Font("Segoe UI",18,FontStyle.Bold),AutoSize=true});
        Controls.Add(new Label {Text="Choisis les touches à utiliser pour chaque bouton PS2.",Location=new Point(22,53),ForeColor=Color.LightSteelBlue,AutoSize=true});
        grid.Location=new Point(22,82); grid.Size=new Size(564,410); grid.AllowUserToAddRows=false; grid.AllowUserToDeleteRows=false; grid.RowHeadersVisible=false; grid.AutoSizeColumnsMode=DataGridViewAutoSizeColumnsMode.Fill;
        grid.BackgroundColor=Ps2Style.Surface; grid.BorderStyle=BorderStyle.None; grid.EnableHeadersVisualStyles=false;
        grid.DefaultCellStyle.ForeColor=Color.White; grid.DefaultCellStyle.BackColor=Ps2Style.Field;
        grid.DefaultCellStyle.SelectionBackColor=Color.FromArgb(31,95,135); grid.DefaultCellStyle.SelectionForeColor=Color.White;
        grid.ColumnHeadersDefaultCellStyle.BackColor=Color.FromArgb(21,50,72); grid.ColumnHeadersDefaultCellStyle.ForeColor=Color.White;
        grid.GridColor=Color.FromArgb(44,68,90);
        grid.Columns.Add(new DataGridViewTextBoxColumn {HeaderText="Bouton PS2",ReadOnly=true});
        var choices=new DataGridViewComboBoxColumn {HeaderText="Touche"}; choices.Items.AddRange(names); grid.Columns.Add(choices); Controls.Add(grid);
        var existing=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
        if(File.Exists(path)) foreach(string line in File.ReadAllLines(path)) { int i=line.IndexOf('='); if(i>0) existing[line.Substring(0,i).Trim()]=line.Substring(i+1).Split('#')[0].Trim(); }
        for(int i=0;i<actions.GetLength(0);i++) {
            string value; if(!existing.TryGetValue(actions[i,1],out value)) value=actions[i,2];
            if(!choices.Items.Contains(value)) choices.Items.Add(value); grid.Rows.Add(actions[i,0],value);
        }
        var save=new Button {Text="Enregistrer",Location=new Point(438,512),Size=new Size(148,36),BackColor=Color.FromArgb(33,112,222),ForeColor=Color.White,FlatStyle=FlatStyle.Flat};
        var back=new Button {Text="Retour",Location=new Point(278,512),Size=new Size(145,36)};
        Ps2Style.Button(back); back.Click+=delegate { Close(); }; Controls.Add(back);
        Ps2Style.Button(save,true);
        save.Click+=delegate {
            try {
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                var changed=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
                for(int i=0;i<actions.GetLength(0);i++) changed[actions[i,1]]=Convert.ToString(grid.Rows[i].Cells[1].Value);
                var lines=File.Exists(path)?new List<string>(File.ReadAllLines(path)):new List<string>();
                for(int i=0;i<lines.Count;i++) { int eq=lines[i].IndexOf('='); if(eq>0) { string key=lines[i].Substring(0,eq).Trim(); if(changed.ContainsKey(key)){ lines[i]=key+" = "+changed[key]; changed.Remove(key); } } }
                foreach(var entry in changed) lines.Add(entry.Key+" = "+entry.Value);
                string temp=path+"."+Guid.NewGuid().ToString("N")+".part";
                try { File.WriteAllLines(temp,lines.ToArray()); if(File.Exists(path)) File.Replace(temp,path,null); else File.Move(temp,path); }
                finally { if(File.Exists(temp)) File.Delete(temp); }
                Close();
            } catch(Exception ex) {MessageBox.Show("Impossible d’enregistrer les touches :\n"+ex.Message,"PhantomStation");}
        }; Controls.Add(save);
    }
}
