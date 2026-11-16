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

#include "rf2_sdk/InternalsPlugin.hpp"

namespace race_tools {

// Converts a 3x3 rotation matrix to a normalized, canonically signed quaternion.
void Matrix3x3ToQuaternion(const double r[3][3], double& qx, double& qy, double& qz, double& qw);

// Converts a 3x3 orientation matrix to a quaternion
void MatrixToQuaternion(const TelemVect3 ori[3], double& qx, double& qy, double& qz, double& qw);

// Enforces hemisphere continuity against the previous finite quaternion sample.
void AlignQuaternionHemisphere(double& qx, double& qy, double& qz, double& qw,
                               double prevQx, double prevQy, double prevQz, double prevQw);

// Converts rFactor 2 TelemInfoV01::mOri (columns: Left, Up, Back) to Game NED + FRD quaternion.
void VehicleOriToNedQuaternion(const TelemVect3 mOri[3], double& qx, double& qy, double& qz, double& qw);

// Converts rFactor 2 GraphicsInfoV02::mCamOri (rows: Right, Up, Forward) to Game NED + FRD quaternion.
void CameraOriToNedQuaternion(const TelemVect3 mCamOri[3], double& qx, double& qy, double& qz, double& qw);

} // namespace race_tools
