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

// This file contains a mock telemetry runner program that simulates a telemetry session
// for the BrBrDbTelemetryPlugin. It initializes the plugin, constructs mock scoring
// and vehicle data, transitions into the realtime cockpit state, simulates several frames
// of vehicle telemetry updates, and finally shuts down the plugin. This is used for testing
// the plugin's telemetry extraction and logging capabilities without needing the actual rFactor 2 game.
#define _CRT_SECURE_NO_WARNINGS
#include "brbrdb_telemetry_plugin.h"
#include <iostream>
#include <thread>
#include <chrono>
#include <cstring>

using namespace race_tools;

int main(int argc, char* argv[]) {
  std::cout << "Starting Mock Telemetry Runner..." << std::endl;

  std::wstring testLogDir = L"";
  if (argc > 1) {
    std::string arg(argv[1]);
    testLogDir = std::wstring(arg.begin(), arg.end());
    std::wcout << L"Using configured log directory: " << testLogDir << std::endl;
  }

  BrBrDbTelemetryPlugin plugin(testLogDir);
  plugin.Startup(7);

  // Create mock ScoringInfo and zero-initialize it
  ScoringInfoV01 scoring = {};
  std::strcpy(scoring.mTrackName, "Rowrah");
  scoring.mSession = 6; // Qualify (5-8 is Qualify)
  std::strcpy(scoring.mPlayerName, "Kibergus");
  scoring.mNumVehicles = 1;

  VehicleScoringInfoV01 vehicle = {};
  vehicle.mID = 1;
  std::strcpy(vehicle.mDriverName, "Kibergus");
  std::strcpy(vehicle.mVehicleName, "IAME Cadet #55");
  std::strcpy(vehicle.mVehicleClass, "IAME Waterswift Cadet");
  vehicle.mIsPlayer = true;

  scoring.mVehicle = &vehicle;

  std::cout << "Entering realtime cockpit..." << std::endl;
  plugin.EnterRealtime();

  std::cout << "Sending scoring update..." << std::endl;
  plugin.UpdateScoring(scoring);

  // Create mock TelemInfo and zero-initialize it
  TelemInfoV01 telem = {};
  telem.mElapsedTime = 1.2345;
  telem.mLapNumber = 2;
  telem.mPos.x = 10.0;
  telem.mPos.y = 1.5;
  telem.mPos.z = 20.0;
  telem.mLocalVel.x = 5.0;
  telem.mLocalVel.y = 0.0;
  telem.mLocalVel.z = 15.0;
  telem.mLocalAccel.x = 1.0;
  telem.mLocalAccel.y = 0.0;
  telem.mLocalAccel.z = 2.0;
  telem.mOri[0].y = 0.0;
  telem.mOri[1].y = 1.0;
  telem.mOri[2].y = 0.0;
  telem.mOri[2].x = 0.0;
  telem.mOri[2].z = 1.0;
  telem.mOri[0].x = 1.0;
  telem.mLocalRot.x = 0.0;
  telem.mLocalRot.y = 0.0;
  telem.mLocalRot.z = 0.0;
  telem.mEngineRPM = 8000.0;
  telem.mEngineWaterTemp = 85.0;
  telem.mEngineOilTemp = 95.0;
  telem.mGear = 1;
  telem.mFilteredSteering = 0.12;
  telem.mSteeringShaftTorque = 1.5;
  telem.mFilteredThrottle = 0.85;
  telem.mFilteredBrake = 0.0;
  telem.mFilteredClutch = 0.0;
  telem.mFuel = 4.5;

  // Explicitly set the vehicle name to mock driver name
  std::strcpy(telem.mVehicleName, "IAME Cadet #55");
  std::strcpy(telem.mTrackName, "Rowrah");

  for (int i = 0; i < 4; ++i) {
    telem.mWheel[i].mRotation = 50.0;
    telem.mWheel[i].mSuspensionDeflection = 0.05;
    telem.mWheel[i].mSuspForce = 500.0;
    telem.mWheel[i].mLateralPatchVel = 0.1;
    telem.mWheel[i].mLongitudinalPatchVel = 0.2;
    telem.mWheel[i].mTireLoad = 1000.0;
    telem.mWheel[i].mTemperature[0] = 320.0; // Kelvin
    telem.mWheel[i].mTemperature[1] = 325.0;
    telem.mWheel[i].mTemperature[2] = 320.0;
    telem.mWheel[i].mWear = 0.99;
    telem.mWheel[i].mToe = 0.01;
    telem.mWheel[i].mLateralForce = 1200.0;
    telem.mWheel[i].mLongitudinalForce = 800.0;
    telem.mWheel[i].mGripFract = 0.95;
  }

  std::cout << "Logging 200 frames..." << std::endl;
  for (int k = 0; k < 200; ++k) {
    telem.mElapsedTime += 0.01;
    if (k == 100) {
      std::cout << "Simulating blue flag mid-session..." << std::endl;
      vehicle.mFlag = 6;
      plugin.UpdateScoring(scoring);
    }
    plugin.UpdateTelemetry(telem);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  std::cout << "Exiting realtime cockpit..." << std::endl;
  plugin.ExitRealtime();

  std::cout << "Shutting down plugin..." << std::endl;
  plugin.Shutdown();

  std::cout << "Mock Telemetry Runner finished successfully!" << std::endl;
  return 0;
}
