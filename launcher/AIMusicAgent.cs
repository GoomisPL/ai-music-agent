// AI Music Agent - launcher (.NET Framework 4.x, C# 5 - compiled on the target PC by setup.ps1 with csc.exe)
//
//   AIMusicAgent.exe              full run: research -> music -> video -> YouTube upload
//   AIMusicAgent.exe --dry-run    everything except the upload
//   AIMusicAgent.exe --auth       one-time YouTube login
//   AIMusicAgent.exe --check      environment check
//   AIMusicAgent.exe --no-pause   do not wait for Enter at the end (for Task Scheduler)
//   AIMusicAgent.exe --gui        open the control window (app\gui.py, no console window)
//
// The launcher only prepares the environment and starts app\run.py with the local Python.
// It never talks to any cloud AI - the text model is the local Ollama.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

internal static class Program
{
    [DllImport("kernel32.dll")]
    private static extern uint SetThreadExecutionState(uint flags);

    private const uint ES_CONTINUOUS = 0x80000000;
    private const uint ES_SYSTEM_REQUIRED = 0x00000001;

    private static readonly string[] EnvPrefixes = { "AIMUSIC_", "OLLAMA_", "SA3_", "HF_HOME", "PIP_CACHE_DIR", "PYTHON" };

    private static int Main(string[] args)
    {
        try { Console.OutputEncoding = Encoding.UTF8; } catch { }
        Console.Title = "AI Music Agent";

        bool noPause = false;
        bool gui = false;
        List<string> pass = new List<string>();
        foreach (string a in args)
        {
            if (string.Equals(a, "--no-pause", StringComparison.OrdinalIgnoreCase)) noPause = true;
            else if (string.Equals(a, "--gui", StringComparison.OrdinalIgnoreCase)) gui = true;
            else pass.Add(a);
        }
        if (gui)
        {
            int g = 1;
            try { g = StartPanel(); }
            catch (Exception ex) { Write(ConsoleColor.Red, "Blad uruchamiania panelu: " + ex.Message); }
            if (g != 0)
            {
                Console.WriteLine("Nacisnij Enter, aby zamknac okno...");
                Console.ReadLine();
            }
            return g;
        }

        int rc = 1;
        bool createdNew;
        using (Mutex mutex = new Mutex(true, "Global\\AIMusicAgentRun", out createdNew))
        {
            if (!createdNew)
            {
                Console.WriteLine("Agent juz dziala w innym oknie. Koniec.");
                rc = 2;
            }
            else
            {
                try { rc = Run(pass); }
                catch (Exception ex)
                {
                    Console.ForegroundColor = ConsoleColor.Red;
                    Console.WriteLine("Blad uruchamiania: " + ex.Message);
                    Console.ResetColor();
                    rc = 1;
                }
                finally { SetThreadExecutionState(ES_CONTINUOUS); }
            }
        }

        Console.WriteLine();
        if (rc == 0) Write(ConsoleColor.Green, "[OK] Zakonczono.");
        else if (rc == 2) Write(ConsoleColor.Yellow, "[--] Pominieto (inne uruchomienie trwa).");
        else Write(ConsoleColor.Red, "[BLAD] Kod " + rc + ". Szczegoly powyzej i w folderze logs.");
        if (!noPause)
        {
            Console.WriteLine("Nacisnij Enter, aby zamknac okno...");
            Console.ReadLine();
        }
        return rc;
    }

    private static void Write(ConsoleColor color, string text)
    {
        Console.ForegroundColor = color;
        Console.WriteLine(text);
        Console.ResetColor();
    }

    private static string MachineEnv(string name)
    {
        string v = Environment.GetEnvironmentVariable(name, EnvironmentVariableTarget.Machine);
        if (string.IsNullOrEmpty(v)) v = Environment.GetEnvironmentVariable(name, EnvironmentVariableTarget.User);
        if (string.IsNullOrEmpty(v)) v = Environment.GetEnvironmentVariable(name);
        return v;
    }

