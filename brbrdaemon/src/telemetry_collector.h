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
#include <memory>
#include <thread>
#include <atomic>
#include <vector>
#include <chrono>

#include "ace_shared_memory.h"
#include "config.h"
#include "logger.h"
#include "safe_queue.h"
#include "sink.h"

namespace race_tools {

enum class SessionState {
  Idle,
  Live,
  Paused
};

struct SessionIdentity {
  std::string track;
  std::string trackConfiguration;
  std::string carModel;
  std::string driverName;
  std::string sessionType;
  int session = -1;

  bool operator==(const SessionIdentity& o) const {
    return track == o.track &&
           trackConfiguration == o.trackConfiguration &&
           carModel == o.carModel &&
           driverName == o.driverName &&
           sessionType == o.sessionType &&
           session == o.session;
  }
  bool operator!=(const SessionIdentity& o) const {
    return !(*this == o);
  }
};

// Manages polling, session lifecycle, and formatting for Assetto Corsa / Evo / Rally telemetry
class TelemetryCollector {
public:
  TelemetryCollector(std::shared_ptr<AceSharedMemoryReader> shmReader,
                     const PluginConfig& config,
                     Logger& logger,
                     const std::wstring& defaultLogDir = L"");
  ~TelemetryCollector();

  // Start background collector thread
  void Start();

  // Stop background collector thread
  void Stop();

  // Formats a single CSV line for testing or output (UnifiedGraphicInfo)
  static std::string FormatTelemetryLine(
      const SPageFilePhysics& physics,
      const UnifiedGraphicInfo& graphics,
      const std::chrono::system_clock::time_point& sessionStartRealTime,
      std::chrono::steady_clock::time_point sessionStartSteadyTime,
      std::chrono::steady_clock::time_point nowSteadyTime);

  // Formats a single CSV line for testing or output (backward-compatibility overload for SPageFileGraphicEvo)
  static std::string FormatTelemetryLine(
      const SPageFilePhysics& physics,
      const SPageFileGraphicEvo& graphics,
      const std::chrono::system_clock::time_point& sessionStartRealTime,
      std::chrono::steady_clock::time_point sessionStartSteadyTime,
      std::chrono::steady_clock::time_point nowSteadyTime);

  // Checks if currently actively capturing on-track telemetry
  bool IsSessionActive() const { return inSession_.load(std::memory_order_relaxed); }

  SessionState GetSessionState() const { return sessionState_; }

  std::string GetConnectedSimulatorName() const;

  int GetActiveUploadCount() const { return activeUploads_.load(std::memory_order_relaxed); }

  // Set shared memory reader (useful for tests before Start is called)
  void SetSharedMemoryReader(std::shared_ptr<AceSharedMemoryReader> reader) {
    if (!running_.load()) {
      shmReader_ = reader;
    }
  }

private:
  void PollingThreadWorker();
  void StartSession(const SPageFilePhysics& physics,
                    const UnifiedGraphicInfo& graphics,
                    const UnifiedStaticInfo& statics);
  void EndSession();

  std::shared_ptr<AceSharedMemoryReader> shmReader_;
  PluginConfig config_;
  Logger& logger_;
  std::wstring defaultLogDir_;

  std::atomic<bool> running_{false};
  std::thread workerThread_;

  // Active session state machine
  std::atomic<bool> inSession_{false};
  SessionState sessionState_ = SessionState::Idle;
  SessionIdentity currentIdentity_;
  int lastPacketId_ = -1;
  std::chrono::steady_clock::time_point lastPacketAdvanceTime_;
  std::chrono::system_clock::time_point sessionStartTime_;
  std::chrono::steady_clock::time_point sessionStartSteadyTime_;
  std::atomic<int> activeUploads_{0};

  // Sinks & queues for active session
  std::shared_ptr<SafeQueue<Sink::Message>> sessionQueue_;
  std::vector<std::unique_ptr<SinkWorker>> sinkWorkers_;
};

} // namespace race_tools
