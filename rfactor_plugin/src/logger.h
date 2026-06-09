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
#include <filesystem>
#include <windows.h>

namespace race_tools {

class Logger {
public:
  Logger() = default;
  ~Logger() {
    Close();
  }

  // Disable copy and assignment to prevent multiple ownership of file handles
  Logger(const Logger&) = delete;
  Logger& operator=(const Logger&) = delete;

  // Open the log file
  bool Open(const std::filesystem::path& logFilePath) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (file_.is_open()) {
      file_.close();
    }
    file_.open(logFilePath, std::ios::out | std::ios::app);
    return file_.is_open();
  }

  // Close the log file
  void Close() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (file_.is_open()) {
      file_.close();
    }
  }

  // Write a message with a timestamp
  void Write(const std::string& message) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (file_.is_open()) {
      auto now = std::chrono::system_clock::now();
      std::time_t time_c = std::chrono::system_clock::to_time_t(now);
      struct tm local_tm;
      localtime_s(&local_tm, &time_c);
      char timestamp[64];
      sprintf_s(timestamp, sizeof(timestamp), "%04d-%02d-%02d %02d:%02d:%02d",
                local_tm.tm_year + 1900, local_tm.tm_mon + 1, local_tm.tm_mday,
                local_tm.tm_hour, local_tm.tm_min, local_tm.tm_sec);
      file_ << "[" << timestamp << "] " << message << "\n";
      file_.flush(); // Ensure output is flushed to disk
    }
  }

  // Log a critical error, show messagebox, and terminate if enabled.
  void LogCritical(const std::string& message) {
    Write("CRITICAL ERROR: " + message);

    if (showMessageBoxOnCritical_) {
      MessageBoxA(NULL, message.c_str(), "Telemetry Extractor - Critical Error", MB_OK | MB_ICONERROR);
    }

    if (terminateOnCritical_) {
      std::terminate();
    }
  }

  void SetTerminateOnCritical(bool shouldTerminate) {
    terminateOnCritical_ = shouldTerminate;
  }

  void SetShowMessageBoxOnCritical(bool show) {
    showMessageBoxOnCritical_ = show;
  }

private:
  std::ofstream file_;
  std::mutex mutex_;
  bool terminateOnCritical_ = true;
  bool showMessageBoxOnCritical_ = true;
};

} // namespace race_tools
