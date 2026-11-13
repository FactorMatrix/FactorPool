#pragma once
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstdlib>
#include <exception>
#include <fstream>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#ifdef __linux__
#include <sched.h>
#endif

namespace fp {
inline std::size_t compute_threads() {
  std::size_t available = std::max(1u, std::thread::hardware_concurrency());
#ifdef __linux__
  cpu_set_t affinity;
  CPU_ZERO(&affinity);
  if (sched_getaffinity(0, sizeof(affinity), &affinity) == 0)
    available = std::min(available, std::max(1, CPU_COUNT(&affinity)) * std::size_t{1});
#endif
  std::ifstream control("/sys/fs/cgroup/cpu.max");
  std::string quota;
  unsigned long long period = 0;
  if (control >> quota >> period && quota != "max" && period) {
    const auto count = std::stoull(quota) / period;
    available = std::min(available, static_cast<std::size_t>(std::max(1ULL, count)));
  } else if (!control) {
    std::ifstream q("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"),
                  p("/sys/fs/cgroup/cpu/cpu.cfs_period_us");
    long long limit = -1, span = 0;
    if (q >> limit && p >> span && limit > 0 && span > 0)
      available = std::min(available, static_cast<std::size_t>(std::max(1LL, limit / span)));
  }
  if (const auto* requested = std::getenv("FACTORPOOL_COMPUTE_THREADS");
      requested && std::string(requested) != "auto") {
    char* end = nullptr;
    errno = 0;
    const auto value = std::strtol(requested, &end, 10);
    if (errno || end == requested || *end || value < 1 || value > 64)
      throw std::invalid_argument("FACTORPOOL_COMPUTE_THREADS must be auto or an integer in 1..64");
    available = std::min(available, static_cast<std::size_t>(value));
  }
  return std::min<std::size_t>(64, available);
}

// Numeric rows/columns are independent. Each worker retains the original
// per-row iteration order; Python publication and ordered reductions stay on
// the caller thread. Small matrices avoid thread creation altogether.
template<class Function>
void parallel_for(std::size_t count, std::size_t work_per_item, Function&& function,
                  bool dynamic = false) {
  if (!count) return;
  const auto limit = compute_threads();
  const auto minimum = 1 + (65536 - 1) / std::max<std::size_t>(1, work_per_item);
  const auto workers = std::min(count, limit);
  if (workers < 2 || count < minimum) {
    for (std::size_t index = 0; index < count; ++index) function(index);
    return;
  }
  std::atomic<bool> failed{false};
  std::atomic<std::size_t> next{0};
  std::exception_ptr error;
  std::mutex mutex;
  const auto run = [&](std::size_t worker) {
    try {
      if (dynamic) {
        while (!failed.load(std::memory_order_relaxed)) {
          const auto index = next.fetch_add(1, std::memory_order_relaxed);
          if (index >= count) break;
          function(index);
        }
      } else {
        const auto begin = worker * count / workers,
                   end = (worker + 1) * count / workers;
        for (auto index = begin; index < end && !failed.load(std::memory_order_relaxed); ++index)
          function(index);
      }
    } catch (...) {
      std::lock_guard guard(mutex);
      if (!error) error = std::current_exception();
      failed.store(true, std::memory_order_relaxed);
    }
  };
  std::vector<std::jthread> threads;
  threads.reserve(workers - 1);
  for (std::size_t worker = 1; worker < workers; ++worker)
    threads.emplace_back([&, worker] { run(worker); });
  run(0);
  threads.clear();
  if (error) std::rethrow_exception(error);
}
} // namespace fp
