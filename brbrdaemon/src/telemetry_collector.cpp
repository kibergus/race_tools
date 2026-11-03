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

#include "telemetry_collector.h"
#include <cmath>
#include <iomanip>
#include <sstream>
#include <filesystem>
#include <regex>
#include <cctype>
#include <algorithm>
#include <set>
#include <cwctype>
#include <timeapi.h>
#pragma comment(lib, "winmm.lib")

#include "net_utils.h"

namespace race_tools {

namespace {

constexpr double PI = 3.14159265358979323846;
constexpr double TWO_PI = 2.0 * PI;

std::string Trim(const std::string& str) {
  size_t start = str.find_first_not_of(" \t\r\n");
  if (start == std::string::npos) return "";
  size_t end = str.find_last_not_of(" \t\r\n");
  return str.substr(start, end - start + 1);
}

std::string EscapeCsvMetadata(const std::string& val) {
  if (val.find_first_of(",\"\r\n") == std::string::npos) {
    return val;
  }
  std::string escaped = "\"";
  for (char c : val) {
    if (c == '"') escaped += "\"\"";
    else escaped += c;
  }
  escaped += "\"";
  return escaped;
}

struct TrackGeoOrigin {
  double latitude = 0.0;
  double longitude = 0.0;
};

TrackGeoOrigin ResolveTrackOrigin(const std::string& trackName, float shmLat, float shmLon) {
  if (shmLat != 0.0f || shmLon != 0.0f) {
    return {static_cast<double>(shmLat), static_cast<double>(shmLon)};
  }

  std::string lower;
  lower.reserve(trackName.size());
  for (char c : trackName) {
    lower.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
  }

  if (lower.find("monza") != std::string::npos) return {45.613890, 9.286240};
  if (lower.find("imola") != std::string::npos) return {44.341200, 11.713400};
  if (lower.find("spa") != std::string::npos) return {50.437200, 5.971400};
  if (lower.find("brands") != std::string::npos) return {51.356800, 0.263100};
  if (lower.find("cota") != std::string::npos || lower.find("americas") != std::string::npos) return {30.132800, -97.641100};
  if (lower.find("donington") != std::string::npos) return {52.830600, -1.375300};
  if (lower.find("fuji") != std::string::npos) return {35.371700, 138.926900};
  if (lower.find("kyalami") != std::string::npos) return {-25.998900, 28.075800};
  if (lower.find("laguna") != std::string::npos) return {36.584400, -121.753600};
  if (lower.find("panorama") != std::string::npos || lower.find("bathurst") != std::string::npos) return {-33.447500, 149.554400};
  if (lower.find("nurburg") != std::string::npos || lower.find("nordschleife") != std::string::npos) return {50.335600, 6.947500};
  if (lower.find("oulton") != std::string::npos) return {53.177200, -2.613600};
  if (lower.find("ricard") != std::string::npos) return {43.250600, 5.791700};
  if (lower.find("red_bull") != std::string::npos || lower.find("redbull") != std::string::npos) return {47.219700, 14.764700};
  if (lower.find("atlanta") != std::string::npos) return {34.148100, -83.817800};
  if (lower.find("sebring") != std::string::npos) return {27.454700, -81.348300};
  if (lower.find("suzuka") != std::string::npos) return {34.843100, 136.541000};
  if (lower.find("watkins") != std::string::npos) return {42.336900, -76.927200};

  return {0.0, 0.0};
}

std::string ExtractNumber(const std::string& str) {
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
    if (!res.empty()) return res;
  }

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
  return !currentNum.empty() ? currentNum : lastNum;
}

void EulerToQuaternion(double heading, double pitch, double roll,
                       double& qx, double& qy, double& qz, double& qw) {
  double cy = std::cos(heading * 0.5);
  double sy = std::sin(heading * 0.5);
  double cp = std::cos(pitch * 0.5);
  double sp = std::sin(pitch * 0.5);
  double cr = std::cos(roll * 0.5);
  double sr = std::sin(roll * 0.5);

  qw = cr * cp * cy + sr * sp * sy;
  qx = sr * cp * cy - cr * sp * sy;
  qy = cr * sp * cy + sr * cp * sy;
  qz = cr * cp * sy - sr * sp * cy;
}

std::string SessionTypeToString(int sessionType) {
  switch (sessionType) {
    case 0: return "Practice";
    case 1: return "Qualify";
    case 2: return "Race";
    case 3: return "Hotlap";
    case 4: return "TimeAttack";
    case 5: return "Drift";
    case 6: return "Drag";
    default: return "Practice";
  }
}

// Produces a comparison key for a directory so that "C:\\T", "c:/t/" and "C:\\T\\." are
// treated as the same location (Windows paths are case-insensitive).
std::wstring NormalizeDirKey(const std::wstring& dir) {
  std::error_code ec;
  std::filesystem::path p = std::filesystem::absolute(std::filesystem::path(dir), ec);
  if (ec) p = std::filesystem::path(dir);
  p = p.lexically_normal();
  std::wstring key = p.wstring();
  while (key.size() > 1 && (key.back() == L'\\' || key.back() == L'/')) {
    key.pop_back();
  }
  for (auto& c : key) {
    c = (c == L'/') ? L'\\' : static_cast<wchar_t>(towlower(c));
  }
  return key;
}

} // namespace

