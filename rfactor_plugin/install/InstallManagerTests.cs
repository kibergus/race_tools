using System;
using System.IO;
using System.Text.RegularExpressions;
using System.Collections.Generic;

namespace BrBrDbTelemetry
{
    class InstallManagerTests
    {
        private static int testsRun = 0;
        private static int testsFailed = 0;

        static int Main(string[] args)
        {
            Console.WriteLine("==================================================");
            Console.WriteLine("Running InstallManager C# Unit Tests...");
            Console.WriteLine("==================================================");

            RunTest("TestIsValidRF2Directory", TestIsValidRF2Directory);
            RunTest("TestParseLibraryFolders", TestParseLibraryFolders);
            RunTest("TestSetPluginActivation_FileDoesNotExist_Enable", TestSetPluginActivation_FileDoesNotExist_Enable);
            RunTest("TestSetPluginActivation_FileDoesNotExist_Disable", TestSetPluginActivation_FileDoesNotExist_Disable);
            RunTest("TestSetPluginActivation_EntryDoesNotExist_Enable", TestSetPluginActivation_EntryDoesNotExist_Enable);
            RunTest("TestSetPluginActivation_EntryExistsDisabled_Enable", TestSetPluginActivation_EntryExistsDisabled_Enable);
            RunTest("TestSetPluginActivation_EntryExistsEnabled_Disable", TestSetPluginActivation_EntryExistsEnabled_Disable);
            RunTest("TestGetInstalledPluginDetails_FileMissing", TestGetInstalledPluginDetails_FileMissing);
            RunTest("TestGetInstalledPluginDetails_FileExistsInvalid", TestGetInstalledPluginDetails_FileExistsInvalid);
            RunTest("TestIsInstalledVersionNewer", TestIsInstalledVersionNewer);
            RunTest("TestGetLogFilePath", TestGetLogFilePath);
            RunTest("TestGetSessionsDirectoryPath", TestGetSessionsDirectoryPath);
            RunTest("TestGetSessionFiles", TestGetSessionFiles);
            RunTest("TestGetSessionMetadata", TestGetSessionMetadata);

            Console.WriteLine("==================================================");
            Console.WriteLine(string.Format("Tests run: {0}, Failed: {1}", testsRun, testsFailed));
            Console.WriteLine("==================================================");

            return testsFailed == 0 ? 0 : 1;
        }

        static void RunTest(string name, Action testAction)
        {
            testsRun++;
            try
            {
                testAction();
                Console.WriteLine(string.Format("[PASS] {0}", name));
            }
            catch (Exception ex)
            {
                testsFailed++;
                Console.WriteLine(string.Format("[FAIL] {0}: {1}", name, ex.Message));
                Console.WriteLine(ex.StackTrace);
            }
        }

        static void Assert(bool condition, string message)
        {
            if (!condition)
            {
                throw new Exception("Assertion failed: " + message);
            }
        }

