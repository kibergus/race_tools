using System;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Reflection;
using System.Diagnostics;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32;

namespace BrBrDbTelemetry
{
    public static class InstallManager
    {
        // Retrieve the version of the bundled DLL dynamically from the embedded resource
        private static string _cachedBundledVersion = null;
        public static string BundledVersion
        {
            get
            {
                if (_cachedBundledVersion == null)
                {
                    string tempPath = null;
                    try
                    {
                        string tempDir = Path.GetTempPath();
                        string tempFileName = string.Format("BrBrDbTelemetry64_temp_{0}.dll", Guid.NewGuid().ToString("N"));
                        tempPath = Path.Combine(tempDir, tempFileName);
                        ExtractEmbeddedResource("BrBrDbTelemetry64.dll", tempPath);
                        
                        var vi = FileVersionInfo.GetVersionInfo(tempPath);
                        if (vi.FileMajorPart == 0 && vi.FileMinorPart == 0 && vi.FileBuildPart == 0)
                        {
                            throw new InvalidOperationException("Could not retrieve version details from the embedded DLL.");
                        }
                        _cachedBundledVersion = string.Format("{0}.{1}.{2}", vi.FileMajorPart, vi.FileMinorPart, vi.FileBuildPart);
                    }
                    finally
                    {
                        if (tempPath != null && File.Exists(tempPath))
                        {
                            try
                            {
                                File.Delete(tempPath);
                            }
                            catch { }
                        }
                    }
                }
                return _cachedBundledVersion;
            }
        }

        private static string _cachedBundledAceVersion = null;
        public static string BundledAceVersion => BundledDaemonVersion;

        public static string BundledDaemonVersion
        {
            get
            {
                if (_cachedBundledAceVersion == null)
                {
                    string tempPath = null;
                    try
                    {
                        string tempDir = Path.GetTempPath();
                        string tempFileName = string.Format("brbrdaemon_temp_{0}.exe", Guid.NewGuid().ToString("N"));
                        tempPath = Path.Combine(tempDir, tempFileName);
                        try
                        {
                            ExtractEmbeddedResource("brbrdaemon.exe", tempPath);
                        }
                        catch
                        {
                            ExtractEmbeddedResource("BrBrDbTelemetryAce.exe", tempPath);
                        }
                        
                        var vi = FileVersionInfo.GetVersionInfo(tempPath);
                        if (vi.FileMajorPart == 0 && vi.FileMinorPart == 0 && vi.FileBuildPart == 0)
                        {
                            throw new InvalidOperationException("Could not retrieve version details from the embedded daemon executable.");
                        }
                        _cachedBundledAceVersion = string.Format("{0}.{1}.{2}", vi.FileMajorPart, vi.FileMinorPart, vi.FileBuildPart);
                    }
                    catch
                    {
                        _cachedBundledAceVersion = "1.0.0";
                    }
                    finally
                    {
                        if (tempPath != null && File.Exists(tempPath))
                        {
                            try { File.Delete(tempPath); } catch { }
                        }
                    }
                }
                return _cachedBundledAceVersion;
            }
        }

