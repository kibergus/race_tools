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

#include "brbrdb_telemetry_plugin.h"
#include <cmath>
#include <iomanip>
#include <chrono>
#include <sstream>
#include <filesystem>
#include <cctype>
#include <stdexcept>

#include <windows.h>
#include "net_utils.h"
#include <winhttp.h>
#pragma comment(lib, "winhttp.lib")

namespace race_tools {

// -------------------------------------------------------------
// Helper to create directory path (C++17, no COM/shell dependency)
// -------------------------------------------------------------
void CreateTelemetryDirectory(const std::wstring& path) {
  std::error_code ec;
  std::filesystem::create_directories(path, ec);
  if (ec) {
    throw std::runtime_error("Failed to create telemetry directory: " + ec.message() + " at " +
                             std::filesystem::path(path).string());
  }
}

// -------------------------------------------------------------
// Helper to extract a number from a string
// -------------------------------------------------------------
std::string ExtractNumber(const std::string& str) {
  // First try to look for '#' followed by digits
  size_t hashPos = str.find('#');
  if (hashPos != std::string::npos) {
    std::string res;
    for (size_t i = hashPos + 1; i < str.length(); ++i) {
      if (std::isdigit(static_cast<unsigned char>(str[i]))) {
        res += str[i];
      } else if (!res.empty()) {
        break;
      }
    }
    if (!res.empty())
      return res;
  }

  // Otherwise, look for the last sequence of digits in the string
  std::string currentNum;
  std::string lastNum;
  for (char c : str) {
    if (std::isdigit(static_cast<unsigned char>(c))) {
      currentNum += c;
    } else {
      if (!currentNum.empty()) {
        lastNum = currentNum;
        currentNum.clear();
      }
    }
  }
  if (!currentNum.empty()) {
    lastNum = currentNum;
  }

  return lastNum;
}

// -------------------------------------------------------------
// Helper to convert session index to string representation
// -------------------------------------------------------------
std::string GetSessionTypeStr(long session) {
  if (session == 0)
    return "Testday";
  if (session >= 1 && session <= 4)
    return "Practice";
  if (session >= 5 && session <= 8)
    return "Qualify";
  if (session == 9)
    return "Warmup";
  if (session >= 10 && session <= 13)
    return "Race";
  return "Unknown";
}


static std::string WStringToString(const std::wstring& wstr) {
  std::string str;
  str.reserve(wstr.length());
  for (wchar_t wc : wstr) {
    str.push_back(static_cast<char>(wc));
  }
  return str;
}

// -------------------------------------------------------------
// Plugin Class Methods Implementation
// -------------------------------------------------------------

void BrBrDbTelemetryPlugin::LoadAndApplyConfig() {
  std::wstring configPath = GetConfigFilePath();
  try {
    config_ = LoadPluginConfig(configPath);
  } catch (const std::exception& e) {
    logger_.LogCritical("Failed to load plugin config at " + WStringToString(configPath) + ".\nDetails: " + e.what());
  }

  try {
    carNameRegex_ = std::regex(config_.carNameRegexp, std::regex_constants::ECMAScript | std::regex_constants::icase);
  } catch (const std::regex_error& e) {
    logger_.LogCritical("Invalid car_name_regexp \"" + config_.carNameRegexp + "\".\nDetails: " + e.what());
  }

  compiledDriverNameOverrides_.clear();
  for (const auto& item : config_.driverNameOverrides) {
    try {
      compiledDriverNameOverrides_.push_back({
        std::regex(item.carNameRegexp, std::regex_constants::ECMAScript | std::regex_constants::icase),
        item.driverName
      });
    } catch (const std::regex_error& e) {
      logger_.LogCritical("Invalid regex \"" + item.carNameRegexp + "\" in driver_name_overrides.\nDetails: " + e.what());
    }
  }
}

BrBrDbTelemetryPlugin::BrBrDbTelemetryPlugin(const std::wstring& logDirectory)
    : inRealtime_{false}, loggingActive_{false} {
  try {
    if (!logDirectory.empty()) {
      logDirectory_ = logDirectory;
    } else {
      std::wstring dllDir = GetCurrentModuleDirectory();
      std::filesystem::path rf2Root = std::filesystem::path(dllDir).parent_path().parent_path();
      logDirectory_ = (rf2Root / L"UserData" / L"BrBrDbTelemetry").wstring();
    }
  } catch (const std::exception& e) {
    MessageBoxA(NULL, (std::string("Fatal error during plugin construction: ") + e.what()).c_str(),
                "BrBrDb Telemetry - Fatal Error", MB_OK | MB_ICONERROR);
    std::terminate();
  }
}

BrBrDbTelemetryPlugin::~BrBrDbTelemetryPlugin() { Shutdown(); }

void BrBrDbTelemetryPlugin::Startup(long version) {
  (void)version;
  try {
    // 1. Create telemetry logs directory
    CreateTelemetryDirectory(logDirectory_);

    // Initialize and open the logger
    logger_.Open(logDirectory_ + L"\\BrBrDbTelemetry.log");

    logger_.Write("Plugin Startup - Version " + std::to_string(version));

    // 2. Load config
    LoadAndApplyConfig();

    csvQueue_.Clear();
    sinkWorkers_.clear();

    // Initialize Sinks from Config
    for (const auto& sinkConf : config_.sinks) {
      if (!sinkConf.enabled) {
        continue;
      }
      if (sinkConf.type == "file") {
        std::wstring path = logDirectory_;
        if (!sinkConf.path.empty()) {
          path = std::filesystem::path(sinkConf.path).wstring();
        }
        try {
          CreateTelemetryDirectory(path);
          sinkWorkers_.push_back(std::make_unique<SinkWorker>(
              std::make_unique<FileSink>(path, logger_), csvQueue_.reader()));
        } catch (const std::exception& e) {
          logger_.LogCritical("Failed to create/use telemetry directory: " + WStringToString(path) + " - Error: " + e.what());
        }
      } else if (sinkConf.type == "http") {
        sinkWorkers_.push_back(std::make_unique<SinkWorker>(
            std::make_unique<HttpSink>(sinkConf.serverAddress, sinkConf.apiKey, logger_, activeUploads_), csvQueue_.reader()));
      }
    }

    // 2. Start the background telemetry logging thread
    loggingActive_ = true;
    loggerThread_ = std::thread(&BrBrDbTelemetryPlugin::LoggerThreadWorker, this);
  } catch (const std::exception& e) {
    MessageBoxA(NULL, (std::string("Fatal error during plugin startup: ") + e.what()).c_str(),
                "BrBrDb Telemetry - Fatal Error", MB_OK | MB_ICONERROR);
    std::terminate();
  }
}

void BrBrDbTelemetryPlugin::Shutdown() {
  if (loggingActive_) {
    logger_.Write("Plugin Shutdown - Stopping background worker thread");
    // 1. Signal logging worker thread to terminate
    loggingActive_ = false;
    telemetryQueue_.SignalShutdown();

    // 2. Wait for the thread to complete writing and join
    if (loggerThread_.joinable()) {
      loggerThread_.join();
    }
    logger_.Write("Plugin Shutdown - Background worker thread joined successfully");

    // Signal shutdown to the CSV line multi-reader queue
    csvQueue_.SignalShutdown();

    logger_.Write("Plugin Shutdown - Stopping all sink workers");
    sinkWorkers_.clear();
    logger_.Write("Plugin Shutdown - All sink workers stopped");

    // Wait for active uploads to complete (max 5 seconds)
    int waitTimeMs = 0;
    while (activeUploads_ > 0 && waitTimeMs < 5000) {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      waitTimeMs += 100;
    }
    if (activeUploads_ > 0) {
      logger_.Write("Plugin Shutdown - Warning: " + std::to_string(activeUploads_) + " uploads did not complete in time");
    } else {
      logger_.Write("Plugin Shutdown - All uploads completed");
    }

    logger_.Close();
  }
}

void BrBrDbTelemetryPlugin::EnterRealtime() {
  // Reload config so any external changes take effect
  LoadAndApplyConfig();

  // Clear any leftover queues
  telemetryQueue_.Clear();
  csvQueue_.Clear();

  // Reset scoring initialization state
  scoringInitialized_ = false;
  carRegexMatched_ = false;
  currentFlag_ = 0;

  // Set flag to true - now UpdateTelemetry will push events to queue
  inRealtime_ = true;

  // Reset timestamp interpolation state
  firstTelemetryReceived_ = false;
  lastElapsedTime_ = 0.0;

  logger_.Write("Realtime cockpit entered - Telemetry extraction enabled");
}

void BrBrDbTelemetryPlugin::ExitRealtime() {
  // Stop capturing new telemetry
  inRealtime_ = false;

  logger_.Write("Realtime cockpit exited - Telemetry extraction disabled");

  // Push sentinel to trigger session end
  TelemInfoV01 sentinel = {};
  sentinel.mElapsedTime = -1.0;
  telemetryQueue_.Push(sentinel);
}

void BrBrDbTelemetryPlugin::UpdateTelemetry(const TelemInfoV01& info) {
  // Only capture and queue data if we are actively driving in cockpit
  if (inRealtime_) {
    // Only capture once scoring has initialized and the car matches regex filter
    if (!scoringInitialized_ || !carRegexMatched_) {
      return;
    }

    TelemInfoV01 localInfo = info;
    auto now = std::chrono::steady_clock::now();

    if (!firstTelemetryReceived_) {
      lastElapsedTime_ = info.mElapsedTime;
      lastElapsedTimeRealTime_ = now;
      firstTelemetryReceived_ = true;
    } else {
      if (info.mElapsedTime != lastElapsedTime_) {
        lastElapsedTime_ = info.mElapsedTime;
        lastElapsedTimeRealTime_ = now;
      } else {
        double realWorldDelta = std::chrono::duration<double>(now - lastElapsedTimeRealTime_).count();
        localInfo.mElapsedTime += realWorldDelta;
      }
    }

    telemetryQueue_.Push(localInfo);
  }
}

void BrBrDbTelemetryPlugin::UpdateScoring(const ScoringInfoV01& info) {
  std::lock_guard<std::mutex> lock(metadataMutex_);

  metadata_.trackName = info.mTrackName;
  metadata_.sessionType = GetSessionTypeStr(info.mSession);

  // Cache track conditions (atomic so logger thread can read without holding the lock)
  cachedTrackTemp_.store(info.mTrackTemp);
  cachedAmbientTemp_.store(info.mAmbientTemp);
  cachedRaining_.store(info.mRaining);
  cachedAvgWetness_.store(info.mAvgPathWetness);

  bool playerFound = false;
  if (info.mVehicle != nullptr) {
    for (long i = 0; i < info.mNumVehicles; ++i) {
      const VehicleScoringInfoV01& v = info.mVehicle[i];
      if (v.mIsPlayer) {
        metadata_.driverName = v.mDriverName;
        for (const auto& override_item : compiledDriverNameOverrides_) {
          if (std::regex_search(v.mVehicleName, override_item.regex)) {
            metadata_.driverName = override_item.driverName;
            break;
          }
        }
        metadata_.vehicleClass = v.mVehicleClass;
        metadata_.vehicleName = v.mVehicleName;
        metadata_.kartNumber = ExtractNumber(v.mVehicleName);
        if (metadata_.kartNumber.empty()) {
          metadata_.kartNumber = ExtractNumber(v.mDriverName);
        }
        playerFound = true;

        // Cache player flag status
        currentFlag_ = v.mFlag;

        // Perform vehicle name regex matching and initialize scoring state
        if (!scoringInitialized_) {
          bool carMatched = std::regex_search(v.mVehicleName, carNameRegex_);
          // Optionally filter by kart number
          bool kartMatched = true;
          if (!config_.kartNumberFilter.empty()) {
            kartMatched = (metadata_.kartNumber == config_.kartNumberFilter);
          }
          carRegexMatched_ = carMatched && kartMatched;
          scoringInitialized_ = true;
        }
        break;
      }
    }
  }

  if (!playerFound) {
    if (metadata_.driverName.empty() && info.mPlayerName[0] != '\0') {
      metadata_.driverName = info.mPlayerName;
    }
  }
}

// -------------------------------------------------------------
// Telemetry Parsing & CSV Export Logic
// -------------------------------------------------------------

void BrBrDbTelemetryPlugin::LoggerThreadWorker() {
  TelemInfoV01 info;
  bool isSessionActive = false;
  auto reader = telemetryQueue_.reader();

  while (loggingActive_) {
    // Block and wait for telemetry structures to arrive in queue using the reader
    if (!reader.Pop(info)) {
      break; // Shutdown signal received
    }

    if (info.mElapsedTime < 0.0) // Sentinel value to close session
    {
      if (isSessionActive) {
        csvQueue_.Push(Sink::EndSession{});
        isSessionActive = false;
        logger_.Write("Telemetry session ended via sentinel.");
      }
      continue;
    }

    // Start a new session if not active
    if (!isSessionActive) {
      sessionStartRealTime_ = std::chrono::system_clock::now();
      startElapsedTimeCaptured_ = false;
      startElapsedTime_ = 0.0;
      isSessionActive = true;

      logger_.Write("Telemetry session started.");

      // Push Start Event
      csvQueue_.Push(Sink::StartSession{sessionStartRealTime_});

      // Write CSV Metadata Headers
      {
        std::lock_guard<std::mutex> lock(metadataMutex_);

        std::time_t start_time_t = std::chrono::system_clock::to_time_t(sessionStartRealTime_);
        struct tm start_tm;
        gmtime_s(&start_tm, &start_time_t);
        char dateBuf[32];
        char timeBuf[32];
        sprintf_s(dateBuf, "%04d-%02d-%02d", start_tm.tm_year + 1900, start_tm.tm_mon + 1, start_tm.tm_mday);
        sprintf_s(timeBuf, "%02d:%02d:%02d", start_tm.tm_hour, start_tm.tm_min, start_tm.tm_sec);

        std::stringstream ss;
        ss << "Track name,"
           << (metadata_.trackName.empty() ? (info.mTrackName[0] != '\0' ? info.mTrackName : "Unknown Track")
                                            : metadata_.trackName)
           << "\n";
        ss << "Date," << dateBuf << "\n";
        ss << "Time," << timeBuf << "\n";
        ss << "Driver name," << (metadata_.driverName.empty() ? "Unknown Driver" : metadata_.driverName) << "\n";
        ss << "League," << config_.league << "\n";
        ss << "Class," << (metadata_.vehicleClass.empty() ? "Unknown Class" : metadata_.vehicleClass) << "\n";
        ss << "Session," << (metadata_.sessionType.empty() ? "Practice" : metadata_.sessionType) << "\n";
        if (!metadata_.kartNumber.empty()) {
          ss << "Kart number," << metadata_.kartNumber << "\n";
        }
        ss << "\n"; // Blank line separating metadata header and telemetry rows

        csvQueue_.Push(Sink::Line{ss.str(), /*isHeader=*/true});
      }

      // Write CSV Column Headers
      std::stringstream colSs;
      colSs << "Time,x,z,Speed,GForceX,GForceY,GForceZ,Throttle,Brake,Steering Angle,Lap,LapDistance,"
            << "Toe FL,Toe FR,Toe RL,Toe RR,"
            << "Ori Quat X,Ori Quat Y,Ori Quat Z,Ori Quat W,"
            << "RPS FL,RPS FR,RPS RL,RPS RR,"
            << "Lat Patch Vel FL,Lat Patch Vel FR,Lat Patch Vel RL,Lat Patch Vel RR,"
            << "Long Patch Vel FL,Long Patch Vel FR,Long Patch Vel RL,Long Patch Vel RR,"
            << "Tyre Load FL,Tyre Load FR,Tyre Load RL,Tyre Load RR,"
            << "Lat Force FL,Lat Force FR,Lat Force RL,Lat Force RR,"
            << "Slide Pct FL,Slide Pct FR,Slide Pct RL,Slide Pct RR,"
            << "Flag,"
            << "Track Temp,Ambient Temp,Raining,Avg Track Wetness,"
            << "Tyre Surf Temp FL,Tyre Surf Temp FR,Tyre Surf Temp RL,Tyre Surf Temp RR,"
            << "Tyre Carcass Temp FL,Tyre Carcass Temp FR,Tyre Carcass Temp RL,Tyre Carcass Temp RR\n";
      csvQueue_.Push(Sink::Line{colSs.str(), /*isHeader=*/true});
    }

    if (isSessionActive) {
      std::string line = FormatTelemetryLine(info);
      csvQueue_.Push(Sink::Line{line, /*isHeader=*/false});
    }
  }

  if (isSessionActive) {
    csvQueue_.Push(Sink::EndSession{});
    isSessionActive = false;
    logger_.Write("Telemetry session ended via shutdown.");
  }
}

std::string BrBrDbTelemetryPlugin::FormatTelemetryLine(const TelemInfoV01& info) {
  if (!startElapsedTimeCaptured_) {
    startElapsedTime_ = info.mElapsedTime;
    startElapsedTimeCaptured_ = true;
  }

  // 1. Time (ISO-8601 UTC)
  double elapsedDiff = info.mElapsedTime - startElapsedTime_;
  if (elapsedDiff < 0.0)
    elapsedDiff = 0.0;
  auto tp = sessionStartRealTime_ + std::chrono::duration_cast<std::chrono::system_clock::duration>(
                                         std::chrono::milliseconds(static_cast<long long>(elapsedDiff * 1000.0)));

  std::time_t time_c = std::chrono::system_clock::to_time_t(tp);
  struct tm gm_tm;
  gmtime_s(&gm_tm, &time_c);
  auto duration = tp.time_since_epoch();
  auto millis = std::chrono::duration_cast<std::chrono::milliseconds>(duration).count() % 1000;
  char timeStr[64];
  sprintf_s(timeStr, "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ", gm_tm.tm_year + 1900, gm_tm.tm_mon + 1, gm_tm.tm_mday,
            gm_tm.tm_hour, gm_tm.tm_min, gm_tm.tm_sec, static_cast<int>(millis));

  // 2. Speed (magnitude of local velocity vector)
  double speed = sqrt(info.mLocalVel.x * info.mLocalVel.x + info.mLocalVel.y * info.mLocalVel.y +
                      info.mLocalVel.z * info.mLocalVel.z);

  // 3. G-Forces (convert m/s^2 to G, divide by 9.80665)
  const double g_const = 9.80665;
  double gForceX = info.mLocalAccel.x / g_const;
  double gForceY = info.mLocalAccel.z / g_const;
  double gForceZ = info.mLocalAccel.y / g_const;

  // 4. Pedals (scale 0-1 to 0-100)
  double throttle = info.mFilteredThrottle * 100.0;
  double brake = info.mFilteredBrake * 100.0;

  // 5. Steering (convert normal -1 to 1 into degrees using physical range, default 360)
  double steerRange = 360.0;
  if (info.mPhysicalSteeringWheelRange > 0.0) {
    steerRange = info.mPhysicalSteeringWheelRange;
  }
  double steering = info.mFilteredSteering * (steerRange / 2.0);

  // 6. Orientation Quaternion
  double qx = 0.0, qy = 0.0, qz = 0.0, qw = 1.0;
  race_tools::MatrixToQuaternion(info.mOri, qx, qy, qz, qw);

  std::stringstream ss;
  ss << std::fixed << std::setprecision(5) << timeStr << "," << info.mPos.x << "," << info.mPos.z << "," << speed
     << "," << gForceX << "," << gForceY << "," << gForceZ << "," << throttle << "," << brake << "," << steering
     << "," << info.mLapNumber << ","
     << "" << ","; // LapDistance left empty

  // Wheels Toe FL, FR, RL, RR
  ss << info.mWheel[0].mToe << "," << info.mWheel[1].mToe << "," << info.mWheel[2].mToe << "," << info.mWheel[3].mToe
     << ",";

  // Orientation Quaternion
  ss << qx << "," << qy << "," << qz << "," << qw << ",";

  // Wheels RPS FL, FR, RL, RR
  const double two_pi = 2.0 * 3.14159265358979323846;
  ss << (info.mWheel[0].mRotation / two_pi) << "," << (info.mWheel[1].mRotation / two_pi) << ","
     << (info.mWheel[2].mRotation / two_pi) << "," << (info.mWheel[3].mRotation / two_pi) << ",";

  // Wheels Lat Patch Vel FL, FR, RL, RR
  ss << info.mWheel[0].mLateralPatchVel << "," << info.mWheel[1].mLateralPatchVel << ","
     << info.mWheel[2].mLateralPatchVel << "," << info.mWheel[3].mLateralPatchVel << ",";

  // Wheels Long Patch Vel FL, FR, RL, RR
  ss << info.mWheel[0].mLongitudinalPatchVel << "," << info.mWheel[1].mLongitudinalPatchVel << ","
     << info.mWheel[2].mLongitudinalPatchVel << "," << info.mWheel[3].mLongitudinalPatchVel << ",";

  // Wheels Tyre Load FL, FR, RL, RR
  ss << info.mWheel[0].mTireLoad << "," << info.mWheel[1].mTireLoad << "," << info.mWheel[2].mTireLoad << ","
     << info.mWheel[3].mTireLoad << ",";

  // Wheels Lat Force FL, FR, RL, RR
  ss << info.mWheel[0].mLateralForce << "," << info.mWheel[1].mLateralForce << "," << info.mWheel[2].mLateralForce
     << "," << info.mWheel[3].mLateralForce << ",";

  // Wheels Slide Pct FL, Slide Pct FR, Slide Pct RL, Slide Pct RR (mGripFract * 100)
  ss << (info.mWheel[0].mGripFract * 100.0) << "," << (info.mWheel[1].mGripFract * 100.0) << ","
     << (info.mWheel[2].mGripFract * 100.0) << "," << (info.mWheel[3].mGripFract * 100.0) << ","
     << static_cast<int>(currentFlag_.load()) << ",";

  // Track conditions (sourced from ScoringInfoV01 via cached atomics)
  ss << cachedTrackTemp_.load() << ","
     << cachedAmbientTemp_.load() << ","
     << cachedRaining_.load() << ","
     << cachedAvgWetness_.load() << ",";

  // Tyre temperatures (Kelvin): centre surface + carcass per wheel
  ss << info.mWheel[0].mTemperature[1] << ","
     << info.mWheel[1].mTemperature[1] << ","
     << info.mWheel[2].mTemperature[1] << ","
     << info.mWheel[3].mTemperature[1] << ","
     << info.mWheel[0].mTireCarcassTemperature << ","
     << info.mWheel[1].mTireCarcassTemperature << ","
     << info.mWheel[2].mTireCarcassTemperature << ","
     << info.mWheel[3].mTireCarcassTemperature << "\n";

  return ss.str();
}

} // namespace race_tools

// -------------------------------------------------------------
// 5. Native Windows C-Export Bootstrap Functions
// -------------------------------------------------------------

#define PLUGIN_EXPORT extern "C" __declspec(dllexport)

// Returns unique plugin name shown in UI
PLUGIN_EXPORT const char* __cdecl GetPluginName() { return "BrBrDb Telemetry"; }

// Returns plugin categories. Internals Plugin category = 3 (PO_INTERNALS)
PLUGIN_EXPORT unsigned char __cdecl GetPluginType() { return 3; }

// Returns version representation — must match the InternalsPluginVNN class used (V07 = 7)
PLUGIN_EXPORT int __cdecl GetPluginVersion() { return 7; }

// Factory instantiation of our C++ plugin class
PLUGIN_EXPORT ::PluginObject* __cdecl CreatePluginObject() {
  return new race_tools::BrBrDbTelemetryPlugin();
}

// Memory cleanup when engine unloads the plugin
PLUGIN_EXPORT void __cdecl DestroyPluginObject(::PluginObject* ptr) {
  if (ptr) {
    delete static_cast<race_tools::BrBrDbTelemetryPlugin*>(ptr);
  }
}
