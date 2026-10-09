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

#include <vector>
#include <queue>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <atomic>
#include <string>
#include <fstream>
#include <regex>
#include <chrono>
#include <map>
#include <unordered_map>
#include <limits>
#include <memory>
#include <variant>
#include "config.h"
#include "logger.h"
#include "safe_queue.h"
#include "sink.h"

#include "rf2_sdk/InternalsPlugin.hpp"
#include "math_utils.h"

namespace race_tools {

std::string SanitizeFilename(const std::string& name);

struct SessionMetadata {
  std::string trackName;
  std::string driverName;
  std::string sessionType;
  std::string vehicleClass;
  std::string vehicleName;
  std::string kartNumber;
};

struct ParticipantInfo {
  long mID = 0;
  std::string driverName;
  std::string vehicleClass;
  std::string vehicleName;
  std::string kartNumber;
  unsigned char flag = 0;
  bool isPlayer = false;
};

struct VehicleTimeTracker {
  double lastElapsedTime = 0.0;
  std::chrono::steady_clock::time_point lastElapsedTimeRealTime;
  bool firstTelemetryReceived = false;
};

struct ParticipantSession {
  long vehicleId = 0;
  std::string participantTag;
  std::string driverName;
  std::string vehicleClass;
  std::string kartNumber;
  std::string trackName;
  std::string sessionType;
  std::chrono::system_clock::time_point sessionStartTime;
  double startElapsedTime = 0.0;
  bool startElapsedTimeCaptured = false;

  SafeQueue<Sink::Message> csvQueue;
  std::vector<std::unique_ptr<SinkWorker>> sinkWorkers;
};

// The Telemetry Plugin Implementation Class
class BrBrDbTelemetryPlugin : public InternalsPluginV07 {
public:
  explicit BrBrDbTelemetryPlugin(const std::wstring& logDirectory = L"");
  virtual ~BrBrDbTelemetryPlugin();

  // Game flow overrides
  virtual void Startup(long version) override;
  virtual void Shutdown() override;
  virtual void EnterRealtime() override;
  virtual void ExitRealtime() override;

  virtual long WantsTelemetryUpdates() override { return config_.allParticipantsInRace ? 2 : 1; }
  virtual void UpdateTelemetry(const TelemInfoV01& info) override;

  virtual bool WantsScoringUpdates() override { return true; }
  virtual void UpdateScoring(const ScoringInfoV01& info) override;

private:
  std::atomic<bool> inRealtime_{false};
  std::atomic<bool> loggingActive_{false};

  std::thread loggerThread_;
  SafeQueue<TelemInfoV01> telemetryQueue_;
  std::wstring logDirectory_;

  SessionMetadata metadata_;
  std::mutex metadataMutex_;
  std::unordered_map<long, ParticipantInfo> participants_;

  PluginConfig config_;
  std::regex carNameRegex_;

  struct CompiledDriverNameOverride {
    std::regex regex;
    std::string driverName;
  };
  std::vector<CompiledDriverNameOverride> compiledDriverNameOverrides_;

  std::atomic<bool> scoringInitialized_{false};
  std::atomic<bool> carRegexMatched_{false};
  std::atomic<unsigned char> currentFlag_{0};

  // Cached track conditions (updated from scoring thread, read from logger thread)
  std::atomic<double> cachedTrackTemp_{0.0};
  std::atomic<double> cachedAmbientTemp_{0.0};
  std::atomic<double> cachedRaining_{0.0};
  std::atomic<double> cachedAvgWetness_{0.0};

  Logger logger_;
  std::atomic<int> activeUploads_{0};

  std::mutex timeTrackersMutex_;
  std::unordered_map<long, VehicleTimeTracker> timeTrackers_;

  std::unordered_map<long, std::unique_ptr<ParticipantSession>> participantSessions_;

  void LoadAndApplyConfig();
  void LoggerThreadWorker();
  std::string FormatTelemetryLine(const TelemInfoV01& info,
                                 const std::chrono::system_clock::time_point& sessionStartTime,
                                 double startElapsedTime,
                                 unsigned char flag);
};

} // namespace race_tools