TelemetryCollector::TelemetryCollector(std::shared_ptr<AceSharedMemoryReader> shmReader,
                                       const PluginConfig& config,
                                       Logger& logger,
                                       const std::wstring& defaultLogDir)
    : shmReader_(shmReader), config_(config), logger_(logger), defaultLogDir_(defaultLogDir) {
  if (defaultLogDir_.empty()) {
    try {
      defaultLogDir_ = GetCurrentModuleDirectory() + L"\\Telemetry";
    } catch (...) {
      defaultLogDir_ = L"Telemetry";
    }
  }
}

TelemetryCollector::~TelemetryCollector() {
  Stop();
}

void TelemetryCollector::Start() {
  if (running_) return;

  running_ = true;
  workerThread_ = std::thread(&TelemetryCollector::PollingThreadWorker, this);
  logger_.Write("TelemetryCollector worker thread started.");
}

void TelemetryCollector::Stop() {
  if (!running_) return;

  running_ = false;
  if (workerThread_.joinable()) {
    workerThread_.join();
  }

  EndSession();
  logger_.Write("TelemetryCollector stopped.");
}

std::string TelemetryCollector::GetConnectedSimulatorName() const {
  if (shmReader_ && shmReader_->IsConnected()) {
    return shmReader_->GetSimulatorName();
  }
  return "Assetto Corsa";
}

