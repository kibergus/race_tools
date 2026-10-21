// Copyright 2026 Alexey Guseynov. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
// ==============================================================================

#include <iostream>
#include <cassert>
#include <filesystem>
#include <cmath>
#include <thread>
#include <chrono>

#include "ace_shared_memory.h"
#include "mock_shared_memory.h"
#include "telemetry_collector.h"
#include "config.h"
#include "net_utils.h"
#include "sink.h"
#include "logger.h"

using namespace race_tools;

static int g_testsRun = 0;
static int g_testsFailed = 0;

#define TEST_ASSERT(cond, msg) \
  do { \
    if (!(cond)) { \
      std::cerr << "FAILED: " << msg << " (" << __FILE__ << ":" << __LINE__ << ")\n"; \
      g_testsFailed++; \
      return; \
    } \
  } while (0)

#define RUN_TEST(fn) \
  do { \
    g_testsRun++; \
    std::cout << "Running " #fn "... "; \
    int failedBefore = g_testsFailed; \
    fn(); \
    if (g_testsFailed == failedBefore) { \
      std::cout << "[PASS]\n"; \
    } \
  } while (0)

void TestParseServerAddress() {
  auto info1 = ParseServerAddress("https://example.com");
  TEST_ASSERT(info1.useHttps == true, "Expected https");
  TEST_ASSERT(info1.port == INTERNET_DEFAULT_HTTPS_PORT, "Expected 443");
  TEST_ASSERT(info1.host == L"example.com", "Expected example.com");

  auto info2 = ParseServerAddress("http://localhost:8080");
  TEST_ASSERT(info2.useHttps == false, "Expected http");
  TEST_ASSERT(info2.port == 8080, "Expected port 8080");
  TEST_ASSERT(info2.host == L"localhost", "Expected localhost");

  // URL with path
  auto info3 = ParseServerAddress("https://custom.telemetry.org:8443/api/v1/stream");
  TEST_ASSERT(info3.useHttps == true, "Expected https");
  TEST_ASSERT(info3.port == 8443, "Expected port 8443");
  TEST_ASSERT(info3.host == L"custom.telemetry.org", "Expected custom.telemetry.org");
  TEST_ASSERT(info3.path == L"/api/v1/stream", "Expected path /api/v1/stream");

  // IPv6 literal
  auto info4 = ParseServerAddress("http://[::1]:9090");
  TEST_ASSERT(info4.port == 9090, "Expected port 9090");
  TEST_ASSERT(info4.host.find(L"::1") != std::wstring::npos, "Expected IPv6 loopback");
}

void TestConfigSerialization() {
  std::wstring tempPath = std::filesystem::temp_directory_path().wstring() + L"\\test_ace_config.json";

  PluginConfig cfg;
  cfg.league = "unit_test_league";
  cfg.pollingRateHz = 60;
  cfg.carNameRegexp = "Ferrari.*";

  SinkConfig s1;
  s1.type = "file";
  s1.path = "C:\\telemetry";
  s1.enabled = true;
  cfg.sinks.push_back(s1);

  SinkConfig s2;
  s2.type = "http";
  s2.serverAddress = "https://test.server.com";
  s2.apiKey = "secret_key_123";
  s2.enabled = false;
  cfg.sinks.push_back(s2);

  DriverNameOverride dno;
  dno.carNameRegexp = "Porsche.*";
  dno.driverName = "Test Driver";
  cfg.driverNameOverrides.push_back(dno);

  cfg.simulators["acr"].league = "wrc_league";
  cfg.simulators["ac"].league = "classic_league";

  SavePluginConfig(tempPath, cfg);
  TEST_ASSERT(std::filesystem::exists(tempPath), "Saved config should exist");

  PluginConfig loaded = LoadPluginConfig(tempPath);
  TEST_ASSERT(loaded.league == "unit_test_league", "League mismatch");
  TEST_ASSERT(loaded.pollingRateHz == 60, "Polling rate mismatch");
  TEST_ASSERT(loaded.carNameRegexp == "Ferrari.*", "Car regexp mismatch");
  TEST_ASSERT(loaded.sinks.size() == 2, "Expected 2 sinks");
  TEST_ASSERT(loaded.sinks[0].type == "file" && loaded.sinks[0].path == "C:\\telemetry", "File sink mismatch");
  TEST_ASSERT(loaded.sinks[1].type == "http" && loaded.sinks[1].apiKey == "secret_key_123", "Http sink mismatch");
  TEST_ASSERT(loaded.driverNameOverrides.size() == 1, "Driver override count mismatch");
  TEST_ASSERT(loaded.driverNameOverrides[0].driverName == "Test Driver", "Driver override name mismatch");
  TEST_ASSERT(loaded.simulators.size() == 2, "Expected 2 simulator overrides");
  TEST_ASSERT(loaded.GetLeagueForSim("acr", "acr") == "wrc_league", "ACR league should match override");
  TEST_ASSERT(loaded.GetLeagueForSim("ac", "ac") == "classic_league", "AC league should match override");
  TEST_ASSERT(loaded.GetLeagueForSim("ace", "acevo") == "unit_test_league", "ACE should fall back to root league");

  std::error_code ec;
  std::filesystem::remove(tempPath, ec);
}

