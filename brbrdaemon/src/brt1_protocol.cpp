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

#include "brt1_protocol.h"
#include "thirdparty/nlohmann/json.hpp"
#include <cmath>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace race_tools {

const std::vector<Brt1Channel>& GetBrBrDaemonBrt1Channels() {
  static const std::vector<Brt1Channel> channels = {
    {"Time", 0.001, 5, false, true},
    {"x", 0.001, 5, false, true},
    {"z", 0.001, 5, false, true},
    {"Speed", 0.01, 5, false, true},
    {"GForceLat", 0.001, 5, false, true},
    {"GForceLon", 0.001, 5, false, true},
    {"GForceVert", 0.001, 5, false, true},
    {"Throttle", 0.1, 5, false, true},
    {"Brake", 0.1, 5, false, true},
    {"Steering Angle", 0.05, 5, false, true},
    {"Clutch", 0.1, 5, false, true},
    {"Handbrake", 0.1, 5, false, true},
    {"Gear", 1.0, 0, true, true},
    {"Toe FL", 0.0001, 5, false, true},
    {"Toe FR", 0.0001, 5, false, true},
    {"Toe RL", 0.0001, 5, false, true},
    {"Toe RR", 0.0001, 5, false, true},
    {"Ori Quat X", 0.0001, 5, false, true},
    {"Ori Quat Y", 0.0001, 5, false, true},
    {"Ori Quat Z", 0.0001, 5, false, true},
    {"Ori Quat W", 0.0001, 5, false, true},
    {"RPS FL", 0.01, 5, false, true},
    {"RPS FR", 0.01, 5, false, true},
    {"RPS RL", 0.01, 5, false, true},
    {"RPS RR", 0.01, 5, false, true},
    {"Lat Patch Vel FL", 0.01, 5, false, true},
    {"Lat Patch Vel FR", 0.01, 5, false, true},
    {"Lat Patch Vel RL", 0.01, 5, false, true},
    {"Lat Patch Vel RR", 0.01, 5, false, true},
    {"Long Patch Vel FL", 0.01, 5, false, true},
    {"Long Patch Vel FR", 0.01, 5, false, true},
    {"Long Patch Vel RL", 0.01, 5, false, true},
    {"Long Patch Vel RR", 0.01, 5, false, true},
    {"Tyre Load FL", 0.1, 5, false, true},
    {"Tyre Load FR", 0.1, 5, false, true},
    {"Tyre Load RL", 0.1, 5, false, true},
    {"Tyre Load RR", 0.1, 5, false, true},
    {"Lat Force FL", 0.1, 5, false, true},
    {"Lat Force FR", 0.1, 5, false, true},
    {"Lat Force RL", 0.1, 5, false, true},
    {"Lat Force RR", 0.1, 5, false, true},
    {"Long Force FL", 0.1, 5, false, true},
    {"Long Force FR", 0.1, 5, false, true},
    {"Long Force RL", 0.1, 5, false, true},
    {"Long Force RR", 0.1, 5, false, true},
    {"Slide Pct FL", 0.1, 5, false, true},
    {"Slide Pct FR", 0.1, 5, false, true},
    {"Slide Pct RL", 0.1, 5, false, true},
    {"Slide Pct RR", 0.1, 5, false, true},
    {"Flag", 1.0, 0, true, true},
    {"Track Temp", 0.01, 5, false, true},
    {"Ambient Temp", 0.01, 5, false, true},
    {"Raining", 0.001, 5, false, true},
    {"Avg Track Wetness", 0.001, 5, false, true},
    {"Tyre Surf Temp FL", 0.01, 5, false, true},
    {"Tyre Surf Temp FR", 0.01, 5, false, true},
    {"Tyre Surf Temp RL", 0.01, 5, false, true},
    {"Tyre Surf Temp RR", 0.01, 5, false, true},
    {"Tyre Carcass Temp FL", 0.01, 5, false, true},
    {"Tyre Carcass Temp FR", 0.01, 5, false, true},
    {"Tyre Carcass Temp RL", 0.01, 5, false, true},
    {"Tyre Carcass Temp RR", 0.01, 5, false, true}
  };
  return channels;
}

