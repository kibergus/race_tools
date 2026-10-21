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
#include <atomic>
#include <thread>
#include "ace_shared_memory.h"
#include "ac_protocol.h"

namespace race_tools {

// Mock provider that creates and updates named shared memory blocks for testing
class MockSharedMemory {
public:
  MockSharedMemory(const std::wstring& physicsName = L"Local\\test_acevo_pmf_physics",
                   const std::wstring& graphicsName = L"Local\\test_acevo_pmf_graphics",
                   const std::wstring& staticName = L"Local\\test_acevo_pmf_static");
  ~MockSharedMemory();

  bool Initialize();
  void Shutdown();

  // Updates single frame (Evo format)
  void WritePhysics(const SPageFilePhysics& phys);
  void WriteGraphics(const SPageFileGraphicEvo& graph);
  void WriteStatic(const SPageFileStaticEvo& stat);

  // Updates single frame (Classic AC format)
  void WriteGraphicsClassic(const ks::SPageFileGraphicClassic& graph);
  void WriteStaticClassic(const ks::SPageFileStaticClassic& stat);

  // Starts an automated simulation loop at ~100Hz
  void StartSim(int durationMs = 0);
  void StopSim();

  std::wstring GetPhysicsMapName() const { return physicsName_; }
  std::wstring GetGraphicsMapName() const { return graphicsName_; }
  std::wstring GetStaticMapName() const { return staticName_; }

private:
  void SimThreadWorker(int durationMs);

  std::wstring physicsName_;
  std::wstring graphicsName_;
  std::wstring staticName_;

  HANDLE hPhysics_ = nullptr;
  HANDLE hGraphics_ = nullptr;
  HANDLE hStatic_ = nullptr;

  void* pPhysics_ = nullptr;
  void* pGraphics_ = nullptr;
  void* pStatic_ = nullptr;

  std::atomic<bool> simRunning_{false};
  std::thread simThread_;
};

} // namespace race_tools
