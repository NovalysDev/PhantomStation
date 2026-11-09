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
  string one=PhantomGameJournal.NewSession(args[0],"Grandia II","11111111111111111111111111111111",1);
  string two=PhantomGameJournal.NewSession(args[0],"House: The Dead","22222222222222222222222222222222",1);
  string again=PhantomGameJournal.NewSession(args[0],"Grandia II","11111111111111111111111111111111",1);
  if(one==again || Path.GetDirectoryName(one)!=Path.GetDirectoryName(again) || Path.GetDirectoryName(one)==Path.GetDirectoryName(two)) return 1;
  var a=Task.Run(()=>PhantomGameJournal.Drain(new StringReader("grandia\n[HEARTBEAT] ignore\n"),one+"-moteur.log","Grandia",false));
  var b=Task.Run(()=>PhantomGameJournal.Drain(new StringReader("house\n"),two+"-moteur.log","House",false));
  var c=Task.Run(()=>PhantomGameJournal.Drain(new StringReader("error\n"),two+"-erreurs.log","House",true));
  Task.WaitAll(a,b,c);
  string x=File.ReadAllText(one+"-moteur.log"), y=File.ReadAllText(two+"-moteur.log");
  if(!x.Contains("grandia") || x.Contains("HEARTBEAT") || x.Contains("house") || !y.Contains("house") || !c.Result.Contains("error")) return 2;
  var reader=new StringReader("un\ndeux\n");
  if(!PhantomGameJournal.Drain(reader,Path.Combine(args[0],"absent","test.log"),"test",true).Contains("deux") || reader.ReadLine()!=null) return 3;
  try { PhantomGameJournal.NewSession(args[0],"invalid","../invalid",1); return 4; } catch(ArgumentException) {}
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
