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

#include "mock_shared_memory.h"
#include <cstring>
#include <cmath>
#include <chrono>
#include <algorithm>

namespace race_tools {

MockSharedMemory::MockSharedMemory(const std::wstring& physicsName,
                                   const std::wstring& graphicsName,
                                   const std::wstring& staticName)
    : physicsName_(physicsName), graphicsName_(graphicsName), staticName_(staticName) {}

MockSharedMemory::~MockSharedMemory() {
  Shutdown();
}

bool MockSharedMemory::Initialize() {
  Shutdown();

  // Create Physics Mapping
  hPhysics_ = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0,
                                 sizeof(SPageFilePhysics), physicsName_.c_str());
  if (hPhysics_) {
    pPhysics_ = MapViewOfFile(hPhysics_, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(SPageFilePhysics));
    if (pPhysics_) {
      std::memset(pPhysics_, 0, sizeof(SPageFilePhysics));
    }
  }

  // Create Graphics Mapping (allocate enough for either Evo or Classic)
  size_t graphSize = (std::max)(sizeof(SPageFileGraphicEvo), sizeof(ks::SPageFileGraphicClassic));
  hGraphics_ = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0,
                                  static_cast<DWORD>(graphSize), graphicsName_.c_str());
  if (hGraphics_) {
    pGraphics_ = MapViewOfFile(hGraphics_, FILE_MAP_ALL_ACCESS, 0, 0, graphSize);
    if (pGraphics_) {
      std::memset(pGraphics_, 0, graphSize);
    }
  }

  // Create Static Mapping (allocate enough for either Evo or Classic)
  size_t staticSize = (std::max)(sizeof(SPageFileStaticEvo), sizeof(ks::SPageFileStaticClassic));
  hStatic_ = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0,
                                static_cast<DWORD>(staticSize), staticName_.c_str());
  if (hStatic_) {
    pStatic_ = MapViewOfFile(hStatic_, FILE_MAP_ALL_ACCESS, 0, 0, staticSize);
    if (pStatic_) {
      std::memset(pStatic_, 0, staticSize);
    }
  }

  return (pPhysics_ != nullptr && pGraphics_ != nullptr && pStatic_ != nullptr);
}

void MockSharedMemory::Shutdown() {
  StopSim();

  if (pPhysics_) {
    UnmapViewOfFile(pPhysics_);
    pPhysics_ = nullptr;
  }
  if (hPhysics_) {
    CloseHandle(hPhysics_);
    hPhysics_ = nullptr;
  }

  if (pGraphics_) {
    UnmapViewOfFile(pGraphics_);
    pGraphics_ = nullptr;
  }
  if (hGraphics_) {
    CloseHandle(hGraphics_);
    hGraphics_ = nullptr;
  }

  if (pStatic_) {
    UnmapViewOfFile(pStatic_);
    pStatic_ = nullptr;
  }
  if (hStatic_) {
    CloseHandle(hStatic_);
    hStatic_ = nullptr;
  }
}

void MockSharedMemory::WritePhysics(const SPageFilePhysics& phys) {
  if (pPhysics_) {
    std::memcpy(pPhysics_, &phys, sizeof(SPageFilePhysics));
  }
}

void MockSharedMemory::WriteGraphics(const SPageFileGraphicEvo& graph) {
  if (pGraphics_) {
    std::memcpy(pGraphics_, &graph, sizeof(SPageFileGraphicEvo));
  }
}

void MockSharedMemory::WriteStatic(const SPageFileStaticEvo& stat) {
  if (pStatic_) {
    std::memcpy(pStatic_, &stat, sizeof(SPageFileStaticEvo));
  }
}

void MockSharedMemory::WriteGraphicsClassic(const ks::SPageFileGraphicClassic& graph) {
  if (pGraphics_) {
    std::memcpy(pGraphics_, &graph, sizeof(ks::SPageFileGraphicClassic));
  }
}

void MockSharedMemory::WriteStaticClassic(const ks::SPageFileStaticClassic& stat) {
  if (pStatic_) {
    std::memcpy(pStatic_, &stat, sizeof(ks::SPageFileStaticClassic));
  }
}

void MockSharedMemory::StartSim(int durationMs) {
  if (simRunning_) return;
  simRunning_ = true;
  simThread_ = std::thread(&MockSharedMemory::SimThreadWorker, this, durationMs);
}

void MockSharedMemory::StopSim() {
  if (!simRunning_) return;
  simRunning_ = false;
  if (simThread_.joinable()) {
    simThread_.join();
  }
}

void MockSharedMemory::SimThreadWorker(int durationMs) {
  if (!pPhysics_ || !pGraphics_ || !pStatic_) return;

  // Initialize static data with 0.0f GPS to test fallback resolution of Monza
  SPageFileStaticEvo stat{};
  strcpy_s(stat.sm_version, "1.0");
  strcpy_s(stat.track, "Monza");
  strcpy_s(stat.session_name, "Practice");
  strcpy_s(stat.ac_evo_version, "0.1.0");
  stat.session = AC_CRUISE;
  stat.latitude = 0.0f;
  stat.longitude = 0.0f;
  WriteStatic(stat);

  SPageFileGraphicEvo graph{};
  graph.status = AC_LIVE;
  strcpy_s(graph.driver_name, "MockDriver");
  strcpy_s(graph.car_model, "Ferrari 296 GT3");
  graph.flag = AC_GREEN_FLAG;

  SPageFilePhysics phys{};
  phys.gear = 2; // neutral = 1, 1st = 2
  phys.gas = 1.0f;
  phys.heading = 0.05f;

  int packetCounter = 1;
  auto startTime = std::chrono::steady_clock::now();

  while (simRunning_) {
    if (durationMs > 0) {
      auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now() - startTime).count();
      if (elapsed >= durationMs) {
        break;
      }
    }

    phys.packetId = packetCounter;
    graph.packetId = packetCounter;

    // Simulate increasing speed and changing positions
    float t = packetCounter * 0.01f;
    phys.speedKmh = 120.0f + 20.0f * std::sin(t);
    phys.steerAngle = 0.05f * std::sin(t * 2.0f);
    phys.accG[0] = 0.5f * std::sin(t * 2.0f);
    phys.accG[1] = 0.2f * std::cos(t);
    phys.accG[2] = 1.0f;

    // Mock tyre contact points (Monza straight line)
    float posX = 100.0f + t * 10.0f;
    float posZ = 200.0f + t * 25.0f;
    for (int i = 0; i < 4; ++i) {
      phys.tyreContactPoint[i][0] = posX;
      phys.tyreContactPoint[i][2] = posZ;
      phys.wheelAngularSpeed[i] = (phys.speedKmh / 3.6f) / 0.32f;
      phys.wheelLoad[i] = 3500.0f;
      phys.tyreTempM[i] = 85.0f;
      phys.tyreCoreTemperature[i] = 90.0f;
    }

    phys.roadTemp = 28.5f;
    phys.airTemp = 21.0f;

    WritePhysics(phys);
    WriteGraphics(graph);

    packetCounter++;
    std::this_thread::sleep_for(std::chrono::milliseconds(10)); // 100 Hz
  }

  // Set session to off at end
  graph.status = AC_OFF;
  WriteGraphics(graph);
}

} // namespace race_tools