        static string CreateTempDirectory()
        {
            string path = Path.Combine(Path.GetTempPath(), "RF2Test_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(path);
            return path;
        }

        static void DeleteTempDirectory(string path)
        {
            try
            {
                if (Directory.Exists(path))
                {
                    Directory.Delete(path, true);
                }
            }
            catch {}
        }

        static void TestIsValidRF2Directory()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                // 1. Empty directory should be invalid
                Assert(!InstallManager.IsValidRF2Directory(tempDir), "Empty directory should be invalid");

                // 2. Directory with correct subfolders should be valid
                string pluginsPath = Path.Combine(tempDir, @"Bin64\Plugins");
                Directory.CreateDirectory(pluginsPath);
                Assert(InstallManager.IsValidRF2Directory(tempDir), "Directory with Bin64\\Plugins should be valid");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestParseLibraryFolders()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string vdfPath = Path.Combine(tempDir, "libraryfolders.vdf");
                string targetDir1 = Path.Combine(tempDir, "Lib1").Replace("/", "\\");
                string targetDir2 = Path.Combine(tempDir, "Lib2").Replace("/", "\\");

                Directory.CreateDirectory(targetDir1);
                Directory.CreateDirectory(targetDir2);

                // Create mock libraryfolders.vdf content
                string content = string.Format(
                    "\"libraryfolders\"\n" +
                    "{{\n" +
                    "  \"0\"\n" +
                    "  {{\n" +
                    "    \"path\" \"{0}\"\n" +
                    "  }}\n" +
                    "  \"1\"\n" +
                    "  {{\n" +
                    "    \"path\" \"{1}\"\n" +
                    "  }}\n" +
                    "}}",
                    targetDir1.Replace("\\", "\\\\"),
                    targetDir2.Replace("\\", "\\\\")
                );

                File.WriteAllText(vdfPath, content);

                var libs = InstallManager.ParseLibraryFolders(vdfPath);
                Assert(libs.Count == 2, "Expected 2 libraries parsed");
                Assert(libs.Contains(targetDir1), "Expected library 1 to be found");
                Assert(libs.Contains(targetDir2), "Expected library 2 to be found");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestSetPluginActivation_FileDoesNotExist_Enable()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string playerDir = Path.Combine(tempDir, @"UserData\player");
                string configPath = Path.Combine(playerDir, "CustomPluginVariables.json");

                InstallManager.SetPluginActivation(tempDir, true);

                Assert(File.Exists(configPath), "Config file should be created");
                string content = File.ReadAllText(configPath);
                Assert(content.Contains("\"BrBrDbTelemetry64.dll\""), "Should contain DLL entry");
                Assert(content.Contains("\" Enabled\": 1"), "Should be enabled");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestSetPluginActivation_FileDoesNotExist_Disable()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string playerDir = Path.Combine(tempDir, @"UserData\player");
                string configPath = Path.Combine(playerDir, "CustomPluginVariables.json");

                InstallManager.SetPluginActivation(tempDir, false);

                Assert(!File.Exists(configPath), "Config file should NOT be created when enabling false and missing");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestSetPluginActivation_EntryDoesNotExist_Enable()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string playerDir = Path.Combine(tempDir, @"UserData\player");
                Directory.CreateDirectory(playerDir);
                string configPath = Path.Combine(playerDir, "CustomPluginVariables.json");

                string initialJson = "{\n  \"SomeOtherPlugin64.dll\": {\n    \" Enabled\": 1\n  }\n}";
                File.WriteAllText(configPath, initialJson);

                InstallManager.SetPluginActivation(tempDir, true);

                string content = File.ReadAllText(configPath);
                Assert(content.Contains("\"SomeOtherPlugin64.dll\""), "Should retain other plugins");
                Assert(content.Contains("\"BrBrDbTelemetry64.dll\""), "Should append our plugin");
                Assert(content.Contains("\" Enabled\": 1"), "Our plugin should be enabled");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestSetPluginActivation_EntryExistsDisabled_Enable()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string playerDir = Path.Combine(tempDir, @"UserData\player");
                Directory.CreateDirectory(playerDir);
                string configPath = Path.Combine(playerDir, "CustomPluginVariables.json");

                string initialJson = "{\n  \"BrBrDbTelemetry64.dll\": {\n    \" Enabled\": 0\n  }\n}";
                File.WriteAllText(configPath, initialJson);

                InstallManager.SetPluginActivation(tempDir, true);

                string content = File.ReadAllText(configPath);
                Assert(content.Contains("\"BrBrDbTelemetry64.dll\""), "Should contain our plugin");
                Assert(content.Contains("\" Enabled\":1") || content.Contains("\" Enabled\": 1"), "Should be enabled");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestSetPluginActivation_EntryExistsEnabled_Disable()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string playerDir = Path.Combine(tempDir, @"UserData\player");
                Directory.CreateDirectory(playerDir);
                string configPath = Path.Combine(playerDir, "CustomPluginVariables.json");

                string initialJson = "{\n  \"BrBrDbTelemetry64.dll\": {\n    \" Enabled\": 1\n  }\n}";
                File.WriteAllText(configPath, initialJson);

                InstallManager.SetPluginActivation(tempDir, false);

                string content = File.ReadAllText(configPath);
                Assert(content.Contains("\"BrBrDbTelemetry64.dll\""), "Should contain our plugin");
                Assert(content.Contains("\" Enabled\":0") || content.Contains("\" Enabled\": 0"), "Should be disabled");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestGetInstalledPluginDetails_FileMissing()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string version;
                bool result = InstallManager.GetInstalledPluginDetails(tempDir, out version);
                Assert(!result, "Should return false when file is missing");
                Assert(version == "", "Version should be empty");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestGetInstalledPluginDetails_FileExistsInvalid()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string pluginsDir = Path.Combine(tempDir, @"Bin64\Plugins");
                Directory.CreateDirectory(pluginsDir);
                string dllPath = Path.Combine(pluginsDir, "BrBrDbTelemetry64.dll");
                File.WriteAllText(dllPath, "not a real dll");

                string version;
                bool result = InstallManager.GetInstalledPluginDetails(tempDir, out version);
                Assert(result, "Should return true when file exists");
                Assert(version == "0.0.0", "Version should fallback to 0.0.0 when FileVersionInfo fails");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestIsInstalledVersionNewer()
        {
            Assert(InstallManager.IsInstalledVersionNewer("1.2.3", "1.2.2"), "1.2.3 should be newer than 1.2.2");
            Assert(!InstallManager.IsInstalledVersionNewer("1.2.3", "1.2.3"), "1.2.3 should not be newer than 1.2.3");
            Assert(!InstallManager.IsInstalledVersionNewer("1.2.1", "1.2.3"), "1.2.1 should not be newer than 1.2.3");
            Assert(!InstallManager.IsInstalledVersionNewer("invalid", "1.2.3"), "Invalid version should return false");
        }

        static void TestGetLogFilePath()
        {
            Assert(InstallManager.GetLogFilePath("") == "", "Empty rf2Path should return empty string");
            string path = InstallManager.GetLogFilePath(@"C:\rFactor2");
            Assert(path == @"C:\rFactor2\UserData\BrBrDbTelemetry\BrBrDbTelemetry.log", "Log file path mismatch");
        }

        static void TestGetSessionsDirectoryPath()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                var config = new PluginConfig();
                config.sinks = new List<SinkConfig>
                {
                    new SinkConfig { type = "file", path = @"C:\CustomTelemetry" }
                };
                Assert(InstallManager.GetSessionsDirectoryPath(tempDir, config) == @"C:\CustomTelemetry", "Should use configured file sink path");

                config.sinks[0].path = "";
                string defaultRf2Dir = Path.Combine(tempDir, @"UserData\BrBrDbTelemetry");
                Directory.CreateDirectory(defaultRf2Dir);
                Assert(InstallManager.GetSessionsDirectoryPath(tempDir, config) == defaultRf2Dir, "Should fallback to rFactor 2 UserData dir when it exists");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestGetSessionFiles()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                Assert(InstallManager.GetSessionFiles("non_existent_path").Length == 0, "Non-existent path should return 0 files");

                string file1 = Path.Combine(tempDir, "BrBrDbTelemetry_100_server.csv");
                string file2 = Path.Combine(tempDir, "BrBrDbTelemetry_200_server.csv");
                File.WriteAllText(file1, "header,data\n1,2");
                System.Threading.Thread.Sleep(10);
                File.WriteAllText(file2, "header,data\n3,4");

                FileInfo[] files = InstallManager.GetSessionFiles(tempDir);
                Assert(files.Length == 2, "Should find 2 CSV files");
                Assert(files[0].Name == "BrBrDbTelemetry_200_server.csv", "Newer file should be first");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestGetSessionMetadata()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string csvPath = Path.Combine(tempDir, "test_session.csv");
                string content = "Track name,Bayford Meadows\nDate,2026-07-25\nTime,14:25:49\nTime,x,z\n1,2,3\n";
                File.WriteAllText(csvPath, content);

                var fi = new FileInfo(csvPath);
                var meta = InstallManager.GetSessionMetadata(fi);

                Assert(meta.Track == "Bayford Meadows", "Track name should be parsed correctly");
                Assert(meta.Date == "2026-07-25", "Date should be parsed correctly");
                Assert(meta.Time == "14:25:49", "Time should be parsed correctly");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }
    }
}
