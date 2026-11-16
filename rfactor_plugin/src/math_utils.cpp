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

#include "math_utils.h"
#include <cmath>

namespace race_tools {

namespace {

void CanonicalizeQuaternion(double& qx, double& qy, double& qz, double& qw) {
  double norm = std::sqrt(qx * qx + qy * qy + qz * qz + qw * qw);
  if (norm > 0.0) {
    qx /= norm;
    qy /= norm;
    qz /= norm;
    qw /= norm;
  } else {
    qx = 0.0;
    qy = 0.0;
    qz = 0.0;
    qw = 1.0;
    return;
  }

  constexpr double kEps = 1e-12;
  bool negate = false;
  if (std::abs(qw) > kEps) {
    negate = (qw < 0.0);
  } else if (std::abs(qz) > kEps) {
    negate = (qz < 0.0);
  } else if (std::abs(qy) > kEps) {
    negate = (qy < 0.0);
  } else if (std::abs(qx) > kEps) {
    negate = (qx < 0.0);
  }
  if (negate) {
    qx = -qx;
    qy = -qy;
    qz = -qz;
    qw = -qw;
  }
}

} // namespace

void Matrix3x3ToQuaternion(const double r[3][3], double& qx, double& qy, double& qz, double& qw) {
  double r00 = r[0][0], r01 = r[0][1], r02 = r[0][2];
  double r10 = r[1][0], r11 = r[1][1], r12 = r[1][2];
  double r20 = r[2][0], r21 = r[2][1], r22 = r[2][2];

  double trace = r00 + r11 + r22;
  if (trace > 0.0) {
    double s = 0.5 / std::sqrt(trace + 1.0);
    qw = 0.25 / s;
    qx = (r21 - r12) * s;
    qy = (r02 - r20) * s;
    qz = (r10 - r01) * s;
  } else {
    if (r00 > r11 && r00 > r22) {
      double s = 2.0 * std::sqrt(1.0 + r00 - r11 - r22);
      qw = (r21 - r12) / s;
      qx = 0.25 * s;
      qy = (r01 + r10) / s;
      qz = (r02 + r20) / s;
    } else if (r11 > r22) {
      double s = 2.0 * std::sqrt(1.0 + r11 - r00 - r22);
      qw = (r02 - r20) / s;
      qx = (r01 + r10) / s;
      qy = 0.25 * s;
      qz = (r12 + r21) / s;
    } else {
      double s = 2.0 * std::sqrt(1.0 + r22 - r00 - r11);
      qw = (r10 - r01) / s;
      qx = (r02 + r20) / s;
      qy = (r12 + r21) / s;
      qz = 0.25 * s;
    }
  }
  CanonicalizeQuaternion(qx, qy, qz, qw);
}

void MatrixToQuaternion(const TelemVect3 ori[3], double& qx, double& qy, double& qz, double& qw) {
  double r[3][3] = {
    {ori[0].x, ori[0].y, ori[0].z},
    {ori[1].x, ori[1].y, ori[1].z},
    {ori[2].x, ori[2].y, ori[2].z}
  };
  Matrix3x3ToQuaternion(r, qx, qy, qz, qw);
}

void AlignQuaternionHemisphere(double& qx, double& qy, double& qz, double& qw,
                               double prevQx, double prevQy, double prevQz, double prevQw) {
  if (qx * prevQx + qy * prevQy + qz * prevQz + qw * prevQw < 0.0) {
    qx = -qx;
    qy = -qy;
    qz = -qz;
    qw = -qw;
  }
}

void VehicleOriToNedQuaternion(const TelemVect3 mOri[3], double& qx, double& qy, double& qz, double& qw) {
  double r[3][3] = {
    {-mOri[2].z, -mOri[2].x, -mOri[2].y},  // North = +Z_rf: [Fwd, Right, Down]
    {-mOri[0].z, -mOri[0].x, -mOri[0].y},  // East  = +X_rf: [Fwd, Right, Down]
    { mOri[1].z,  mOri[1].x,  mOri[1].y}   // Down  = -Y_rf: [Fwd, Right, Down]
  };
  Matrix3x3ToQuaternion(r, qx, qy, qz, qw);
}

void CameraOriToNedQuaternion(const TelemVect3 mCamOri[3], double& qx, double& qy, double& qz, double& qw) {
  double r[3][3] = {
    { mCamOri[2].z,  mCamOri[0].z, -mCamOri[1].z},  // North = +Z_rf: [Fwd, Right, Down]
    { mCamOri[2].x,  mCamOri[0].x, -mCamOri[1].x},  // East  = +X_rf: [Fwd, Right, Down]
    {-mCamOri[2].y, -mCamOri[0].y,  mCamOri[1].y}   // Down  = -Y_rf: [Fwd, Right, Down]
  };
  Matrix3x3ToQuaternion(r, qx, qy, qz, qw);
}

} // namespace race_tools
