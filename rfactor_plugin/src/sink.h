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

#include <vector>
#include <deque>
#include <string>
#include <chrono>
#include <memory>
#include <variant>
#include <thread>
#include <atomic>
#include <fstream>
#include <windows.h>
#include <winhttp.h>
#include "logger.h"
#include "safe_queue.h"

namespace race_tools {

class RetryBackoff {
public:
  RetryBackoff(std::chrono::seconds windowDuration = std::chrono::minutes(5), int maxAttempts = 6);

  bool CanAttempt(std::chrono::steady_clock::time_point now);
  void RecordAttempt(std::chrono::steady_clock::time_point now);
  void Reset();

private:
  std::vector<std::chrono::steady_clock::time_point> attempts_;
  std::chrono::seconds windowDuration_;
  int maxAttempts_;
  void CleanOldAttempts(std::chrono::steady_clock::time_point now);
};

// Abstract base class representing a telemetry destination (sink).
// Processes session start, telemetry lines, and session end events via a visitor interface.
class Sink {
public:
  // Signals that a telemetry logging session has started.
  struct StartSession {
    std::chrono::system_clock::time_point sessionStartTime;
  };

  // A chunk of metadata or CSV telemetry to be written to the sink.
  struct Line {
    std::string text;
    bool is_header = false;
  };

  // Signals that the telemetry logging session has ended.
  struct EndSession {};

  using Message = std::variant<StartSession, Line, EndSession>;

  // A visitor interface. std::visit can be used to dispatch the Message to it.
  virtual void operator()(const StartSession& start) = 0;
  virtual void operator()(const Line& line) = 0;
  virtual void operator()(const EndSession& end) = 0;

  virtual void Abort() {}

  virtual ~Sink() = default;
};

class FileSink : public Sink {
public:
  FileSink(const std::wstring& logDirectory, Logger& logger);

  virtual void operator()(const StartSession& start) override;
  virtual void operator()(const Line& line) override;
  virtual void operator()(const EndSession& end) override;

  virtual ~FileSink() override;
private:
  std::wstring logDirectory_;
  Logger& logger_;
  std::wstring filePath_;
  std::ofstream file_;
};

class HttpSink : public Sink {
public:
  HttpSink(const std::string& serverAddress, const std::string& apiKey, Logger& logger, std::atomic<int>& activeUploads,
           std::chrono::seconds bufferDuration = std::chrono::minutes(5),
           std::chrono::seconds backoffWindow = std::chrono::minutes(5),
           int maxBackoffAttempts = 6,
           std::chrono::seconds maxConnectionDuration = std::chrono::minutes(2));

  virtual void operator()(const StartSession& start) override;
  virtual void operator()(const Line& line) override;
  virtual void operator()(const EndSession& end) override;

  virtual void Abort() override;

  virtual ~HttpSink() override;
private:
  std::string serverAddress_;
  std::string apiKey_;
  Logger& logger_;
  std::atomic<int>& activeUploads_;
  std::chrono::system_clock::time_point sessionStartTime_;

  // WinHTTP handle tracking
  HINTERNET hSession_ = nullptr;
  HINTERNET hConnect_ = nullptr;
  HINTERNET hRequest_ = nullptr;
  bool connected_ = false;

  // Stream state
  std::vector<std::string> headers_;
  int lineCount_ = 0;

  // Telemetry buffer
  struct BufferedLine {
    std::chrono::steady_clock::time_point timestamp;
    std::string text;
  };
  std::deque<BufferedLine> buffer_;
  std::chrono::seconds bufferDuration_;

  // Reconnection tracking
  RetryBackoff backoff_;
  std::chrono::steady_clock::time_point lastConnectTime_;
  std::chrono::seconds maxConnectionDuration_;

  // Helpers
  bool Connect();
  void Disconnect();
  void WriteChunk(const std::string& data);
  void WriteFinalChunk();
  bool Finalize();
};

// Runs a background thread that pops telemetry messages from a queue reader and
// dispatches them to a specific Sink implementation.
class SinkWorker {
public:
  SinkWorker(std::unique_ptr<Sink> sink, SafeQueue<Sink::Message>::Reader reader);
  ~SinkWorker();

  // Disable copy/move to prevent undefined behavior when the thread runs
  SinkWorker(const SinkWorker&) = delete;
  SinkWorker& operator=(const SinkWorker&) = delete;
  SinkWorker(SinkWorker&&) = delete;
  SinkWorker& operator=(SinkWorker&&) = delete;

private:
  std::unique_ptr<Sink> sink_;
  SafeQueue<Sink::Message>::Reader reader_;
  std::atomic<bool> active_{true};
  std::thread thread_; // Declared last to ensure other members are initialized first
  void WorkerThread();
};

} // namespace race_tools
