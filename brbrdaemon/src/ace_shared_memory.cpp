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

#include "ace_shared_memory.h"
#include <tlhelp32.h>
#include <cstring>
#include <vector>
#include <algorithm>
#include <cctype>
#include <atomic>

namespace race_tools {

namespace {

std::string WideToUtf8Str(const wchar_t* wstr, size_t maxLen = 0) {
  if (!wstr || wstr[0] == L'\0') return "";
  size_t len = (maxLen > 0) ? wcsnlen(wstr, maxLen) : wcslen(wstr);
  while (len > 0 && (wstr[len - 1] == L' ' || wstr[len - 1] == L'\r' ||
                     wstr[len - 1] == L'\n' || wstr[len - 1] == L'\t')) {
    len--;
  }
  if (len == 0) return "";
  int sizeNeeded = WideCharToMultiByte(CP_UTF8, 0, wstr, static_cast<int>(len), NULL, 0, NULL, NULL);
  if (sizeNeeded <= 0) return "";
  std::string result(sizeNeeded, 0);
  WideCharToMultiByte(CP_UTF8, 0, wstr, static_cast<int>(len), &result[0], sizeNeeded, NULL, NULL);
  return result;
}

std::string CharToUtf8Str(const char* str, size_t maxLen = 0) {
  if (!str || str[0] == '\0') return "";
  size_t len = (maxLen > 0) ? strnlen(str, maxLen) : strlen(str);
  while (len > 0 && (str[len - 1] == ' ' || str[len - 1] == '\r' ||
                     str[len - 1] == '\n' || str[len - 1] == '\t')) {
    len--;
  }
  return std::string(str, len);
}

struct MapCandidate {
  SimulatorType sim;
  const wchar_t* phys;
  const wchar_t* graph;
  const wchar_t* stat;
};

const MapCandidate kAcrCandidates[] = {
    {SimulatorType::AcRally, L"Local\\acpmf_physics", L"Local\\acpmf_graphics", L"Local\\acpmf_static"},
    {SimulatorType::AcRally, L"Local\\acr_pmf_physics", L"Local\\acr_pmf_graphics", L"Local\\acr_pmf_static"},
    {SimulatorType::AcRally, L"Local\\acrpmf_physics", L"Local\\acrpmf_graphics", L"Local\\acrpmf_static"},
    {SimulatorType::AcRally, L"Local\\acr_physics", L"Local\\acr_graphics", L"Local\\acr_static"}
};

const MapCandidate kAceCandidates[] = {
    {SimulatorType::AceEvo, L"Local\\acevo_pmf_physics", L"Local\\acevo_pmf_graphics", L"Local\\acevo_pmf_static"}
};

const MapCandidate kAcCandidates[] = {
    {SimulatorType::AssettoCorsa, L"Local\\acpmf_physics", L"Local\\acpmf_graphics", L"Local\\acpmf_static"}
};

template <typename T>
ShmReadResult ReadSeqlockStruct(const void* rawPtr, size_t regionSize, T& out) {
  if (!rawPtr) return ShmReadResult::NotConnected;
  if (regionSize < sizeof(int)) return ShmReadResult::NotConnected;

  const volatile int* pPacketId = static_cast<const volatile int*>(rawPtr);
  constexpr int kMaxAttempts = 5;
  size_t bytesToCopy = (std::min)(regionSize, sizeof(T));

  for (int attempt = 0; attempt < kMaxAttempts; ++attempt) {
    int idBefore = *pPacketId;
    std::atomic_thread_fence(std::memory_order_acquire);
    std::memset(&out, 0, sizeof(T));
    std::memcpy(&out, rawPtr, bytesToCopy);
    std::atomic_thread_fence(std::memory_order_acquire);
    int idAfter = *pPacketId;
    if (idBefore == idAfter) {
      return ShmReadResult::Ok;
    }
  }
  return ShmReadResult::Torn;
}

} // namespace

AceSharedMemoryReader::AceSharedMemoryReader() = default;

AceSharedMemoryReader::~AceSharedMemoryReader() {
  Disconnect();
}

void AceSharedMemoryReader::SetCustomMapNames(const std::wstring& physicsName,
                                              const std::wstring& graphicsName,
                                              const std::wstring& staticName,
                                              SimulatorType simType,
                                              LayoutFormat layout) {
  Disconnect();
  physicsMapName_ = physicsName;
  graphicsMapName_ = graphicsName;
  staticMapName_ = staticName;
  hasCustomMapNames_ = true;
  detectedSim_ = simType;
  customLayout_ = layout;
  detectedLayout_ = layout;
}

SimulatorType AceSharedMemoryReader::DetectRunningSimulator() {
  HANDLE hSnap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
  if (hSnap == INVALID_HANDLE_VALUE) {
    return SimulatorType::Unknown;
  }

  PROCESSENTRY32W pe;
  pe.dwSize = sizeof(pe);

  SimulatorType result = SimulatorType::Unknown;
  if (Process32FirstW(hSnap, &pe)) {
    do {
      std::wstring procName = pe.szExeFile;
      for (auto& c : procName) {
        c = static_cast<wchar_t>(towlower(c));
      }

      if (procName == L"acr.exe" ||
          procName.find(L"assettocorsarally") != std::wstring::npos ||
          procName.find(L"ac_rally") != std::wstring::npos) {
        result = SimulatorType::AcRally;
        break;
      }
      if (procName == L"assettocorsaevo.exe" ||
          procName == L"assettocorsaevo-win64-shipping.exe" ||
          procName == L"acevo.exe") {
        result = SimulatorType::AceEvo;
        break;
      }
      if (procName == L"acs.exe" ||
          procName == L"assettocorsa.exe" ||
          procName == L"acs_x64.exe" ||
          procName == L"acs_x86.exe") {
        result = SimulatorType::AssettoCorsa;
        break;
      }
    } while (Process32NextW(hSnap, &pe));
  }
  CloseHandle(hSnap);
  return result;
}

bool AceSharedMemoryReader::TryOpenMapGroup(SimulatorType simType,
                                            const std::wstring& physName,
                                            const std::wstring& graphName,
                                            const std::wstring& statName) {
  Disconnect();

  HANDLE hPhys = OpenFileMappingW(FILE_MAP_READ, FALSE, physName.c_str());
  if (!hPhys) {
    return false;
  }

  HANDLE hGraph = OpenFileMappingW(FILE_MAP_READ, FALSE, graphName.c_str());
  if (!hGraph) {
    CloseHandle(hPhys);
    return false;
  }

  HANDLE hStat = OpenFileMappingW(FILE_MAP_READ, FALSE, statName.c_str());
  if (!hStat) {
    CloseHandle(hPhys);
    CloseHandle(hGraph);
    return false;
  }

  const void* pPhys = MapViewOfFile(hPhys, FILE_MAP_READ, 0, 0, 0);
  const void* pGraph = MapViewOfFile(hGraph, FILE_MAP_READ, 0, 0, 0);
  const void* pStat = MapViewOfFile(hStat, FILE_MAP_READ, 0, 0, 0);

  if (!pPhys || !pGraph || !pStat) {
    if (pPhys) UnmapViewOfFile(pPhys);
    if (pGraph) UnmapViewOfFile(pGraph);
    if (pStat) UnmapViewOfFile(pStat);
    CloseHandle(hPhys);
    CloseHandle(hGraph);
    CloseHandle(hStat);
    return false;
  }

  hPhysics_ = hPhys;
  hGraphics_ = hGraph;
  hStatic_ = hStat;
  pPhysicsRaw_ = pPhys;
  pGraphicsRaw_ = pGraph;
  pStaticRaw_ = pStat;

  auto queryRegionSize = [](const void* ptr) -> size_t {
    if (!ptr) return 0;
    MEMORY_BASIC_INFORMATION mbi{};
    if (VirtualQuery(ptr, &mbi, sizeof(mbi)) != 0) {
      return mbi.RegionSize;
    }
    return 0;
  };

  physicsSize_ = queryRegionSize(pPhys);
  graphicsSize_ = queryRegionSize(pGraph);
  staticSize_ = queryRegionSize(pStat);

  physicsMapName_ = physName;
  graphicsMapName_ = graphName;
  staticMapName_ = statName;
  detectedSim_ = simType;

  // Detect memory layout (Evo char strings vs Classic wchar_t strings)
  if (customLayout_ != LayoutFormat::Unknown) {
    detectedLayout_ = customLayout_;
  } else if (pStaticRaw_) {
    const char* raw = static_cast<const char*>(pStaticRaw_);
    if (raw[0] != '\0') {
      detectedLayout_ = (raw[1] == '\0') ? LayoutFormat::Classic : LayoutFormat::Evo;
    } else if (raw[15] != '\0') {
      detectedLayout_ = (raw[16] == '\0') ? LayoutFormat::Classic : LayoutFormat::Evo;
    } else {
      if (physName.find(L"acevo") != std::wstring::npos ||
          simType == SimulatorType::AceEvo) {
        detectedLayout_ = LayoutFormat::Evo;
      } else {
        detectedLayout_ = LayoutFormat::Classic;
      }
    }
  } else {
    detectedLayout_ = (simType == SimulatorType::AceEvo)
                          ? LayoutFormat::Evo
                          : LayoutFormat::Classic;
  }

  return true;
}

bool AceSharedMemoryReader::Connect(SimulatorType requestedSim) {
  if (pPhysicsRaw_ != nullptr) {
    return true;
  }

  if (hasCustomMapNames_) {
    return TryOpenMapGroup(detectedSim_ != SimulatorType::Unknown ? detectedSim_ : SimulatorType::AceEvo,
                           physicsMapName_, graphicsMapName_, staticMapName_);
  }

  SimulatorType runningProcSim = DetectRunningSimulator();

  auto tryGroup = [this](const MapCandidate& cand) -> bool {
    return TryOpenMapGroup(cand.sim, cand.phys, cand.graph, cand.stat);
  };

  // If a specific simulator was requested, attempt candidate mappings for it
  if (requestedSim == SimulatorType::AcRally) {
    for (const auto& c : kAcrCandidates) {
      if (tryGroup(c)) return true;
    }
    return false;
  } else if (requestedSim == SimulatorType::AceEvo) {
    for (const auto& c : kAceCandidates) {
      if (tryGroup(c)) return true;
    }
    return false;
  } else if (requestedSim == SimulatorType::AssettoCorsa) {
    for (const auto& c : kAcCandidates) {
      if (tryGroup(c)) return true;
    }
    return false;
  }

  // SimulatorType::Auto mode:
  // If a simulator process is currently running, connect exclusively to its candidate maps.
  if (runningProcSim == SimulatorType::AcRally) {
    for (const auto& c : kAcrCandidates) {
      if (tryGroup(c)) return true;
    }
    return false;
  } else if (runningProcSim == SimulatorType::AceEvo) {
    for (const auto& c : kAceCandidates) {
      if (tryGroup(c)) return true;
    }
    return false;
  } else if (runningProcSim == SimulatorType::AssettoCorsa) {
    for (const auto& c : kAcCandidates) {
      if (tryGroup(c)) return true;
    }
    return false;
  }

  // If no simulator process was detected, do not latch onto ghost/idle memory blocks.
  return false;
}

void AceSharedMemoryReader::Disconnect() {
  if (pPhysicsRaw_) {
    UnmapViewOfFile(pPhysicsRaw_);
    pPhysicsRaw_ = nullptr;
  }
  if (hPhysics_) {
    CloseHandle(hPhysics_);
    hPhysics_ = nullptr;
  }

  if (pGraphicsRaw_) {
    UnmapViewOfFile(pGraphicsRaw_);
    pGraphicsRaw_ = nullptr;
  }
  if (hGraphics_) {
    CloseHandle(hGraphics_);
    hGraphics_ = nullptr;
  }

  if (pStaticRaw_) {
    UnmapViewOfFile(pStaticRaw_);
    pStaticRaw_ = nullptr;
  }
  if (hStatic_) {
    CloseHandle(hStatic_);
    hStatic_ = nullptr;
  }

  physicsSize_ = 0;
  graphicsSize_ = 0;
  staticSize_ = 0;

  if (!hasCustomMapNames_) {
    detectedSim_ = SimulatorType::Unknown;
    detectedLayout_ = LayoutFormat::Unknown;
  }
}

bool AceSharedMemoryReader::IsConnected() const {
  return (pPhysicsRaw_ != nullptr);
}

std::string AceSharedMemoryReader::GetSimulatorName() const {
  switch (detectedSim_) {
    case SimulatorType::AcRally: return "Assetto Corsa Rally";
    case SimulatorType::AssettoCorsa: return "Assetto Corsa";
    case SimulatorType::AceEvo: return "Assetto Corsa Evo";
    default: return "Assetto Corsa / Evo / Rally";
  }
}

std::string AceSharedMemoryReader::GetDefaultLeague() const {
  switch (detectedSim_) {
    case SimulatorType::AcRally: return "acr";
    case SimulatorType::AssettoCorsa: return "ac";
    case SimulatorType::AceEvo: return "acevo";
    default: return "acevo";
  }
}

ShmReadResult AceSharedMemoryReader::ReadPhysicsResult(SPageFilePhysics& out) {
  return ReadSeqlockStruct(pPhysicsRaw_, physicsSize_, out);
}

bool AceSharedMemoryReader::ReadPhysics(SPageFilePhysics& out) {
  return ReadPhysicsResult(out) == ShmReadResult::Ok;
}

ShmReadResult AceSharedMemoryReader::ReadUnifiedStaticResult(UnifiedStaticInfo& out) {
  if (!pStaticRaw_) return ShmReadResult::NotConnected;

  const char* raw = static_cast<const char*>(pStaticRaw_);
  if (customLayout_ != LayoutFormat::Unknown) {
    detectedLayout_ = customLayout_;
  } else if (raw[0] != '\0') {
    detectedLayout_ = (raw[1] == '\0') ? LayoutFormat::Classic : LayoutFormat::Evo;
  } else if (raw[15] != '\0') {
    detectedLayout_ = (raw[16] == '\0') ? LayoutFormat::Classic : LayoutFormat::Evo;
  }

  if (detectedLayout_ == LayoutFormat::Classic) {
    ks::SPageFileStaticClassic st{};
    size_t copyBytes = (std::min)(staticSize_, sizeof(st));
    std::memcpy(&st, pStaticRaw_, copyBytes);
    out.track = WideToUtf8Str(st.track, 33);
    out.trackConfiguration = WideToUtf8Str(st.trackConfiguration, 33);
    out.gameVersion = WideToUtf8Str(st.acVersion, 15);
    if (out.gameVersion.empty()) {
      out.gameVersion = WideToUtf8Str(st.smVersion, 15);
    }
    out.sessionType = st.numberOfSessions;
    out.sessionName = "";
    out.latitude = 0.0f;
    out.longitude = 0.0f;
    return ShmReadResult::Ok;
  } else {
    SPageFileStaticEvo st{};
    size_t copyBytes = (std::min)(staticSize_, sizeof(st));
    std::memcpy(&st, pStaticRaw_, copyBytes);
    out.track = CharToUtf8Str(st.track, sizeof(st.track));
    out.trackConfiguration = CharToUtf8Str(st.track_configuration, sizeof(st.track_configuration));
    out.gameVersion = CharToUtf8Str(st.ac_evo_version, sizeof(st.ac_evo_version));
    if (out.gameVersion.empty()) {
      out.gameVersion = CharToUtf8Str(st.sm_version, sizeof(st.sm_version));
    }
    out.sessionName = CharToUtf8Str(st.session_name, sizeof(st.session_name));
    out.sessionType = st.session;
    out.latitude = st.latitude;
    out.longitude = st.longitude;
    return ShmReadResult::Ok;
  }
}

bool AceSharedMemoryReader::ReadUnifiedStatic(UnifiedStaticInfo& out) {
  return ReadUnifiedStaticResult(out) == ShmReadResult::Ok;
}

ShmReadResult AceSharedMemoryReader::ReadUnifiedGraphicResult(UnifiedGraphicInfo& out) {
  if (!pGraphicsRaw_) return ShmReadResult::NotConnected;

  if (detectedLayout_ == LayoutFormat::Classic) {
    ks::SPageFileGraphicClassic gr{};
    ShmReadResult res = ReadSeqlockStruct(pGraphicsRaw_, graphicsSize_, gr);
    if (res != ShmReadResult::Ok) return res;

    out.packetId = gr.packetId;
    out.status = gr.status;
    out.session = gr.session;
    out.handbrakePercent = 0.0f;
    out.flag = gr.flag;
    out.carCoordinates[0] = gr.carCoordinates[0][0];
    out.carCoordinates[1] = gr.carCoordinates[0][1];
    out.carCoordinates[2] = gr.carCoordinates[0][2];

    if (pStaticRaw_) {
      ks::SPageFileStaticClassic st{};
      size_t copyBytes = (std::min)(staticSize_, sizeof(st));
      std::memcpy(&st, pStaticRaw_, copyBytes);
      out.driverName = WideToUtf8Str(st.playerName, 33);
      out.driverSurname = WideToUtf8Str(st.playerSurname, 33);
      out.carModel = WideToUtf8Str(st.carModel, 33);
    }
    return ShmReadResult::Ok;
  } else {
    SPageFileGraphicEvo gr{};
    ShmReadResult res = ReadSeqlockStruct(pGraphicsRaw_, graphicsSize_, gr);
    if (res != ShmReadResult::Ok) return res;

    out.packetId = gr.packetId;
    out.status = gr.status;
    out.session = 0;
    out.handbrakePercent = gr.handbrake_percent;
    out.flag = static_cast<int>(gr.flag);
    out.steerDegrees = gr.steer_degrees;
    out.driverName = CharToUtf8Str(gr.driver_name, sizeof(gr.driver_name));
    out.driverSurname = CharToUtf8Str(gr.driver_surname, sizeof(gr.driver_surname));
    out.carModel = CharToUtf8Str(gr.car_model, sizeof(gr.car_model));
    out.carCoordinates[0] = gr.car_coordinates[0][0];
    out.carCoordinates[1] = gr.car_coordinates[0][1];
    out.carCoordinates[2] = gr.car_coordinates[0][2];
    return ShmReadResult::Ok;
  }
}

bool AceSharedMemoryReader::ReadUnifiedGraphic(UnifiedGraphicInfo& out) {
  return ReadUnifiedGraphicResult(out) == ShmReadResult::Ok;
}

ShmReadResult AceSharedMemoryReader::ReadGraphicsResult(SPageFileGraphicEvo& out) {
  if (!pGraphicsRaw_) return ShmReadResult::NotConnected;

  if (detectedLayout_ == LayoutFormat::Classic) {
    UnifiedGraphicInfo ug;
    ShmReadResult res = ReadUnifiedGraphicResult(ug);
    if (res != ShmReadResult::Ok) return res;
    out.packetId = ug.packetId;
    out.status = ug.status;
    out.flag = ug.flag;
    out.handbrake_percent = ug.handbrakePercent;
    out.car_coordinates[0][0] = ug.carCoordinates[0];
    out.car_coordinates[0][1] = ug.carCoordinates[1];
    out.car_coordinates[0][2] = ug.carCoordinates[2];
    strncpy_s(out.driver_name, ug.driverName.c_str(), _TRUNCATE);
    strncpy_s(out.driver_surname, ug.driverSurname.c_str(), _TRUNCATE);
    strncpy_s(out.car_model, ug.carModel.c_str(), _TRUNCATE);
    return ShmReadResult::Ok;
  }

  return ReadSeqlockStruct(pGraphicsRaw_, graphicsSize_, out);
}

bool AceSharedMemoryReader::ReadGraphics(SPageFileGraphicEvo& out) {
  return ReadGraphicsResult(out) == ShmReadResult::Ok;
}

ShmReadResult AceSharedMemoryReader::ReadStaticResult(SPageFileStaticEvo& out) {
  if (!pStaticRaw_) return ShmReadResult::NotConnected;

  if (detectedLayout_ == LayoutFormat::Classic) {
    UnifiedStaticInfo us;
    ShmReadResult res = ReadUnifiedStaticResult(us);
    if (res != ShmReadResult::Ok) return res;
    strncpy_s(out.track, us.track.c_str(), _TRUNCATE);
    strncpy_s(out.track_configuration, us.trackConfiguration.c_str(), _TRUNCATE);
    strncpy_s(out.ac_evo_version, us.gameVersion.c_str(), _TRUNCATE);
    strncpy_s(out.session_name, us.sessionName.c_str(), _TRUNCATE);
    out.session = us.sessionType;
    out.latitude = us.latitude;
    out.longitude = us.longitude;
    return ShmReadResult::Ok;
  }

  std::memset(&out, 0, sizeof(out));
  size_t copyBytes = (std::min)(staticSize_, sizeof(out));
  std::memcpy(&out, pStaticRaw_, copyBytes);
  return ShmReadResult::Ok;
}

bool AceSharedMemoryReader::ReadStatic(SPageFileStaticEvo& out) {
  return ReadStaticResult(out) == ShmReadResult::Ok;
}

bool AceSharedMemoryReader::ReadGraphicsClassic(SPageFileGraphicClassic& out) {
  return ReadSeqlockStruct(pGraphicsRaw_, graphicsSize_, out) == ShmReadResult::Ok;
}

bool AceSharedMemoryReader::ReadStaticClassic(SPageFileStaticClassic& out) {
  if (!pStaticRaw_) return false;
  std::memset(&out, 0, sizeof(out));
  size_t copyBytes = (std::min)(staticSize_, sizeof(out));
  std::memcpy(&out, pStaticRaw_, copyBytes);
  return true;
}

} // namespace race_tools