void TelemetryCollector::ExtractTelemetryData(
    const SPageFilePhysics& physics,
    const UnifiedGraphicInfo& graphics,
    const std::chrono::system_clock::time_point& sessionStartRealTime,
    std::chrono::steady_clock::time_point sessionStartSteadyTime,
    std::chrono::steady_clock::time_point nowSteadyTime,
    std::vector<double>& outValues,
    std::string& outCsvLine) {

  // 1. Time (ISO-8601 UTC)
  double elapsedSec = std::chrono::duration<double>(nowSteadyTime - sessionStartSteadyTime).count();
  if (elapsedSec < 0.0) elapsedSec = 0.0;

  auto tp = sessionStartRealTime + std::chrono::duration_cast<std::chrono::system_clock::duration>(
                                       std::chrono::milliseconds(static_cast<long long>(elapsedSec * 1000.0)));

  std::time_t time_c = std::chrono::system_clock::to_time_t(tp);
  struct tm gm_tm;
  gmtime_s(&gm_tm, &time_c);
  auto duration = tp.time_since_epoch();
  auto millis = std::chrono::duration_cast<std::chrono::milliseconds>(duration).count() % 1000;
  char timeStr[64];
  sprintf_s(timeStr, "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
            gm_tm.tm_year + 1900, gm_tm.tm_mon + 1, gm_tm.tm_mday,
            gm_tm.tm_hour, gm_tm.tm_min, gm_tm.tm_sec, static_cast<int>(millis));

  double timeSeconds = std::chrono::duration<double>(duration).count();

  // 2. Position: x, z
  double posX = 0.0, posZ = 0.0;
  bool hasTyreContact = false;
  for (int i = 0; i < 4; ++i) {
    if (physics.tyreContactPoint[i][0] != 0.0f || physics.tyreContactPoint[i][2] != 0.0f) {
      hasTyreContact = true;
      break;
    }
  }

  if (hasTyreContact) {
    posX = 0.25 * (static_cast<double>(physics.tyreContactPoint[0][0]) +
                   static_cast<double>(physics.tyreContactPoint[1][0]) +
                   static_cast<double>(physics.tyreContactPoint[2][0]) +
                   static_cast<double>(physics.tyreContactPoint[3][0]));
    posZ = 0.25 * (static_cast<double>(physics.tyreContactPoint[0][2]) +
                   static_cast<double>(physics.tyreContactPoint[1][2]) +
                   static_cast<double>(physics.tyreContactPoint[2][2]) +
                   static_cast<double>(physics.tyreContactPoint[3][2]));
  } else {
    posX = graphics.carCoordinates[0];
    posZ = graphics.carCoordinates[2];
  }

  // 3. Speed: km/h to m/s
  double speed = static_cast<double>(physics.speedKmh) / 3.6;

  // 4. G-Forces
  double gForceLat = physics.accG[0];
  double gForceLon = physics.accG[1];
  double gForceVert = physics.accG[2];

  // 5. Pedals and Inputs (0-100)
  double throttle = physics.gas * 100.0;
  double brake = physics.brake * 100.0;
  double clutch = physics.clutch * 100.0;
  if (clutch < 0.0) clutch = 0.0;
  else if (clutch > 100.0) clutch = 100.0;
  double handbrake = graphics.handbrakePercent * 100.0;
  if (handbrake < 0.0) handbrake = 0.0;
  else if (handbrake > 100.0) handbrake = 100.0;
  int gear = (physics.gear > 0) ? (physics.gear - 1) : -1;

  // 6. Steering Angle in degrees
  double steering = 0.0;
  if (graphics.steerDegrees != 0) {
    steering = static_cast<double>(graphics.steerDegrees);
  } else {
    steering = physics.steerAngle * (180.0 / PI);
  }

  // 7. Orientation Quaternion
  double qx = 0.0, qy = 0.0, qz = 0.0, qw = 1.0;
  EulerToQuaternion(physics.heading, physics.pitch, physics.roll, qx, qy, qz, qw);

  double rpsFL = physics.wheelAngularSpeed[0] / TWO_PI;
  double rpsFR = physics.wheelAngularSpeed[1] / TWO_PI;
  double rpsRL = physics.wheelAngularSpeed[2] / TWO_PI;
  double rpsRR = physics.wheelAngularSpeed[3] / TWO_PI;

  double slidePctFL = physics.wheelSlip[0] * 100.0;
  double slidePctFR = physics.wheelSlip[1] * 100.0;
  double slidePctRL = physics.wheelSlip[2] * 100.0;
  double slidePctRR = physics.wheelSlip[3] * 100.0;

  double raining = 0.0;
  double trackWetness = 0.0;

  const double nanVal = std::numeric_limits<double>::quiet_NaN();

  auto getTempK = [](float tempC) -> double {
    if (std::isnan(tempC) || tempC < -273.15f) return std::numeric_limits<double>::quiet_NaN();
    return static_cast<double>(tempC) + 273.15;
  };

  double surfTempFL = getTempK(physics.tyreTempM[0]);
  double surfTempFR = getTempK(physics.tyreTempM[1]);
  double surfTempRL = getTempK(physics.tyreTempM[2]);
  double surfTempRR = getTempK(physics.tyreTempM[3]);

  double coreTempFL = getTempK(physics.tyreCoreTemperature[0]);
  double coreTempFR = getTempK(physics.tyreCoreTemperature[1]);
  double coreTempRL = getTempK(physics.tyreCoreTemperature[2]);
  double coreTempRR = getTempK(physics.tyreCoreTemperature[3]);

  outValues = {
    timeSeconds,
    posX,
    posZ,
    speed,
    gForceLat,
    gForceLon,
    gForceVert,
    throttle,
    brake,
    steering,
    clutch,
    handbrake,
    static_cast<double>(gear),
    nanVal, nanVal, nanVal, nanVal,
    qx, qy, qz, qw,
    rpsFL, rpsFR, rpsRL, rpsRR,
    nanVal, nanVal, nanVal, nanVal,
    nanVal, nanVal, nanVal, nanVal,
    physics.wheelLoad[0], physics.wheelLoad[1], physics.wheelLoad[2], physics.wheelLoad[3],
    physics.fy[0], physics.fy[1], physics.fy[2], physics.fy[3],
    physics.fx[0], physics.fx[1], physics.fx[2], physics.fx[3],
    slidePctFL, slidePctFR, slidePctRL, slidePctRR,
    static_cast<double>(graphics.flag),
    physics.roadTemp,
    physics.airTemp,
    raining,
    trackWetness,
    surfTempFL, surfTempFR, surfTempRL, surfTempRR,
    coreTempFL, coreTempFR, coreTempRL, coreTempRR
  };

  outCsvLine = FormatCsvLineFromValues(timeStr, outValues);
}