std::string GetBrBrDaemonCsvHeaders() {
  const auto& channels = GetBrBrDaemonBrt1Channels();
  std::string headers;
  for (size_t i = 0; i < channels.size(); ++i) {
    if (i > 0) headers += ",";
    headers += channels[i].name;
  }
  headers += "\n";
  return headers;
}

std::string FormatCsvLineFromValues(const std::string& timeStr, const std::vector<double>& values) {
  const auto& channels = GetBrBrDaemonBrt1Channels();
  std::stringstream ss;
  ss << std::fixed;

  for (size_t i = 0; i < channels.size(); ++i) {
    if (i > 0) ss << ",";
    if (i == 0) {
      // Channel 0 is Time: string representation formatted as ISO-8601
      ss << timeStr;
      continue;
    }

    if (i >= values.size()) {
      continue;
    }

    double val = values[i];
    if (std::isnan(val)) {
      if (!channels[i].emptyWhenNan) {
        ss << "nan";
      }
      // If emptyWhenNan is true, nothing is appended between the commas
    } else if (channels[i].isInteger) {
      ss << static_cast<long long>(std::round(val));
    } else {
      ss << std::setprecision(channels[i].csvPrecision) << val;
    }
  }
  ss << "\n";
  return ss.str();
}

std::string FrameBrt1Chunk(uint8_t type, const void* payload, uint16_t len) {
  std::string chunk;
  chunk.resize(3 + len);
  chunk[0] = static_cast<char>(len & 0xFF);
  chunk[1] = static_cast<char>((len >> 8) & 0xFF);
  chunk[2] = static_cast<char>(type);
  if (len > 0 && payload != nullptr) {
    std::memcpy(&chunk[3], payload, len);
  }
  return chunk;
}

Brt1StreamEncoder::Brt1StreamEncoder(std::vector<Brt1Channel> channels, int maxSamplesBetweenKeyframes)
    : channels_(std::move(channels)),
      maxSamplesBetweenKeyframes_(maxSamplesBetweenKeyframes),
      hasBaseline_(false),
      samplesSinceKeyframe_(0) {}

std::string Brt1StreamEncoder::EncodeMagicChunk() const {
  const char magic[4] = {'B', 'R', 'T', '1'};
  return FrameBrt1Chunk(static_cast<uint8_t>(Brt1ChunkType::Magic), magic, 4);
}

std::string Brt1StreamEncoder::EncodeMetadataChunk(const Brt1SessionMetadata& metadata) const {
  std::string className = metadata.className.empty() ? "Unknown Class" : metadata.className;
  std::string trackName = metadata.trackName.empty() ? "Unknown Track" : metadata.trackName;
  std::string driverName = metadata.driverName.empty() ? "Unknown Driver" : metadata.driverName;
  std::string sessionName = metadata.sessionName.empty() ? "Practice" : metadata.sessionName;
  std::string league = metadata.league.empty() ? "kartsim" : metadata.league;

  nlohmann::json j;
  nlohmann::json meta = {
    {"track_name", trackName},
    {"Track name", trackName},
    {"date", metadata.date},
    {"Date", metadata.date},
    {"time", metadata.time},
    {"Time", metadata.time},
    {"driver_name", driverName},
    {"Driver name", driverName},
    {"league", league},
    {"League", league},
    {"class", className},
    {"class_name", className},
    {"Class", className},
    {"session", sessionName},
    {"session_name", sessionName},
    {"Session", sessionName}
  };
  if (!metadata.kartNumber.empty()) {
    meta["kart_number"] = metadata.kartNumber;
    meta["Kart number"] = metadata.kartNumber;
  }
  j["metadata"] = meta;

  nlohmann::json channelsArr = nlohmann::json::array();
  for (const auto& ch : channels_) {
    channelsArr.push_back({
      {"name", ch.name},
      {"scale", ch.scale}
    });
  }
  j["channels"] = channelsArr;

  std::string jsonStr = j.dump();
  if (jsonStr.size() > 65535) {
    throw std::runtime_error("Metadata JSON exceeds 65535 bytes");
  }
  return FrameBrt1Chunk(static_cast<uint8_t>(Brt1ChunkType::MetadataJson),
                        jsonStr.data(),
                        static_cast<uint16_t>(jsonStr.size()));
}

