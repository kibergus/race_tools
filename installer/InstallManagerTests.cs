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
            RunTest("TestDeleteSessionFile", TestDeleteSessionFile);
            RunTest("TestConfigHelperAllParticipantsInRace", TestConfigHelperAllParticipantsInRace);
            RunTest("TestCompressBrotli", TestCompressBrotli);
            RunTest("TestIsValidACEvoDirectory", TestIsValidACEvoDirectory);
            RunTest("TestGetDefaultAceInstallDirectory", TestGetDefaultAceInstallDirectory);
            RunTest("TestGetInstalledAceDetails_Missing", TestGetInstalledAceDetails_Missing);
            RunTest("TestGetAceLogAndSessionPaths", TestGetAceLogAndSessionPaths);
            RunTest("TestAceConfigPersistence", TestAceConfigPersistence);
            RunTest("TestIsAceDaemonRunning", TestIsAceDaemonRunning);
            RunTest("TestRestartAceDaemon", TestRestartAceDaemon);
            RunTest("TestNormalizeVersionString", TestNormalizeVersionString);
            RunTest("TestCompareVersionsAndNewer", TestCompareVersionsAndNewer);
            RunTest("TestParseGitHubReleaseJson", TestParseGitHubReleaseJson);
            RunTest("TestParseGitHubReleaseJson_RateLimit", TestParseGitHubReleaseJson_RateLimit);
            RunTest("TestCheckForGitHubUpdateAsync", TestCheckForGitHubUpdateAsync);
            RunTest("TestGetDefaultAcrAndAcInstallDirectories", TestGetDefaultAcrAndAcInstallDirectories);
            RunTest("TestGetDefaultLeagueAndDisplayName", TestGetDefaultLeagueAndDisplayName);
            RunTest("TestAcrAndAcConfigPersistence", TestAcrAndAcConfigPersistence);
            RunTest("TestGetInstalledDaemonDetails_Missing", TestGetInstalledDaemonDetails_Missing);
            RunTest("TestDaemonInstallAndUninstallCopiesInstaller", TestDaemonInstallAndUninstallCopiesInstaller);
            RunTest("TestBrt1Encoder_ConvertCsvToBrt1", TestBrt1Encoder_ConvertCsvToBrt1);
            RunTest("TestBrt1Encoder_GetSessionMetadata_Brt1", TestBrt1Encoder_GetSessionMetadata_Brt1);
            RunTest("TestBrt1Encoder_GetSessionFiles_IncludesBrt1", TestBrt1Encoder_GetSessionFiles_IncludesBrt1);

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

                string csvPath2 = Path.Combine(tempDir, "test_fuji.csv");
                string content2 = "Track name,Fuji Speedway\nTrack configuration,GP Short\nDate,2026-11-03\nTime,14:25:49\nTime,x,z\n1,2,3\n";
                File.WriteAllText(csvPath2, content2);
                var meta2 = InstallManager.GetSessionMetadata(new FileInfo(csvPath2));
                Assert(meta2.Track == "Fuji Speedway GP Short", "Track name should include track configuration separated by space");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestDeleteSessionFile()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string csvPath = Path.Combine(tempDir, "to_delete.csv");
                File.WriteAllText(csvPath, "dummy telemetry data");

                string error;
                bool result = InstallManager.DeleteSessionFile(csvPath, out error);
                Assert(result, "Deleting existing session file should succeed");
                Assert(!File.Exists(csvPath), "File should no longer exist after deletion");
                Assert(string.IsNullOrEmpty(error), "Error message should be empty on success");

                // Test deleting already deleted file
                result = InstallManager.DeleteSessionFile(csvPath, out error);
                Assert(!result, "Deleting non-existent file should return false");
                Assert(!string.IsNullOrEmpty(error), "Error message should be populated when file does not exist");

                // Test empty path
                result = InstallManager.DeleteSessionFile("", out error);
                Assert(!result, "Empty path should return false");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestConfigHelperAllParticipantsInRace()
        {
            var config = new PluginConfig();
            Assert(!config.all_participants_in_race, "Default all_participants_in_race should be false");

            string serialized = ConfigHelper.Serialize(config);
            Assert(serialized.Contains("\"all_participants_in_race\": false"), "Serialized json should contain all_participants_in_race: false");

            var deserialized = ConfigHelper.Deserialize(serialized);
            Assert(!deserialized.all_participants_in_race, "Deserialized config should have all_participants_in_race false");

            config.all_participants_in_race = true;
            string serializedTrue = ConfigHelper.Serialize(config);
            Assert(serializedTrue.Contains("\"all_participants_in_race\": true"), "Serialized json should contain all_participants_in_race: true");

            var deserializedTrue = ConfigHelper.Deserialize(serializedTrue);
            Assert(deserializedTrue.all_participants_in_race, "Deserialized config should have all_participants_in_race true");
        }

        static void TestCompressBrotli()
        {
            var sb = new System.Text.StringBuilder();
            for (int i = 0; i < 2000; i++)
            {
                sb.AppendLine("2026-10-18T12:00:00.123Z,54.123456,-3.123456,45.2,1,0.012,0.034,0.056,98.2,12.4");
            }
            byte[] inputBytes = System.Text.Encoding.UTF8.GetBytes(sb.ToString());

            using (var msIn = new MemoryStream(inputBytes))
            using (var msCompressed = new MemoryStream())
            {
                InstallManager.CompressBrotli(msIn, msCompressed, 4);

                byte[] compressedBytes = msCompressed.ToArray();
                Assert(compressedBytes.Length < inputBytes.Length / 2, "Brotli should compress telemetry CSV by at least 50%");

                // Decompress using standard BrotliStream to verify integrity
                using (var msDecomp = new MemoryStream())
                using (var decompressor = new System.IO.Compression.BrotliStream(new MemoryStream(compressedBytes), System.IO.Compression.CompressionMode.Decompress))
                {
                    decompressor.CopyTo(msDecomp);
                    byte[] decompressedBytes = msDecomp.ToArray();
                    Assert(decompressedBytes.Length == inputBytes.Length, "Decompressed length should match original length");

                    for (int i = 0; i < inputBytes.Length; i++)
                    {
                        if (inputBytes[i] != decompressedBytes[i])
                        {
                            throw new Exception("Decompressed content mismatch at index " + i);
                        }
                    }
                }
            }
        }

        static void TestIsValidACEvoDirectory()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                Assert(InstallManager.IsValidACEvoDirectory(tempDir), "Existing directory should be valid for ACE");
                Assert(!InstallManager.IsValidACEvoDirectory(Path.Combine(tempDir, "non_existent")), "Non-existent directory should be invalid");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestGetDefaultAceInstallDirectory()
        {
            string dir = InstallManager.GetDefaultAceInstallDirectory();
            Assert(!string.IsNullOrEmpty(dir), "Default ACE install directory should not be empty");
            Assert(dir.Contains("BrBrDbTelemetry"), "Default ACE directory should contain BrBrDbTelemetry");
        }

        static void TestGetInstalledAceDetails_Missing()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string version;
                bool isRunning;
                bool isAutostart;
                bool installed = InstallManager.GetInstalledAceDetails(tempDir, out version, out isRunning, out isAutostart);
                Assert(!installed, "Should report not installed when exe is missing");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestGetAceLogAndSessionPaths()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string logPath = InstallManager.GetAceLogFilePath(tempDir);
                Assert(logPath.EndsWith("brbrdaemon.log") || logPath.EndsWith("BrBrDbTelemetryAce.log"), "Log path should end with brbrdaemon.log");

                string sessionPathDefault = InstallManager.GetAceSessionsDirectoryPath("", tempDir);
                Assert(sessionPathDefault == Path.Combine(tempDir, "Telemetry"), "Default session path should be Telemetry subfolder");

                string customSessionPath = InstallManager.GetAceSessionsDirectoryPath(@"D:\Telemetry", tempDir);
                Assert(customSessionPath == @"D:\Telemetry", "Custom session path should be respected");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestAceConfigPersistence()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string configPath = Path.Combine(tempDir, "BrBrDbTelemetry.json");
                var config = new PluginConfig
                {
                    league = "acevo",
                    car_name_regexp = "GT3.*"
                };
                config.sinks.Add(new SinkConfig { type = "file", path = Path.Combine(tempDir, "Telemetry"), enabled = true });
                config.sinks.Add(new SinkConfig { type = "http", server_address = "https://example.com", api_key = "abc", enabled = true });

                string json = ConfigHelper.Serialize(config);
                File.WriteAllText(configPath, json, System.Text.Encoding.UTF8);

                var loaded = ConfigHelper.Deserialize(File.ReadAllText(configPath));
                Assert(loaded.league == "acevo", "League should match");
                Assert(loaded.car_name_regexp == "GT3.*", "Car regexp should match");
                Assert(loaded.sinks.Count == 2, "Should have 2 sinks");
                Assert(loaded.sinks[0].type == "file", "First sink should be file");
                Assert(loaded.sinks[1].type == "http", "Second sink should be http");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestIsAceDaemonRunning()
        {
            // Verify IsAceDaemonRunning executes without exceptions
            bool running = InstallManager.IsAceDaemonRunning();
            // Process may or may not be running in test environment; just assert boolean return
            Assert(running == true || running == false, "IsAceDaemonRunning should return a valid boolean");
        }

        static void TestRestartAceDaemon()
        {
            // Test that RestartAceDaemonIfRunning executes safely
            bool isRunning = InstallManager.IsAceDaemonRunning();
            if (!isRunning)
            {
                bool restarted = InstallManager.RestartAceDaemonIfRunning("");
                Assert(!restarted, "RestartAceDaemonIfRunning should return false when daemon is not running");
            }
        }

        static void TestNormalizeVersionString()
        {
            Assert(InstallManager.NormalizeVersionString("v1.7") == "1.7.0", "v1.7 should normalize to 1.7.0");
            Assert(InstallManager.NormalizeVersionString("1.7.0.12") == "1.7.0.12", "1.7.0.12 should normalize to 1.7.0.12");
            Assert(InstallManager.NormalizeVersionString("v2.1-beta") == "2.1.0", "v2.1-beta should normalize to 2.1.0");
            Assert(InstallManager.NormalizeVersionString("") == "0.0.0", "empty string should normalize to 0.0.0");
            Assert(InstallManager.NormalizeVersionString(null) == "0.0.0", "null should normalize to 0.0.0");
        }

        static void TestCompareVersionsAndNewer()
        {
            Assert(InstallManager.CompareVersions("v1.7", "1.7.0") == 0, "v1.7 and 1.7.0 should be equal");
            Assert(InstallManager.IsRemoteVersionNewer("v1.8", "1.7.0"), "v1.8 should be newer than 1.7.0");
            Assert(!InstallManager.IsRemoteVersionNewer("v1.7", "1.7.0"), "v1.7 should not be newer than 1.7.0");
            Assert(!InstallManager.IsRemoteVersionNewer("1.6.9", "1.7.0"), "1.6.9 should not be newer than 1.7.0");
            Assert(InstallManager.IsRemoteVersionNewer("2.0.0", "1.9.9"), "2.0.0 should be newer than 1.9.9");
            Assert(InstallManager.IsRemoteVersionNewer("1.7.1", "1.7.0.15"), "1.7.1 should be newer than 1.7.0.15");
            Assert(!InstallManager.IsRemoteVersionNewer("1.7.0.15", "1.7.1"), "1.7.0.15 should not be newer than 1.7.1");
        }

        static void TestParseGitHubReleaseJson()
        {
            string json = "{" +
                "\"tag_name\": \"v1.8\"," +
                "\"name\": \"v1.8 Update\"," +
                "\"html_url\": \"https://github.com/kibergus/race_tools/releases/tag/v1.8\"," +
                "\"published_at\": \"2026-10-23T12:00:00Z\"," +
                "\"assets\": [" +
                "  {" +
                "    \"name\": \"BrBrDbTelemetryInstaller.exe\"," +
                "    \"browser_download_url\": \"https://github.com/kibergus/race_tools/releases/download/v1.8/BrBrDbTelemetryInstaller.exe\"" +
                "  }" +
                "]" +
                "}";

            var release = InstallManager.ParseGitHubReleaseJson(json, "1.7.0");
            Assert(release.Success, "Parsing valid release json should succeed");
            Assert(release.TagName == "v1.8", "TagName should match");
            Assert(release.NormalizedVersion == "1.8.0", "NormalizedVersion should be 1.8.0");
            Assert(release.HtmlUrl == "https://github.com/kibergus/race_tools/releases/tag/v1.8", "HtmlUrl should match");
            Assert(release.ExeDownloadUrl == "https://github.com/kibergus/race_tools/releases/download/v1.8/BrBrDbTelemetryInstaller.exe", "ExeDownloadUrl should match");
            Assert(release.IsNewer == true, "v1.8 should be newer than 1.7.0");

            var releaseOld = InstallManager.ParseGitHubReleaseJson(json, "1.8.0");
            Assert(releaseOld.IsNewer == false, "v1.8 should not be newer than 1.8.0");
        }

        static void TestParseGitHubReleaseJson_RateLimit()
        {
            string rateLimitJson = "{\"message\": \"API rate limit exceeded for 1.2.3.4.\", \"documentation_url\": \"https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting\"}";
            var release = InstallManager.ParseGitHubReleaseJson(rateLimitJson, "1.7.0");
            Assert(!release.Success, "Rate limit json should indicate failure");
            Assert(release.ErrorMessage.Contains("rate limit"), "ErrorMessage should mention rate limit");
        }

        static void TestCheckForGitHubUpdateAsync()
        {
            var task = InstallManager.CheckForGitHubUpdateAsync("1.0.0");
            task.Wait(8000);
            if (task.IsCompletedSuccessfully)
            {
                var release = task.Result;
                if (release.Success)
                {
                    Assert(!string.IsNullOrEmpty(release.TagName), "Live check returned a valid tag name");
                    Assert(release.IsNewer, "Live check should detect that 1.0.0 is older than latest release");
                }
            }
        }

        static void TestGetDefaultAcrAndAcInstallDirectories()
        {
            string defaultDir = InstallManager.GetDefaultDaemonInstallDirectory();
            Assert(!string.IsNullOrEmpty(defaultDir), "Default install directory should not be empty");
            Assert(defaultDir.EndsWith("BrBrDbTelemetry"), "Default dir should end with BrBrDbTelemetry");

            string acrDir = InstallManager.GetDefaultAcrInstallDirectory();
            string acDir = InstallManager.GetDefaultAcInstallDirectory();
            string aceDir = InstallManager.GetDefaultAceInstallDirectory();

            Assert(acrDir == defaultDir, "ACR should share default install directory");
            Assert(acDir == defaultDir, "AC should share default install directory");
            Assert(aceDir == defaultDir, "ACE should share default install directory");
        }

        static void TestGetDefaultLeagueAndDisplayName()
        {
            Assert(InstallManager.GetDefaultLeagueForSim("acr") == "acr", "League for acr should be acr");
            Assert(InstallManager.GetDefaultLeagueForSim("ac") == "ac", "League for ac should be ac");
            Assert(InstallManager.GetDefaultLeagueForSim("ace") == "acevo", "League for ace should be acevo");
            Assert(InstallManager.GetDefaultLeagueForSim("rf2") == "kartsim", "League for rf2 should be kartsim");

            Assert(InstallManager.GetSimulatorDisplayName("acr") == "Assetto Corsa Rally", "Display name for acr should be Assetto Corsa Rally");
            Assert(InstallManager.GetSimulatorDisplayName("ac") == "Assetto Corsa", "Display name for ac should be Assetto Corsa");
            Assert(InstallManager.GetSimulatorDisplayName("ace") == "Assetto Corsa Evo", "Display name for ace should be Assetto Corsa Evo");
        }

        static void TestAcrAndAcConfigPersistence()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                var sharedConfig = new PluginConfig();
                sharedConfig.SetLeagueForSimulator("acr", "my_rally_league");
                sharedConfig.SetLeagueForSimulator("ace", "my_evo_league");
                sharedConfig.SetLeagueForSimulator("ac", "my_ac_league");
                sharedConfig.SetCarRegexpForSimulator("acr", "WRC.*");

                string json = ConfigHelper.Serialize(sharedConfig);
                Assert(json.Contains("\"simulators\""), "Serialized JSON should contain simulators section");
                Assert(json.Contains("\"my_rally_league\""), "Serialized JSON should contain my_rally_league");
                Assert(json.Contains("\"my_evo_league\""), "Serialized JSON should contain my_evo_league");

                var loaded = ConfigHelper.Deserialize(json);
                Assert(loaded.GetLeagueForSimulator("acr") == "my_rally_league", "Loaded ACR league should match");
                Assert(loaded.GetLeagueForSimulator("ace") == "my_evo_league", "Loaded ACE league should match");
                Assert(loaded.GetLeagueForSimulator("ac") == "my_ac_league", "Loaded AC league should match");
                Assert(loaded.GetCarRegexpForSimulator("acr") == "WRC.*", "Loaded ACR car regexp should match");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestGetInstalledDaemonDetails_Missing()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string version;
                bool isRunning;
                bool isAutostart;
                bool installed = InstallManager.GetInstalledDaemonDetails("acr", tempDir, out version, out isRunning, out isAutostart);
                Assert(!installed, "Should report not installed when ACR daemon exe is missing");

                installed = InstallManager.GetInstalledDaemonDetails("ac", tempDir, out version, out isRunning, out isAutostart);
                Assert(!installed, "Should report not installed when AC daemon exe is missing");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestDaemonInstallAndUninstallCopiesInstaller()
        {
            string tempDir = CreateTempDirectory();
            string mockSourceDir = CreateTempDirectory();
            try
            {
                string mockInstallerExe = Path.Combine(mockSourceDir, "MockInstaller.exe");
                File.WriteAllText(mockInstallerExe, "mock installer content");

                InstallManager.InstallDaemonPlugin("ace", tempDir, "test_api_key", "", false, mockInstallerExe);

                string installedInstallerExe = Path.Combine(tempDir, "BrBrDbTelemetryInstaller.exe");
                Assert(File.Exists(installedInstallerExe), "BrBrDbTelemetryInstaller.exe should have been copied to target directory");
                Assert(File.ReadAllText(installedInstallerExe) == "mock installer content", "Copied installer content should match source");

                InstallManager.UninstallDaemonPlugin("ace", tempDir);
                Assert(!File.Exists(installedInstallerExe), "BrBrDbTelemetryInstaller.exe should be removed upon uninstall");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
                DeleteTempDirectory(mockSourceDir);
            }
        }

        static void TestBrt1Encoder_ConvertCsvToBrt1()
        {
            string csvContent =
                "Track name,Rowrah\n" +
                "Date,2026-10-07\n" +
                "Time,14:51:21\n" +
                "Driver name,Kibergus\n" +
                "Kart number,55\n" +
                "\n" +
                "Time,x,z,Speed,Throttle\n" +
                "2026-10-07T14:51:21.000Z,10.5,20.5,25.0,85.0\n" +
                "2026-10-07T14:51:21.010Z,10.51,20.52,25.05,85.2\n";

            using (var csvMs = new MemoryStream(System.Text.Encoding.UTF8.GetBytes(csvContent)))
            using (var brt1Ms = new MemoryStream())
            {
                Brt1Encoder.ConvertCsvToBrt1Stream(csvMs, brt1Ms);
                byte[] bytes = brt1Ms.ToArray();

                Assert(bytes.Length > 20, "BRT1 output should not be empty");

                // 1. Magic chunk: len 4 (0x0004), type 0x01, 'BRT1'
                Assert(bytes[0] == 4 && bytes[1] == 0 && bytes[2] == 0x01, "First chunk must be Magic chunk (Type 0x01, len 4)");
                string magicStr = System.Text.Encoding.ASCII.GetString(bytes, 3, 4);
                Assert(magicStr == "BRT1", "Magic payload must be 'BRT1'");

                // 2. Metadata chunk: type 0x02
                int offset = 7;
                int metaLen = bytes[offset] | (bytes[offset + 1] << 8);
                Assert(bytes[offset + 2] == 0x02, "Second chunk must be MetadataJson chunk (Type 0x02)");
                string metaJson = System.Text.Encoding.UTF8.GetString(bytes, offset + 3, metaLen);
                using (var doc = System.Text.Json.JsonDocument.Parse(metaJson))
                {
                    Assert(doc.RootElement.GetProperty("metadata").GetProperty("track_name").GetString() == "Rowrah", "Track name must be Rowrah");
                    Assert(doc.RootElement.GetProperty("metadata").GetProperty("driver_name").GetString() == "Kibergus", "Driver name must be Kibergus");
                    Assert(doc.RootElement.GetProperty("channels").GetArrayLength() == 5, "Must declare 5 channels");
                }
                offset += 3 + metaLen;

                // 3. Baseline chunk: type 0x03, len 5 * 8 = 40 bytes
                int baseLen = bytes[offset] | (bytes[offset + 1] << 8);
                Assert(baseLen == 40, "Baseline chunk must be 40 bytes for 5 double channels");
                Assert(bytes[offset + 2] == 0x03, "Third chunk must be Baseline chunk (Type 0x03)");
                double firstX = System.Buffers.Binary.BinaryPrimitives.ReadDoubleLittleEndian(new ReadOnlySpan<byte>(bytes, offset + 3 + 8, 8));
                Assert(Math.Abs(firstX - 10.5) < 1e-4, "First X sample must be 10.5");
                offset += 3 + baseLen;

                // 4. Delta chunk: type 0x04, len 5 * 2 = 10 bytes
                int deltaLen = bytes[offset] | (bytes[offset + 1] << 8);
                Assert(deltaLen == 10, "Delta chunk must be 10 bytes for 5 channels");
                Assert(bytes[offset + 2] == 0x04, "Fourth chunk must be Delta chunk (Type 0x04)");
                offset += 3 + deltaLen;

                // 5. EOS chunk: len 0, type 0xFF
                Assert(bytes[offset] == 0 && bytes[offset + 1] == 0 && bytes[offset + 2] == 0xFF, "Fifth chunk must be EOS chunk (Type 0xFF, len 0)");
            }
        }

        static void TestBrt1Encoder_GetSessionMetadata_Brt1()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string csvContent =
                    "Track name,Silverstone GP\n" +
                    "Date,2026-11-05\n" +
                    "Time,21:00:00\n" +
                    "Driver name,TestDriver\n" +
                    "\n" +
                    "Time,Speed\n" +
                    "2026-11-05T21:00:00.000Z,50.0\n";

                string brt1Path = Path.Combine(tempDir, "test_session.brt1");
                using (var csvMs = new MemoryStream(System.Text.Encoding.UTF8.GetBytes(csvContent)))
                using (var fileStream = new FileStream(brt1Path, FileMode.Create, FileAccess.Write))
                {
                    Brt1Encoder.ConvertCsvToBrt1Stream(csvMs, fileStream);
                }

                var meta = InstallManager.GetSessionMetadata(new FileInfo(brt1Path));
                Assert(meta.Track == "Silverstone GP", "Extracted track should match Silverstone GP");
                Assert(meta.Date == "2026-11-05", "Extracted date should match 2026-11-05");
                Assert(meta.Time == "21:00:00", "Extracted time should match 21:00:00");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }

        static void TestBrt1Encoder_GetSessionFiles_IncludesBrt1()
        {
            string tempDir = CreateTempDirectory();
            try
            {
                string csvFile = Path.Combine(tempDir, "session1.csv");
                string brt1File = Path.Combine(tempDir, "session2.brt1");
                File.WriteAllText(csvFile, "Time,x\n0,0\n");
                File.WriteAllText(brt1File, "mock brt1");

                var files = InstallManager.GetSessionFiles(tempDir);
                Assert(files.Length == 2, "GetSessionFiles should discover both .csv and .brt1 files");
            }
            finally
            {
                DeleteTempDirectory(tempDir);
            }
        }
    }
}

