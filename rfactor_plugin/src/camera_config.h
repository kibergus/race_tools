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

#include <filesystem>
#include <optional>
#include <string>
#include <windows.h>

namespace race_tools {

struct CameraProjectionInfo {
  bool enabled = false;
  double vfovDeg = 50.0;
  int resolutionW = 1920;
  int resolutionH = 1080;
};

struct PlayerJsonCameraSettings {
  double verticalFovDeg = 9.0;
  std::string vehicleFile;
  bool found = false;
};

// Parses configured video resolution from UserData/Config_DX11.ini.
bool ParseConfigDx11Resolution(const std::filesystem::path& configIniPath, int& outWidth, int& outHeight);

// Parses "Graphic Options" -> "Vertical FOV Degrees" and "DRIVER" -> "Vehicle File" from player.JSON.
PlayerJsonCameraSettings ParsePlayerJsonCameraSettings(const std::filesystem::path& playerJsonPath);

// Looks up per-vehicle FOV override in all_vehicles.ini. Returns std::nullopt when FOV is 0, 9, or not found.
std::optional<double> FindVehicleFovOverride(const std::filesystem::path& allVehiclesIniPath,
                                             const std::string& vehicleFile,
                                             const std::string& vehicleName);

// Queries live Win32 client area width and height in pixels for the game window.
bool QueryWindowClientResolution(HWND hwnd, int& outWidth, int& outHeight);

// Resolves single-screen vertical FOV and resolution for an rFactor 2 session.
CameraProjectionInfo ResolveCameraProjection(const std::filesystem::path& userDataDir,
                                             const std::string& plrFileName,
                                             const std::string& vehicleName,
                                             HWND hwnd,
                                             std::string* outWarning = nullptr);

} // namespace race_tools
