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

void MatrixToQuaternion(const TelemVect3 ori[3], double& qx, double& qy, double& qz, double& qw) {
  double r00 = ori[0].x;
  double r01 = ori[0].y;
  double r02 = ori[0].z;
  double r10 = ori[1].x;
  double r11 = ori[1].y;
  double r12 = ori[1].z;
  double r20 = ori[2].x;
  double r21 = ori[2].y;
  double r22 = ori[2].z;

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
}

} // namespace race_tools