void TestSharedMemoryMappingAndReading() {
  std::wstring physMap = L"Local\\test_unit_phys";
  std::wstring graphMap = L"Local\\test_unit_graph";
  std::wstring statMap = L"Local\\test_unit_stat";

  MockSharedMemory mock(physMap, graphMap, statMap);
  TEST_ASSERT(mock.Initialize(), "MockSharedMemory should initialize");

  SPageFilePhysics physIn{};
  physIn.packetId = 42;
  physIn.speedKmh = 180.5f;
  physIn.gas = 0.85f;
  mock.WritePhysics(physIn);

  SPageFileGraphicEvo graphIn{};
  graphIn.packetId = 100;
  graphIn.status = AC_LIVE; // AC_LIVE
  strcpy_s(graphIn.driver_name, "Max");
  mock.WriteGraphics(graphIn);

  SPageFileStaticEvo statIn{};
  strcpy_s(statIn.track, "Spa");
  strcpy_s(statIn.session_name, "Race");
  strcpy_s(statIn.ac_evo_version, "0.1.0");
  mock.WriteStatic(statIn);

  AceSharedMemoryReader reader;
  reader.SetCustomMapNames(physMap, graphMap, statMap);
  TEST_ASSERT(reader.Connect(), "Reader should connect to mock mappings");

  SPageFilePhysics physOut{};
  TEST_ASSERT(reader.ReadPhysics(physOut), "Should read physics");
  TEST_ASSERT(physOut.packetId == 42, "Physics packetId should match");
  TEST_ASSERT(std::abs(physOut.speedKmh - 180.5f) < 0.01f, "Physics speed should match");
  TEST_ASSERT(std::abs(physOut.gas - 0.85f) < 0.01f, "Physics gas should match");

  SPageFileGraphicEvo graphOut{};
  TEST_ASSERT(reader.ReadGraphics(graphOut), "Should read graphics");
  TEST_ASSERT(graphOut.status == AC_LIVE, "Graphics status should match");
  TEST_ASSERT(strcmp(graphOut.driver_name, "Max") == 0, "Graphics driver name should match");

  SPageFileStaticEvo statOut{};
  TEST_ASSERT(reader.ReadStatic(statOut), "Should read static");
  TEST_ASSERT(strcmp(statOut.track, "Spa") == 0, "Static track should match");
  TEST_ASSERT(strcmp(statOut.session_name, "Race") == 0, "Static session should match");
  TEST_ASSERT(strcmp(statOut.ac_evo_version, "0.1.0") == 0, "Static ac_evo_version should match");

  reader.Disconnect();
  mock.Shutdown();
}

