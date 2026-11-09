"""Check game journals remain separate and keep draining on I/O failure."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
SOURCE = r"""using System; using System.IO; using System.Threading.Tasks;
class TestJournal {
 static int Main(string[] args) {
  string id1="11111111111111111111111111111111", id2="22222222222222222222222222222222";
  using(var one=PhantomGameJournal.NewSession(args[0],"Grandia II",id1,1))
  using(var two=PhantomGameJournal.NewSession(args[0],"House: The Dead",id2,1)) {
   var a=Task.Run(()=>one.Drain(new StringReader("grandia\n[HEARTBEAT] ignore\n"),false));
   var b=Task.Run(()=>two.Drain(new StringReader("house\n"),false));
   var c=Task.Run(()=>two.Drain(new StringReader("erreur\n"),true));
   Task.WaitAll(a,b,c); if(!c.Result.Contains("erreur")) return 1;
  }
  string games=Path.Combine(args[0],"Jeux"), first=Directory.GetFiles(games,"journal.log",SearchOption.AllDirectories)[0];
  string[] files=Directory.GetFiles(games,"journal.log",SearchOption.AllDirectories);
  if(files.Length!=2) return 2;
  string grandia=Array.Find(files,x=>x.Contains(id1)), house=Array.Find(files,x=>x.Contains(id2));
  if(grandia==null || house==null || !File.ReadAllText(grandia).Contains("grandia") || File.ReadAllText(grandia).Contains("HEARTBEAT") || !File.ReadAllText(house).Contains("erreur")) return 3;
  using(var again=PhantomGameJournal.NewSession(args[0],"Nouveau nom",id1,2)) again.Drain(new StringReader("nouveau\n"),false);
  if(File.ReadAllText(grandia).Contains("grandia") || !File.ReadAllText(grandia).Contains("nouveau") || !File.ReadAllText(house).Contains("house")) return 4;
  if(Directory.GetFiles(games,"journal.log",SearchOption.AllDirectories).Length!=2) return 5;
  try { PhantomGameJournal.NewSession(args[0],"invalid","../invalid",1); return 6; } catch(ArgumentException) {}
  return 0;
 }
}"""

class JournalTests(unittest.TestCase):
    @unittest.skipUnless(CSC.is_file(), "Windows .NET compiler unavailable")
    def test_isolated_sessions_and_unavailable_log(self):
        with tempfile.TemporaryDirectory(prefix="phantom-journaux-") as tmp:
            directory = Path(tmp)
            source = directory / "test.cs"
            source.write_text(SOURCE, encoding="utf-8")
            executable = directory / "test.exe"
            subprocess.run([str(CSC), "/nologo", "/target:exe", "/main:TestJournal",
                            "/out:" + str(executable), "/reference:System.Windows.Forms.dll",
                            "/reference:System.Drawing.dll", "/reference:System.IO.Compression.dll",
                            "/reference:System.IO.Compression.FileSystem.dll",
                            str(ROOT / "unique_launcher.cs"), str(source)], check=True, capture_output=True)
            subprocess.run([str(executable), str(directory)], check=True, capture_output=True)

if __name__ == "__main__":
    unittest.main()
