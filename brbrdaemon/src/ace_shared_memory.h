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

#include <windows.h>
#include <string>
#include <memory>
#include "ace_evo_protocol.h"
#include "ac_protocol.h"

namespace race_tools {

using SPageFilePhysics = ks::SPageFilePhysics;
using SPageFileGraphicEvo = ks::SPageFileGraphicEvo;
using SPageFileStaticEvo = ks::SPageFileStaticEvo;
using SPageFileGraphicClassic = ks::SPageFileGraphicClassic;
using SPageFileStaticClassic = ks::SPageFileStaticClassic;

enum class SimulatorType {
  Unknown,
  Auto,
  AceEvo,
  AcRally,
  AssettoCorsa
};

enum class LayoutFormat {
  Unknown,
  Evo,      // Single-byte char strings (ACEVO)
  Classic   // Wide wchar_t strings (AC, ACC, classic ACR)
};

// Unified session metadata extracted from either Evo or Classic shared memory
struct UnifiedStaticInfo {
  std::string track;
  std::string trackConfiguration;
  std::string sessionName;
  int sessionType = -1;
  std::string gameVersion;
  float latitude = 0.0f;
  float longitude = 0.0f;
};

// Unified dynamic graphics state extracted from either Evo or Classic shared memory
struct UnifiedGraphicInfo {
  int packetId = 0;
  int status = 0;
  int session = 0;
  float handbrakePercent = 0.0f;
  int flag = 0;
  int steerDegrees = 0;
  std::string driverName;
  std::string driverSurname;
  std::string carModel;
  float carCoordinates[3] = {0.0f, 0.0f, 0.0f};
};

// Reads shared memory blocks from Assetto Corsa Evo, Assetto Corsa Rally, and Assetto Corsa
class AceSharedMemoryReader {
public:
  AceSharedMemoryReader();
  ~AceSharedMemoryReader();

  // Connects to the shared memory blocks. Probes running games if sim is Auto.
  bool Connect(SimulatorType requestedSim = SimulatorType::Auto);
  void Disconnect();
  bool IsConnected() const;

  // Read current snapshots
  bool ReadPhysics(SPageFilePhysics& out);
  bool ReadGraphics(SPageFileGraphicEvo& out);
  bool ReadStatic(SPageFileStaticEvo& out);
  bool ReadGraphicsClassic(SPageFileGraphicClassic& out);
  bool ReadStaticClassic(SPageFileStaticClassic& out);

  // Unified snapshot reading (handles Evo and Classic transparently)
  bool ReadUnifiedStatic(UnifiedStaticInfo& out);
  bool ReadUnifiedGraphic(UnifiedGraphicInfo& out);

  // Detection accessors
  SimulatorType GetSimulatorType() const { return detectedSim_; }
  LayoutFormat GetLayoutFormat() const { return detectedLayout_; }
  std::string GetSimulatorName() const;
  std::string GetDefaultLeague() const;

  // Set custom map names (primarily for testing/mocking)
  void SetCustomMapNames(const std::wstring& physicsName,
                         const std::wstring& graphicsName,
                         const std::wstring& staticName,
                         SimulatorType simType = SimulatorType::AceEvo,
                         LayoutFormat layout = LayoutFormat::Unknown);

  static SimulatorType DetectRunningSimulator();

private:
  bool TryOpenMapGroup(SimulatorType simType,
                       const std::wstring& physName,
                       const std::wstring& graphName,
                       const std::wstring& statName);

  std::wstring physicsMapName_ = L"Local\\acevo_pmf_physics";
  std::wstring graphicsMapName_ = L"Local\\acevo_pmf_graphics";
  std::wstring staticMapName_ = L"Local\\acevo_pmf_static";

  bool hasCustomMapNames_ = false;
  SimulatorType detectedSim_ = SimulatorType::Unknown;
  LayoutFormat detectedLayout_ = LayoutFormat::Unknown;
  LayoutFormat customLayout_ = LayoutFormat::Unknown;

  HANDLE hPhysics_ = nullptr;
  HANDLE hGraphics_ = nullptr;
  HANDLE hStatic_ = nullptr;

  const void* pPhysicsRaw_ = nullptr;
  const void* pGraphicsRaw_ = nullptr;
  const void* pStaticRaw_ = nullptr;
};

} // namespace race_tools
