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

#pragma once

#include <string>
#include <vector>

namespace race_tools {

struct DriverNameOverride {
  std::string carNameRegexp;
  std::string driverName;
};

struct SinkConfig {
  std::string type; // "file" or "http"
  std::string path; // for file sink
  std::string serverAddress; // for http sink
  std::string apiKey; // for http sink
  bool enabled = true;
};

// Structure holding the configuration options for the telemetry extractor plugin
struct PluginConfig {
  std::string carNameRegexp = "KSP.*";
  std::string kartNumberFilter = ""; // empty = match all kart numbers
  std::string league = "kartsim";
  std::vector<DriverNameOverride> driverNameOverrides;
  std::vector<SinkConfig> sinks;
};

// Retrieve the directory where the current compiled binary (DLL or EXE) is located.
std::wstring GetCurrentModuleDirectory();

// Retrieve the full path to the configuration INI file.
std::wstring GetConfigFilePath();

// Load the configuration from the specified INI file path.
// Throws an exception if the file does not exist or cannot be read.
PluginConfig LoadPluginConfig(const std::wstring& path);

// Save the configuration to the specified INI file path.
void SavePluginConfig(const std::wstring& path, const PluginConfig& config);

} // namespace race_tools
