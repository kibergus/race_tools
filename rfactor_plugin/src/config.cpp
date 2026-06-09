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
#include "thirdparty/nlohmann/json.hpp"

namespace race_tools {

std::wstring GetCurrentModuleDirectory() {
  wchar_t path[MAX_PATH];
  HMODULE hm = NULL;
  // Uses the address of the current function to retrieve the current DLL/EXE module handle
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
        if (!item.contains("server_address") || !item["server_address"].is_string()) {
          throw std::runtime_error("Item in 'sinks' of type 'http' is missing or has a non-string 'server_address'");
        }
        sink.serverAddress = item["server_address"].get<std::string>();
        if (item.contains("api_key")) {
          if (!item["api_key"].is_string()) {
            throw std::runtime_error("Field 'api_key' in http sink must be a string");
          }
          sink.apiKey = item["api_key"].get<std::string>();
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
      DriverNameOverride override_item;
      override_item.carNameRegexp = item["car_name_regexp"].get<std::string>();
      override_item.driverName = item["driver_name"].get<std::string>();
      outConfig.driverNameOverrides.push_back(override_item);
    }
  }

  return outConfig;
}

void SavePluginConfig(const std::wstring& path, const PluginConfig& config) {
  std::ofstream file(path);
  if (!file.is_open()) {
    throw std::runtime_error("Failed to open configuration file for writing: " + std::filesystem::path(path).string());
  }

  nlohmann::json j;
  j["car_name_regexp"] = config.carNameRegexp;
  j["kart_number_filter"] = config.kartNumberFilter;
  j["league"] = config.league;

  nlohmann::json sinks_arr = nlohmann::json::array();
  for (const auto& item : config.sinks) {
    nlohmann::json item_json;
    item_json["type"] = item.type;
    item_json["enabled"] = item.enabled;
    if (item.type == "file") {
      item_json["path"] = item.path;
    } else if (item.type == "http") {
      item_json["server_address"] = item.serverAddress;
      item_json["api_key"] = item.apiKey;
    }
    sinks_arr.push_back(item_json);
  }
  j["sinks"] = sinks_arr;

  nlohmann::json overrides_arr = nlohmann::json::array();
  for (const auto& item : config.driverNameOverrides) {
    nlohmann::json item_json;
    item_json["car_name_regexp"] = item.carNameRegexp;
    item_json["driver_name"] = item.driverName;
    overrides_arr.push_back(item_json);
  }
  j["driver_name_overrides"] = overrides_arr;

  file << j.dump(4);
  file.close();
}

} // namespace race_tools
