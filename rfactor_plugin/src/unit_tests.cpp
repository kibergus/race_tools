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

#include "math_utils.h"
#include "net_utils.h"
#include "config.h"
#include "logger.h"
#include "sink.h"
#include <iostream>
#include <cassert>
#include <cmath>
#include <filesystem>
#include <fstream>

using namespace race_tools;

// Helper to check closeness of doubles
bool IsClose(double a, double b, double epsilon = 1e-6) {
  return std::abs(a - b) < epsilon;
}

void TestIdentityMatrix() {
  TelemVect3 ori[3];
  ori[0].Set(1.0, 0.0, 0.0);
  ori[1].Set(0.0, 1.0, 0.0);
  ori[2].Set(0.0, 0.0, 1.0);

  double qx = 0.0, qy = 0.0, qz = 0.0, qw = 0.0;
  MatrixToQuaternion(ori, qx, qy, qz, qw);

  // Identity rotation should correspond to quaternion [0, 0, 0, 1] (or [0, 0, 0, -1])
  assert(IsClose(qx, 0.0));
  assert(IsClose(qy, 0.0));
  assert(IsClose(qz, 0.0));
  assert(IsClose(std::abs(qw), 1.0));
  std::cout << "TestIdentityMatrix passed!" << std::endl;
}

void TestRotation90X() {
  // 90 degree rotation around X axis:
  // R = [ 1   0   0 ]
  //     [ 0   0  -1 ]
  //     [ 0   1   0 ]
  TelemVect3 ori[3];
  ori[0].Set(1.0, 0.0, 0.0);
  ori[1].Set(0.0, 0.0, -1.0);
  ori[2].Set(0.0, 1.0, 0.0);

  double qx = 0.0, qy = 0.0, qz = 0.0, qw = 0.0;
  MatrixToQuaternion(ori, qx, qy, qz, qw);

  // q = [sin(45), 0, 0, cos(45)] = [0.707107, 0, 0, 0.707107]
  assert(IsClose(std::abs(qx), 0.70710678));
  assert(IsClose(qy, 0.0));
  assert(IsClose(qz, 0.0));
  assert(IsClose(std::abs(qw), 0.70710678));
  std::cout << "TestRotation90X passed!" << std::endl;
}

void TestRotation90Y() {
  // 90 degree rotation around Y axis:
  // R = [  0  0  1 ]
  //     [  0  1  0 ]
  //     [ -1  0  0 ]
  TelemVect3 ori[3];
  ori[0].Set(0.0, 0.0, 1.0);
  ori[1].Set(0.0, 1.0, 0.0);
  ori[2].Set(-1.0, 0.0, 0.0);

  double qx = 0.0, qy = 0.0, qz = 0.0, qw = 0.0;
  MatrixToQuaternion(ori, qx, qy, qz, qw);

  // q = [0, sin(45), 0, cos(45)] = [0, 0.707107, 0, 0.707107]
  assert(IsClose(qx, 0.0));
  assert(IsClose(std::abs(qy), 0.70710678));
  assert(IsClose(qz, 0.0));
  assert(IsClose(std::abs(qw), 0.70710678));
  std::cout << "TestRotation90Y passed!" << std::endl;
}

void TestRotation90Z() {
  // 90 degree rotation around Z axis:
  // R = [ 0 -1  0 ]
  //     [ 1  0  0 ]
  //     [ 0  0  1 ]
  TelemVect3 ori[3];
  ori[0].Set(0.0, -1.0, 0.0);
  ori[1].Set(1.0, 0.0, 0.0);
  ori[2].Set(0.0, 0.0, 1.0);

  double qx = 0.0, qy = 0.0, qz = 0.0, qw = 0.0;
  MatrixToQuaternion(ori, qx, qy, qz, qw);

  // q = [0, 0, sin(45), cos(45)] = [0, 0, 0.707107, 0.707107]
  assert(IsClose(qx, 0.0));
  assert(IsClose(qy, 0.0));
  assert(IsClose(std::abs(qz), 0.70710678));
  assert(IsClose(std::abs(qw), 0.70710678));
  std::cout << "TestRotation90Z passed!" << std::endl;
}

