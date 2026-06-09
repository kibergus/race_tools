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

#include "net_utils.h"
#include <cctype>

namespace race_tools {

ServerAddressInfo ParseServerAddress(const std::string& address) {
  ServerAddressInfo info;
  std::string host = address;
  info.port = INTERNET_DEFAULT_HTTP_PORT;
  info.useHttps = false;

  // Check scheme
  if (host.rfind("https://", 0) == 0) {
    info.useHttps = true;
    host = host.substr(8);
    info.port = INTERNET_DEFAULT_HTTPS_PORT;
  } else if (host.rfind("http://", 0) == 0) {
    info.useHttps = false;
    host = host.substr(7);
    info.port = INTERNET_DEFAULT_HTTP_PORT;
  }

  // Check port
  size_t colon = host.find_last_of(':');
  if (colon != std::string::npos) {
    bool isPort = true;
    std::string portStr = host.substr(colon + 1);
    if (portStr.empty())
      isPort = false;
    for (char c : portStr) {
      if (!std::isdigit(static_cast<unsigned char>(c))) {
        isPort = false;
        break;
      }
    }
    if (isPort) {
      info.port = static_cast<INTERNET_PORT>(std::stoi(portStr));
      host = host.substr(0, colon);
    }
  }

  info.host = std::wstring(host.begin(), host.end());
  return info;
}

} // namespace race_tools
