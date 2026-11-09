using System;
using System.IO;

// Prepare a complete VMU before launching older engines that cannot create it.
internal static class PhantomDreamcastVmu {
    static void Put16(byte[] data, int at, int value) {
        data[at] = (byte)value; data[at + 1] = (byte)(value >> 8);
    }

    static byte[] Blank() {
        var data = new byte[256 * 512];
        int root = 255 * 512, fat = 254 * 512;
        for (int i = 0; i < 16; ++i) data[root + i] = 0x55;
        int[,] fields = { {0x40,255}, {0x42,0}, {0x44,255}, {0x46,254}, {0x48,1},
                          {0x4a,253}, {0x4c,13}, {0x50,200}, {0x52,31}, {0x54,0}, {0x56,128} };
        for (int i = 0; i < fields.GetLength(0); ++i) Put16(data, root + fields[i,0], fields[i,1]);
        for (int i = 0; i < 256; ++i) Put16(data, fat + 2 * i, 0xfffc);
        Put16(data, fat + 2 * 255, 0xfffa); Put16(data, fat + 2 * 254, 0xfffa);
        for (int i = 253; i > 241; --i) Put16(data, fat + 2 * i, i - 1);
        Put16(data, fat + 2 * 241, 0xfffa);
        return data;
    }

    public static string Prepare(string saves) {
        Directory.CreateDirectory(saves);
        string target = Path.Combine(saves, "carte-dreamcast.vmu");
        // A previous engine created a directory with the card's name. Keep it
        // untouched and use another file so no personal data is overwritten.
        if (Directory.Exists(target)) target = Path.Combine(saves, "carte-dreamcast-reparee.vmu");
        if (Directory.Exists(target)) throw new IOException("Le chemin de la carte Dreamcast est un dossier.");
        if (!File.Exists(target)) {
            string temp = target + "." + Guid.NewGuid().ToString("N") + ".part";
            try {
                using (var output = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                    byte[] blank = Blank(); output.Write(blank, 0, blank.Length); output.Flush(true);
                }
                try { File.Move(temp, target); }
                catch (IOException) { if (!File.Exists(target)) throw; }
            } finally { if (File.Exists(temp)) File.Delete(temp); }
        }
        if (new FileInfo(target).Length != 256 * 512)
            throw new IOException("La carte Dreamcast existe mais sa taille est invalide ; elle a été conservée.");
        return target;
    }
}