std::string TelemetryCollector::FormatTelemetryLine(
    const SPageFilePhysics& physics,
    const UnifiedGraphicInfo& graphics,
    const std::chrono::system_clock::time_point& sessionStartRealTime,
    std::chrono::steady_clock::time_point sessionStartSteadyTime,
    std::chrono::steady_clock::time_point nowSteadyTime) {
  std::vector<double> values;
  std::string line;
  ExtractTelemetryData(physics, graphics, sessionStartRealTime, sessionStartSteadyTime, nowSteadyTime, values, line);
  return line;
}

std::string TelemetryCollector::FormatTelemetryLine(
    const SPageFilePhysics& physics,
    const SPageFileGraphicEvo& graphics,
    const std::chrono::system_clock::time_point& sessionStartRealTime,
    std::chrono::steady_clock::time_point sessionStartSteadyTime,
    std::chrono::steady_clock::time_point nowSteadyTime) {
  UnifiedGraphicInfo ug{};
  ug.packetId = graphics.packetId;
  ug.status = graphics.status;
  ug.flag = static_cast<int>(graphics.flag);
  ug.handbrakePercent = graphics.handbrake_percent;
  ug.steerDegrees = graphics.steer_degrees;
  ug.carCoordinates[0] = graphics.car_coordinates[0][0];
  ug.carCoordinates[1] = graphics.car_coordinates[0][1];
  ug.carCoordinates[2] = graphics.car_coordinates[0][2];
  return FormatTelemetryLine(physics, ug, sessionStartRealTime, sessionStartSteadyTime, nowSteadyTime);
}