void TestParseServerAddress() {
  {
    ServerAddressInfo info = ParseServerAddress("http://localhost");
    assert(info.host == L"localhost");
    assert(info.port == 80);
    assert(info.useHttps == false);
  }
  {
    ServerAddressInfo info = ParseServerAddress("https://127.0.0.1:8080");
    assert(info.host == L"127.0.0.1");
    assert(info.port == 8080);
    assert(info.useHttps == true);
  }
  {
    ServerAddressInfo info = ParseServerAddress("example.com:443");
    assert(info.host == L"example.com");
    assert(info.port == 443);
    assert(info.useHttps == false);
  }
  {
    ServerAddressInfo info = ParseServerAddress("localhost");
    assert(info.host == L"localhost");
    assert(info.port == 80);
    assert(info.useHttps == false);
  }
  std::cout << "TestParseServerAddress passed!" << std::endl;
}

void TestConfigLoadSave() {
  std::wstring tempConfigPath = L"test_temp_config.json";

  // Cleanup any left-over file
  std::error_code ec;
  std::filesystem::remove(tempConfigPath, ec);

  // 1. Test loading from a non-existent file.
  // It should throw an exception.
  bool threwOnMissing = false;
  try {
    LoadPluginConfig(tempConfigPath);
  } catch (const std::exception&) {
    threwOnMissing = true;
  }
  assert(threwOnMissing);

  // 2. Test saving a customized configuration
  PluginConfig customConfig;
  customConfig.carNameRegexp = "IAME Cadet #.*";
  customConfig.league = "cadet_cup";
  customConfig.driverNameOverrides.push_back({"#8", "Alexey Guseynov"});
  customConfig.driverNameOverrides.push_back({"#9", "Second Driver"});

  SinkConfig sink1;
  sink1.type = "file";
  sink1.path = "c:/code/rfactor/telemetry_test";
  // sink1.enabled defaults to true
  customConfig.sinks.push_back(sink1);

  SinkConfig sink2;
  sink2.type = "http";
  sink2.serverAddress = "https://myracedb.com:8080";
  sink2.apiKey = "secret_api_key_123";
  sink2.enabled = false;
  customConfig.sinks.push_back(sink2);

  SavePluginConfig(tempConfigPath, customConfig);

  // 3. Test loading the customized configuration back
  PluginConfig loadedCustom = LoadPluginConfig(tempConfigPath);
  assert(loadedCustom.carNameRegexp == "IAME Cadet #.*");
  assert(loadedCustom.league == "cadet_cup");
  assert(loadedCustom.driverNameOverrides.size() == 2);
  assert(loadedCustom.driverNameOverrides[0].carNameRegexp == "#8");
  assert(loadedCustom.driverNameOverrides[0].driverName == "Alexey Guseynov");
  assert(loadedCustom.driverNameOverrides[1].carNameRegexp == "#9");
  assert(loadedCustom.driverNameOverrides[1].driverName == "Second Driver");
  assert(loadedCustom.sinks.size() == 2);
  assert(loadedCustom.sinks[0].type == "file");
  assert(loadedCustom.sinks[0].path == "c:/code/rfactor/telemetry_test");
  assert(loadedCustom.sinks[0].enabled == true);
  assert(loadedCustom.sinks[1].type == "http");
  assert(loadedCustom.sinks[1].serverAddress == "https://myracedb.com:8080");
  assert(loadedCustom.sinks[1].apiKey == "secret_api_key_123");
  assert(loadedCustom.sinks[1].enabled == false);

  // 4. Test loading malformed JSON.
  // Let's overwrite the file with bad JSON.
  {
    std::ofstream badFile(tempConfigPath);
    badFile << "{ \"car_name_regexp\": \"unterminated_string ";
    badFile.close();
  }

  // LoadPluginConfig should throw an exception (nlohmann::json::parse_error)
  bool threwException = false;
  try {
    LoadPluginConfig(tempConfigPath);
  } catch (const std::exception&) {
    threwException = true;
  }
  assert(threwException);

  // 5. Test loading invalid type inside driver_name_overrides
  {
    std::ofstream badFile(tempConfigPath);
    badFile << "{ \"driver_name_overrides\": [ { \"car_name_regexp\": 123, \"driver_name\": \"Alexey\" } ] }";
    badFile.close();
  }
  bool threwOnBadOverrideType = false;
  try {
    LoadPluginConfig(tempConfigPath);
  } catch (const std::runtime_error&) {
    threwOnBadOverrideType = true;
  }
  assert(threwOnBadOverrideType);

  // 6. Test loading missing driver_name in driver_name_overrides
  {
    std::ofstream badFile(tempConfigPath);
    badFile << "{ \"driver_name_overrides\": [ { \"car_name_regexp\": \"#8\" } ] }";
    badFile.close();
  }
  bool threwOnMissingField = false;
  try {
    LoadPluginConfig(tempConfigPath);
  } catch (const std::runtime_error&) {
    threwOnMissingField = true;
  }
  assert(threwOnMissingField);

  // 7. Test loading invalid sink type
  {
    std::ofstream badFile(tempConfigPath);
    badFile << "{ \"sinks\": [ { \"type\": \"invalid_sink_type\" } ] }";
    badFile.close();
  }
  bool threwOnBadSinkType = false;
  try {
    LoadPluginConfig(tempConfigPath);
  } catch (const std::runtime_error&) {
    threwOnBadSinkType = true;
  }
  assert(threwOnBadSinkType);

  // 8. Test loading http sink missing server_address
  {
    std::ofstream badFile(tempConfigPath);
    badFile << "{ \"sinks\": [ { \"type\": \"http\", \"api_key\": \"123\" } ] }";
    badFile.close();
  }
  bool threwOnMissingServerAddress = false;
  try {
    LoadPluginConfig(tempConfigPath);
  } catch (const std::runtime_error&) {
    threwOnMissingServerAddress = true;
  }
  assert(threwOnMissingServerAddress);

  // Cleanup
  std::filesystem::remove(tempConfigPath, ec);

  std::cout << "TestConfigLoadSave passed!" << std::endl;
}