std::string Brt1StreamEncoder::EncodeBaselineChunk(const std::vector<double>& values) {
  size_t M = channels_.size();
  std::vector<double> payloadDoubles(M);
  for (size_t i = 0; i < M; ++i) {
    if (i < values.size()) {
      payloadDoubles[i] = values[i];
    } else {
      payloadDoubles[i] = std::numeric_limits<double>::quiet_NaN();
    }
  }

  trackedValues_ = payloadDoubles;
  hasBaseline_ = true;
  samplesSinceKeyframe_ = 0;

  uint16_t byteLen = static_cast<uint16_t>(M * sizeof(double));
  return FrameBrt1Chunk(static_cast<uint8_t>(Brt1ChunkType::Baseline),
                        payloadDoubles.data(),
                        byteLen);
}

std::string Brt1StreamEncoder::EncodeDeltaChunk(const std::vector<int16_t>& deltas) const {
  uint16_t byteLen = static_cast<uint16_t>(deltas.size() * sizeof(int16_t));
  return FrameBrt1Chunk(static_cast<uint8_t>(Brt1ChunkType::Delta),
                        deltas.data(),
                        byteLen);
}

std::string Brt1StreamEncoder::EncodeEOSChunk() const {
  return FrameBrt1Chunk(static_cast<uint8_t>(Brt1ChunkType::EndOfStream), nullptr, 0);
}

std::string Brt1StreamEncoder::EncodeSample(const std::vector<double>& currentValues) {
  size_t M = channels_.size();
  if (!hasBaseline_ || samplesSinceKeyframe_ >= maxSamplesBetweenKeyframes_) {
    return EncodeBaselineChunk(currentValues);
  }

  // Check if any channel transitioned from NaN to non-NaN
  for (size_t j = 0; j < M; ++j) {
    double prev = (j < trackedValues_.size()) ? trackedValues_[j] : std::numeric_limits<double>::quiet_NaN();
    double curr = (j < currentValues.size()) ? currentValues[j] : std::numeric_limits<double>::quiet_NaN();
    if (std::isnan(prev) && !std::isnan(curr)) {
      return EncodeBaselineChunk(currentValues);
    }
  }

  std::vector<int16_t> deltas(M);
  std::vector<double> nextTracked(M);
  bool needsKeyframe = false;

  for (size_t j = 0; j < M; ++j) {
    double prev = trackedValues_[j];
    double curr = (j < currentValues.size()) ? currentValues[j] : std::numeric_limits<double>::quiet_NaN();

    if (std::isnan(curr)) {
      deltas[j] = BRT1_NAN_DELTA;
      nextTracked[j] = std::numeric_limits<double>::quiet_NaN();
    } else {
      double diff = curr - prev;
      double scale = channels_[j].scale;
      int64_t q = std::lround(diff / scale);
      if (q < -32767 || q > 32767) {
        needsKeyframe = true;
        break;
      }
      deltas[j] = static_cast<int16_t>(q);
      nextTracked[j] = prev + (static_cast<double>(deltas[j]) * scale);
    }
  }

  if (needsKeyframe) {
    return EncodeBaselineChunk(currentValues);
  }

  trackedValues_ = std::move(nextTracked);
  samplesSinceKeyframe_++;
  return EncodeDeltaChunk(deltas);
}

void Brt1StreamEncoder::Reset() {
  hasBaseline_ = false;
  trackedValues_.clear();
  samplesSinceKeyframe_ = 0;
}

void Brt1StreamEncoder::ResetDeltaState() {
  hasBaseline_ = false;
  trackedValues_.clear();
  samplesSinceKeyframe_ = 0;
}

Brt1StreamDecoder::Brt1StreamDecoder() = default;

void Brt1StreamDecoder::Feed(const void* data, size_t size) {
  if (data != nullptr && size > 0) {
    buffer_.append(static_cast<const char*>(data), size);
  }
}

void Brt1StreamDecoder::Feed(const std::string& data) {
  buffer_.append(data);
}

