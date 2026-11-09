using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Windows.Forms;

internal static class PhantomCompiler {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetDllDirectory(string path);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr LoadLibrary(string path);
    [DllImport("kernel32.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    private static extern IntPtr GetProcAddress(IntPtr module, string name);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void SetProgramName(IntPtr name);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int PyMain(int argc, IntPtr argv);

    private static int RunPortablePython(string home, string script) {
        string interpreter = Path.Combine(home, "pythonw.exe");
        string dll = null;
        foreach (string file in Directory.GetFiles(home, "python3*.dll")) {
            if (!Regex.IsMatch(Path.GetFileName(file), @"^python3\d{1,2}\.dll$", RegexOptions.IgnoreCase)) continue;
            if (dll != null) throw new InvalidOperationException("Plusieurs versions de Python sont présentes dans le programme.");
            dll = file;
        }
        if (dll == null) throw new FileNotFoundException("Le moteur Python intégré est introuvable.");
        Environment.SetEnvironmentVariable("PYTHONHOME", home);
        Environment.SetEnvironmentVariable("PYTHONPATH", null);
        Environment.SetEnvironmentVariable("PYTHONUSERBASE", null);
        Environment.SetEnvironmentVariable("VIRTUAL_ENV", null);
        Environment.SetEnvironmentVariable("PYTHONNOUSERSITE", "1");
        if (!SetDllDirectory(home)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        IntPtr module = LoadLibrary(dll);
        if (module == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        IntPtr setAddress = GetProcAddress(module, "Py_SetProgramName");
        IntPtr mainAddress = GetProcAddress(module, "Py_Main");
        if (setAddress == IntPtr.Zero || mainAddress == IntPtr.Zero)
            throw new InvalidOperationException("Le moteur Python intégré est incompatible.");
        var setName = (SetProgramName)Marshal.GetDelegateForFunctionPointer(setAddress, typeof(SetProgramName));
        var main = (PyMain)Marshal.GetDelegateForFunctionPointer(mainAddress, typeof(PyMain));
        string[] values = {interpreter, script};
        IntPtr program = Marshal.StringToHGlobalUni(interpreter);
        IntPtr array = Marshal.AllocHGlobal(IntPtr.Size * (values.Length + 1));
        var strings = new IntPtr[values.Length];
        try {
            setName(program);
            for (int i = 0; i < values.Length; i++) {
                strings[i] = Marshal.StringToHGlobalUni(values[i]);
                Marshal.WriteIntPtr(array, i * IntPtr.Size, strings[i]);
            }
            Marshal.WriteIntPtr(array, values.Length * IntPtr.Size, IntPtr.Zero);
            return main(values.Length, array);
        } finally {
            foreach (IntPtr value in strings) if (value != IntPtr.Zero) Marshal.FreeHGlobal(value);
            Marshal.FreeHGlobal(array);
            Marshal.FreeHGlobal(program);
        }
    }

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
        bool hasPortablePython = File.Exists(portable);
        string python = hasPortablePython ? portable : "pythonw.exe";
        try {
            if (hasPortablePython) {
                int result = RunPortablePython(Path.GetDirectoryName(portable), script);
                if (result != 0) throw new InvalidOperationException("L’interface s’est arrêtée (code " + result + ").");
                return;
            }
            var start = new ProcessStartInfo(python, "\"" + script + "\"") {
                WorkingDirectory = directory, UseShellExecute = false, CreateNoWindow = true
            };
            Process.Start(start);
        } catch (Exception ex) {
            MessageBox.Show("Impossible d'ouvrir PhantomStation. Python avec Tkinter et Pillow est nécessaire.\n\n" + ex.Message,
                            "PhantomStation", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
