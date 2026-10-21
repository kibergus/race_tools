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

#include <string>
#include <windows.h>
#include <winhttp.h>

namespace race_tools {

struct ServerAddressInfo {
  std::wstring host;
  INTERNET_PORT port = INTERNET_DEFAULT_HTTP_PORT;
  bool useHttps = false;
  std::wstring path;
};

// Robust string encoding conversions between UTF-8 and wide UTF-16
std::wstring Utf8ToWide(const std::string& str);
std::string WideToUtf8(const std::wstring& wstr);

// Sanitizes a string for use in filenames, replacing illegal filesystem characters
// while preserving valid Unicode characters (e.g. Cyrillic, Kanji)
std::string SanitizeFilename(const std::string& name);

// Parses server address string (e.g. http://localhost:8080 or https://example.com/api)
// using WinHttpCrackUrl, supporting HTTPS, custom ports, IPv6, and optional URL paths.
ServerAddressInfo ParseServerAddress(const std::string& address);

// Validates that an API key does not contain CR/LF characters (prevents HTTP header injection)
bool IsValidApiKey(const std::string& apiKey);

} // namespace race_tools