Brt1StreamDecoder::StepResult Brt1StreamDecoder::Step(std::unordered_map<std::string, double>& outRecord,
                                                     std::string& outError) {
  outRecord.clear();
  outError.clear();

  if (buffer_.size() < 3) {
    return StepResult::NeedMoreData;
  }

  uint16_t chunkLen = static_cast<uint8_t>(buffer_[0]) |
                      (static_cast<uint16_t>(static_cast<uint8_t>(buffer_[1])) << 8);
  uint8_t chunkType = static_cast<uint8_t>(buffer_[2]);

  if (buffer_.size() < static_cast<size_t>(3 + chunkLen)) {
    return StepResult::NeedMoreData;
  }

  std::string payload = buffer_.substr(3, chunkLen);
  buffer_.erase(0, 3 + chunkLen);

  if (chunkType == static_cast<uint8_t>(Brt1ChunkType::Magic)) {
    if (chunkLen != 4 || payload != "BRT1") {
      outError = "Invalid magic bytes in BRT1 stream: expected 'BRT1'";
      return StepResult::Error;
    }
    magicValidated_ = true;
    return StepResult::MagicReceived;
  }

  if (!magicValidated_) {
    outError = "Stream missing initial Magic chunk (Type 0x01)";
    return StepResult::Error;
  }

  if (chunkType == static_cast<uint8_t>(Brt1ChunkType::MetadataJson)) {
    try {
      auto j = nlohmann::json::parse(payload);
      if (j.contains("metadata") && j["metadata"].is_object()) {
        const auto& m = j["metadata"];
        metadata_.trackName = m.value("track_name", m.value("Track name", ""));
        metadata_.date = m.value("date", m.value("Date", ""));
        metadata_.time = m.value("time", m.value("Time", ""));
        metadata_.driverName = m.value("driver_name", m.value("Driver name", ""));
        metadata_.league = m.value("league", m.value("League", ""));
        metadata_.className = m.value("class_name", m.value("Class", m.value("class", "")));
        metadata_.sessionName = m.value("session_name", m.value("Session", m.value("session", "")));
        metadata_.kartNumber = m.value("kart_number", m.value("Kart number", ""));
      }
      channels_.clear();
      if (j.contains("channels") && j["channels"].is_array()) {
        for (const auto& item : j["channels"]) {
          Brt1Channel ch;
          ch.name = item.value("name", "");
          ch.scale = item.value("scale", 1.0);
          channels_.push_back(ch);
        }
      }
      currentValues_.assign(channels_.size(), std::numeric_limits<double>::quiet_NaN());
      return StepResult::MetadataReceived;
    } catch (const std::exception& e) {
      outError = std::string("Failed to parse Metadata JSON chunk: ") + e.what();
      return StepResult::Error;
    }
  }

  if (chunkType == static_cast<uint8_t>(Brt1ChunkType::Baseline)) {
    size_t M = channels_.size();
    if (chunkLen != M * sizeof(double)) {
      outError = "Baseline chunk length mismatch: expected " + std::to_string(M * sizeof(double)) +
                 ", got " + std::to_string(chunkLen);
      return StepResult::Error;
    }

    currentValues_.resize(M);
    std::memcpy(currentValues_.data(), payload.data(), M * sizeof(double));

    for (size_t i = 0; i < M; ++i) {
      outRecord[channels_[i].name] = currentValues_[i];
    }
    return StepResult::BaselineReceived;
  }

  if (chunkType == static_cast<uint8_t>(Brt1ChunkType::Delta)) {
    size_t M = channels_.size();
    if (chunkLen < M * sizeof(int16_t)) {
      outError = "Delta chunk length mismatch: expected at least " + std::to_string(M * sizeof(int16_t)) +
                 ", got " + std::to_string(chunkLen);
      return StepResult::Error;
    }

    std::vector<int16_t> deltas(M);
    std::memcpy(deltas.data(), payload.data(), M * sizeof(int16_t));

    for (size_t i = 0; i < M; ++i) {
      int16_t d = deltas[i];
      if (d == BRT1_NAN_DELTA) {
        currentValues_[i] = std::numeric_limits<double>::quiet_NaN();
      } else {
        if (std::isnan(currentValues_[i])) {
          outError = "Delta received for channel '" + channels_[i].name + "' without valid baseline";
          return StepResult::Error;
        }
        currentValues_[i] += static_cast<double>(d) * channels_[i].scale;
      }
      outRecord[channels_[i].name] = currentValues_[i];
    }
    return StepResult::SampleDecoded;
  }

  if (chunkType == static_cast<uint8_t>(Brt1ChunkType::EndOfStream)) {
    return StepResult::EndOfStream;
  }

  // Unknown chunk type - skip gracefully
  return StepResult::NeedMoreData;
}

} // namespace race_tools

