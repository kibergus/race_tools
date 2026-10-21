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

#include "config.h"
#include <windows.h>
#include <fstream>
#include <filesystem>
#include <stdexcept>
#include <regex>
#include "thirdparty/nlohmann/json.hpp"

namespace race_tools {

std::wstring GetCurrentModuleDirectory() {
  wchar_t path[MAX_PATH];
  HMODULE hm = NULL;
  if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                         (LPCWSTR)&GetCurrentModuleDirectory, &hm)) {
    GetModuleFileNameW(hm, path, MAX_PATH);
    std::filesystem::path modulePath(path);
    return modulePath.parent_path().wstring();
  }
  throw std::runtime_error("Failed to retrieve current module handle (error code " + std::to_string(GetLastError()) +
                           ")");
}

std::wstring GetConfigFilePath() {
  std::wstring dir = GetCurrentModuleDirectory();
  return dir + L"\\BrBrDbTelemetry.json";
}

PluginConfig LoadPluginConfig(const std::wstring& path) {
  std::ifstream file(path);
  if (!file.is_open()) {
    throw std::runtime_error("Configuration file does not exist or cannot be opened: " +
                             std::filesystem::path(path).string());
  }

  nlohmann::json j;
  file >> j;
  file.close();

  PluginConfig outConfig;
  if (j.contains("simulator")) {
    if (!j["simulator"].is_string()) {
      throw std::runtime_error("Field 'simulator' must be a string");
    }
    outConfig.simulator = j["simulator"].get<std::string>();
  }
  if (j.contains("car_name_regexp")) {
    if (!j["car_name_regexp"].is_string()) {
      throw std::runtime_error("Field 'car_name_regexp' must be a string");
    }
    outConfig.carNameRegexp = j["car_name_regexp"].get<std::string>();
  }
  if (j.contains("kart_number_filter")) {
    if (!j["kart_number_filter"].is_string()) {
      throw std::runtime_error("Field 'kart_number_filter' must be a string");
    }
    outConfig.kartNumberFilter = j["kart_number_filter"].get<std::string>();
  }
  if (j.contains("league")) {
    if (!j["league"].is_string()) {
      throw std::runtime_error("Field 'league' must be a string");
    }
    outConfig.league = j["league"].get<std::string>();
  }
  if (j.contains("polling_rate_hz")) {
    if (!j["polling_rate_hz"].is_number_integer()) {
      throw std::runtime_error("Field 'polling_rate_hz' must be an integer");
    }
    outConfig.pollingRateHz = j["polling_rate_hz"].get<int>();
  }

  if (j.contains("sinks")) {
    if (!j["sinks"].is_array()) {
      throw std::runtime_error("Field 'sinks' must be an array");
    }
    for (const auto& item : j["sinks"]) {
      if (!item.is_object()) {
        throw std::runtime_error("Items in 'sinks' must be JSON objects");
      }
      if (!item.contains("type") || !item["type"].is_string()) {
        throw std::runtime_error("Item in 'sinks' is missing or has a non-string 'type'");
      }
      SinkConfig sink;
      sink.type = item["type"].get<std::string>();
      if (item.contains("enabled")) {
        if (!item["enabled"].is_boolean()) {
          throw std::runtime_error("Field 'enabled' in sink must be a boolean");
        }
        sink.enabled = item["enabled"].get<bool>();
      }
      if (sink.type == "file") {
        if (item.contains("path")) {
          if (!item["path"].is_string()) {
            throw std::runtime_error("Field 'path' in file sink must be a string");
          }
          sink.path = item["path"].get<std::string>();
        }
      } else if (sink.type == "http") {
        if (item.contains("server_address")) {
          if (!item["server_address"].is_string()) {
            throw std::runtime_error("Field 'server_address' in http sink must be a string");
          }
          sink.serverAddress = item["server_address"].get<std::string>();
        }
        if (item.contains("api_key")) {
          if (!item["api_key"].is_string()) {
            throw std::runtime_error("Field 'api_key' in http sink must be a string");
          }
          sink.apiKey = item["api_key"].get<std::string>();
        }
        if (item.contains("max_retries")) {
          if (!item["max_retries"].is_number_integer()) {
            throw std::runtime_error("Field 'max_retries' in http sink must be an integer");
          }
          sink.maxRetries = item["max_retries"].get<int>();
        }
        if (item.contains("buffer_duration_minutes")) {
          if (!item["buffer_duration_minutes"].is_number_integer()) {
            throw std::runtime_error("Field 'buffer_duration_minutes' in http sink must be an integer");
          }
          sink.bufferDurationMinutes = item["buffer_duration_minutes"].get<int>();
        }
      } else {
        throw std::runtime_error("Unknown sink type: " + sink.type);
      }
      outConfig.sinks.push_back(sink);
    }
  }

  if (j.contains("driver_name_overrides")) {
    if (!j["driver_name_overrides"].is_array()) {
      throw std::runtime_error("Field 'driver_name_overrides' must be an array");
    }
    for (const auto& item : j["driver_name_overrides"]) {
      if (!item.is_object()) {
        throw std::runtime_error("Items in 'driver_name_overrides' must be JSON objects");
      }
      if (!item.contains("car_name_regexp") || !item["car_name_regexp"].is_string()) {
        throw std::runtime_error("Item in 'driver_name_overrides' is missing or has a non-string 'car_name_regexp'");
      }
      if (!item.contains("driver_name") || !item["driver_name"].is_string()) {
        throw std::runtime_error("Item in 'driver_name_overrides' is missing or has a non-string 'driver_name'");
      }
      DriverNameOverride dno;
      dno.carNameRegexp = item["car_name_regexp"].get<std::string>();
      dno.driverName = item["driver_name"].get<std::string>();

      try {
        std::regex testRe(dno.carNameRegexp, std::regex::icase);
      } catch (const std::regex_error& e) {
        throw std::runtime_error("Invalid regex in driver_name_overrides ('" + dno.carNameRegexp + "'): " + e.what());
      }

      outConfig.driverNameOverrides.push_back(dno);
    }
  }

  if (j.contains("simulators") && j["simulators"].is_object()) {
    for (auto& [key, val] : j["simulators"].items()) {
      if (val.is_object()) {
        SimulatorSettings simSet;
        if (val.contains("league") && val["league"].is_string()) {
          simSet.league = val["league"].get<std::string>();
        }
        if (val.contains("car_name_regexp") && val["car_name_regexp"].is_string()) {
          simSet.carNameRegexp = val["car_name_regexp"].get<std::string>();
        }
        outConfig.simulators[key] = simSet;
      }
    }
  }

  return outConfig;
}