void TestFormatTelemetryLine() {
  SPageFilePhysics phys{};
  phys.packetId = 123;
  phys.speedKmh = 100.0f;
  phys.gas = 0.5f;
  phys.brake = 0.0f;
  phys.clutch = 0.25f;
  phys.gear = 3; // 2nd gear (3 - 1 = 2)
  phys.steerAngle = 0.1f;
  phys.accG[0] = 0.8f;
  phys.accG[1] = 0.2f;
  phys.accG[2] = 1.0f;
  for (int i = 0; i < 4; ++i) {
    phys.wheelAngularSpeed[i] = 100.0f;
    phys.wheelLoad[i] = 4000.0f;
    phys.tyreTempM[i] = 80.0f;
    phys.tyreCoreTemperature[i] = 85.0f;
  }
  // Set contact points for coordinates testing
  phys.tyreContactPoint[0][0] = 10.0f; phys.tyreContactPoint[0][2] = 20.0f;
  phys.tyreContactPoint[1][0] = 12.0f; phys.tyreContactPoint[1][2] = 20.0f;
  phys.tyreContactPoint[2][0] = 10.0f; phys.tyreContactPoint[2][2] = 16.0f;
  phys.tyreContactPoint[3][0] = 12.0f; phys.tyreContactPoint[3][2] = 16.0f;
  // Average posX = 11.0, posZ = 18.0

  phys.roadTemp = 30.0f;
  phys.airTemp = 20.0f;

  SPageFileGraphicEvo graph{};
  graph.handbrake_percent = 0.15f;
  graph.flag = AC_GREEN_FLAG;

  auto realTime = std::chrono::system_clock::now();
  auto steadyStart = std::chrono::steady_clock::now();
  auto steadyNow = steadyStart + std::chrono::milliseconds(1500);

  std::string line = TelemetryCollector::FormatTelemetryLine(phys, graph, realTime, steadyStart, steadyNow);
  TEST_ASSERT(!line.empty(), "Formatted line should not be empty");
  TEST_ASSERT(line.back() == '\n', "Formatted line should end with newline");
  TEST_ASSERT(line.find("11.00000,18.00000") != std::string::npos, "Should contain accurate tyre contact position");
  TEST_ASSERT(line.find(",25.00000,15.00000,2,") != std::string::npos, "Should contain clutch, handbrake, and gear");
  TEST_ASSERT(line.find("30.00000,20.00000") != std::string::npos, "Should contain road and air temp");
}