void TelemetryCollector::StartSession(const SPageFilePhysics& physics,
                                      const UnifiedGraphicInfo& graphics,
                                      const UnifiedStaticInfo& statics) {
  EndSession();

  std::string trackName = Trim(statics.track);
  std::string carName = Trim(graphics.carModel);
  if (trackName.empty() || carName.empty()) {
    return;
  }

  sessionStartTime_ = std::chrono::system_clock::now();
  sessionStartSteadyTime_ = std::chrono::steady_clock::now();
  sessionQueue_ = std::make_shared<SafeQueue<Sink::Message>>(50000);

  std::string driverName = graphics.driverName;
  std::string surname = graphics.driverSurname;
  if (!surname.empty()) {
    if (!driverName.empty()) {
      driverName += " ";
    }
    driverName += surname;
  }
  if (driverName.empty()) driverName = "Unknown Driver";

  // Apply driver name overrides if regex matches car name
  for (const auto& override_entry : config_.driverNameOverrides) {
    try {
      std::regex re(override_entry.carNameRegexp, std::regex::icase);
      if (std::regex_match(carName, re)) {
        driverName = override_entry.driverName;
        break;
      }
    } catch (...) {}
  }

  std::string sessionType = statics.sessionName;
  if (sessionType.empty()) {
    sessionType = SessionTypeToString(graphics.session >= 0 ? graphics.session : statics.sessionType);
  }
  std::string carNumber = ExtractNumber(carName);

  // League selection
  std::string simKey = "ace";
  switch (shmReader_->GetSimulatorType()) {
    case SimulatorType::AcRally:
      simKey = "acr";
      break;
    case SimulatorType::AssettoCorsa:
      simKey = "ac";
      break;
    case SimulatorType::AceEvo:
    default:
      simKey = "ace";
      break;
  }
  std::string league = config_.GetLeagueForSim(simKey, shmReader_->GetDefaultLeague());

  // Participant tag for file naming
  std::string tag = SanitizeFilename(driverName) + "_" +
                    SanitizeFilename(carName) + "_" +
                    SanitizeFilename(trackName) + "_" +
                    SanitizeFilename(sessionType);

  // Initialize Sinks
  // File names are derived only from session data, so two file sinks pointing at the
  // same directory would open (and truncate) the same CSV. Skip such duplicates.
  std::set<std::wstring> fileSinkDirs;
  for (const auto& sinkConf : config_.sinks) {
    if (!sinkConf.enabled) continue;

    if (sinkConf.type == "file") {
      std::wstring path = defaultLogDir_;
      if (!sinkConf.path.empty()) {
        path = std::filesystem::u8path(sinkConf.path).wstring();
      }
      std::wstring dirKey = NormalizeDirKey(path);
      if (!fileSinkDirs.insert(dirKey).second) {
        logger_.Write("Skipping duplicate file sink for directory: " + WideToUtf8(path));
        continue;
      }
      try {
        std::error_code ec;
        std::filesystem::create_directories(path, ec);
        sinkWorkers_.push_back(std::make_unique<SinkWorker>(
            std::make_unique<FileSink>(path, logger_), sessionQueue_->reader()));
      } catch (const std::exception& e) {
        logger_.Write(std::string("Failed to initialize FileSink: ") + e.what());
      }
    } else if (sinkConf.type == "http") {
      sinkWorkers_.push_back(std::make_unique<SinkWorker>(
          std::make_unique<HttpSink>(sinkConf.serverAddress, sinkConf.apiKey, logger_, activeUploads_,
                                     std::chrono::minutes(sinkConf.bufferDurationMinutes),
                                     std::chrono::minutes(10),
                                     8,
                                     std::chrono::minutes(2),
                                     sinkConf.maxRetries),
          sessionQueue_->reader()));
    }
  }

  // Write CSV Metadata Headers with proper CSV escaping
  std::time_t start_time_t = std::chrono::system_clock::to_time_t(sessionStartTime_);
  struct tm start_tm;
  gmtime_s(&start_tm, &start_time_t);
  char dateBuf[32];
  char timeBuf[32];
  sprintf_s(dateBuf, "%04d-%02d-%02d", start_tm.tm_year + 1900, start_tm.tm_mon + 1, start_tm.tm_mday);
  sprintf_s(timeBuf, "%02d:%02d:%02d", start_tm.tm_hour, start_tm.tm_min, start_tm.tm_sec);

  Sink::SessionMetadata sessionMeta;
  sessionMeta.trackName = trackName.empty() ? "Unknown Track" : trackName;
  sessionMeta.date = dateBuf;
  sessionMeta.time = timeBuf;
  sessionMeta.driverName = driverName.empty() ? "Unknown Driver" : driverName;
  sessionMeta.league = league.empty() ? "kartsim" : league;
  sessionMeta.className = carName.empty() ? "Unknown Class" : carName;
  sessionMeta.sessionName = sessionType.empty() ? "Practice" : sessionType;
  sessionMeta.kartNumber = carNumber;

  // Push Start Event
  sessionQueue_->Push(Sink::StartSession{sessionStartTime_, tag, sessionMeta});

  std::stringstream ss;
  ss << "Track name," << EscapeCsvMetadata(trackName) << "\n";
  if (!statics.trackConfiguration.empty()) {
    ss << "Track configuration," << EscapeCsvMetadata(statics.trackConfiguration) << "\n";
  }
  TrackGeoOrigin origin = ResolveTrackOrigin(trackName, statics.latitude, statics.longitude);
  ss << std::fixed;
  if (origin.latitude != 0.0 || origin.longitude != 0.0) {
    ss << "Track Origin Latitude," << std::setprecision(8) << origin.latitude << "\n";
    ss << "Track Origin Longitude," << std::setprecision(8) << origin.longitude << "\n";
  }
  ss << "Track Origin Heading," << std::setprecision(6) << physics.heading << "\n";
  ss << "Date," << dateBuf << "\n";
  ss << "Time," << timeBuf << "\n";
  ss << "Driver name," << EscapeCsvMetadata(driverName) << "\n";
  ss << "League," << EscapeCsvMetadata(league) << "\n";
  ss << "Class," << EscapeCsvMetadata(carName) << "\n";
  ss << "Session," << EscapeCsvMetadata(sessionType) << "\n";
  if (!carNumber.empty()) {
    ss << "Kart number," << EscapeCsvMetadata(carNumber) << "\n";
  }
  std::string gameVersion = Trim(statics.gameVersion);
  if (!gameVersion.empty()) {
    ss << "Game version," << EscapeCsvMetadata(gameVersion) << "\n";
  }
  ss << "\n"; // Blank line separator

  sessionQueue_->Push(Sink::Line{ss.str(), /*is_header=*/true});
  sessionQueue_->Push(Sink::Line{GetBrBrDaemonCsvHeaders(), /*is_header=*/true});

  inSession_.store(true, std::memory_order_release);
  sessionState_ = SessionState::Live;
  lastPacketId_ = physics.packetId;
  lastPacketAdvanceTime_ = std::chrono::steady_clock::now();

  logger_.Write(shmReader_->GetSimulatorName() + " telemetry session started: " + tag);
}

