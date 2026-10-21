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

#include "sink.h"
#include <cmath>
#include <iomanip>
#include <chrono>
#include <sstream>
#include <filesystem>
#include <cctype>
#include <stdexcept>
#include <algorithm>

#include <windows.h>
#include "net_utils.h"
#include <winhttp.h>

namespace race_tools {

// -------------------------------------------------------------
// RetryBackoff Implementation
// -------------------------------------------------------------

RetryBackoff::RetryBackoff(std::chrono::seconds windowDuration, int maxAttempts)
    : windowDuration_(windowDuration), maxAttempts_(maxAttempts) {}

bool RetryBackoff::CanAttempt(std::chrono::steady_clock::time_point now) {
  CleanOldAttempts(now);
  if (attempts_.empty()) {
    return true;
  }
  size_t n = attempts_.size();
  if (n > static_cast<size_t>(maxAttempts_)) {
    n = maxAttempts_;
  }
  // Clamp exponent to prevent overflow (1 << 4 = 16 seconds max backoff)
  int shift = static_cast<int>((std::min)(n, size_t(4)));
  auto backoffDuration = std::chrono::seconds(1 << shift);
  return (now - attempts_.back()) >= backoffDuration;
}

void RetryBackoff::RecordAttempt(std::chrono::steady_clock::time_point now) {
  CleanOldAttempts(now);
  attempts_.push_back(now);
}

void RetryBackoff::Reset() {
  attempts_.clear();
}

void RetryBackoff::CleanOldAttempts(std::chrono::steady_clock::time_point now) {
  while (!attempts_.empty() && (now - attempts_.front()) > windowDuration_) {
    attempts_.pop_front();
  }
}

// -------------------------------------------------------------
// FileSink Implementation
// -------------------------------------------------------------

FileSink::FileSink(const std::wstring& logDirectory, Logger& logger)
    : logDirectory_(logDirectory), logger_(logger) {}

FileSink::~FileSink() {
  operator()(EndSession{});
}

void FileSink::operator()(const StartSession& start) {
  auto time_t_val = std::chrono::system_clock::to_time_t(start.sessionStartTime);
  std::stringstream ss;
  if (start.participantTag.empty()) {
    ss << "BrBrDbTelemetry_" << time_t_val << "_server.csv";
  } else {
    ss << "BrBrDbTelemetry_" << time_t_val << "_" << start.participantTag << "_server.csv";
  }
  filePath_ = logDirectory_ + L"\\" + Utf8ToWide(ss.str());

  file_.open(filePath_, std::ios::out | std::ios::trunc);
  linesSinceFlush_ = 0;
  if (!file_.is_open()) {
    logger_.Write("FileSink - Failed to open file: " + WideToUtf8(filePath_));
  } else {
    logger_.Write("FileSink - Started session, writing to: " + WideToUtf8(filePath_));
  }
}

void FileSink::operator()(const Line& line) {
  if (file_.is_open()) {
    file_ << line.text;
    if (++linesSinceFlush_ >= 200) { // Flush every ~2 seconds at 100 Hz
      file_.flush();
      linesSinceFlush_ = 0;
    }
  }
}

void FileSink::operator()(const EndSession& end) {
  (void)end;
  if (file_.is_open()) {
    file_.flush();
    file_.close();
    logger_.Write("FileSink - Ended session, file closed: " + WideToUtf8(filePath_));
  }
}

// -------------------------------------------------------------
// HttpSink Implementation
// -------------------------------------------------------------

HttpSink::HttpSink(const std::string& serverAddress,
                   const std::string& apiKey,
                   Logger& logger,
                   std::atomic<int>& activeUploads,
                   const HttpSinkOptions& options)
    : serverAddress_(serverAddress), apiKey_(apiKey), logger_(logger), activeUploads_(activeUploads),
      bufferDuration_(options.bufferDuration),
      backoff_(options.backoffWindow, options.maxBackoffAttempts),
      maxConnectionDuration_(options.maxConnectionDuration),
      maxEndSessionRetries_(options.maxEndSessionRetries) {}

HttpSink::HttpSink(const std::string& serverAddress,
                   const std::string& apiKey,
                   Logger& logger,
                   std::atomic<int>& activeUploads,
                   std::chrono::seconds bufferDuration,
                   std::chrono::seconds backoffWindow,
                   int maxBackoffAttempts,
                   std::chrono::seconds maxConnectionDuration,
                   int maxEndSessionRetries)
    : HttpSink(serverAddress, apiKey, logger, activeUploads,
               HttpSinkOptions{bufferDuration, backoffWindow, maxBackoffAttempts, maxConnectionDuration, maxEndSessionRetries}) {}

HttpSink::~HttpSink() {
  Disconnect();
}

void HttpSink::Abort() {
  aborted_ = true;
  std::lock_guard<std::mutex> lock(handleMutex_);
  if (hRequest_) {
    WinHttpCloseHandle(hRequest_);
    hRequest_ = nullptr;
  }
}

void HttpSink::operator()(const StartSession& start) {
  sessionStartTime_ = start.sessionStartTime;
  headers_.clear();
  buffer_.clear();
  backoff_.Reset();
  aborted_ = false;
  Disconnect();
  logger_.Write("HttpSink - Started session" +
                (start.participantTag.empty() ? "" : " for " + start.participantTag) +
                " using server " + serverAddress_);
}

void HttpSink::operator()(const Line& line) {
  if (aborted_) return;

  auto now = std::chrono::steady_clock::now();

  if (line.is_header) {
    headers_.push_back(line.text);
  } else {
    buffer_.push_back({now, line.text});
    while (!buffer_.empty() && (now - buffer_.front().timestamp) > bufferDuration_) {
      buffer_.pop_front();
    }
  }

  // Check proactive rollover
  if (connected_) {
    if (now - lastConnectTime_ >= maxConnectionDuration_) {
      logger_.Write("HttpSink - Proactively rolling over connection (active for " +
                    std::to_string(std::chrono::duration_cast<std::chrono::seconds>(now - lastConnectTime_).count()) + "s)");
      if (Finalize()) {
        // Successful rollover acknowledged: data sent up to this point does not need to be replayed!
        buffer_.clear();
      }
    }
  }

  // Attempt reconnect if disconnected
  if (!connected_ && !aborted_) {
    if (backoff_.CanAttempt(now)) {
      backoff_.RecordAttempt(now);
      logger_.Write("HttpSink - Attempting to connect/reconnect to " + serverAddress_ + "...");
      if (Connect()) {
        try {
          for (const auto& h : headers_) {
            WriteChunk(h);
          }
          for (const auto& buffered : buffer_) {
            WriteChunk(buffered.text);
          }
          backoff_.Reset();
        } catch (const std::exception& e) {
          logger_.Write(std::string("HttpSink - Failed to write headers/buffer after reconnect: ") + e.what());
          Disconnect();
          return;
        }
      }
    }
  }

  // Write new line immediately upon arrival.
  // NOTE: Intentionally do NOT batch writes here. Lines are written immediately
  // via WinHttpWriteData chunk to ensure minimal latency for real-time monitoring consumers.
  if (connected_ && !aborted_) {
    try {
      WriteChunk(line.text);
    } catch (const std::exception& e) {
      logger_.Write(std::string("HttpSink - Write error: ") + e.what());
      Disconnect();
    }
  }
}

bool HttpSink::Finalize() {
  if (!connected_) return false;

  bool success = false;
  try {
    WriteFinalChunk();

    HINTERNET hReq = nullptr;
    {
      std::lock_guard<std::mutex> lock(handleMutex_);
      hReq = hRequest_;
    }

    if (!hReq || !WinHttpReceiveResponse(hReq, NULL)) {
      throw std::runtime_error("WinHttpReceiveResponse failed (error code " + std::to_string(GetLastError()) + ")");
    }

    DWORD statusCode = 0;
    DWORD statusCodeSize = sizeof(statusCode);
    if (!WinHttpQueryHeaders(hReq, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                             WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize, WINHTTP_NO_HEADER_INDEX)) {
      throw std::runtime_error("Failed to query HTTP status code (error code " + std::to_string(GetLastError()) + ")");
    }

    // Accept 200 OK, 201 Created, and 204 No Content
    if (statusCode != 200 && statusCode != 201 && statusCode != 204) {
      std::string responseBody;
      DWORD bytesAvailable = 0;
      if (WinHttpQueryDataAvailable(hReq, &bytesAvailable) && bytesAvailable > 0) {
        std::vector<char> respBuf(bytesAvailable + 1, 0);
        DWORD bytesRead = 0;
        if (WinHttpReadData(hReq, respBuf.data(), bytesAvailable, &bytesRead)) {
          responseBody = std::string(respBuf.data(), bytesRead);
        }
      }
      std::string err = "Server returned status code " + std::to_string(statusCode);
      if (!responseBody.empty()) {
        err += " - Response: " + responseBody;
      }
      throw std::runtime_error(err);
    }
    success = true;
  } catch (const std::exception& e) {
    logger_.Write("HttpSink - Streaming finalization failed: " + std::string(e.what()));
  }

  Disconnect();
  return success;
}

void HttpSink::operator()(const EndSession& end) {
  (void)end;
  logger_.Write("HttpSink - Session ended, finalizing stream to " + serverAddress_);

  if (aborted_) {
    Disconnect();
    return;
  }

  bool finalized = false;
  if (connected_) {
    finalized = Finalize();
    if (finalized) {
      logger_.Write("HttpSink - Streaming finished successfully, session finalized.");
      return;
    } else {
      logger_.Write("HttpSink - Active stream finalization failed. Retrying full upload of buffer...");
    }
  } else {
    logger_.Write("HttpSink - Session ended while disconnected. Retrying upload of buffered data...");
  }

  // Retry upload of buffered telemetry
  int maxRetries = maxEndSessionRetries_;
  for (int attempt = 1; attempt <= maxRetries && !aborted_; ++attempt) {
    logger_.Write("HttpSink - Re-upload attempt " + std::to_string(attempt) + "/" +
                  std::to_string(maxRetries) + " to " + serverAddress_ + "...");

    Disconnect();

    if (Connect()) {
      try {
        for (const auto& h : headers_) {
          WriteChunk(h);
        }
        for (const auto& buffered : buffer_) {
          WriteChunk(buffered.text);
        }

        if (Finalize()) {
          logger_.Write("HttpSink - Retried upload succeeded on attempt " +
                        std::to_string(attempt) + ", session finalized.");
          finalized = true;
          break;
        }
      } catch (const std::exception& e) {
        logger_.Write("HttpSink - Re-upload attempt " + std::to_string(attempt) + " failed: " + e.what());
      }
    }

    if (attempt < maxRetries && !aborted_) {
      int backoffSec = (std::min)(1 << (std::min)(attempt, 4), 16);
      logger_.Write("HttpSink - Waiting " + std::to_string(backoffSec) + "s before retry...");
      std::this_thread::sleep_for(std::chrono::seconds(backoffSec));
    }
  }

  if (!finalized && !aborted_) {
    logger_.Write("HttpSink - Failed to upload session to " + serverAddress_ +
                  " after " + std::to_string(maxRetries) + " attempts.");
  }
  Disconnect();
}

bool HttpSink::Connect() {
  Disconnect();
  if (aborted_) return false;

  try {
    if (!IsValidApiKey(apiKey_)) {
      throw std::runtime_error("Invalid API key (contains forbidden characters)");
    }

    ServerAddressInfo addrInfo = ParseServerAddress(serverAddress_);
    if (addrInfo.host.empty()) {
      throw std::runtime_error("Invalid server address: " + serverAddress_);
    }

    std::lock_guard<std::mutex> lock(handleMutex_);

    hSession_ = WinHttpOpen(L"BrBrDbTelemetry/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                            WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!hSession_) {
      throw std::runtime_error("WinHttpOpen failed (error code " + std::to_string(GetLastError()) + ")");
    }

    WinHttpSetTimeouts(hSession_, 10000, 10000, 30000, 30000);

    hConnect_ = WinHttpConnect(hSession_, addrInfo.host.c_str(), addrInfo.port, 0);
    if (!hConnect_) {
      throw std::runtime_error("WinHttpConnect failed (error code " + std::to_string(GetLastError()) + ")");
    }

    DWORD flags = addrInfo.useHttps ? WINHTTP_FLAG_SECURE : 0;
    std::wstring path = addrInfo.path.empty() ? L"/api/upload/stream" : addrInfo.path;
    hRequest_ = WinHttpOpenRequest(hConnect_, L"POST", path.c_str(), NULL, WINHTTP_NO_REFERER,
                                   WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
    if (!hRequest_) {
      throw std::runtime_error("WinHttpOpenRequest failed (error code " + std::to_string(GetLastError()) + ")");
    }

    // Add headers for Transfer-Encoding: chunked
    std::wstring headers = L"Content-Type: text/csv\r\nTransfer-Encoding: chunked\r\n";
    if (!apiKey_.empty()) {
      headers += L"X-API-Key: " + Utf8ToWide(apiKey_) + L"\r\n";
    }

    if (!WinHttpAddRequestHeaders(hRequest_, headers.c_str(), static_cast<DWORD>(-1),
                                 WINHTTP_ADDREQ_FLAG_ADD | WINHTTP_ADDREQ_FLAG_REPLACE)) {
      throw std::runtime_error("WinHttpAddRequestHeaders failed (error code " + std::to_string(GetLastError()) + ")");
    }

    // Send Request with WINHTTP_IGNORE_REQUEST_TOTAL_LENGTH
    bool sent = WinHttpSendRequest(hRequest_, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                                   WINHTTP_NO_REQUEST_DATA, 0, WINHTTP_IGNORE_REQUEST_TOTAL_LENGTH, 0);
    if (!sent) {
      throw std::runtime_error("WinHttpSendRequest failed (error code " + std::to_string(GetLastError()) + ")");
    }

    connected_ = true;
    lastConnectTime_ = std::chrono::steady_clock::now();
    activeUploads_++;
    logger_.Write("HttpSink - Connected to " + serverAddress_);
    return true;
  } catch (const std::exception& e) {
    logger_.Write("HttpSink - Connection to " + serverAddress_ + " failed: " + e.what());
    Disconnect();
    return false;
  }
}

void HttpSink::Disconnect() {
  std::lock_guard<std::mutex> lock(handleMutex_);
  if (hRequest_) {
    WinHttpCloseHandle(hRequest_);
    hRequest_ = nullptr;
  }
  if (hConnect_) {
    WinHttpCloseHandle(hConnect_);
    hConnect_ = nullptr;
  }
  if (hSession_) {
    WinHttpCloseHandle(hSession_);
    hSession_ = nullptr;
  }
  if (connected_) {
    connected_ = false;
    activeUploads_--;
  }
}

void HttpSink::WriteChunk(const std::string& data) {
  if (data.empty()) return;

  std::stringstream ss;
  ss << std::hex << data.size() << "\r\n" << data << "\r\n";
  std::string chunk = ss.str();

  HINTERNET hReq = nullptr;
  {
    std::lock_guard<std::mutex> lock(handleMutex_);
    hReq = hRequest_;
  }
  if (!hReq) {
    throw std::runtime_error("Cannot write chunk: disconnected or aborted");
  }

  DWORD bytesWritten = 0;
  if (!WinHttpWriteData(hReq, chunk.data(), static_cast<DWORD>(chunk.size()), &bytesWritten)) {
    throw std::runtime_error("WinHttpWriteData failed (error code " + std::to_string(GetLastError()) + ")");
  }
}

void HttpSink::WriteFinalChunk() {
  HINTERNET hReq = nullptr;
  {
    std::lock_guard<std::mutex> lock(handleMutex_);
    hReq = hRequest_;
  }
  if (!hReq) return;

  DWORD bytesWritten = 0;
  const char chunk[] = "0\r\n\r\n";
  if (!WinHttpWriteData(hReq, chunk, static_cast<DWORD>(sizeof(chunk) - 1), &bytesWritten)) {
    throw std::runtime_error("WinHttpWriteData final chunk failed (error code " + std::to_string(GetLastError()) + ")");
  }
}

// -------------------------------------------------------------
// SinkWorker Implementation
// -------------------------------------------------------------

SinkWorker::SinkWorker(std::unique_ptr<Sink> sink, SafeQueue<Sink::Message>::Reader reader)
    : sink_(std::move(sink)), reader_(std::move(reader)), active_(true) {
  thread_ = std::thread(&SinkWorker::WorkerThread, this);
}

void SinkWorker::Abort() {
  active_ = false;
  if (sink_) {
    sink_->Abort();
  }
}

SinkWorker::~SinkWorker() {
  if (thread_.joinable()) {
    thread_.join();
  }
}

void SinkWorker::WorkerThread() {
  Sink::Message msg;
  while (active_ && reader_.Pop(msg)) {
    try {
      if (sink_) {
        std::visit(*sink_, msg);
      }
    } catch (const std::exception& e) {
      // Exception boundary prevents std::terminate
      (void)e;
    } catch (...) {
      // Prevent any unhandled exception from terminating process
    }
  }
}

} // namespace race_tools