        // Locates the Assetto Corsa Evo directory automatically via Steam registry/libraries
        public static string DetectACEvoPath()
        {
            try
            {
                using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam"))
                {
                    if (key != null)
                    {
                        string steamPath = key.GetValue("SteamPath") as string;
                        if (!string.IsNullOrEmpty(steamPath))
                        {
                            steamPath = steamPath.Replace('/', '\\');
                            
                            // 1. Check primary Steam library path
                            string primaryPath = Path.Combine(steamPath, @"steamapps\common\Assetto Corsa Evo");
                            if (IsValidACEvoDirectory(primaryPath))
                                return primaryPath;

                            // 2. Parse libraryfolders.vdf for secondary libraries
                            string vdfPath = Path.Combine(steamPath, @"steamapps\libraryfolders.vdf");
                            if (File.Exists(vdfPath))
                            {
                                var paths = ParseLibraryFolders(vdfPath);
                                foreach (var libPath in paths)
                                {
                                    string checkPath = Path.Combine(libPath, @"steamapps\common\Assetto Corsa Evo");
                                    if (IsValidACEvoDirectory(checkPath))
                                        return checkPath;
                                }
                            }
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine("Registry path detection failed: " + ex.Message);
            }

            return "";
        }

        public static bool IsValidACEvoDirectory(string path)
        {
            if (string.IsNullOrEmpty(path) || !Directory.Exists(path))
                return false;

            return true;
        }

        public static string GetDefaultDaemonInstallDirectory(string sim = "ace")
        {
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return Path.Combine(localAppData, "BrBrDbTelemetry");
        }

        public static string GetDefaultAceInstallDirectory() => GetDefaultDaemonInstallDirectory("ace");
        public static string GetDefaultAcrInstallDirectory() => GetDefaultDaemonInstallDirectory("acr");
        public static string GetDefaultAcInstallDirectory() => GetDefaultDaemonInstallDirectory("ac");

        public static string GetDefaultLeagueForSim(string sim)
        {
            switch (sim)
            {
                case "acr": return "acr";
                case "ac": return "ac";
                case "ace": return "acevo";
                case "rf2": return "kartsim";
                default: return "acevo";
            }
        }

        public static string GetSimulatorDisplayName(string sim)
        {
            switch (sim)
            {
                case "acr": return "Assetto Corsa Rally";
                case "ac": return "Assetto Corsa";
                case "ace": return "Assetto Corsa Evo";
                case "rf2": return "rFactor 2";
                default: return sim;
            }
        }

        public static bool GetInstalledDaemonDetails(string sim, string targetDir, out string version, out bool isRunning, out bool isAutostart)
        {
            version = "";
            isRunning = false;
            isAutostart = false;

            if (string.IsNullOrEmpty(targetDir))
                targetDir = GetDefaultDaemonInstallDirectory(sim);

            string exePath = Path.Combine(targetDir, "brbrdaemon.exe");
            if (!File.Exists(exePath))
                exePath = Path.Combine(targetDir, "BrBrDbTelemetryAce.exe");
            if (!File.Exists(exePath))
                return false;

            try
            {
                var vi = FileVersionInfo.GetVersionInfo(exePath);
                version = string.Format("{0}.{1}.{2}", vi.FileMajorPart, vi.FileMinorPart, vi.FileBuildPart);
            }
            catch
            {
                version = "0.0.0";
            }

            try
            {
                isRunning = IsDaemonRunning();
            }
            catch { }

            isAutostart = IsDaemonAutostartEnabled(sim, targetDir);
            return true;
        }

        public static bool GetInstalledAceDetails(string targetDir, out string version, out bool isRunning, out bool isAutostart)
            => GetInstalledDaemonDetails("ace", targetDir, out version, out isRunning, out isAutostart);

        public static void InstallDaemonPlugin(string sim, string targetDir, string apiKey, string defaultTelemetryPath, bool enableAutostart, string installerSourcePath = null)
        {
            if (string.IsNullOrEmpty(targetDir))
                targetDir = GetDefaultDaemonInstallDirectory(sim);

            if (!Directory.Exists(targetDir))
                Directory.CreateDirectory(targetDir);

            StopDaemon();

            string exePath = Path.Combine(targetDir, "brbrdaemon.exe");
            try
            {
                ExtractEmbeddedResource("brbrdaemon.exe", exePath);
            }
            catch
            {
                try
                {
                    ExtractEmbeddedResource("BrBrDbTelemetryAce.exe", exePath);
                }
                catch
                {
                    if (!File.Exists(exePath))
                        File.WriteAllText(exePath, "");
                }
            }

            // Remove legacy executable if present
            string legacyExe = Path.Combine(targetDir, "BrBrDbTelemetryAce.exe");
            if (File.Exists(legacyExe) && !string.Equals(legacyExe, exePath, StringComparison.OrdinalIgnoreCase))
            {
                try { File.Delete(legacyExe); } catch { }
            }

            // Copy installer binary itself to target directory so daemon can launch it
            try
            {
                string sourceExe = installerSourcePath;
                if (string.IsNullOrEmpty(sourceExe))
                {
                    sourceExe = Environment.ProcessPath;
                    if (string.IsNullOrEmpty(sourceExe))
                    {
                        sourceExe = Process.GetCurrentProcess().MainModule?.FileName;
                    }
                }

                if (!string.IsNullOrEmpty(sourceExe) && File.Exists(sourceExe))
                {
                    string targetInstallerExe = Path.Combine(targetDir, "BrBrDbTelemetryInstaller.exe");
                    if (!string.Equals(Path.GetFullPath(sourceExe), Path.GetFullPath(targetInstallerExe), StringComparison.OrdinalIgnoreCase))
                    {
                        File.Copy(sourceExe, targetInstallerExe, true);
                    }
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine("Failed to copy installer executable to target directory: " + ex.Message);
            }

            string configPath = Path.Combine(targetDir, "BrBrDbTelemetry.json");
            PluginConfig config = null;
            bool isNewConfig = !File.Exists(configPath);

            if (!isNewConfig)
            {
                try
                {
                    string json = File.ReadAllText(configPath);
                    config = ConfigHelper.Deserialize(json);
                }
                catch
                {
                    config = new PluginConfig();
                    isNewConfig = true;
                }
            }
            else
            {
                config = new PluginConfig();
            }

            config.simulator = "auto";
            if (!config.simulators.ContainsKey("ace"))
            {
                config.simulators["ace"] = new SimulatorSettings { league = "acevo", car_name_regexp = ".*" };
            }
            if (!config.simulators.ContainsKey("acr"))
            {
                config.simulators["acr"] = new SimulatorSettings { league = "acr", car_name_regexp = ".*" };
            }
            if (!config.simulators.ContainsKey("ac"))
            {
                config.simulators["ac"] = new SimulatorSettings { league = "ac", car_name_regexp = ".*" };
            }

            if (isNewConfig || string.IsNullOrEmpty(config.league))
            {
                config.car_name_regexp = ".*";
                config.league = "auto";
            }

            SinkConfig fileSink = config.sinks.Find(s => s.type == "file") ?? new SinkConfig { type = "file" };
            if (isNewConfig || !string.IsNullOrEmpty(defaultTelemetryPath))
                fileSink.path = string.IsNullOrEmpty(defaultTelemetryPath) ? "" : defaultTelemetryPath;
            if (isNewConfig)
                fileSink.enabled = true;

            SinkConfig httpSink = config.sinks.Find(s => s.type == "http") ?? new SinkConfig { type = "http" };
            if (isNewConfig)
                httpSink.server_address = "https://brbrdb.brbrkitten.com";
            if (!string.IsNullOrEmpty(apiKey))
            {
                httpSink.api_key = apiKey;
                httpSink.enabled = true;
            }
            else if (string.IsNullOrEmpty(httpSink.api_key))
            {
                httpSink.api_key = "";
                if (isNewConfig)
                    httpSink.enabled = false;
            }

            config.sinks.Clear();
            config.sinks.Add(httpSink);
            config.sinks.Add(fileSink);

            string configJson = ConfigHelper.Serialize(config);
            File.WriteAllText(configPath, configJson, Encoding.UTF8);

            SetDaemonAutostart(sim, targetDir, enableAutostart);
            StartDaemon(targetDir);
        }

        public static void InstallAcePlugin(string targetDir, string apiKey, string defaultTelemetryPath, bool enableAutostart)
            => InstallDaemonPlugin("ace", targetDir, apiKey, defaultTelemetryPath, enableAutostart);

        public static void UninstallDaemonPlugin(string sim, string targetDir)
        {
            if (string.IsNullOrEmpty(targetDir))
                targetDir = GetDefaultDaemonInstallDirectory(sim);

            StopDaemon();
            SetDaemonAutostart(sim, targetDir, false);

            string exePath = Path.Combine(targetDir, "brbrdaemon.exe");
            if (File.Exists(exePath))
                File.Delete(exePath);

            string legacyExe = Path.Combine(targetDir, "BrBrDbTelemetryAce.exe");
            if (File.Exists(legacyExe))
                File.Delete(legacyExe);

            string installerExe = Path.Combine(targetDir, "BrBrDbTelemetryInstaller.exe");
            if (File.Exists(installerExe))
            {
                try { File.Delete(installerExe); } catch { }
            }

            string configPath = Path.Combine(targetDir, "BrBrDbTelemetry.json");
            if (File.Exists(configPath))
                File.Delete(configPath);
        }

        public static void UninstallAcePlugin(string targetDir) => UninstallDaemonPlugin("ace", targetDir);

        public static void SetDaemonAutostart(string sim, string targetDir, bool enable)
        {
            try
            {
                using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true))
                {
                    if (key != null)
                    {
                        if (enable)
                        {
                            string exePath = Path.Combine(targetDir, "brbrdaemon.exe");
                            if (!File.Exists(exePath))
                                exePath = Path.Combine(targetDir, "BrBrDbTelemetryAce.exe");
                            key.SetValue("brbrdaemon", "\"" + exePath + "\" --tray");
                            key.DeleteValue("brbrdaemon_ace", false);
                            key.DeleteValue("brbrdaemon_acr", false);
                            key.DeleteValue("brbrdaemon_ac", false);
                            key.DeleteValue("BrBrDbTelemetryAce", false);
                        }
                        else
                        {
                            key.DeleteValue("brbrdaemon", false);
                            key.DeleteValue("brbrdaemon_ace", false);
                            key.DeleteValue("brbrdaemon_acr", false);
                            key.DeleteValue("brbrdaemon_ac", false);
                            key.DeleteValue("BrBrDbTelemetryAce", false);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine("Failed to set autostart: " + ex.Message);
            }
        }

        public static void SetAceAutostart(string targetDir, bool enable) => SetDaemonAutostart("ace", targetDir, enable);

        public static bool IsDaemonAutostartEnabled(string sim, string targetDir)
        {
            try
            {
                using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", false))
                {
                    if (key != null)
                    {
                        if (key.GetValue("brbrdaemon") != null)
                            return true;
                        if (key.GetValue("brbrdaemon_ace") != null || key.GetValue("brbrdaemon_acr") != null || key.GetValue("brbrdaemon_ac") != null || key.GetValue("BrBrDbTelemetryAce") != null)
                            return true;
                    }
                }
            }
            catch { }
            return false;
        }

        public static bool IsAceAutostartEnabled(string targetDir) => IsDaemonAutostartEnabled("ace", targetDir);

        public static bool IsDaemonRunning()
        {
            try
            {
                return Process.GetProcessesByName("brbrdaemon").Length > 0 ||
                       Process.GetProcessesByName("BrBrDbTelemetryAce").Length > 0;
            }
            catch
            {
                return false;
            }
        }

        public static bool IsAceDaemonRunning() => IsDaemonRunning();

        public static void StartDaemon(string targetDir)
        {
            try
            {
                if (string.IsNullOrEmpty(targetDir))
                    targetDir = GetDefaultAceInstallDirectory();

                string exePath = Path.Combine(targetDir, "brbrdaemon.exe");
                if (!File.Exists(exePath))
                    exePath = Path.Combine(targetDir, "BrBrDbTelemetryAce.exe");

                if (File.Exists(exePath) && !IsDaemonRunning())
                {
                    var psi = new ProcessStartInfo
                    {
                        FileName = exePath,
                        Arguments = "--tray",
                        WorkingDirectory = targetDir,
                        UseShellExecute = true
                    };
                    Process.Start(psi);
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine("Failed to start daemon: " + ex.Message);
            }
        }

        public static void StartAceDaemon(string targetDir) => StartDaemon(targetDir);

        public static void StopDaemon()
        {
            try
            {
                // Signal graceful stop event
                try
                {
                    if (EventWaitHandle.TryOpenExisting(@"Local\brbrdaemon_stop_event", out var stopEvent))
                    {
                        using (stopEvent)
                        {
                            stopEvent.Set();
                        }
                    }
                }
                catch { }

                var processes = Process.GetProcessesByName("brbrdaemon")
                    .Concat(Process.GetProcessesByName("BrBrDbTelemetryAce"));
                foreach (var p in processes)
                {
                    try
                    {
                        p.CloseMainWindow();
                        if (!p.WaitForExit(3000))
                        {
                            p.Kill();
                            p.WaitForExit(1000);
                        }
                    }
                    catch { }
                }
            }
            catch { }
        }

        public static void StopAceDaemon() => StopDaemon();

        public static void RestartDaemon(string targetDir)
        {
            StopDaemon();
            int retries = 10;
            while (IsDaemonRunning() && retries > 0)
            {
                System.Threading.Thread.Sleep(100);
                retries--;
            }
            StartDaemon(targetDir);
        }

        public static void RestartAceDaemon(string targetDir) => RestartDaemon(targetDir);

        public static bool RestartDaemonIfRunning(string targetDir)
        {
            if (IsDaemonRunning())
            {
                RestartDaemon(targetDir);
                return true;
            }
            return false;
        }

        public static bool RestartAceDaemonIfRunning(string targetDir) => RestartDaemonIfRunning(targetDir);

        public static string GetDaemonLogFilePath(string targetDir)
        {
            if (string.IsNullOrEmpty(targetDir))
                targetDir = GetDefaultAceInstallDirectory();
            string newLog = Path.Combine(targetDir, "brbrdaemon.log");
            if (File.Exists(newLog))
                return newLog;
            string oldLog = Path.Combine(targetDir, "BrBrDbTelemetryAce.log");
            if (File.Exists(oldLog))
                return oldLog;
            return newLog;
        }

        public static string GetAceLogFilePath(string targetDir) => GetDaemonLogFilePath(targetDir);

        public static string GetAceSessionsDirectoryPath(string telemetryPath, string targetDir)
        {
            if (!string.IsNullOrEmpty(telemetryPath))
                return telemetryPath;
            if (string.IsNullOrEmpty(targetDir))
                targetDir = GetDefaultAceInstallDirectory();
            return Path.Combine(targetDir, "Telemetry");
        }

        // Locates the rFactor 2 directory automatically
        // Locates the rFactor 2 directory automatically via Steam registry/libraries
        public static string DetectRFactor2Path()
        {
            try
            {
                using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam"))
                {
                    if (key != null)
                    {
                        string steamPath = key.GetValue("SteamPath") as string;
                        if (!string.IsNullOrEmpty(steamPath))
                        {
                            steamPath = steamPath.Replace('/', '\\');
                            
                            // 1. Check primary Steam library path
                            string primaryPath = Path.Combine(steamPath, @"steamapps\common\rFactor 2");
                            if (IsValidRF2Directory(primaryPath))
                                return primaryPath;

                            // 2. Parse libraryfolders.vdf for secondary libraries
                            string vdfPath = Path.Combine(steamPath, @"steamapps\libraryfolders.vdf");
                            if (File.Exists(vdfPath))
                            {
                                var paths = ParseLibraryFolders(vdfPath);
                                foreach (var libPath in paths)
                                {
                                    string checkPath = Path.Combine(libPath, @"steamapps\common\rFactor 2");
                                    if (IsValidRF2Directory(checkPath))
                                        return checkPath;
                                }
                            }
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine("Registry path detection failed: " + ex.Message);
            }

            return "";
        }

        public static bool IsValidRF2Directory(string path)
        {
            if (string.IsNullOrEmpty(path) || !Directory.Exists(path))
                return false;

            // Must contain Bin64\Plugins
            string pluginsPath = Path.Combine(path, @"Bin64\Plugins");
            return Directory.Exists(pluginsPath);
        }

        public static List<string> ParseLibraryFolders(string vdfPath)
        {
            var paths = new List<string>();
            string text = File.ReadAllText(vdfPath);
            // Extract values matching "path" "..."
            var matches = Regex.Matches(text, @"""path""\s+""([^""]+)""");
            foreach (Match match in matches)
            {
                if (match.Groups.Count > 1)
                {
                    string p = match.Groups[1].Value.Replace(@"\\", @"\");
                    if (Directory.Exists(p))
                    {
                        paths.Add(p);
                    }
                }
            }
            return paths;
        }

        // Checks if the dll is installed and reads its FileVersion
        public static bool GetInstalledPluginDetails(string rf2Path, out string version)
        {
            version = "";
            string dllPath = Path.Combine(rf2Path, @"Bin64\Plugins\BrBrDbTelemetry64.dll");
            if (!File.Exists(dllPath))
                return false;

            try
            {
                var vi = FileVersionInfo.GetVersionInfo(dllPath);
                version = string.Format("{0}.{1}.{2}", vi.FileMajorPart, vi.FileMinorPart, vi.FileBuildPart);
            }
            catch
            {
                version = "0.0.0";
            }
            return true;
        }

        // Extracts embedded DLL and writes config
        public static void InstallPlugin(string rf2Path, string apiKey, string defaultTelemetryPath)
        {
            string pluginsDir = Path.Combine(rf2Path, @"Bin64\Plugins");
            if (!Directory.Exists(pluginsDir))
                Directory.CreateDirectory(pluginsDir);

            // 1. Write the DLL file from resources
            string dllPath = Path.Combine(pluginsDir, "BrBrDbTelemetry64.dll");
            ExtractEmbeddedResource("BrBrDbTelemetry64.dll", dllPath);

            // 2. Setup config — preserve existing config if present, only create defaults for fresh installs
            string configPath = Path.Combine(pluginsDir, "BrBrDbTelemetry.json");
            PluginConfig config = null;
            bool isNewConfig = !File.Exists(configPath);

            if (!isNewConfig)
            {
                try
                {
                    string json = File.ReadAllText(configPath);
                    config = ConfigHelper.Deserialize(json);
                }
                catch
                {
                    config = new PluginConfig();
                    isNewConfig = true;
                }
            }
            else
            {
                config = new PluginConfig();
            }

            // For fresh installs, apply default values
            if (isNewConfig)
            {
                config.car_name_regexp = ".*";
                config.league = "kartsim";
            }

            // Make sure sinks exist
            SinkConfig fileSink = config.sinks.Find(s => s.type == "file") ?? new SinkConfig { type = "file" };
            if (isNewConfig || !string.IsNullOrEmpty(defaultTelemetryPath))
                fileSink.path = string.IsNullOrEmpty(defaultTelemetryPath) ? "" : defaultTelemetryPath;
            if (isNewConfig)
                fileSink.enabled = true;

            SinkConfig httpSink = config.sinks.Find(s => s.type == "http") ?? new SinkConfig { type = "http" };
            if (isNewConfig)
                httpSink.server_address = "https://brbrdb.brbrkitten.com";
            if (!string.IsNullOrEmpty(apiKey))
            {
                httpSink.api_key = apiKey;
                httpSink.enabled = true;
            }
            else if (string.IsNullOrEmpty(httpSink.api_key))
            {
                httpSink.api_key = "";
                if (isNewConfig)
                    httpSink.enabled = false;
            }

            // Ensure http sink is first, then file sink
            config.sinks.Clear();
            config.sinks.Add(httpSink);
            config.sinks.Add(fileSink);

            // Save config
            string configJson = ConfigHelper.Serialize(config);
            File.WriteAllText(configPath, configJson, Encoding.UTF8);
        }

        // Uninstall DLL and config
        public static void UninstallPlugin(string rf2Path)
        {
            string pluginsDir = Path.Combine(rf2Path, @"Bin64\Plugins");
            string dllPath = Path.Combine(pluginsDir, "BrBrDbTelemetry64.dll");
            string configPath = Path.Combine(pluginsDir, "BrBrDbTelemetry.json");

            if (File.Exists(dllPath))
                File.Delete(dllPath);

            if (File.Exists(configPath))
                File.Delete(configPath);

            // Deactivate in CustomPluginVariables.json
            SetPluginActivation(rf2Path, false);
        }

        // Enables or disables the plugin in the game's player variables JSON
        public static void SetPluginActivation(string rf2Path, bool enable)
        {
            string playerDir = Path.Combine(rf2Path, @"UserData\player");
            string configPath = Path.Combine(playerDir, "CustomPluginVariables.json");

            if (!File.Exists(configPath))
            {
                // If it doesn't exist, we don't strictly need to force create it, but we can write a basic layout
                if (enable)
                {
                    if (!Directory.Exists(playerDir))
                        Directory.CreateDirectory(playerDir);
                    string basicJson = "{\n  \"BrBrDbTelemetry64.dll\": {\n    \" Enabled\": 1\n  }\n}";
                    File.WriteAllText(configPath, basicJson);
                }
                return;
            }

            try
            {
                string content = File.ReadAllText(configPath);

                // Regex to find "rF2_TelemetryExtractor64.dll" and modify its " Enabled" key.
                // Format:
                // "rF2_TelemetryExtractor64.dll": {
                //   " Enabled": 1,
                //   ...
                // }
                // Note: The leading space in " Enabled" is critical for rFactor 2.

                string dllKey = "BrBrDbTelemetry64.dll";
                int dllIndex = content.IndexOf(dllKey);

                if (dllIndex >= 0)
                {
                    // Find the next block starting with { and ending with }
                    int braceOpen = content.IndexOf('{', dllIndex);
                    if (braceOpen > 0)
                    {
                        int braceClose = content.IndexOf('}', braceOpen);
                        if (braceClose > 0)
                        {
                            string subBlock = content.Substring(braceOpen, braceClose - braceOpen + 1);
                            
                            // Replace " Enabled": X with " Enabled": 1 or 0
                            string enabledPattern = @"""\s+Enabled""\s*:\s*[0-9]+";
                            string newEnabled = "\" Enabled\":" + (enable ? "1" : "0");

                            if (Regex.IsMatch(subBlock, enabledPattern))
                            {
                                string newSubBlock = Regex.Replace(subBlock, enabledPattern, newEnabled);
                                content = content.Substring(0, braceOpen) + newSubBlock + content.Substring(braceClose + 1);
                            }
                            else
                            {
                                // Insert it after {
                                string insertText = "\n    " + newEnabled + ",";
                                string newSubBlock = "{" + insertText + subBlock.Substring(1);
                                content = content.Substring(0, braceOpen) + newSubBlock + content.Substring(braceClose + 1);
                            }
                        }
                    }
                }
                else if (enable)
                {
                    // DLL key doesn't exist yet in CustomPluginVariables, we need to append it
                    // Remove ending } of the root object and append our DLL entry
                    int lastBrace = content.LastIndexOf('}');
                    if (lastBrace >= 0)
                    {
                        var sb = new StringBuilder(content.Substring(0, lastBrace));
                        
                        // Check if we need a comma
                        string prefix = "";
                        string trimmed = content.Substring(0, lastBrace).TrimEnd();
                        if (trimmed.Length > 0 && trimmed[trimmed.Length - 1] != '{' && trimmed[trimmed.Length - 1] != ',')
                        {
                            prefix = ",\n";
                        }
                        
                        sb.Append(prefix);
                        sb.AppendLine("  \"BrBrDbTelemetry64.dll\": {");
                        sb.AppendLine("    \" Enabled\": 1");
                        sb.AppendLine("  }");
                        sb.Append("}");
                        content = sb.ToString();
                    }
                }

                File.WriteAllText(configPath, content, Encoding.UTF8);
            }
            catch (Exception ex)
            {
                Debug.WriteLine("Failed to modify CustomPluginVariables.json: " + ex.Message);
            }
        }

        // Reads the enabled status in CustomPluginVariables.json
        public static bool IsPluginActiveInGame(string rf2Path)
        {
            string configPath = Path.Combine(rf2Path, @"UserData\player\CustomPluginVariables.json");
            if (!File.Exists(configPath))
                return false;

            try
            {
                string content = File.ReadAllText(configPath);
                string dllKey = "BrBrDbTelemetry64.dll";
                int dllIndex = content.IndexOf(dllKey);
                if (dllIndex >= 0)
                {
                    int braceOpen = content.IndexOf('{', dllIndex);
                    if (braceOpen > 0)
                    {
                        int braceClose = content.IndexOf('}', braceOpen);
                        if (braceClose > 0)
                        {
                            string subBlock = content.Substring(braceOpen, braceClose - braceOpen + 1);
                            var match = Regex.Match(subBlock, @"""\s+Enabled""\s*:\s*([0-9]+)");
                            if (match.Success && match.Groups.Count > 1)
                            {
                                return match.Groups[1].Value == "1";
                            }
                        }
                    }
                }
            }
            catch
            {
                // ignore
            }
            return false;
        }

        // Extract binary DLL from resources to disk
        private static void ExtractEmbeddedResource(string resourceName, string outputPath)
        {
            var assembly = Assembly.GetExecutingAssembly();
            
            // Resource names can be prefixed by the namespace, e.g. "BrBrDbTelemetry.BrBrDbTelemetry64.dll"
            string actualResourceName = "";
            foreach (var name in assembly.GetManifestResourceNames())
            {
                if (name.EndsWith(resourceName))
                {
                    actualResourceName = name;
                    break;
                }
            }

            if (string.IsNullOrEmpty(actualResourceName))
                throw new Exception("Embedded resource not found: " + resourceName);

            using (Stream resourceStream = assembly.GetManifestResourceStream(actualResourceName))
            {
                if (resourceStream == null)
                    throw new Exception("Failed to load stream for resource: " + resourceName);

                using (FileStream fileStream = new FileStream(outputPath, FileMode.Create, FileAccess.Write))
                {
                    resourceStream.CopyTo(fileStream);
                }
            }
        }

        public static bool IsInstalledVersionNewer(string installed, string bundled)
        {
            return CompareVersions(installed, bundled) > 0;
        }

        public static string NormalizeVersionString(string rawVersion)
        {
            if (string.IsNullOrWhiteSpace(rawVersion))
                return "0.0.0";

            string cleaned = rawVersion.Trim();
            if (cleaned.StartsWith("v", StringComparison.OrdinalIgnoreCase))
                cleaned = cleaned.Substring(1);

            int dashIdx = cleaned.IndexOfAny(new[] { '-', '+' });
            if (dashIdx >= 0)
                cleaned = cleaned.Substring(0, dashIdx);

            var parts = cleaned.Split('.');
            var intParts = new List<int>();
            foreach (var p in parts)
            {
                if (int.TryParse(p, out int val))
                    intParts.Add(val);
                else
                    break;
            }

            while (intParts.Count < 3)
                intParts.Add(0);

            if (intParts.Count > 4)
                intParts = intParts.GetRange(0, 4);

            return string.Join(".", intParts);
        }

        public static int CompareVersions(string versionA, string versionB)
        {
            string normA = NormalizeVersionString(versionA);
            string normB = NormalizeVersionString(versionB);

            if (Version.TryParse(normA, out Version vA) && Version.TryParse(normB, out Version vB))
            {
                return vA.CompareTo(vB);
            }
            return 0;
        }

        public static bool IsRemoteVersionNewer(string remoteVersion, string localVersion)
        {
            return CompareVersions(remoteVersion, localVersion) > 0;
        }

        public class GitHubReleaseInfo
        {
            public string TagName { get; set; } = "";
            public string NormalizedVersion { get; set; } = "";
            public string Name { get; set; } = "";
            public string HtmlUrl { get; set; } = "";
            public string ExeDownloadUrl { get; set; } = "";
            public DateTime? PublishedAt { get; set; }
            public bool IsNewer { get; set; }
            public string ErrorMessage { get; set; }

            public bool Success => string.IsNullOrEmpty(ErrorMessage);
        }

        private static readonly HttpClient _updateHttpClient = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(8)
        };

        public static GitHubReleaseInfo ParseGitHubReleaseJson(string json, string currentVersion)
        {
            if (string.IsNullOrWhiteSpace(json))
                return new GitHubReleaseInfo { ErrorMessage = "Empty response received from GitHub." };

            try
            {
                using (var doc = JsonDocument.Parse(json))
                {
                    var root = doc.RootElement;
                    if (root.TryGetProperty("message", out var msgProp) && !root.TryGetProperty("tag_name", out _))
                    {
                        return new GitHubReleaseInfo { ErrorMessage = msgProp.GetString() ?? "Unknown GitHub API message." };
                    }

                    string tagName = root.TryGetProperty("tag_name", out var tagProp) ? tagProp.GetString() ?? "" : "";
                    string name = root.TryGetProperty("name", out var nameProp) ? nameProp.GetString() ?? "" : "";
                    string htmlUrl = root.TryGetProperty("html_url", out var htmlProp) ? htmlProp.GetString() ?? "" : "";

                    DateTime? publishedAt = null;
                    if (root.TryGetProperty("published_at", out var pubProp) && pubProp.TryGetDateTime(out var dt))
                    {
                        publishedAt = dt;
                    }

                    string exeDownloadUrl = "";
                    if (root.TryGetProperty("assets", out var assetsProp) && assetsProp.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var asset in assetsProp.EnumerateArray())
                        {
                            string assetName = asset.TryGetProperty("name", out var aName) ? aName.GetString() ?? "" : "";
                            if (assetName.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
                            {
                                if (asset.TryGetProperty("browser_download_url", out var urlProp))
                                {
                                    exeDownloadUrl = urlProp.GetString() ?? "";
                                    if (assetName.Equals("BrBrDbTelemetryInstaller.exe", StringComparison.OrdinalIgnoreCase))
                                    {
                                        break; // Preferred exact installer asset
                                    }
                                }
                            }
                        }
                    }

                    string normRemote = NormalizeVersionString(tagName);
                    bool isNewer = !string.IsNullOrEmpty(currentVersion) && IsRemoteVersionNewer(tagName, currentVersion);

                    return new GitHubReleaseInfo
                    {
                        TagName = tagName,
                        NormalizedVersion = normRemote,
                        Name = name,
                        HtmlUrl = htmlUrl,
                        ExeDownloadUrl = exeDownloadUrl,
                        PublishedAt = publishedAt,
                        IsNewer = isNewer
                    };
                }
            }
            catch (Exception ex)
            {
                return new GitHubReleaseInfo { ErrorMessage = "Failed to parse release information: " + ex.Message };
            }
        }

        public static async Task<GitHubReleaseInfo> CheckForGitHubUpdateAsync(string currentVersion, string repo = "kibergus/race_tools")
        {
            try
            {
                string url = string.Format("https://api.github.com/repos/{0}/releases/latest", repo);
                using (var req = new HttpRequestMessage(HttpMethod.Get, url))
                {
                    string uaVersion = !string.IsNullOrEmpty(currentVersion) ? currentVersion : "1.0.0";
                    req.Headers.UserAgent.ParseAdd("BrBrDbTelemetryInstaller/" + uaVersion);
                    req.Headers.Accept.ParseAdd("application/vnd.github.v3+json");

                    using (var resp = await _updateHttpClient.SendAsync(req).ConfigureAwait(false))
                    {
                        if (resp.StatusCode == System.Net.HttpStatusCode.Forbidden)
                        {
                            return new GitHubReleaseInfo { ErrorMessage = "GitHub rate limit reached. Please try again later." };
                        }
                        if (resp.StatusCode == System.Net.HttpStatusCode.NotFound)
                        {
                            return new GitHubReleaseInfo { ErrorMessage = "No releases found on GitHub repository." };
                        }
                        if (!resp.IsSuccessStatusCode)
                        {
                            return new GitHubReleaseInfo { ErrorMessage = string.Format("GitHub returned HTTP {0}.", (int)resp.StatusCode) };
                        }

                        string body = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                        return ParseGitHubReleaseJson(body, currentVersion);
                    }
                }
            }
            catch (TaskCanceledException)
            {
                return new GitHubReleaseInfo { ErrorMessage = "Update check timed out." };
            }
            catch (Exception ex)
            {
                return new GitHubReleaseInfo { ErrorMessage = "Could not check GitHub: " + ex.Message };
            }
        }

        public static string GetLogFilePath(string rf2Path)
        {
            if (string.IsNullOrEmpty(rf2Path))
                return "";
            return Path.Combine(rf2Path, @"UserData\BrBrDbTelemetry\BrBrDbTelemetry.log");
        }

        public static string GetSessionsDirectoryPath(string rf2Path, PluginConfig config)
        {
            if (config != null && config.sinks != null)
            {
                SinkConfig fileSink = config.sinks.Find(s => s.type == "file");
                if (fileSink != null && !string.IsNullOrEmpty(fileSink.path))
                {
                    return fileSink.path;
                }
            }

            if (!string.IsNullOrEmpty(rf2Path))
            {
                string rf2Dir = Path.Combine(rf2Path, @"UserData\BrBrDbTelemetry");
                if (Directory.Exists(rf2Dir))
                    return rf2Dir;
            }

            string docsPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "BrBrDbTelemetry");
            if (Directory.Exists(docsPath))
                return docsPath;

            return !string.IsNullOrEmpty(rf2Path) ? Path.Combine(rf2Path, @"UserData\BrBrDbTelemetry") : docsPath;
        }

        public static FileInfo[] GetSessionFiles(string directoryPath)
        {
            if (string.IsNullOrEmpty(directoryPath) || !Directory.Exists(directoryPath))
                return new FileInfo[0];

            try
            {
                DirectoryInfo dir = new DirectoryInfo(directoryPath);
                FileInfo[] files = dir.GetFiles("*.csv");
                Array.Sort(files, (a, b) => b.LastWriteTime.CompareTo(a.LastWriteTime));
                return files;
            }
            catch
            {
                return new FileInfo[0];
            }
        }

        public class SessionMetadata
        {
            public FileInfo FileInfo { get; set; }
            public string Date { get; set; }
            public string Time { get; set; }
            public string Track { get; set; }
            public double SizeMb { get; set; }
        }

        public static SessionMetadata GetSessionMetadata(FileInfo file)
        {
            var meta = new SessionMetadata
            {
                FileInfo = file,
                Date = file.LastWriteTime.ToString("yyyy-MM-dd"),
                Time = file.LastWriteTime.ToString("HH:mm:ss"),
                Track = "Unknown",
                SizeMb = file.Length / (1024.0 * 1024.0)
            };

            if (file == null || !file.Exists)
                return meta;

            try
            {
                using (var stream = new FileStream(file.FullName, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (var reader = new StreamReader(stream))
                {
                    string line;
                    int lineCount = 0;
                    while ((line = reader.ReadLine()) != null && lineCount < 20)
                    {
                        lineCount++;
                        if (string.IsNullOrWhiteSpace(line)) continue;

                        if (line.StartsWith("Time,x,", StringComparison.OrdinalIgnoreCase) || line.StartsWith("Time,x,z", StringComparison.OrdinalIgnoreCase))
                        {
                            break;
                        }

                        string[] parts = line.Split(new char[] { ',' }, 2);
                        if (parts.Length == 2)
                        {
                            string key = parts[0].Trim();
                            string val = parts[1].Trim();
                            if (key.Equals("Track name", StringComparison.OrdinalIgnoreCase))
                            {
                                meta.Track = val;
                            }
                            else if (key.Equals("Date", StringComparison.OrdinalIgnoreCase))
                            {
                                meta.Date = val;
                            }
                            else if (key.Equals("Time", StringComparison.OrdinalIgnoreCase))
                            {
                                meta.Time = val;
                            }
                        }
                    }
                }
            }
            catch {}

            return meta;
        }

        public static void CompressBrotli(Stream input, Stream output, int quality = 4)
        {
            using (var encoder = new System.IO.Compression.BrotliEncoder(quality, 22))
            {
                byte[] inBuf = new byte[65536];
                byte[] outBuf = new byte[65536 * 2];
                int bytesRead;
                while ((bytesRead = input.Read(inBuf, 0, inBuf.Length)) > 0)
                {
                    var source = inBuf.AsSpan(0, bytesRead);
                    while (!source.IsEmpty)
                    {
                        var status = encoder.Compress(source, outBuf, out int bytesConsumed, out int bytesWritten, isFinalBlock: false);
                        if (status == System.Buffers.OperationStatus.Done || status == System.Buffers.OperationStatus.DestinationTooSmall)
                        {
                            if (bytesWritten > 0) output.Write(outBuf, 0, bytesWritten);
                            source = source.Slice(bytesConsumed);
                        }
                        else
                        {
                            throw new InvalidOperationException("Brotli compression failed with status: " + status);
                        }
                    }
                }
                while (true)
                {
                    var status = encoder.Compress(ReadOnlySpan<byte>.Empty, outBuf, out _, out int bytesWritten, isFinalBlock: true);
                    if (bytesWritten > 0) output.Write(outBuf, 0, bytesWritten);
                    if (status == System.Buffers.OperationStatus.Done) break;
                    if (status != System.Buffers.OperationStatus.DestinationTooSmall)
                    {
                        throw new InvalidOperationException("Brotli finalization failed with status: " + status);
                    }
                }
            }
        }

        public static bool UploadSessionFile(string filePath, string serverAddress, string apiKey, out string errorMessage)
        {
            errorMessage = "";
            if (string.IsNullOrEmpty(filePath) || !File.Exists(filePath))
            {
                errorMessage = "Session file does not exist.";
                return false;
            }

            if (string.IsNullOrEmpty(serverAddress))
            {
                errorMessage = "Server address is empty.";
                return false;
            }

            int maxRetries = 5;
            bool tryCompressed = true;
            for (int attempt = 1; attempt <= maxRetries; attempt++)
            {
                try
                {
                    string endpoint = serverAddress.TrimEnd('/') + "/api/upload/stream";
                    var request = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(endpoint);
                    request.Method = "POST";
                    request.ContentType = "text/csv";
                    request.Timeout = 180000; // 3 minutes timeout for telemetry upload
                    request.SendChunked = true;

                    if (tryCompressed)
                    {
                        request.Headers["Content-Encoding"] = "br";
                    }

                    if (!string.IsNullOrEmpty(apiKey))
                    {
                        request.Headers["X-API-Key"] = apiKey;
                    }

                    using (FileStream fs = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                    using (Stream reqStream = request.GetRequestStream())
                    {
                        if (tryCompressed)
                        {
                            CompressBrotli(fs, reqStream, 4);
                        }
                        else
                        {
                            byte[] buffer = new byte[65536];
                            int bytesRead;
                            while ((bytesRead = fs.Read(buffer, 0, buffer.Length)) > 0)
                            {
                                reqStream.Write(buffer, 0, bytesRead);
                            }
                        }
                    }

                    using (var response = (System.Net.HttpWebResponse)request.GetResponse())
                    {
                        if (response.StatusCode == System.Net.HttpStatusCode.OK)
                        {
                            return true;
                        }
                        else
                        {
                            errorMessage = string.Format("Server returned status code {0} ({1}).", (int)response.StatusCode, response.StatusDescription);
                            int code = (int)response.StatusCode;
                            if (tryCompressed && (code == 415 || code == 400 || code == 500))
                            {
                                tryCompressed = false;
                                continue;
                            }
                            if (code >= 400 && code < 500 && code != 408 && code != 429)
                            {
                                return false; // Non-retryable client error
                            }
                        }
                    }
                }
                catch (System.Net.WebException ex)
                {
                    var resp = ex.Response as System.Net.HttpWebResponse;
                    if (resp != null)
                    {
                        try
                        {
                            using (var reader = new StreamReader(resp.GetResponseStream()))
                            {
                                string respText = reader.ReadToEnd();
                                errorMessage = string.Format("Server Error {0}: {1}", (int)resp.StatusCode, respText);
                            }
                        }
                        catch
                        {
                            errorMessage = string.Format("Server Error {0}: {1}", (int)resp.StatusCode, resp.StatusDescription);
                        }

                        int code = (int)resp.StatusCode;
                        if (tryCompressed && (code == 415 || code == 400 || code == 500))
                        {
                            tryCompressed = false;
                            continue;
                        }
                        if (code >= 400 && code < 500 && code != 408 && code != 429)
                        {
                            return false; // Non-retryable client error
                        }
                    }
                    else
                    {
                        errorMessage = ex.Message;
                    }
                }
                catch (Exception ex)
                {
                    errorMessage = ex.Message;
                }

                if (attempt < maxRetries)
                {
                    System.Threading.Thread.Sleep(1000 * Math.Min(1 << attempt, 16));
                }
            }

            return false;
        }

        public static bool DeleteSessionFile(string filePath, out string errorMessage)
        {
            errorMessage = "";
            if (string.IsNullOrEmpty(filePath))
            {
                errorMessage = "Session file path is empty.";
                return false;
            }

            try
            {
                if (File.Exists(filePath))
                {
                    File.Delete(filePath);
                    return true;
                }
                else
                {
                    errorMessage = "Session file does not exist.";
                    return false;
                }
            }
            catch (Exception ex)
            {
                errorMessage = ex.Message;
                return false;
            }
        }
    }
}


