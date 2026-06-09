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
    }
}
