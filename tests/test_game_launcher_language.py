"""Test the game-launcher localization without running a game or editing settings."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import phantom_ps2

with tempfile.TemporaryDirectory(prefix='langue-lanceur-') as tmp:
    root = Path(tmp); source = root/'probe.cs'; executable = root/'probe.exe'
    source.write_text('''using System; using System.Windows.Forms;
class Probe { [STAThread] static int Main() { Console.OutputEncoding = System.Text.Encoding.UTF8;
    var form = new Form();
    var label = new Label { Text = "Qualité de l’image" }; form.Controls.Add(label);
    var game = new Label { Text = "Sauvegardes", Tag = "PhantomKeepText" }; form.Controls.Add(game);
    var combo = new ComboBox(); combo.Items.AddRange(new object[] { "Résolution normale (1x)", "Image plus nette (2x)" });
    combo.SelectedIndex = 1; form.Controls.Add(combo);
    PhantomLanguage.Apply(form);
    Console.WriteLine(label.Text); Console.WriteLine(combo.Items[1]); Console.WriteLine(combo.SelectedIndex); Console.WriteLine(game.Text);
    return 0;
} }''', encoding='utf-8')
    command = [str(phantom_ps2.PYTHON), '/nologo', '/target:exe', '/main:Probe', '/out:'+str(executable),
               '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
               '/reference:System.IO.Compression.dll', '/reference:System.IO.Compression.FileSystem.dll',
               '/resource:'+str(BASE/'assets/phantom-startup.png')+',art',
               '/resource:'+str(BASE/'assets/logo-ps2.png')+',logo',
               '/resource:'+str(BASE/'assets/son-demarrage-n64.wav')+',sound',
               str(BASE/'unique_launcher.cs'), str(source)]
    subprocess.run(command, check=True, capture_output=True)
    for language, expected in [('en', ['Image quality','Sharper image (2x)','1','Sauvegardes']),
                               ('fr', ['Qualité de l’image','Image plus nette (2x)','1','Sauvegardes'])]:
        env = os.environ.copy(); env['PHANTOM_LANGUAGE'] = language
        output = subprocess.run([str(executable)], check=True, capture_output=True, env=env).stdout.decode('utf-8-sig').splitlines()
        assert output == expected, (language, output)
print('OK : lanceurs FR/EN, sélection vidéo et titre du jeu conservés')
