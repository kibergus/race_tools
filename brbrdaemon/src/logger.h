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
#include <fstream>
#include <mutex>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <iostream>
#include <filesystem>

namespace race_tools {

// Thread-safe rotating file and optional console logger for the telemetry application
class Logger {
public:
  Logger() = default;

  explicit Logger(const std::wstring& filePath, bool consoleOutput = false, size_t maxSizeBytes = 5 * 1024 * 1024)
      : consoleOutput_(consoleOutput), maxSizeBytes_(maxSizeBytes) {
    SetLogFile(filePath);
  }

  ~Logger() {
    Close();
  }

  void SetConsoleOutput(bool enabled) {
    std::lock_guard<std::mutex> lock(mutex_);
    consoleOutput_ = enabled;
  }

  void SetLogFile(const std::wstring& filePath) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (file_.is_open()) {
      file_.close();
    }
    filePath_ = filePath;
    try {
      std::filesystem::path p(filePath);
      if (p.has_parent_path()) {
        std::error_code ec;
        std::filesystem::create_directories(p.parent_path(), ec);
      }
    } catch (...) {}

    OpenFileLocked();
  }

  void Write(const std::string& message) {
    auto now = std::chrono::system_clock::now();
    auto in_time_t = std::chrono::system_clock::to_time_t(now);
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()) % 1000;

    std::tm timeInfo;
#if defined(_WIN32)
    localtime_s(&timeInfo, &in_time_t);
#else
    localtime_r(&in_time_t, &timeInfo);
#endif

    std::stringstream ss;
    ss << "[" << std::put_time(&timeInfo, "%Y-%m-%d %H:%M:%S")
       << "." << std::setfill('0') << std::setw(3) << ms.count() << "] "
       << message << "\n";

    std::string formatted = ss.str();

    std::lock_guard<std::mutex> lock(mutex_);
    if (consoleOutput_) {
      std::cout << formatted;
    }

    if (!filePath_.empty()) {
      // Check for size-based log rotation
      currentBytes_ += formatted.size();
      if (currentBytes_ >= maxSizeBytes_) {
        RotateLocked();
      }

      if (file_.is_open()) {
        file_ << formatted;
        file_.flush();
      }
    }
  }

  void Close() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (file_.is_open()) {
      file_.close();
    }
  }

private:
  std::mutex mutex_;
  std::wstring filePath_;
  std::ofstream file_;
  bool consoleOutput_ = false;
  size_t maxSizeBytes_ = 5 * 1024 * 1024;
  size_t currentBytes_ = 0;

  void OpenFileLocked() {
    if (filePath_.empty()) return;
    try {
      std::error_code ec;
      if (std::filesystem::exists(filePath_, ec)) {
        currentBytes_ = std::filesystem::file_size(filePath_, ec);
      } else {
        currentBytes_ = 0;
      }
    } catch (...) {
      currentBytes_ = 0;
    }
    file_.open(filePath_, std::ios::out | std::ios::app);
  }

  void RotateLocked() {
    if (file_.is_open()) {
      file_.close();
    }
    try {
      std::wstring backupPath = filePath_ + L".old";
      std::error_code ec;
      std::filesystem::remove(backupPath, ec);
      std::filesystem::rename(filePath_, backupPath, ec);
    } catch (...) {}
    currentBytes_ = 0;
    file_.open(filePath_, std::ios::out | std::ios::trunc);
  }
};

} // namespace race_tools
