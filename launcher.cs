using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class VoiceLocalLauncher
{
    [STAThread]
    private static int Main(string[] args)
    {
        string folder = AppDomain.CurrentDomain.BaseDirectory;
        string interpreter = Path.Combine(folder, ".venv", "Scripts", "pythonw.exe");
        string application = Path.Combine(folder, "app.py");
        bool checkOnly = args.Length == 1 && args[0] == "--check";

        if (!File.Exists(interpreter) || !File.Exists(application))
        {
            if (!checkOnly)
            {
                MessageBox.Show(
                    "Instale o ambiente .venv nesta pasta seguindo o README.md antes de abrir o Voz Local.",
                    "Voz Local", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            return 2;
        }

        if (checkOnly)
            return 0;

        try
        {
            var start = new ProcessStartInfo();
            start.FileName = interpreter;
            start.Arguments = "\"" + application + "\"";
            start.WorkingDirectory = folder;
            start.UseShellExecute = false;
            start.EnvironmentVariables["HF_HOME"] = Path.Combine(folder, "data", "hf");
            Process.Start(start);
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show("Falha ao abrir o Voz Local: " + error.Message,
                "Voz Local", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
