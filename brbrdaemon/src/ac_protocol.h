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

#include <cstdint>

namespace ks {

#pragma pack(push)
#pragma pack(4)

// Classic Assetto Corsa / ACC Graphic Telemetry struct
struct SPageFileGraphicClassic {
  int packetId = 0;
  int status = 0;
  int session = 0;
  wchar_t currentTime[15] = {0};
  wchar_t lastTime[15] = {0};
  wchar_t bestTime[15] = {0};
  wchar_t split[15] = {0};
  int completedLaps = 0;
  int position = 0;
  int iCurrentTime = 0;
  int iLastTime = 0;
  int iBestTime = 0;
  float sessionTimeLeft = 0.0f;
  float distanceTraveled = 0.0f;
  int isInPit = 0;
  int currentSectorIndex = 0;
  int lastSectorTime = 0;
  int numberOfLaps = 0;
  wchar_t tyreCompound[33] = {0};
  float replayTimeMultiplier = 0.0f;
  float normalizedCarPosition = 0.0f;
  int activeCars = 0;
  float carCoordinates[60][3] = {0};
  int carID[60] = {0};
  int playerCarID = 0;
  float penaltyTime = 0.0f;
  int flag = 0;
  int idealLineOn = 0;
  int isInPitLane = 0;
  float surfaceGrip = 0.0f;
  int mandatoryPitDone = 0;
  float windSpeed = 0.0f;
  float windDirection = 0.0f;
};

// Classic Assetto Corsa / ACC Static Telemetry struct
struct SPageFileStaticClassic {
  wchar_t smVersion[15] = {0};
  wchar_t acVersion[15] = {0};
  int numberOfSessions = 0;
  int numCars = 0;
  wchar_t carModel[33] = {0};
  wchar_t track[33] = {0};
  wchar_t playerName[33] = {0};
  wchar_t playerSurname[33] = {0};
  wchar_t playerNick[33] = {0};
  int sectorCount = 0;
  float maxTorque = 0.0f;
  float maxPower = 0.0f;
  int maxRpm = 0;
  float maxFuel = 0.0f;
  float suspensionMaxTravel[4] = {0};
  float tyreRadius[4] = {0};
  float maxTurboBoost = 0.0f;
  float deprecated_1 = 0.0f;
  float deprecated_2 = 0.0f;
  int penaltiesEnabled = 0;
  float aidFuelRate = 0.0f;
  float aidTireRate = 0.0f;
  float aidMechanicalDamage = 0.0f;
  int aidAllowTyreBlankets = 0;
  float aidStability = 0.0f;
  int aidAutoClutch = 0;
  int aidAutoBlip = 0;
  int hasDRS = 0;
  int hasERS = 0;
  int hasKERS = 0;
  float maxKersKJ = 0.0f;
  int maxEngineBrake = 0;
  int ersPowerController = 0;
  float trackSplineLength = 0.0f;
  wchar_t trackConfiguration[33] = {0};
  float ersMaxJ = 0.0f;
  int isTimedRace = 0;
  int hasExtraLap = 0;
  wchar_t carSkin[33] = {0};
  int reversedGridPositions = 0;
  int pitWindowStart = 0;
  int pitWindowEnd = 0;
  int isOnline = 0;
};

#pragma pack(pop)

} // namespace ks