void TestLoggerCriticalError() {
  std::wstring tempLogPath = L"test_temp_logger.log";
  std::error_code ec;
  std::filesystem::remove(tempLogPath, ec);

  Logger logger;
  assert(logger.Open(tempLogPath));

  logger.SetTerminateOnCritical(false);
  logger.SetShowMessageBoxOnCritical(false);

  logger.LogCritical("This is a test critical error message");
  logger.Close();

  // Open the file and verify it has the logged critical error message
  std::ifstream logFile(tempLogPath);
  assert(logFile.is_open());
  std::string line;
  bool foundMessage = false;
  while (std::getline(logFile, line)) {
    if (line.find("CRITICAL ERROR: This is a test critical error message") != std::string::npos) {
      foundMessage = true;
      break;
    }
  }
  logFile.close();
  std::filesystem::remove(tempLogPath, ec);

  assert(foundMessage);
  std::cout << "TestLoggerCriticalError passed!" << std::endl;
}

void TestRetryBackoff() {
  // Test default 5-minute, 6-attempt 2^n backoff
  {
    RetryBackoff backoff;
    auto T0 = std::chrono::steady_clock::now();

    // 1. Initial attempt should be allowed
    assert(backoff.CanAttempt(T0));

    // 2. Record first attempt
    backoff.RecordAttempt(T0); // attempts: [T0]

    // 3. n=1 -> backoff is 2^1 = 2 seconds
    assert(!backoff.CanAttempt(T0 + std::chrono::seconds(1)));
    assert(backoff.CanAttempt(T0 + std::chrono::seconds(2)));

    // 4. Record second attempt at T0 + 2s
    backoff.RecordAttempt(T0 + std::chrono::seconds(2)); // attempts: [T0, T0+2]

    // 5. n=2 -> backoff is 2^2 = 4 seconds since last attempt (T0+2) -> T0+6
    assert(!backoff.CanAttempt(T0 + std::chrono::seconds(5)));
    assert(backoff.CanAttempt(T0 + std::chrono::seconds(6)));

    // 6. Record third attempt at T0 + 6s
    backoff.RecordAttempt(T0 + std::chrono::seconds(6)); // attempts: [T0, T0+2, T0+6]

    // 7. n=3 -> backoff is 2^3 = 8 seconds since T0+6 -> T0+14
    assert(!backoff.CanAttempt(T0 + std::chrono::seconds(13)));
    assert(backoff.CanAttempt(T0 + std::chrono::seconds(14)));
    backoff.RecordAttempt(T0 + std::chrono::seconds(14)); // attempts: [T0, T0+2, T0+6, T0+14] (n=4)

    // 8. n=4 -> backoff is 2^4 = 16 seconds since T0+14 -> T0+30
    backoff.RecordAttempt(T0 + std::chrono::seconds(30)); // attempts: [T0, T0+2, T0+6, T0+14, T0+30] (n=5)

    // 9. n=5 -> backoff is 2^5 = 32 seconds since T0+30 -> T0+62
    backoff.RecordAttempt(T0 + std::chrono::seconds(62)); // attempts: [T0, T0+2, T0+6, T0+14, T0+30, T0+62] (n=6)

    // 10. n=6 -> backoff is 2^6 = 64 seconds since T0+62 -> T0+126
    backoff.RecordAttempt(T0 + std::chrono::seconds(126)); // attempts: [T0, T0+2, T0+6, T0+14, T0+30, T0+62, T0+126] (size=7)

    // 11. Capped at 6 attempts -> backoff is 64 seconds since T0+126 -> T0+190
    assert(!backoff.CanAttempt(T0 + std::chrono::seconds(189)));
    assert(backoff.CanAttempt(T0 + std::chrono::seconds(190)));

    // 12. Test expiration (5 minutes / 300 seconds window)
    // Check after 300 seconds since T0+126, which is T0+426.
    // The oldest remaining attempt is T0, delta at T0+427 is 427s > 300s.
    // So all attempts should expire and clean up.
    assert(backoff.CanAttempt(T0 + std::chrono::seconds(427)));

    // 13. Test Reset
    backoff.RecordAttempt(T0 + std::chrono::seconds(427));
    assert(!backoff.CanAttempt(T0 + std::chrono::seconds(428))); // n=1, backoff 2s
    backoff.Reset();
    assert(backoff.CanAttempt(T0 + std::chrono::seconds(428))); // Reset cleared everything
  }

  // Test custom configuration (10s window, max 3 attempts)
  {
    RetryBackoff backoff(std::chrono::seconds(10), 3);
    auto T0 = std::chrono::steady_clock::now();

    assert(backoff.CanAttempt(T0));
    backoff.RecordAttempt(T0); // attempts: [T0] (n=1)

    // n=1 -> backoff is 2^1 = 2 seconds
    assert(!backoff.CanAttempt(T0 + std::chrono::seconds(1)));
    assert(backoff.CanAttempt(T0 + std::chrono::seconds(2)));
    backoff.RecordAttempt(T0 + std::chrono::seconds(2)); // attempts: [T0, T0+2] (n=2)

    // n=2 -> backoff is 2^2 = 4 seconds -> T0+6
    assert(!backoff.CanAttempt(T0 + std::chrono::seconds(5)));
    assert(backoff.CanAttempt(T0 + std::chrono::seconds(6)));
    backoff.RecordAttempt(T0 + std::chrono::seconds(6)); // attempts: [T0, T0+2, T0+6] (n=3)

    // n=3 (capped) -> backoff is 2^3 = 8 seconds -> T0+14
    assert(!backoff.CanAttempt(T0 + std::chrono::seconds(13)));
    assert(backoff.CanAttempt(T0 + std::chrono::seconds(14)));

    // Test expiration with 10s window:
    // At T0+13, attempts older than 10 seconds (T0 and T0+2) expire.
    // Remaining attempt: [T0+6]. size = 1.
    // n=1 -> backoff is 2s since T0+6 -> T0+8.
    // Since T0+13 >= T0+8, it should be allowed!
    assert(backoff.CanAttempt(T0 + std::chrono::seconds(13)));
  }

  std::cout << "TestRetryBackoff passed!" << std::endl;
}

int main() {
  std::cout << "Running unit tests for MatrixToQuaternion..." << std::endl;
  TestIdentityMatrix();
  TestRotation90X();
  TestRotation90Y();
  TestRotation90Z();

  std::cout << "Running unit tests for ParseServerAddress..." << std::endl;
  TestParseServerAddress();

  std::cout << "Running unit tests for PluginConfig (JSON)..." << std::endl;
  TestConfigLoadSave();

  std::cout << "Running unit tests for Logger Critical Error..." << std::endl;
  TestLoggerCriticalError();

  std::cout << "Running unit tests for RetryBackoff..." << std::endl;
  TestRetryBackoff();

  std::cout << "All unit tests passed successfully!" << std::endl;
  return 0;
}
