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

#include <windows.h>
#include "net_utils.h"
#include <winhttp.h>
#pragma comment(lib, "winhttp.lib")

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
  // 2 to the power of n
  auto backoffDuration = std::chrono::seconds(1 << n);
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
    attempts_.erase(attempts_.begin());
  }
}

namespace {

struct WinHttpHandle {
  HINTERNET handle = nullptr;
  WinHttpHandle(HINTERNET h = nullptr) : handle(h) {}
  ~WinHttpHandle() {
    if (handle)
      WinHttpCloseHandle(handle);
  }
  HINTERNET get() const { return handle; }
  void reset(HINTERNET h = nullptr) {
    if (handle)
      WinHttpCloseHandle(handle);
    handle = h;
  }
  operator HINTERNET() const { return handle; }
};

void UploadBufferWinHttp(const std::string& data, const std::string& serverAddress,
                                const std::string& apiKey) {
  ServerAddressInfo addrInfo = ParseServerAddress(serverAddress);

  WinHttpHandle hSession = WinHttpOpen(L"BrBrDbTelemetry/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                       WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
  if (!hSession) {
    throw std::runtime_error("WinHttpOpen failed (error code " + std::to_string(GetLastError()) + ")");
  }

  WinHttpSetTimeouts(hSession, 10000, 10000, 30000, 30000);

  WinHttpHandle hConnect = WinHttpConnect(hSession, addrInfo.host.c_str(), addrInfo.port, 0);
  if (!hConnect) {
    throw std::runtime_error("WinHttpConnect failed (error code " + std::to_string(GetLastError()) + ")");
  }

  DWORD flags = addrInfo.useHttps ? WINHTTP_FLAG_SECURE : 0;
  WinHttpHandle hRequest = WinHttpOpenRequest(hConnect, L"POST", L"/api/upload/stream", NULL, WINHTTP_NO_REFERER,
                                              WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
  if (!hRequest) {
    throw std::runtime_error("WinHttpOpenRequest failed (error code " + std::to_string(GetLastError()) + ")");
  }

  // Add headers
  std::wstring headers = L"Content-Type: text/csv\r\n";
  if (!apiKey.empty()) {
    std::wstring wApiKey(apiKey.begin(), apiKey.end());
    headers += L"X-API-Key: " + wApiKey + L"\r\n";
  }

  WinHttpAddRequestHeaders(hRequest, headers.c_str(), (DWORD)-1, WINHTTP_ADDREQ_FLAG_ADD | WINHTTP_ADDREQ_FLAG_REPLACE);

  // Send Request with data size
  bool sent =
      WinHttpSendRequest(hRequest, WINHTTP_NO_ADDITIONAL_HEADERS, 0, WINHTTP_NO_REQUEST_DATA, 0, (DWORD)data.size(), 0);
  if (!sent) {
    throw std::runtime_error("WinHttpSendRequest failed (error code " + std::to_string(GetLastError()) + ")");
  }

  // Write data in chunks
  DWORD bytesWritten = 0;
  size_t offset = 0;
  while (offset < data.size()) {
    size_t chunkSize = (std::min)(data.size() - offset, size_t(16384));
    if (!WinHttpWriteData(hRequest, data.data() + offset, (DWORD)chunkSize, &bytesWritten)) {
      throw std::runtime_error("WinHttpWriteData failed (error code " + std::to_string(GetLastError()) + ")");
    }
    offset += chunkSize;
  }

  if (!WinHttpReceiveResponse(hRequest, NULL)) {
    throw std::runtime_error("WinHttpReceiveResponse failed (error code " + std::to_string(GetLastError()) + ")");
  }

  DWORD statusCode = 0;
  DWORD statusCodeSize = sizeof(statusCode);
  if (!WinHttpQueryHeaders(hRequest, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                           WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize, WINHTTP_NO_HEADER_INDEX)) {
    throw std::runtime_error("Failed to query HTTP status code (error code " + std::to_string(GetLastError()) + ")");
  }

  if (statusCode != 200) {
    std::string responseBody;
    DWORD bytesAvailable = 0;
    if (WinHttpQueryDataAvailable(hRequest, &bytesAvailable) && bytesAvailable > 0) {
      std::vector<char> respBuf(bytesAvailable + 1, 0);
      DWORD bytesRead = 0;
      if (WinHttpReadData(hRequest, respBuf.data(), bytesAvailable, &bytesRead)) {
        responseBody = std::string(respBuf.data(), bytesRead);
      }
    }
    std::string err = "Server returned status code " + std::to_string(statusCode);
    if (!responseBody.empty()) {
      err += " - Response: " + responseBody;
    }
    throw std::runtime_error(err);
  }
}

std::string WStringToString(const std::wstring& wstr) {
  std::string str;
  str.reserve(wstr.length());
  for (wchar_t wc : wstr) {
    str.push_back(static_cast<char>(wc));
  }
  return str;
}

} // namespace

// -------------------------------------------------------------
// Sinks Implementation
// -------------------------------------------------------------

FileSink::FileSink(const std::wstring& logDirectory, Logger& logger)
    : logDirectory_(logDirectory), logger_(logger) {}

FileSink::~FileSink() {
  operator()(EndSession{});
}

void FileSink::operator()(const StartSession& start) {
  auto time_t_val = std::chrono::system_clock::to_time_t(start.sessionStartTime);
  std::stringstream ss;
  ss << "BrBrDbTelemetry_" << time_t_val << "_server.csv";
  filePath_ = logDirectory_ + L"\\" + std::filesystem::path(ss.str()).wstring();

  file_.open(filePath_, std::ios::out | std::ios::trunc);
  if (!file_.is_open()) {
    logger_.Write("FileSink - Failed to open file: " + WStringToString(filePath_));
  } else {
    logger_.Write("FileSink - Started session, writing to: " + WStringToString(filePath_));
  }
}

void FileSink::operator()(const Line& line) {
  if (file_.is_open()) {
    file_ << line.text;
  }
}

void FileSink::operator()(const EndSession& end) {
  (void)end;
  if (file_.is_open()) {
    file_.close();
    logger_.Write("FileSink - Ended session, file closed: " + WStringToString(filePath_));
  }
}

HttpSink::HttpSink(const std::string& serverAddress, const std::string& apiKey, Logger& logger, std::atomic<int>& activeUploads,
                   std::chrono::seconds bufferDuration, std::chrono::seconds backoffWindow, int maxBackoffAttempts)
    : serverAddress_(serverAddress), apiKey_(apiKey), logger_(logger), activeUploads_(activeUploads),
      bufferDuration_(bufferDuration), backoff_(backoffWindow, maxBackoffAttempts) {}

HttpSink::~HttpSink() {
  Disconnect();
}

void HttpSink::operator()(const StartSession& start) {
  sessionStartTime_ = start.sessionStartTime;
  headers_.clear();
  lineCount_ = 0;
  buffer_.clear();
  backoff_.Reset();
  Disconnect();
  logger_.Write("HttpSink - Started session using server " + serverAddress_);
}

void HttpSink::operator()(const Line& line) {
  auto now = std::chrono::steady_clock::now();

  if (line.is_header) {
    headers_.push_back(line.text);
  } else {
    buffer_.push_back({now, line.text});
    // Remove old lines from the buffer.
    while (!buffer_.empty() && (now - buffer_.front().timestamp) > bufferDuration_) {
      buffer_.pop_front();
    }
  }

  if (!connected_) {
    if (backoff_.CanAttempt(now)) {
      backoff_.RecordAttempt(now);
      logger_.Write("HttpSink - Attempting to connect/reconnect to " + serverAddress_ + "...");
      if (Connect()) {
        try {
          for (const auto& h : headers_) {
            WriteChunk(h);
          }
          if (!line.is_header && buffer_.size() > 1) {
            for (size_t i = 0; i < buffer_.size() - 1; ++i) {
              WriteChunk(buffer_[i].text);
            }
          }
        } catch (const std::exception& e) {
          logger_.Write(std::string("HttpSink - Failed to write headers/buffer after reconnect: ") + e.what());
          Disconnect();
          return;
        }
      }
    }
  }

  if (connected_) {
    try {
      WriteChunk(line.text);
    } catch (const std::exception& e) {
      logger_.Write(std::string("HttpSink - Write error: ") + e.what());
      Disconnect();
    }
  }
}

void HttpSink::operator()(const EndSession& end) {
  (void)end;
  if (connected_) {
    logger_.Write("HttpSink - Session ended, finalizing stream to " + serverAddress_);
    try {
      WriteFinalChunk();

      if (!WinHttpReceiveResponse(hRequest_, NULL)) {
        throw std::runtime_error("WinHttpReceiveResponse failed (error code " + std::to_string(GetLastError()) + ")");
      }

      DWORD statusCode = 0;
      DWORD statusCodeSize = sizeof(statusCode);
      if (!WinHttpQueryHeaders(hRequest_, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                               WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize, WINHTTP_NO_HEADER_INDEX)) {
        throw std::runtime_error("Failed to query HTTP status code (error code " + std::to_string(GetLastError()) + ")");
      }

      if (statusCode != 200) {
        std::string responseBody;
        DWORD bytesAvailable = 0;
        if (WinHttpQueryDataAvailable(hRequest_, &bytesAvailable) && bytesAvailable > 0) {
          std::vector<char> respBuf(bytesAvailable + 1, 0);
          DWORD bytesRead = 0;
          if (WinHttpReadData(hRequest_, respBuf.data(), bytesAvailable, &bytesRead)) {
            responseBody = std::string(respBuf.data(), bytesRead);
          }
        }
        std::string err = "Server returned status code " + std::to_string(statusCode);
        if (!responseBody.empty()) {
          err += " - Response: " + responseBody;
        }
        throw std::runtime_error(err);
      }
      logger_.Write("HttpSink - Streaming finished successfully, session finalized.");
    } catch (const std::exception& e) {
      logger_.Write("HttpSink - Streaming finalization failed: " + std::string(e.what()));
    }
  } else {
    logger_.Write("HttpSink - Session ended but not connected. Stream could not be finalized.");
  }

  Disconnect();
}

bool HttpSink::Connect() {
  Disconnect(); // Ensure clean state before connecting

  try {
    ServerAddressInfo addrInfo = ParseServerAddress(serverAddress_);

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
    hRequest_ = WinHttpOpenRequest(hConnect_, L"POST", L"/api/upload/stream", NULL, WINHTTP_NO_REFERER,
                                   WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
    if (!hRequest_) {
      throw std::runtime_error("WinHttpOpenRequest failed (error code " + std::to_string(GetLastError()) + ")");
    }

    // Add headers for Transfer-Encoding: chunked
    std::wstring headers = L"Content-Type: text/csv\r\nTransfer-Encoding: chunked\r\n";
    if (!apiKey_.empty()) {
      std::wstring wApiKey(apiKey_.begin(), apiKey_.end());
      headers += L"X-API-Key: " + wApiKey + L"\r\n";
    }

    if (!WinHttpAddRequestHeaders(hRequest_, headers.c_str(), (DWORD)-1, WINHTTP_ADDREQ_FLAG_ADD | WINHTTP_ADDREQ_FLAG_REPLACE)) {
      throw std::runtime_error("WinHttpAddRequestHeaders failed (error code " + std::to_string(GetLastError()) + ")");
    }

    // Send Request with WINHTTP_IGNORE_REQUEST_TOTAL_LENGTH
    bool sent = WinHttpSendRequest(hRequest_, WINHTTP_NO_ADDITIONAL_HEADERS, 0, WINHTTP_NO_REQUEST_DATA, 0, WINHTTP_IGNORE_REQUEST_TOTAL_LENGTH, 0);
    if (!sent) {
      throw std::runtime_error("WinHttpSendRequest failed (error code " + std::to_string(GetLastError()) + ")");
    }

    connected_ = true;
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

  DWORD bytesWritten = 0;
  if (!WinHttpWriteData(hRequest_, chunk.data(), (DWORD)chunk.size(), &bytesWritten)) {
    throw std::runtime_error("WinHttpWriteData failed (error code " + std::to_string(GetLastError()) + ")");
  }
}

void HttpSink::WriteFinalChunk() {
  DWORD bytesWritten = 0;
  std::string chunk = "0\r\n\r\n";
  if (!WinHttpWriteData(hRequest_, chunk.data(), (DWORD)chunk.size(), &bytesWritten)) {
    throw std::runtime_error("WinHttpWriteData final chunk failed (error code " + std::to_string(GetLastError()) + ")");
  }
}

SinkWorker::SinkWorker(std::unique_ptr<Sink> sink, SafeQueue<Sink::Message>::Reader reader)
    : sink_(std::move(sink)), reader_(std::move(reader)), active_(true) {
  thread_ = std::thread(&SinkWorker::WorkerThread, this);
}

SinkWorker::~SinkWorker() {
  active_ = false;
  if (thread_.joinable()) {
    thread_.join();
  }
}

void SinkWorker::WorkerThread() {
  while (active_) {
    Sink::Message msg;
    if (!reader_.Pop(msg)) {
      break;
    }
    std::visit(*sink_, msg);
  }
}

} // namespace race_tools
