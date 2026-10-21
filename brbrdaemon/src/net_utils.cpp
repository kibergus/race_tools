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
#include <algorithm>
#include <stdexcept>

namespace race_tools {

std::wstring Utf8ToWide(const std::string& str) {
  if (str.empty()) return L"";
  int sizeNeeded = MultiByteToWideChar(CP_UTF8, 0, str.data(), static_cast<int>(str.size()), nullptr, 0);
  if (sizeNeeded <= 0) return L"";
  std::wstring wstr(sizeNeeded, 0);
  MultiByteToWideChar(CP_UTF8, 0, str.data(), static_cast<int>(str.size()), &wstr[0], sizeNeeded);
  return wstr;
}

std::string WideToUtf8(const std::wstring& wstr) {
  if (wstr.empty()) return "";
  int sizeNeeded = WideCharToMultiByte(CP_UTF8, 0, wstr.data(), static_cast<int>(wstr.size()), nullptr, 0, nullptr, nullptr);
  if (sizeNeeded <= 0) return "";
  std::string str(sizeNeeded, 0);
  WideCharToMultiByte(CP_UTF8, 0, wstr.data(), static_cast<int>(wstr.size()), &str[0], sizeNeeded, nullptr, nullptr);
  return str;
}

bool IsValidApiKey(const std::string& apiKey) {
  for (char c : apiKey) {
    if (c == '\r' || c == '\n') return false;
  }
  return true;
}

std::string SanitizeFilename(const std::string& name) {
  std::string sanitized;
  for (unsigned char c : name) {
    if (c < 32 || c == '\\' || c == '/' || c == ':' || c == '*' || c == '?' ||
        c == '"' || c == '<' || c == '>' || c == '|') {
      if (!sanitized.empty() && sanitized.back() != '_') {
        sanitized += '_';
      }
    } else if (c == ' ') {
      if (!sanitized.empty() && sanitized.back() != '_') {
        sanitized += '_';
      }
    } else {
      sanitized += static_cast<char>(c);
    }
  }
  while (!sanitized.empty() && (sanitized.back() == '_' || sanitized.back() == '.' || sanitized.back() == ' ')) {
    sanitized.pop_back();
  }
  return sanitized.empty() ? "session" : sanitized;
}

ServerAddressInfo ParseServerAddress(const std::string& address) {
  ServerAddressInfo info;
  std::string urlStr = address;

  // Trim whitespace
  while (!urlStr.empty() && (urlStr.front() == ' ' || urlStr.front() == '\t')) urlStr.erase(0, 1);
  while (!urlStr.empty() && (urlStr.back() == ' ' || urlStr.back() == '\t' || urlStr.back() == '\r' || urlStr.back() == '\n')) urlStr.pop_back();

  if (urlStr.empty()) {
    return info;
  }

  // If no scheme was provided, default to http://
  if (urlStr.find("://") == std::string::npos) {
    urlStr = "http://" + urlStr;
  }

  std::wstring wUrl = Utf8ToWide(urlStr);

  URL_COMPONENTS urlComp{};
  urlComp.dwStructSize = sizeof(urlComp);
  urlComp.dwSchemeLength = static_cast<DWORD>(-1);
  urlComp.dwHostNameLength = static_cast<DWORD>(-1);
  urlComp.dwUrlPathLength = static_cast<DWORD>(-1);

  if (WinHttpCrackUrl(wUrl.c_str(), static_cast<DWORD>(wUrl.length()), 0, &urlComp)) {
    info.useHttps = (urlComp.nScheme == INTERNET_SCHEME_HTTPS);
    info.port = urlComp.nPort;
    if (urlComp.lpszHostName && urlComp.dwHostNameLength > 0) {
      info.host = std::wstring(urlComp.lpszHostName, urlComp.dwHostNameLength);
    }
    if (urlComp.lpszUrlPath && urlComp.dwUrlPathLength > 0) {
      info.path = std::wstring(urlComp.lpszUrlPath, urlComp.dwUrlPathLength);
    }
  } else {
    // Fallback simple parsing if WinHttpCrackUrl fails
    std::string hostPart = urlStr;
    if (hostPart.rfind("https://", 0) == 0) {
      info.useHttps = true;
      info.port = INTERNET_DEFAULT_HTTPS_PORT;
      hostPart = hostPart.substr(8);
    } else if (hostPart.rfind("http://", 0) == 0) {
      info.useHttps = false;
      info.port = INTERNET_DEFAULT_HTTP_PORT;
      hostPart = hostPart.substr(7);
    }
    size_t slashPos = hostPart.find('/');
    if (slashPos != std::string::npos) {
      info.path = Utf8ToWide(hostPart.substr(slashPos));
      hostPart = hostPart.substr(0, slashPos);
    }
    size_t colonPos = hostPart.rfind(':');
    if (colonPos != std::string::npos && colonPos > hostPart.rfind(']')) {
      try {
        int parsedPort = std::stoi(hostPart.substr(colonPos + 1));
        if (parsedPort > 0 && parsedPort <= 65535) {
          info.port = static_cast<INTERNET_PORT>(parsedPort);
        }
      } catch (...) {}
      hostPart = hostPart.substr(0, colonPos);
    }
    info.host = Utf8ToWide(hostPart);
  }

  return info;
}

} // namespace race_tools