void TelemetryCollector::EndSession() {
  if (!inSession_.exchange(false, std::memory_order_acq_rel)) {
    sessionState_ = SessionState::Idle;
    return;
  }

  sessionState_ = SessionState::Idle;
  if (sessionQueue_) {
    sessionQueue_->Push(Sink::EndSession{});
    sessionQueue_->SignalShutdown();
  }

  if (!sinkWorkers_.empty()) {
    auto workers = std::make_shared<std::vector<std::unique_ptr<SinkWorker>>>(std::move(sinkWorkers_));
    auto queue = std::move(sessionQueue_);
    // Join old workers in background so polling thread is never blocked for minutes
    std::thread([workers, queue]() {
      workers->clear();
    }).detach();
  }
  sinkWorkers_.clear();
  sessionQueue_.reset();

  logger_.Write(shmReader_->GetSimulatorName() + " telemetry session ended.");
}

void TelemetryCollector::PollingThreadWorker() {
  // Boost timer resolution to 1ms
  timeBeginPeriod(1);

  SPageFilePhysics physics{};
  UnifiedGraphicInfo graphics{};
  UnifiedStaticInfo statics{};

  SimulatorType reqSim = SimulatorType::Auto;
  if (config_.simulator == "acr") {
    reqSim = SimulatorType::AcRally;
  } else if (config_.simulator == "ac") {
    reqSim = SimulatorType::AssettoCorsa;
  } else if (config_.simulator == "acevo") {
    reqSim = SimulatorType::AceEvo;
  }

  int targetHz = (config_.pollingRateHz > 0) ? config_.pollingRateHz : 100;
  auto pollPeriod = std::chrono::microseconds(1000000 / targetHz);
  auto nextTick = std::chrono::steady_clock::now();

  int procCheckCounter = 0;

  try {
    while (running_.load(std::memory_order_relaxed)) {
      nextTick += pollPeriod;

      if (!shmReader_->IsConnected()) {
        if (!shmReader_->Connect(reqSim)) {
          std::this_thread::sleep_for(std::chrono::milliseconds(500));
          nextTick = std::chrono::steady_clock::now();
          continue;
        }
        logger_.Write("Connected to " + shmReader_->GetSimulatorName() + " shared memory.");
      }

      // Read current data snapshots
      ShmReadResult physRes = shmReader_->ReadPhysicsResult(physics);
      ShmReadResult graphRes = shmReader_->ReadUnifiedGraphicResult(graphics);
      ShmReadResult statRes = shmReader_->ReadUnifiedStaticResult(statics);

      if (physRes == ShmReadResult::NotConnected ||
          graphRes == ShmReadResult::NotConnected ||
          statRes == ShmReadResult::NotConnected) {
        if (inSession_.load()) {
          EndSession();
        }
        shmReader_->Disconnect();
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        nextTick = std::chrono::steady_clock::now();
        continue;
      }

      if (physRes == ShmReadResult::Torn ||
          graphRes == ShmReadResult::Torn ||
          statRes == ShmReadResult::Torn) {
        // Transient torn read detected across seqlock retry attempts.
        // Skip this sampling tick without disconnecting or resetting the session.
        auto now = std::chrono::steady_clock::now();
        if (nextTick > now) {
          std::this_thread::sleep_until(nextTick);
        } else {
          nextTick = now;
        }
        continue;
      }

      auto nowSteady = std::chrono::steady_clock::now();

      // Check whether physics packets are actively advancing
      bool packetsAdvancing = (physics.packetId != lastPacketId_ && physics.packetId > 0);
      if (packetsAdvancing) {
        lastPacketAdvanceTime_ = nowSteady;
        lastPacketId_ = physics.packetId;
      }

      // Periodically check running process matches
      if (++procCheckCounter >= targetHz) {
        procCheckCounter = 0;
        if (reqSim == SimulatorType::Auto) {
          SimulatorType runningProc = shmReader_->DetectRunningSimulator();
          if (runningProc != shmReader_->GetSimulatorType()) {
            if (inSession_.load()) {
              EndSession();
            }
            shmReader_->Disconnect();
            logger_.Write("Simulator process changed or terminated. Disconnecting.");
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
            nextTick = std::chrono::steady_clock::now();
            continue;
          }
        }
      }

      // Staleness check: if physics packets have not advanced for 5s while in session,
      // verify simulator is still alive. If game crashed/exited, end session.
      if (inSession_.load() && sessionState_ == SessionState::Live) {
        if (nowSteady - lastPacketAdvanceTime_ > std::chrono::seconds(5)) {
          SimulatorType runningProc = shmReader_->DetectRunningSimulator();
          if (runningProc == SimulatorType::Unknown ||
              (nowSteady - lastPacketAdvanceTime_ > std::chrono::seconds(10))) {
            logger_.Write("Physics packets stale (>10s) or simulator exited. Ending session.");
            EndSession();
            shmReader_->Disconnect();
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
            nextTick = std::chrono::steady_clock::now();
            continue;
          }
        }
      }

      // Explicit State Machine:
      // status: AC_OFF=0, AC_REPLAY=1, AC_LIVE=2, AC_PAUSE=3
      bool hasTrackAndCar = !Trim(statics.track).empty() && !Trim(graphics.carModel).empty();
      if (graphics.status == 2 /* AC_LIVE */ && hasTrackAndCar) {
        SessionIdentity newId;
        newId.track = statics.track;
        newId.trackConfiguration = statics.trackConfiguration;
        newId.carModel = graphics.carModel;
        newId.driverName = graphics.driverName;
        newId.sessionType = statics.sessionName;
        newId.session = graphics.session >= 0 ? graphics.session : statics.sessionType;

        if (sessionState_ == SessionState::Idle) {
          StartSession(physics, graphics, statics);
          currentIdentity_ = newId;
          sessionState_ = SessionState::Live;
        } else if (sessionState_ == SessionState::Paused) {
          if (newId != currentIdentity_) {
            EndSession();
            StartSession(physics, graphics, statics);
            currentIdentity_ = newId;
          }
          sessionState_ = SessionState::Live;
          inSession_.store(true, std::memory_order_release);
          logger_.Write("Session resumed from pause.");
        } else if (sessionState_ == SessionState::Live) {
          // Check if session changed while live (e.g. restart or stage change)
          if (newId != currentIdentity_) {
            logger_.Write("Session identity changed. Starting new session.");
            EndSession();
            StartSession(physics, graphics, statics);
            currentIdentity_ = newId;
          }
        }

        if (packetsAdvancing && inSession_.load()) {
          std::vector<double> values;
          std::string line;
          ExtractTelemetryData(physics, graphics, sessionStartTime_,
                               sessionStartSteadyTime_, nowSteady, values, line);
          sessionQueue_->Push(Sink::Line{std::move(line), /*is_header=*/false, std::move(values)});
        }
      } else if (graphics.status == 3 /* AC_PAUSE */) {
        if (sessionState_ == SessionState::Live) {
          sessionState_ = SessionState::Paused;
          logger_.Write("Session paused. Retaining active session.");
        }
      } else if (graphics.status == 1 /* AC_REPLAY */ || graphics.status == 0 /* AC_OFF */ || !hasTrackAndCar) {
        if (sessionState_ == SessionState::Live || sessionState_ == SessionState::Paused) {
          logger_.Write(graphics.status == 1 ? "Replay active. Ending session." : "Leaving session.");
          EndSession();
          sessionState_ = SessionState::Idle;
        }
      }

      // Precise sleep to avoid timer drift
      auto now = std::chrono::steady_clock::now();
      if (nextTick > now) {
        std::this_thread::sleep_until(nextTick);
      } else {
        nextTick = now;
      }
    }
  } catch (const std::exception& e) {
    logger_.Write(std::string("PollingThreadWorker exception: ") + e.what());
  } catch (...) {
    logger_.Write("PollingThreadWorker unhandled exception.");
  }

  timeEndPeriod(1);
}

} // namespace race_tools
