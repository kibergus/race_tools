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

#include <cstdint>
#include <string>
#include <vector>
#include <unordered_map>

namespace race_tools {

enum class Brt1ChunkType : uint8_t {
  Magic = 0x01,
  MetadataJson = 0x02,
  Baseline = 0x03,
  Delta = 0x04,
  EndOfStream = 0xFF
};

inline constexpr int16_t BRT1_NAN_DELTA = static_cast<int16_t>(-32768); // 0x8000

struct Brt1Channel {
  std::string name;
  double scale;
  int csvPrecision = 5;      // decimal places for CSV output, or 0 for integer formatting
  bool isInteger = false;     // format as integer if true
  bool emptyWhenNan = true;   // output empty string "" if NaN
};

struct Brt1SessionMetadata {
  std::string trackName;
  std::string date; // YYYY-MM-DD
  std::string time; // HH:MM:SS
  std::string driverName;
  std::string league;
  std::string className;
  std::string sessionName;
  std::string kartNumber;
  std::string trackConfiguration;
  std::string gameVersion;
  std::string trackOriginLat;
  std::string trackOriginLon;
};

// Returns standard 62 channels for Assetto Corsa / Evo (includes Handbrake)
const std::vector<Brt1Channel>& GetBrBrDaemonBrt1Channels();

// Returns standard CSV column headers derived from GetBrBrDaemonBrt1Channels()
std::string GetBrBrDaemonCsvHeaders();

// Formats a single telemetry sample into a CSV line given timestamp string and values vector
std::string FormatCsvLineFromValues(const std::string& timeStr, const std::vector<double>& values);

// Returns a framed BRT1 chunk: [length (2B le)] + [type (1B)] + [payload]
std::string FrameBrt1Chunk(uint8_t type, const void* payload, uint16_t len);

class Brt1StreamEncoder {
public:
  explicit Brt1StreamEncoder(std::vector<Brt1Channel> channels = GetBrBrDaemonBrt1Channels(),
                             int maxSamplesBetweenKeyframes = 3000);

  // Chunk 0x01: Magic ('BRT1')
  std::string EncodeMagicChunk() const;

  // Chunk 0x02: Metadata JSON
  std::string EncodeMetadataChunk(const Brt1SessionMetadata& metadata) const;

  // Chunk 0x03: Baseline / Keyframe (M contiguous float64_le)
  std::string EncodeBaselineChunk(const std::vector<double>& values);

  // Chunk 0x04: Telemetry Delta (M contiguous int16_le)
  std::string EncodeDeltaChunk(const std::vector<int16_t>& deltas) const;

  // Chunk 0xFF: End of Stream
  std::string EncodeEOSChunk() const;

  // Encodes a single sample. Emits Baseline (0x03) or Delta (0x04) depending on state,
  // NaN transitions, overflows, or keyframe interval.
  std::string EncodeSample(const std::vector<double>& currentValues);

  // Resets full encoder state
  void Reset();

  // Resets delta tracking so the very next sample is emitted as a Baseline chunk
  void ResetDeltaState();

  const std::vector<Brt1Channel>& GetChannels() const { return channels_; }

private:
  std::vector<Brt1Channel> channels_;
  int maxSamplesBetweenKeyframes_;
  bool hasBaseline_ = false;
  std::vector<double> trackedValues_;
  int samplesSinceKeyframe_ = 0;
};

class Brt1StreamDecoder {
public:
  Brt1StreamDecoder();

  void Feed(const void* data, size_t size);
  void Feed(const std::string& data);

  enum class StepResult {
    NeedMoreData,
    MagicReceived,
    MetadataReceived,
    BaselineReceived,
    SampleDecoded,
    EndOfStream,
    Error
  };

  StepResult Step(std::unordered_map<std::string, double>& outRecord, std::string& outError);

  const Brt1SessionMetadata& GetMetadata() const { return metadata_; }
  const std::vector<Brt1Channel>& GetChannels() const { return channels_; }
  const std::vector<double>& GetCurrentValues() const { return currentValues_; }

private:
  std::string buffer_;
  bool magicValidated_ = false;
  Brt1SessionMetadata metadata_;
  std::vector<Brt1Channel> channels_;
  std::vector<double> currentValues_;
};

} // namespace race_tools

