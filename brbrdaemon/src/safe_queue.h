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
#include <memory>
#include <map>
#include <limits>
#include <algorithm>

namespace race_tools {

// Thread-safe multi-reader queue with reader tracking and automatic pruning.
// Items are retained in the internal buffer only until all active readers have
// consumed them, ensuring O(1) steady-state memory usage.
template <typename T>
class SafeQueue : public std::enable_shared_from_this<SafeQueue<T>> {
public:
  class Reader {
  public:
    Reader() : queue_(nullptr), readerId_(0) {}

    explicit Reader(std::shared_ptr<SafeQueue<T>> queue) : queue_(std::move(queue)) {
      if (queue_) {
        readerId_ = queue_->RegisterReader();
      }
    }

    ~Reader() {
      if (queue_) {
        queue_->UnregisterReader(readerId_);
      }
    }

    Reader(const Reader&) = delete;
    Reader& operator=(const Reader&) = delete;

    Reader(Reader&& other) noexcept
        : queue_(std::move(other.queue_)), readerId_(other.readerId_) {
      other.queue_ = nullptr;
    }

    Reader& operator=(Reader&& other) noexcept {
      if (this != &other) {
        if (queue_) {
          queue_->UnregisterReader(readerId_);
        }
        queue_ = std::move(other.queue_);
        readerId_ = other.readerId_;
        other.queue_ = nullptr;
      }
      return *this;
    }

    // Blocking pop. Returns false when queue is shutdown and all available items have been read.
    bool Pop(T& out) {
      if (!queue_) return false;
      return queue_->PopInternal(readerId_, out);
    }

    // Non-blocking pop. Returns true if an item was popped, false if empty or shutdown with no items.
    bool TryPop(T& out) {
      if (!queue_) return false;
      return queue_->TryPopInternal(readerId_, out);
    }

    bool IsEmpty() const {
      if (!queue_) return true;
      return queue_->IsEmptyInternal(readerId_);
    }

  private:
    friend class SafeQueue<T>;
    std::shared_ptr<SafeQueue<T>> queue_;
    size_t readerId_ = 0;
  };

  explicit SafeQueue(size_t capacity = 50000)
      : capacity_(capacity), baseIndex_(0), droppedCount_(0), shutdown_(false) {}

  ~SafeQueue() {
    SignalShutdown();
  }

  SafeQueue(const SafeQueue&) = delete;
  SafeQueue& operator=(const SafeQueue&) = delete;

  Reader reader() {
    return Reader(this->shared_from_this());
  }

  void Push(const T& item) {
    std::lock_guard<std::mutex> lock(mutex_);
    deque_.push_back(item);
    EnforceCapacityLocked();
    cv_.notify_all();
  }

  void Push(T&& item) {
    std::lock_guard<std::mutex> lock(mutex_);
    deque_.push_back(std::move(item));
    EnforceCapacityLocked();
    cv_.notify_all();
  }

  void SignalShutdown() {
    std::lock_guard<std::mutex> lock(mutex_);
    shutdown_ = true;
    cv_.notify_all();
  }

  void Clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    deque_.clear();
    baseIndex_ = 0;
    for (auto& pair : readerIndices_) {
      pair.second = baseIndex_;
    }
  }

  size_t Size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return deque_.size();
  }

  size_t DroppedCount() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return droppedCount_;
  }

private:
  mutable std::mutex mutex_;
  std::condition_variable cv_;
  std::deque<T> deque_;
  size_t capacity_ = 0; // 0 = unlimited
  size_t baseIndex_ = 0;
  size_t nextReaderId_ = 0;
  size_t droppedCount_ = 0;
  std::map<size_t, size_t> readerIndices_;
  bool shutdown_ = false;

  size_t RegisterReader() {
    std::lock_guard<std::mutex> lock(mutex_);
    size_t readerId = nextReaderId_++;
    readerIndices_[readerId] = baseIndex_ + deque_.size();
    return readerId;
  }

  void UnregisterReader(size_t readerId) {
    std::lock_guard<std::mutex> lock(mutex_);
    readerIndices_.erase(readerId);
    PruneLocked();
  }

  bool HasItemsLocked(size_t readerId) const {
    auto it = readerIndices_.find(readerId);
    if (it == readerIndices_.end()) return false;
    return it->second < baseIndex_ + deque_.size();
  }

  bool IsEmptyInternal(size_t readerId) const {
    std::lock_guard<std::mutex> lock(mutex_);
    return !HasItemsLocked(readerId);
  }

  bool PopInternal(size_t readerId, T& out) {
    std::unique_lock<std::mutex> lock(mutex_);
    cv_.wait(lock, [this, readerId]() {
      return HasItemsLocked(readerId) || shutdown_;
    });

    if (!HasItemsLocked(readerId) && shutdown_) {
      return false;
    }

    auto it = readerIndices_.find(readerId);
    if (it == readerIndices_.end()) {
      return false;
    }

    // Clamp reader position in case capacity enforcement pushed baseIndex_ ahead
    if (it->second < baseIndex_) {
      it->second = baseIndex_;
    }

    if (it->second >= baseIndex_ + deque_.size()) {
      return false;
    }

    size_t localIndex = it->second - baseIndex_;
    out = deque_[localIndex];
    it->second++;
    PruneLocked();
    return true;
  }

  bool TryPopInternal(size_t readerId, T& out) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!HasItemsLocked(readerId)) {
      return false;
    }

    auto it = readerIndices_.find(readerId);
    if (it == readerIndices_.end()) {
      return false;
    }

    if (it->second < baseIndex_) {
      it->second = baseIndex_;
    }

    if (it->second >= baseIndex_ + deque_.size()) {
      return false;
    }

    size_t localIndex = it->second - baseIndex_;
    out = deque_[localIndex];
    it->second++;
    PruneLocked();
    return true;
  }

  void EnforceCapacityLocked() {
    if (capacity_ > 0 && deque_.size() > capacity_) {
      size_t overflow = deque_.size() - capacity_;
      for (size_t i = 0; i < overflow; ++i) {
        deque_.pop_front();
      }
      baseIndex_ += overflow;
      droppedCount_ += overflow;
      for (auto& pair : readerIndices_) {
        if (pair.second < baseIndex_) {
          pair.second = baseIndex_;
        }
      }
    }
  }

  void PruneLocked() {
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
      size_t itemsToRemove = (std::min)(minGlobalIndex - baseIndex_, deque_.size());
      for (size_t i = 0; i < itemsToRemove; ++i) {
        deque_.pop_front();
      }
      baseIndex_ += itemsToRemove;
    }
  }
};

} // namespace race_tools
