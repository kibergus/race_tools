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
#include <thread>
#include <chrono>
#include <filesystem>

#include "mock_shared_memory.h"
#include "ace_shared_memory.h"
#include "telemetry_collector.h"
#include "config.h"
#include "logger.h"

using namespace race_tools;

int main() {
  std::cout << "==================================================\n";
  std::cout << "Starting Assetto Corsa Evo Mock Telemetry Runner\n";
  std::cout << "==================================================\n";

  std::wstring physicsMap = L"Local\\mock_acevo_pmf_physics";
  std::wstring graphicsMap = L"Local\\mock_acevo_pmf_graphics";
  std::wstring staticMap = L"Local\\mock_acevo_pmf_static";

  MockSharedMemory mock(physicsMap, graphicsMap, staticMap);
  if (!mock.Initialize()) {
    std::cerr << "Failed to initialize MockSharedMemory!\n";
    return 1;
  }

  std::wstring outDir = std::filesystem::current_path().wstring() + L"\\MockTelemetry";
  std::error_code ec;
  std::filesystem::create_directories(outDir, ec);

  Logger logger(outDir + L"\\mock_runner.log");

  PluginConfig config;
  config.league = "acevo_mock";
  config.pollingRateHz = 100;
  SinkConfig fileSink;
  fileSink.type = "file";
  fileSink.path = std::filesystem::path(outDir).string();
  fileSink.enabled = true;
  config.sinks.push_back(fileSink);

  auto reader = std::make_shared<AceSharedMemoryReader>();
  reader->SetCustomMapNames(physicsMap, graphicsMap, staticMap);

  TelemetryCollector collector(reader, config, logger, outDir);

  std::cout << "Starting Mock Shared Memory simulation at 100Hz...\n";
  mock.StartSim();

  std::cout << "Starting Telemetry Collector...\n";
  collector.Start();

  std::cout << "Streaming mock telemetry for 3 seconds...\n";
  std::this_thread::sleep_for(std::chrono::seconds(3));

  std::cout << "Stopping simulation and collector...\n";
  mock.StopSim();
  collector.Stop();
  mock.Shutdown();

  std::cout << "SUCCESS: Mock telemetry run completed. Check output at: "
            << std::filesystem::path(outDir).string() << "\n";
  return 0;
}
