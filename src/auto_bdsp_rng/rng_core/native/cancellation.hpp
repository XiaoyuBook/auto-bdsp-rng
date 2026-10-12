#pragma once
#include <atomic>
#include <stdexcept>

struct SearchCancelled : std::runtime_error {
    SearchCancelled() : std::runtime_error("Search cancelled") {}
};

class SearchCancellation {
    std::atomic<bool> cancelled_{false};
public:
    void cancel() { cancelled_.store(true, std::memory_order_relaxed); }
    bool is_cancelled() const { return cancelled_.load(std::memory_order_relaxed); }
    void check() const { if (is_cancelled()) throw SearchCancelled(); }
};

inline void check_cancelled(const SearchCancellation* cancellation) {
    if (cancellation) cancellation->check();
}
