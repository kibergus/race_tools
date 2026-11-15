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

#include "camera_config.h"
#include "thirdparty/nlohmann/json.hpp"
#include <algorithm>
#include <cctype>
#include <cmath>
#include <fstream>
#include <sstream>
#include <vector>

#pragma comment(lib, "user32.lib")

namespace race_tools {

namespace {

std::string Trim(const std::string& s) {
  size_t start = 0;
  while (start < s.size() && std::isspace(static_cast<unsigned char>(s[start]))) {
    ++start;
  }
  size_t end = s.size();
  while (end > start && std::isspace(static_cast<unsigned char>(s[end - 1]))) {
    --end;
  }
  return s.substr(start, end - start);
}

std::string ToLowerAscii(std::string s) {
  for (char& c : s) {
    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  }
  return s;
}

std::string NormalizePathForCompare(const std::string& raw) {
  std::string out;
  out.reserve(raw.size());
  for (char c : raw) {
    if (c == '/') {
      out.push_back('\\');
    } else {
      out.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
    }
  }
  return Trim(out);
}

std::string ExtractFilenameLower(const std::string& pathStr) {
  std::string norm = NormalizePathForCompare(pathStr);
  size_t pos = norm.find_last_of('\\');
  if (pos == std::string::npos) {
    return norm;
  }
  return norm.substr(pos + 1);
}

std::string StripStem(const std::string& filename) {
  size_t dot = filename.find_last_of('.');
  if (dot == std::string::npos) {
    return filename;
  }
  return filename.substr(0, dot);
}

std::string StripLineComment(const std::string& line) {
  bool inQuotes = false;
  for (size_t i = 0; i < line.size(); ++i) {
    if (line[i] == '"') {
      inQuotes = !inQuotes;
    } else if (!inQuotes && i + 1 < line.size() && line[i] == '/' && line[i + 1] == '/') {
      return line.substr(0, i);
    }
  }
  return line;
}

std::vector<double> ParseTupleNumbers(const std::string& rawVal) {
  std::vector<double> nums;
  size_t openParen = rawVal.find('(');
  size_t closeParen = rawVal.find(')');
  std::string body = rawVal;
  if (openParen != std::string::npos && closeParen != std::string::npos && closeParen > openParen) {
    body = rawVal.substr(openParen + 1, closeParen - openParen - 1);
  }
  std::stringstream ss(body);
  std::string item;
  while (std::getline(ss, item, ',')) {
    std::string trimmed = Trim(item);
    if (!trimmed.empty()) {
      try {
        nums.push_back(std::stod(trimmed));
      } catch (...) {
      }
    }
  }
  return nums;
}

std::filesystem::path ResolvePlayerJsonPath(const std::filesystem::path& userDataDir,
                                            const std::string& plrFileName) {
  if (!plrFileName.empty()) {
    std::filesystem::path p(plrFileName);
    std::string stem = p.stem().string();
    if (!stem.empty()) {
      std::error_code ec;
      auto candidate1 = userDataDir / stem / (stem + ".JSON");
      if (std::filesystem::exists(candidate1, ec)) return candidate1;
      auto candidate2 = userDataDir / "player" / (stem + ".JSON");
      if (std::filesystem::exists(candidate2, ec)) return candidate2;
      auto candidate3 = userDataDir / stem / "player.JSON";
      if (std::filesystem::exists(candidate3, ec)) return candidate3;
    }
  }
  return userDataDir / "player" / "player.JSON";
}

} // namespace

bool ParseConfigDx11Resolution(const std::filesystem::path& configIniPath, int& outWidth, int& outHeight) {
  std::ifstream file(configIniPath);
  if (!file.is_open()) {
    return false;
  }

  int resW = 0;
  int resH = 0;
  int customW = 0;
  int customH = 0;
  std::string rawLine;
  while (std::getline(file, rawLine)) {
    std::string line = Trim(StripLineComment(rawLine));
    if (line.empty() || line[0] == '[') {
      continue;
    }
    size_t eq = line.find('=');
    if (eq == std::string::npos) {
      continue;
    }
    std::string key = ToLowerAscii(Trim(line.substr(0, eq)));
    std::string val = Trim(line.substr(eq + 1));

    try {
      if (key == "videoresw") {
        resW = std::stoi(val);
      } else if (key == "videoresh") {
        resH = std::stoi(val);
      } else if (key == "customvidres") {
        auto nums = ParseTupleNumbers(val);
        if (nums.size() >= 2) {
          customW = static_cast<int>(std::lround(nums[0]));
          customH = static_cast<int>(std::lround(nums[1]));
        }
      }
    } catch (...) {
      // Ignore malformed individual INI entries and continue parsing
    }
  }

  if (customW > 0 && customH > 0) {
    outWidth = customW;
    outHeight = customH;
    return true;
  }
  if (resW > 0 && resH > 0) {
    outWidth = resW;
    outHeight = resH;
    return true;
  }
  return false;
}

PlayerJsonCameraSettings ParsePlayerJsonCameraSettings(const std::filesystem::path& playerJsonPath) {
  PlayerJsonCameraSettings settings;
  std::ifstream file(playerJsonPath);
  if (!file.is_open()) {
    return settings;
  }

  nlohmann::json j = nlohmann::json::parse(file, nullptr, /*allow_exceptions=*/false, /*ignore_comments=*/true);
  if (j.is_discarded() || !j.is_object()) {
    return settings;
  }

  for (auto it = j.begin(); it != j.end(); ++it) {
    std::string sectionKey = ToLowerAscii(it.key());
    if (sectionKey == "graphic options" && it.value().is_object()) {
      for (auto git = it.value().begin(); git != it.value().end(); ++git) {
        if (ToLowerAscii(git.key()) == "vertical fov degrees" && git.value().is_number()) {
          settings.verticalFovDeg = git.value().get<double>();
          settings.found = true;
        }
      }
    } else if (sectionKey == "driver" && it.value().is_object()) {
      for (auto dit = it.value().begin(); dit != it.value().end(); ++dit) {
        if (ToLowerAscii(dit.key()) == "vehicle file" && dit.value().is_string()) {
          settings.vehicleFile = dit.value().get<std::string>();
        }
      }
    }
  }

  return settings;
}

std::optional<double> FindVehicleFovOverride(const std::filesystem::path& allVehiclesIniPath,
                                             const std::string& vehicleFile,
                                             const std::string& vehicleName) {
  std::ifstream file(allVehiclesIniPath);
  if (!file.is_open()) {
    return std::nullopt;
  }

  const std::string targetNormPath = NormalizePathForCompare(vehicleFile);
  const std::string targetFilename = ExtractFilenameLower(vehicleFile);
  const std::string targetVehicleNameLower = ToLowerAscii(Trim(vehicleName));

  struct VehicleEntry {
    std::string fileNorm;
    std::string filenameLower;
    std::string stemLower;
    long long metersDriven = 0;
    double fov = 0.0;
    bool hasFov = false;
  };

  std::vector<VehicleEntry> entries;
  VehicleEntry current;
  bool inVehicle = false;

  auto flushCurrent = [&]() {
    if (inVehicle && !current.fileNorm.empty()) {
      entries.push_back(current);
    }
    current = VehicleEntry{};
  };

  std::string rawLine;
  while (std::getline(file, rawLine)) {
    std::string line = Trim(StripLineComment(rawLine));
    if (line.empty()) {
      continue;
    }
    if (line[0] == '[') {
      flushCurrent();
      inVehicle = (ToLowerAscii(line) == "[vehicle]");
      continue;
    }
    if (!inVehicle) {
      continue;
    }

    size_t eq = line.find('=');
    if (eq == std::string::npos) {
      continue;
    }
    std::string key = ToLowerAscii(Trim(line.substr(0, eq)));
    std::string val = Trim(line.substr(eq + 1));
    if (val.size() >= 2 && val.front() == '"' && val.back() == '"') {
      val = val.substr(1, val.size() - 2);
    }

    try {
      if (key == "file") {
        current.fileNorm = NormalizePathForCompare(val);
        current.filenameLower = ExtractFilenameLower(val);
        current.stemLower = StripStem(current.filenameLower);
      } else if (key == "metersdriven") {
        current.metersDriven = std::stoll(val);
      } else if (key == "fov") {
        current.fov = std::stod(val);
        current.hasFov = true;
      }
    } catch (...) {
    }
  }
  flushCurrent();

  auto isValidOverrideFov = [](double fov) {
    return fov > 0.0 && std::abs(fov - 9.0) > 1e-6;
  };

  // Priority 1: Exact normalized full path match to player.JSON "Vehicle File"
  if (!targetNormPath.empty()) {
    for (const auto& entry : entries) {
      if (entry.fileNorm == targetNormPath) {
        if (entry.hasFov && isValidOverrideFov(entry.fov)) {
          return entry.fov;
        }
        return std::nullopt;
      }
    }
  }

  // Priority 2: Filename match to player.JSON "Vehicle File" (preferring highest MetersDriven)
  if (!targetFilename.empty()) {
    const VehicleEntry* best = nullptr;
    for (const auto& entry : entries) {
      if (entry.filenameLower == targetFilename) {
        if (best == nullptr || entry.metersDriven >= best->metersDriven) {
          best = &entry;
        }
      }
    }
    if (best != nullptr) {
      if (best->hasFov && isValidOverrideFov(best->fov)) {
        return best->fov;
      }
      return std::nullopt;
    }
  }

  // Priority 3: Fallback match by vehicleName against .VEH stem
  if (!targetVehicleNameLower.empty()) {
    const VehicleEntry* best = nullptr;
    for (const auto& entry : entries) {
      if (!entry.stemLower.empty() &&
          (entry.stemLower == targetVehicleNameLower ||
           targetVehicleNameLower.find(entry.stemLower) != std::string::npos)) {
        if (best == nullptr || entry.metersDriven >= best->metersDriven) {
          best = &entry;
        }
      }
    }
    if (best != nullptr && best->hasFov && isValidOverrideFov(best->fov)) {
      return best->fov;
    }
  }

  return std::nullopt;
}

bool QueryWindowClientResolution(HWND hwnd, int& outWidth, int& outHeight) {
  if (hwnd == nullptr || !IsWindow(hwnd)) {
    return false;
  }

  RECT clientRect = {};
  if (!GetClientRect(hwnd, &clientRect)) {
    return false;
  }
  int cw = clientRect.right - clientRect.left;
  int ch = clientRect.bottom - clientRect.top;
  if (cw <= 0 || ch <= 0) {
    return false;
  }

  outWidth = cw;
  outHeight = ch;
  return true;
}

CameraProjectionInfo ResolveCameraProjection(const std::filesystem::path& userDataDir,
                                             const std::string& plrFileName,
                                             const std::string& vehicleName,
                                             HWND hwnd,
                                             std::string* outWarning) {
  CameraProjectionInfo info;
  info.enabled = true;

  if (!userDataDir.empty()) {
    ParseConfigDx11Resolution(userDataDir / "Config_DX11.ini", info.resolutionW, info.resolutionH);
  }
  QueryWindowClientResolution(hwnd, info.resolutionW, info.resolutionH);

  std::filesystem::path playerJsonPath = ResolvePlayerJsonPath(userDataDir, plrFileName);
  PlayerJsonCameraSettings plrSettings = ParsePlayerJsonCameraSettings(playerJsonPath);
  std::filesystem::path allVehiclesPath = playerJsonPath.parent_path() / "all_vehicles.ini";

  auto vehFov = FindVehicleFovOverride(allVehiclesPath, plrSettings.vehicleFile, vehicleName);
  if (vehFov.has_value()) {
    info.vfovDeg = *vehFov;
  } else if (plrSettings.found && plrSettings.verticalFovDeg > 0.0 &&
             std::abs(plrSettings.verticalFovDeg - 9.0) > 1e-6) {
    info.vfovDeg = plrSettings.verticalFovDeg;
  } else {
    info.vfovDeg = 50.0;
    if (outWarning != nullptr) {
      *outWarning =
          "Camera FOV is set to 'Default' (9) in player.JSON with no per-vehicle FOV override in all_vehicles.ini; "
          "using fallback vfov_deg=50.0. For exact gaze reconstruction on encrypted DLC vehicles, set an explicit "
          "numeric Vertical FOV in rFactor 2 Settings -> Graphics.";
    }
  }

  return info;
}

} // namespace race_tools