    private static string FindHome()
    {
        string exeDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        if (File.Exists(Path.Combine(exeDir, "app\\run.py"))) return exeDir;
        string home = MachineEnv("AIMUSIC_HOME");
        if (!string.IsNullOrEmpty(home) && File.Exists(Path.Combine(home, "app\\run.py"))) return home;
        return null;
    }

    // Panel: pythonw.exe app\gui.py - a normal window, no console.
    private static int StartPanel()
    {
        string home = FindHome();
        if (home == null)
        {
            Write(ConsoleColor.Red, "Nie znaleziono workspace (app\\run.py). Uruchom najpierw Uruchom-Setup.cmd.");
            return 1;
        }
        string python = PrepareEnv(home);
        if (python == null) return 1;
        string pyw = Path.Combine(home, ".venv\\Scripts\\pythonw.exe");
        if (!File.Exists(Path.Combine(home, "app\\gui.py")))
        {
            Write(ConsoleColor.Red, "Brak app\\gui.py - uruchom ponownie Uruchom-Setup.cmd (opcja 4).");
            return 1;
        }
        ProcessStartInfo psi = new ProcessStartInfo(File.Exists(pyw) ? pyw : python, Quote(Path.Combine(home, "app\\gui.py")));
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.WorkingDirectory = home;
        Process.Start(psi);
        return 0;
    }

    private static string PrepareEnv(string home)
    {

        // Copy the machine-level variables set by setup.ps1 into this process - works even before re-login.
        foreach (System.Collections.DictionaryEntry e in Environment.GetEnvironmentVariables(EnvironmentVariableTarget.Machine))
        {
            string key = (string)e.Key;
            foreach (string p in EnvPrefixes)
            {
                if (key.StartsWith(p, StringComparison.OrdinalIgnoreCase))
                {
                    Environment.SetEnvironmentVariable(key, (string)e.Value);
                    break;
                }
            }
        }
        Environment.SetEnvironmentVariable("AIMUSIC_HOME", home);
        Environment.SetEnvironmentVariable("PYTHONUTF8", "1");
        Environment.SetEnvironmentVariable("PYTHONIOENCODING", "utf-8");

        string python = Path.Combine(home, ".venv\\Scripts\\python.exe");
        if (!File.Exists(python))
        {
            Write(ConsoleColor.Red, "Brak " + python + ". Uruchom ponownie Uruchom-Setup.cmd (krok 7 - srodowisko Python).");
            return null;
        }
        return python;
    }

    private static int Run(List<string> pass)
    {
        string home = FindHome();
        if (home == null)
        {
            Write(ConsoleColor.Red, "Nie znaleziono workspace (app\\run.py). Uruchom najpierw Uruchom-Setup.cmd.");
            return 1;
        }
        string python = PrepareEnv(home);
        if (python == null) return 1;

        Console.WriteLine("======================================================================");
        Console.WriteLine("  AI Music Agent   " + DateTime.Now.ToString("yyyy-MM-dd HH:mm"));
        Console.WriteLine("  Workspace: " + home);
        Console.WriteLine("  Lokalny model jezykowy (Ollama) - bez chmury.");
        Console.WriteLine("  Komputer nie zasnie, dopoki agent pracuje. Pelny przebieg na CPU moze trwac kilka godzin.");
        Console.WriteLine("======================================================================");
        SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED);

        StringBuilder argLine = new StringBuilder();
        argLine.Append(Quote(Path.Combine(home, "app\\run.py")));
        foreach (string a in pass) { argLine.Append(' ').Append(Quote(a)); }

        ProcessStartInfo psi = new ProcessStartInfo(python, argLine.ToString());
        psi.UseShellExecute = false;
        psi.WorkingDirectory = home;
        using (Process proc = Process.Start(psi))
        {
            proc.WaitForExit();
            return proc.ExitCode;
        }
    }

    private static string Quote(string s)
    {
        if (s.Length > 0 && s.IndexOfAny(new char[] { ' ', '\t', '"' }) < 0) return s;
        return "\"" + s.Replace("\"", "\\\"") + "\"";
    }
}