std::string PluginConfig::GetLeagueForSim(const std::string& sim, const std::string& fallback) const {
  auto it = simulators.find(sim);
  if (it != simulators.end() && !it->second.league.empty() && it->second.league != "auto") {
    return it->second.league;
  }
  if (!league.empty() && league != "auto") {
    return league;
  }
  return fallback;
}

std::string PluginConfig::GetCarNameRegexpForSim(const std::string& sim) const {
  auto it = simulators.find(sim);
  if (it != simulators.end() && !it->second.carNameRegexp.empty()) {
    return it->second.carNameRegexp;
  }
  return carNameRegexp;
}

void SavePluginConfig(const std::wstring& path, const PluginConfig& config) {
  nlohmann::json j;
  j["simulator"] = config.simulator;
  j["car_name_regexp"] = config.carNameRegexp;
  j["kart_number_filter"] = config.kartNumberFilter;
  j["league"] = config.league;
  j["polling_rate_hz"] = config.pollingRateHz;

  if (!config.simulators.empty()) {
    nlohmann::json simMap = nlohmann::json::object();
    for (const auto& [key, simSet] : config.simulators) {
      nlohmann::json simObj = nlohmann::json::object();
      if (!simSet.league.empty()) {
        simObj["league"] = simSet.league;
      }
      if (!simSet.carNameRegexp.empty()) {
        simObj["car_name_regexp"] = simSet.carNameRegexp;
      }
      simMap[key] = simObj;
    }
    j["simulators"] = simMap;
  }

  j["sinks"] = nlohmann::json::array();
  for (const auto& sink : config.sinks) {
    nlohmann::json sinkJson;
    sinkJson["type"] = sink.type;
    sinkJson["enabled"] = sink.enabled;
    if (sink.type == "file") {
      sinkJson["path"] = sink.path;
    } else if (sink.type == "http") {
      sinkJson["server_address"] = sink.serverAddress;
      sinkJson["api_key"] = sink.apiKey;
      sinkJson["max_retries"] = sink.maxRetries;
      sinkJson["buffer_duration_minutes"] = sink.bufferDurationMinutes;
    }
    j["sinks"].push_back(sinkJson);
  }

  j["driver_name_overrides"] = nlohmann::json::array();
  for (const auto& dno : config.driverNameOverrides) {
    nlohmann::json dnoJson;
    dnoJson["car_name_regexp"] = dno.carNameRegexp;
    dnoJson["driver_name"] = dno.driverName;
    j["driver_name_overrides"].push_back(dnoJson);
  }

  std::ofstream file(path);
  if (!file.is_open()) {
    throw std::runtime_error("Cannot write to config file: " + std::filesystem::path(path).string());
  }
  file << j.dump(2);
  file.close();
}

} // namespace race_tools
