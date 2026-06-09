using System;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Reflection;
using System.Diagnostics;
using System.Collections.Generic;
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
    }
}
