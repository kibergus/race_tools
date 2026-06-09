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

#include <deque>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <map>
#include <limits>
#include <algorithm>

namespace race_tools {

// Thread-Safe Multi-Reader Queue for Asynchronous Data Streaming
template <typename T>
class SafeQueue {
public:
  class Reader {
  public:
    Reader(SafeQueue* queue) : queue_(queue) {
      if (queue_) {
        readerId_ = queue_->RegisterReader();
      }
    }

    ~Reader() {
      if (queue_) {
        queue_->UnregisterReader(readerId_);
      }
    }

    // Disable copy
    Reader(const Reader&) = delete;
    Reader& operator=(const Reader&) = delete;

    // Enable move
    Reader(Reader&& other) noexcept
        : queue_(other.queue_), readerId_(other.readerId_) {
      other.queue_ = nullptr;
    }

    Reader& operator=(Reader&& other) noexcept {
      if (this != &other) {
        if (queue_) {
          queue_->UnregisterReader(readerId_);
        }
        queue_ = other.queue_;
        readerId_ = other.readerId_;
        other.queue_ = nullptr;
      }
      return *this;
    }

    bool Pop(T& item) {
      if (!queue_)
        return false;
      return queue_->Pop(readerId_, item);
    }

  private:
    friend class SafeQueue;
    SafeQueue* queue_ = nullptr;
    size_t readerId_ = 0;
  };

  SafeQueue() = default;
  ~SafeQueue() = default;

  // Disable copy
  SafeQueue(const SafeQueue&) = delete;
  SafeQueue& operator=(const SafeQueue&) = delete;

  Reader reader() {
    return Reader(this);
  }

  void Push(const T& item) {
    std::lock_guard<std::mutex> lock(mutex_);
    deque_.push_back(item);
    cond_.notify_all();
  }

  void SignalShutdown() {
    shutdown_ = true;
    cond_.notify_all();
  }

  void Clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    deque_.clear();
    baseIndex_ = 0;
    for (auto& pair : readerIndices_) {
      pair.second = baseIndex_;
    }
  }

private:
  std::deque<T> deque_;
  size_t baseIndex_ = 0;
  size_t nextReaderId_ = 0;
  std::map<size_t, size_t> readerIndices_;
  std::mutex mutex_;
  std::condition_variable cond_;
  std::atomic<bool> shutdown_{false};

  size_t RegisterReader() {
    std::lock_guard<std::mutex> lock(mutex_);
    size_t readerId = nextReaderId_++;
    readerIndices_[readerId] = baseIndex_ + deque_.size();
    return readerId;
  }

  void UnregisterReader(size_t readerId) {
    std::lock_guard<std::mutex> lock(mutex_);
    readerIndices_.erase(readerId);
    Prune();
  }

  bool Pop(size_t readerId, T& item) {
    std::unique_lock<std::mutex> lock(mutex_);
    cond_.wait(lock, [this, readerId]() {
      return HasItems(readerId) || shutdown_;
    });

    if (!HasItems(readerId) && shutdown_)
      return false;

    size_t localIndex = readerIndices_[readerId] - baseIndex_;
    item = deque_[localIndex];
    readerIndices_[readerId]++;
    Prune();
    return true;
  }

  bool HasItems(size_t readerId) const {
    auto it = readerIndices_.find(readerId);
    if (it == readerIndices_.end())
      return false;
    return it->second < baseIndex_ + deque_.size();
  }

  void Prune() {
    if (readerIndices_.empty()) {
      deque_.clear();
      baseIndex_ = 0;
      return;
    }
    size_t minGlobalIndex = (std::numeric_limits<size_t>::max)();
    for (const auto& pair : readerIndices_) {
      minGlobalIndex = (std::min)(minGlobalIndex, pair.second);
    }
    if (minGlobalIndex > baseIndex_) {
      size_t itemsToRemove = minGlobalIndex - baseIndex_;
      for (size_t i = 0; i < itemsToRemove; ++i) {
        deque_.pop_front();
      }
      baseIndex_ = minGlobalIndex;
    }
  }
};

} // namespace race_tools
