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

// Converts a 3x3 orientation matrix to a quaternion
void MatrixToQuaternion(const TelemVect3 ori[3], double& qx, double& qy, double& qz, double& qw);

} // namespace race_tools