void TestTelemetryCollectorWithMockSim() {
  std::wstring physMap = L"Local\\test_collector_phys";
  std::wstring graphMap = L"Local\\test_collector_graph";
  std::wstring statMap = L"Local\\test_collector_stat";

  MockSharedMemory mock(physMap, graphMap, statMap);
  TEST_ASSERT(mock.Initialize(), "MockSharedMemory should initialize");

  std::wstring tempLogDir = std::filesystem::temp_directory_path().wstring() + L"\\ace_test_logs_" +
                            std::to_wstring(std::chrono::system_clock::now().time_since_epoch().count());
  std::error_code ec;
  std::filesystem::create_directories(tempLogDir, ec);

  Logger logger(tempLogDir + L"\\test.log");

  PluginConfig cfg;
  cfg.league = "unit_test";
  cfg.pollingRateHz = 100;
  SinkConfig fSink;
  fSink.type = "file";
  fSink.path = std::filesystem::path(tempLogDir).string();
  fSink.enabled = true;
  cfg.sinks.push_back(fSink);

  auto reader = std::make_shared<AceSharedMemoryReader>();
  reader->SetCustomMapNames(physMap, graphMap, statMap);

  TelemetryCollector collector(reader, cfg, logger, tempLogDir);

  mock.StartSim();
  collector.Start();

  std::this_thread::sleep_for(std::chrono::milliseconds(500));
  TEST_ASSERT(collector.IsSessionActive(), "Collector should detect active session");

  collector.Stop();
  mock.StopSim();
  mock.Shutdown();

  // Verify that at least one CSV file was generated and contains origin metadata
  bool foundCsv = false;
  for (const auto& entry : std::filesystem::directory_iterator(tempLogDir)) {
    if (entry.path().extension() == ".csv") {
      foundCsv = true;
      std::uintmax_t size = std::filesystem::file_size(entry.path());
      TEST_ASSERT(size > 100, "CSV file should have content");
      std::ifstream f(entry.path());
      std::string content((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
      TEST_ASSERT(content.find("Track name,Monza") != std::string::npos, "CSV must contain Track name");
      TEST_ASSERT(content.find("Game version,0.1.0") != std::string::npos, "CSV must contain Game version");
      TEST_ASSERT(content.find("Track Origin Latitude,45.61389000") != std::string::npos, "CSV must contain Track Origin Latitude for Monza");
      TEST_ASSERT(content.find("Track Origin Longitude,9.28624000") != std::string::npos, "CSV must contain Track Origin Longitude for Monza");
      TEST_ASSERT(content.find("Track Origin Heading,") != std::string::npos, "CSV must contain Track Origin Heading");
      TEST_ASSERT(content.find("\nOrigin Heading,") == std::string::npos, "CSV must NOT contain standalone Origin Heading");
      TEST_ASSERT(content.find("\nOrigin Latitude,") == std::string::npos, "CSV must NOT contain standalone Origin Latitude");
      break;
    }
  }
  TEST_ASSERT(foundCsv, "Collector should have created a CSV file");

  std::filesystem::remove_all(tempLogDir, ec);
}

void TestAcRallySharedMemoryMappingAndReading() {
  std::wstring physMap = L"Local\\acr_pmf_physics";
  std::wstring graphMap = L"Local\\acr_pmf_graphics";
  std::wstring statMap = L"Local\\acr_pmf_static";

  MockSharedMemory mock(physMap, graphMap, statMap);
  TEST_ASSERT(mock.Initialize(), "MockSharedMemory for ACR should initialize");

  SPageFilePhysics physIn{};
  physIn.packetId = 10;
  physIn.speedKmh = 145.0f;
  mock.WritePhysics(physIn);

  SPageFileGraphicEvo graphIn{};
  graphIn.packetId = 20;
  graphIn.status = AC_LIVE;
  strcpy_s(graphIn.driver_name, "RallyDriver");
  strcpy_s(graphIn.car_model, "Lancia Delta HF");
  mock.WriteGraphics(graphIn);

  SPageFileStaticEvo statIn{};
  strcpy_s(statIn.sm_version, "1.0");
  strcpy_s(statIn.track, "RallyStage1");
  strcpy_s(statIn.ac_evo_version, "1.0.0");
  mock.WriteStatic(statIn);

  AceSharedMemoryReader reader;
  reader.SetCustomMapNames(physMap, graphMap, statMap, SimulatorType::AcRally);
  TEST_ASSERT(reader.Connect(), "Reader should connect to ACR mappings");
  TEST_ASSERT(reader.GetSimulatorType() == SimulatorType::AcRally, "Simulator type should be AcRally");
  TEST_ASSERT(reader.GetDefaultLeague() == "acr", "Default league should be acr");
  TEST_ASSERT(reader.GetSimulatorName() == "Assetto Corsa Rally", "Simulator name should match");

  UnifiedStaticInfo statOut;
  TEST_ASSERT(reader.ReadUnifiedStatic(statOut), "Should read unified static");
  TEST_ASSERT(statOut.track == "RallyStage1", "Track should match");

  UnifiedGraphicInfo graphOut;
  TEST_ASSERT(reader.ReadUnifiedGraphic(graphOut), "Should read unified graphic");
  TEST_ASSERT(graphOut.driverName == "RallyDriver", "Driver name should match");
  TEST_ASSERT(graphOut.carModel == "Lancia Delta HF", "Car model should match");

  reader.Disconnect();
  mock.Shutdown();
}

void TestClassicAssettoCorsaLayout() {
  std::wstring physMap = L"Local\\test_classic_ac_phys";
  std::wstring graphMap = L"Local\\test_classic_ac_graph";
  std::wstring statMap = L"Local\\test_classic_ac_stat";

  MockSharedMemory mock(physMap, graphMap, statMap);
  TEST_ASSERT(mock.Initialize(), "MockSharedMemory for Classic AC should initialize");

  SPageFilePhysics physIn{};
  physIn.packetId = 55;
  physIn.speedKmh = 160.0f;
  mock.WritePhysics(physIn);

  ks::SPageFileGraphicClassic graphClassic{};
  graphClassic.packetId = 77;
  graphClassic.status = 2; // AC_LIVE
  graphClassic.session = 0; // Practice
  mock.WriteGraphicsClassic(graphClassic);

  ks::SPageFileStaticClassic statClassic{};
  wcscpy_s(statClassic.smVersion, L"1.7");
  wcscpy_s(statClassic.acVersion, L"1.16.4");
  wcscpy_s(statClassic.track, L"Imola");
  wcscpy_s(statClassic.trackConfiguration, L"GP");
  wcscpy_s(statClassic.playerName, L"ClassicDriver");
  wcscpy_s(statClassic.playerSurname, L"Smith");
  wcscpy_s(statClassic.carModel, L"BMW M3 E30");
  mock.WriteStaticClassic(statClassic);

  AceSharedMemoryReader reader;
  reader.SetCustomMapNames(physMap, graphMap, statMap, SimulatorType::AssettoCorsa);
  TEST_ASSERT(reader.Connect(), "Reader should connect to Classic AC mapping");
  TEST_ASSERT(reader.GetSimulatorType() == SimulatorType::AssettoCorsa, "Simulator type should be AssettoCorsa");
  TEST_ASSERT(reader.GetDefaultLeague() == "ac", "Default league should be ac");
  TEST_ASSERT(reader.GetLayoutFormat() == LayoutFormat::Classic, "Layout should be detected as Classic");

  UnifiedStaticInfo statOut;
  TEST_ASSERT(reader.ReadUnifiedStatic(statOut), "Should read unified static");
  TEST_ASSERT(statOut.track == "Imola", "Track should be Imola");
  TEST_ASSERT(statOut.trackConfiguration == "GP", "Track configuration should be GP");
  TEST_ASSERT(statOut.gameVersion == "1.16.4", "Game version should match");

  UnifiedGraphicInfo graphOut;
  TEST_ASSERT(reader.ReadUnifiedGraphic(graphOut), "Should read unified graphic");
  TEST_ASSERT(graphOut.driverName == "ClassicDriver", "Driver name should match");
  TEST_ASSERT(graphOut.driverSurname == "Smith", "Driver surname should match");
  TEST_ASSERT(graphOut.carModel == "BMW M3 E30", "Car model should match");
  TEST_ASSERT(graphOut.status == 2, "Status should be 2 (live)");

  reader.Disconnect();
  mock.Shutdown();
}

void TestAcRallyTelemetryCollectorSession() {
  std::wstring physMap = L"Local\\test_acr_session_phys";
  std::wstring graphMap = L"Local\\test_acr_session_graph";
  std::wstring statMap = L"Local\\test_acr_session_stat";

  MockSharedMemory mock(physMap, graphMap, statMap);
  TEST_ASSERT(mock.Initialize(), "MockSharedMemory should initialize");

  std::wstring tempLogDir = std::filesystem::temp_directory_path().wstring() + L"\\acr_test_logs_" +
                            std::to_wstring(std::chrono::system_clock::now().time_since_epoch().count());
  std::error_code ec;
  std::filesystem::create_directories(tempLogDir, ec);

  Logger logger(tempLogDir + L"\\acr_test.log");

  PluginConfig cfg;
  cfg.simulator = "acr";
  cfg.league = "auto"; // Auto should resolve to "acr"
  cfg.pollingRateHz = 100;
  SinkConfig fSink;
  fSink.type = "file";
  fSink.path = std::filesystem::path(tempLogDir).string();
  fSink.enabled = true;
  cfg.sinks.push_back(fSink);

  auto reader = std::make_shared<AceSharedMemoryReader>();
  reader->SetCustomMapNames(physMap, graphMap, statMap, SimulatorType::AcRally);

  TelemetryCollector collector(reader, cfg, logger, tempLogDir);

  mock.StartSim();
  collector.Start();

  std::this_thread::sleep_for(std::chrono::milliseconds(500));
  TEST_ASSERT(collector.IsSessionActive(), "Collector should detect active ACR session");

  collector.Stop();
  mock.StopSim();
  mock.Shutdown();

  // Verify that generated CSV file contains League,acr
  bool foundAcrCsv = false;
  for (const auto& entry : std::filesystem::directory_iterator(tempLogDir)) {
    if (entry.path().extension() == ".csv") {
      foundAcrCsv = true;
      std::ifstream f(entry.path());
      std::string content((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
      TEST_ASSERT(content.find("League,acr") != std::string::npos, "CSV must contain League,acr");
      TEST_ASSERT(content.find("Track name,Monza") != std::string::npos, "CSV must contain Track name");
      break;
    }
  }
  TEST_ASSERT(foundAcrCsv, "Collector should have created a CSV file for ACR");

  std::filesystem::remove_all(tempLogDir, ec);
}

void TestSafeQueue() {
  auto queue = std::make_shared<SafeQueue<int>>(10);

  auto reader1 = queue->reader();
  auto reader2 = queue->reader();

  // Push items
  for (int i = 0; i < 5; ++i) {
    queue->Push(i * 10);
  }

  TEST_ASSERT(queue->Size() == 5, "Queue size should be 5");

  // Reader 1 reads 5 items
  for (int i = 0; i < 5; ++i) {
    int val = 0;
    TEST_ASSERT(reader1.Pop(val), "Reader 1 should pop");
    TEST_ASSERT(val == i * 10, "Reader 1 value mismatch");
  }

  // Reader 2 hasn't read, so items should still be in queue
  TEST_ASSERT(queue->Size() == 5, "Items retained because reader 2 hasn't consumed them");

  // Reader 2 reads 5 items
  for (int i = 0; i < 5; ++i) {
    int val = 0;
    TEST_ASSERT(reader2.Pop(val), "Reader 2 should pop");
    TEST_ASSERT(val == i * 10, "Reader 2 value mismatch");
  }

  // Automatic pruning: now that both readers have consumed all items, queue buffer size should be 0!
  TEST_ASSERT(queue->Size() == 0, "Pruning should drop all items consumed by both readers");

  // Test capacity bound and dropping oldest
  auto queueBounded = std::make_shared<SafeQueue<int>>(5);
  auto slowReader = queueBounded->reader();

  for (int i = 0; i < 12; ++i) {
    queueBounded->Push(i);
  }

  TEST_ASSERT(queueBounded->Size() == 5, "Bounded queue size should not exceed capacity");
  TEST_ASSERT(queueBounded->DroppedCount() == 7, "Bounded queue should have dropped 7 items");

  // Slow reader should read the 5 retained items (7..11) without error
  int lastVal = -1;
  while (slowReader.TryPop(lastVal)) {}
  TEST_ASSERT(lastVal == 11, "Slow reader should have read newest item 11");
}

void TestRetryBackoff() {
  RetryBackoff backoff(std::chrono::seconds(60), 6);
  auto t0 = std::chrono::steady_clock::now();

  TEST_ASSERT(backoff.CanAttempt(t0), "First attempt should be allowed immediately");
  backoff.RecordAttempt(t0);

  // Attempt 1 backoff: 2^1 = 2 seconds
  TEST_ASSERT(!backoff.CanAttempt(t0 + std::chrono::seconds(1)), "Should not allow retry after 1s");
  TEST_ASSERT(backoff.CanAttempt(t0 + std::chrono::seconds(2)), "Should allow retry after 2s");

  backoff.RecordAttempt(t0 + std::chrono::seconds(2));
  // Attempt 2 backoff: 2^2 = 4 seconds
  TEST_ASSERT(!backoff.CanAttempt(t0 + std::chrono::seconds(5)), "Should not allow retry before 4s backoff");
  TEST_ASSERT(backoff.CanAttempt(t0 + std::chrono::seconds(6)), "Should allow retry after 4s backoff");

  // Reset clears history
  backoff.Reset();
  TEST_ASSERT(backoff.CanAttempt(t0 + std::chrono::seconds(3)), "Reset should allow immediate attempt");
}

void TestSanitizeFilename() {
  std::string clean = SanitizeFilename("Max_Verstappen");
  TEST_ASSERT(clean == "Max_Verstappen", "Alphanumeric and underscores should remain");

  std::string illegal = SanitizeFilename("Driver: *?\"<>| Name");
  TEST_ASSERT(illegal.find(':') == std::string::npos, "Colon should be stripped");
  TEST_ASSERT(illegal.find('*') == std::string::npos, "Asterisk should be stripped");
  TEST_ASSERT(illegal.find('?') == std::string::npos, "Question mark should be stripped");
  TEST_ASSERT(illegal.find('"') == std::string::npos, "Quote should be stripped");

  // UTF-8 multi-byte characters should be preserved
  std::string utf8Name = "Алексей";
  std::string sanitizedUtf8 = SanitizeFilename(utf8Name);
  TEST_ASSERT(sanitizedUtf8 == utf8Name, "Unicode Cyrillic should be preserved in filenames");
}

void TestSessionStateTransitions() {
  std::wstring physMap = L"Local\\test_states_phys";
  std::wstring graphMap = L"Local\\test_states_graph";
  std::wstring statMap = L"Local\\test_states_stat";

  MockSharedMemory mock(physMap, graphMap, statMap);
  TEST_ASSERT(mock.Initialize(), "MockSharedMemory should initialize");

  std::wstring tempLogDir = std::filesystem::temp_directory_path().wstring() + L"\\state_test_logs_" +
                            std::to_wstring(std::chrono::system_clock::now().time_since_epoch().count());
  std::error_code ec;
  std::filesystem::create_directories(tempLogDir, ec);

  Logger logger(tempLogDir + L"\\state_test.log");

  PluginConfig cfg;
  cfg.pollingRateHz = 100;

  auto reader = std::make_shared<AceSharedMemoryReader>();
  reader->SetCustomMapNames(physMap, graphMap, statMap);

  TelemetryCollector collector(reader, cfg, logger, tempLogDir);

  // 1. Initial state: simulator off (AC_OFF = 0)
  SPageFileStaticEvo stat{};
  strcpy_s(stat.track, "Spa");
  strcpy_s(stat.session_name, "Practice");
  mock.WriteStatic(stat);

  SPageFileGraphicEvo graph{};
  graph.status = AC_OFF; // 0
  graph.packetId = 1;
  mock.WriteGraphics(graph);

  SPageFilePhysics phys{};
  phys.packetId = 1;
  mock.WritePhysics(phys);

  collector.Start();
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  TEST_ASSERT(!collector.IsSessionActive(), "AC_OFF should not start a session");

  // 2. Replay (AC_REPLAY = 1) should NOT start a telemetry session
  graph.status = AC_REPLAY; // 1
  graph.packetId = 2;
  phys.packetId = 2;
  mock.WriteGraphics(graph);
  mock.WritePhysics(phys);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  TEST_ASSERT(!collector.IsSessionActive(), "AC_REPLAY should not start a telemetry session");

  // 3. Live driving (AC_LIVE = 2) starts session
  graph.status = AC_LIVE; // 2
  graph.packetId = 3;
  phys.packetId = 3;
  mock.WriteGraphics(graph);
  mock.WritePhysics(phys);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  TEST_ASSERT(collector.IsSessionActive(), "AC_LIVE should start a telemetry session");
  TEST_ASSERT(collector.GetSessionState() == SessionState::Live, "Session state should be Live");

  // 4. Pausing (AC_PAUSE = 3) transitions to Paused and keeps session active (does NOT close file)
  graph.status = AC_PAUSE; // 3
  mock.WriteGraphics(graph);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  TEST_ASSERT(collector.IsSessionActive(), "Pausing should keep the session active");
  TEST_ASSERT(collector.GetSessionState() == SessionState::Paused, "Session state should be Paused");

  // 5. Resume from pause (AC_LIVE = 2) transitions back to Live seamlessly
  graph.status = AC_LIVE; // 2
  graph.packetId = 4;
  phys.packetId = 4;
  mock.WriteGraphics(graph);
  mock.WritePhysics(phys);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  TEST_ASSERT(collector.IsSessionActive(), "Session should remain active after resume");
  TEST_ASSERT(collector.GetSessionState() == SessionState::Live, "Session state should return to Live");

  // 6. Leaving session (AC_OFF = 0) cleanly ends the session
  graph.status = AC_OFF; // 0
  mock.WriteGraphics(graph);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  TEST_ASSERT(!collector.IsSessionActive(), "Leaving track should end the session");
  TEST_ASSERT(collector.GetSessionState() == SessionState::Idle, "Session state should be Idle");

  collector.Stop();
  mock.Shutdown();
  std::filesystem::remove_all(tempLogDir, ec);
}

int main() {
  std::cout << "==================================================\n";
  std::cout << "Running Assetto Corsa / Evo / Rally Unit Tests...\n";
  std::cout << "==================================================\n";

  RUN_TEST(TestSafeQueue);
  RUN_TEST(TestRetryBackoff);
  RUN_TEST(TestSanitizeFilename);
  RUN_TEST(TestParseServerAddress);
  RUN_TEST(TestConfigSerialization);
  RUN_TEST(TestSharedMemoryMappingAndReading);
  RUN_TEST(TestAcRallySharedMemoryMappingAndReading);
  RUN_TEST(TestClassicAssettoCorsaLayout);
  RUN_TEST(TestFormatTelemetryLine);
  RUN_TEST(TestSessionStateTransitions);
  RUN_TEST(TestTelemetryCollectorWithMockSim);
  RUN_TEST(TestAcRallyTelemetryCollectorSession);

  std::cout << "==================================================\n";
  std::cout << "Tests run: " << g_testsRun << ", Failed: " << g_testsFailed << "\n";
  std::cout << "==================================================\n";

  return g_testsFailed == 0 ? 0 : 1;
}
