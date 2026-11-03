# Threading vs Multiprocessing in Python: Choosing the Right Concurrency Model and Benchmarking It

## Introduction

In the world of Python, the words *thread* and *process* are tossed around a lot, but understanding when to reach for `threading` and when to spin up a `multiprocessing` pool can feel like navigating a maze of GIL‑related myths, new free‑threading experiments, and performance quirks. In this guide we’ll demystify the core differences, walk through reproducible benchmarks on modern CPython 3.13 (including its experimental free‑threading mode), and show you concrete code you can drop into your own projects to make an informed choice for CPU‑bound and I/O‑bound workloads. By the end, you’ll have a clear mental model, a ready‑to‑run benchmark suite, and best‑practice recommendations that keep your code fast and maintainable.


![High-level Python concurrency architecture diagram](../images/treading_diagram_1.png)

*Threading vs Multiprocessing architecture in Python with GIL*


## Conceptual Foundations: Threads, Processes, and the GIL

### Diagram: Threading vs Multiprocessing Model

```mermaid
graph TD
    subgraph Process_A[Process A (GIL A)]
        A_Heap[(Heap A)]
    end
    subgraph Process_B[Process B (GIL B)]
        B_Heap[(Heap B)]
    end
    subgraph Process_C[Process C (GIL C)]
        C_Heap[(Heap C)]
    end
    GIL[Global Interpreter Lock (GIL)]
    Thread1[OS Thread 1]
    Thread2[OS Thread 2]
    Thread1 -->|shares| GIL
    Thread2 -->|shares| GIL
    GIL -->|serializes bytecode| Process_A
    GIL -->|serializes bytecode| Process_B
    GIL -->|serializes bytecode| Process_C
    style GIL fill:#f9f,stroke:#333,stroke-width:2px
    style Thread1 fill:#bbf,stroke:#333,stroke-width:1px
    style Thread2 fill:#bbf,stroke:#333,stroke-width:1px
```



In CPython the **Global Interpreter Lock (GIL)** is a mutex that protects access to Python objects, allowing only one native thread to execute bytecode at a time.  
`threading` creates OS threads that share the same address space; the GIL serializes bytecode execution, but I/O‑bound tasks can still overlap because the lock is released while waiting on system calls.  
`multiprocessing` spawns separate interpreter instances, each with its own GIL and private heap. Processes communicate via pickled objects sent over pipes or shared memory, incurring serialization overhead.

```
+----------------------+          +----------------------+          +----------------------+
|  Process A (GIL A)   |          |  Process B (GIL B)   |          |  Process C (GIL C)   |
|  Heap A              |          |  Heap B              |          |  Heap C              |
+----------------------+          +----------------------+          +----------------------+
        ^                               ^                               ^
        |                               |                               |
        +-----------+-------------------+-------------------+-----------+
                    |                               |
               OS Thread 1 (shared GIL)         OS Thread 2 (shared GIL)
```

**Threading vs. Multiprocessing**

| Feature | `threading` | `multiprocessing` |
|---------|-------------|-------------------|
| Memory | Shared heap | Separate heaps |
| GIL | Single GIL | One GIL per process |
| Overhead | Low | High (fork/serialization) |
| Use‑case | I/O bound, lightweight | CPU bound, isolation |

The new **free‑threading** experiment in Python 3.13 removes the GIL entirely when building with the `--enable-free-threading` flag. Each interpreter thread runs independently, but the runtime still enforces fine‑grained locks around CPython internals. Benchmarks show that, without the extra build options, 3.12 and 3.13 perform similarly, and `multiprocessing` remains slower than single‑threaded execution due to inter‑process communication costs.

```
[Free‑Threading]          [Traditional GIL]
   |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
```


## Hands‑On Benchmark Suite: CPU‑Bound vs I/O‑Bound Workloads

Below is a self‑contained script that measures the execution time of a CPU‑bound Fibonacci recursion and an I/O‑bound HTTP download task.  
The same workload is run in three modes:

| Mode | Python 3.13 | Python 3.12 |
|------|-------------|-------------|
| Single‑threaded | ✅ | ✅ |
| `threading.Thread` | ✅ | ✅ |
| `multiprocessing.Process` | ✅ | ✅ |

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import threading
import multiprocessing
import urllib.request
from pathlib import Path

# ------------------------------------------------------------------
# 1. CPU‑bound task – Fibonacci recursion
# ------------------------------------------------------------------
def fib(n: int) -> int:
    return n if n <= 1 else fib(n-1) + fib(n-2)

def cpu_task(iterations: int = 20):
    for _ in range(iterations):
        fib(30)  # 30th Fibonacci number

# ------------------------------------------------------------------
# 2. I/O‑bound task – concurrent HTTP downloads
# ------------------------------------------------------------------
URLS = [
    "https://httpbin.org/delay/1",
    "https://httpbin.org/delay/1",
    "https://httpbin.org/delay/1",
    "https://httpbin.org/delay/1",
]

def download(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:
        r.read()  # discard content

def io_task():
    for url in URLS:
        download(url)

# ------------------------------------------------------------------
# 3. Benchmark harness
# ------------------------------------------------------------------
def run_single(func, *args):
    start = time.perf_counter()
    func(*args)
    return time.perf_counter() - start

def run_threaded(func, *args):
    thread = threading.Thread(target=func, args=args)
    start = time.perf_counter()
    thread.start()
    thread.join()
    return time.perf_counter() - start

def run_multiprocess(func, *args):
    proc = multiprocessing.Process(target=func, args=args)
    start = time.perf_counter()
    proc.start()
    proc.join()
    return time.perf_counter() - start

# ------------------------------------------------------------------
# 4. Main
# ------------------------------------------------------------------
if __name__ == "__main__":
    tasks = {
        "CPU‑bound": cpu_task,
        "I/O‑bound": io_task,
    }

    results = []

    for name, task in tasks.items():
        for mode, runner in [("Single‑threaded", run_single),
                             ("Threading", run_threaded),
                             ("Multiprocessing", run_multiprocess)]:
            elapsed = runner(task)
            results.append((name, mode, elapsed))

    # Pretty‑print results
    print("\nBenchmark results (seconds)")
    print(f"{'Task':<12} {'Mode':<18} {'Time':>8}")
    for task, mode, t in results:
        print(f"{task:<12} {mode:<18} {t:>8.3f}")
```

### Toggling the free‑threading build flag in Python 3.13

Python 3.13 ships a *free‑threading* build that removes the Global Interpreter Lock (GIL).  
To enable it:

```bash
# Rebuild from source
./configure --enable-free-threading
make -j$(nproc)
sudo make altinstall
```

After installation, verify with:

```bash
python3.13 -c "import sys; print(sys.flags.free_threading)"
```

The flag is `True` when the build is free‑threading.

### Interpreting the results

| Observation | Explanation |
|-------------|-------------|
| CPU‑bound: Threading ≈ Single‑threaded | GIL serialises bytecode; threads add context‑switch overhead. |
| CPU‑bound: Multiprocessing ≈ Single‑threaded | Process start/join overhead dominates; no GIL contention. |
| I/O‑bound: Threading < Single‑threaded | Threads overlap network waits; GIL is released during I/O. |
| I/O‑bound: Multiprocessing < Threading | Processes avoid GIL and have less context‑switch cost, but IPC adds a small penalty. |
| Free‑threading build: Threading ≈ Multiprocessing for CPU‑bound | With GIL removed, true parallelism is achieved, but process overhead still exists. |

**Overheads**  
* **Threading**: lightweight but still pays the GIL cost for CPU work and context‑switches.  
* **Multiprocessing**: heavier start‑up and IPC, but scales with CPU cores for CPU‑bound tasks and eliminates GIL for I/O‑bound work.  

Use the free‑threading build only when you need true parallelism on a single interpreter instance; otherwise, the standard build is simpler and often sufficient.


![Benchmark data pipeline flowchart for threading and multiprocessing](../images/treading_diagram_2.png)

*Sequence flow of tasks through ThreadPoolExecutor and ProcessPoolExecutor during benchmarking*


## Real‑World Use Cases: When to Prefer Threads or Processes

| Scenario | Workload | Preferred Model | Reasoning |
|----------|----------|-----------------|-----------|
| Web Scraping | I/O‑bound, many HTTP requests | **Threads** | GIL is released during network I/O, so a pool of threads scales well without inter‑process overhead. |
| Data‑Science Pipelines | CPU‑bound transformations, heavy NumPy/Pandas ops | **Processes** | Parallelism avoids the GIL, each process gets a full interpreter and memory space; pickled data is transferred only once. |
| Real‑Time Sensor Fusion (Biomechanics) | Mixed I/O (serial ports) and CPU‑heavy filtering | **Hybrid** (thread for I/O, process for computation) | Threads collect data quickly; a worker process performs Kalman filtering, keeping UI responsive. |

### 1. Web Scraping – Threaded

```python
import threading, requests

def fetch(url):
    r = requests.get(url)
    print(f"{url} -> {r.status_code}")

urls = ["https://example.com"] * 10
threads = [threading.Thread(target=fetch, args=(u,)) for u in urls]
for t in threads: t.start()
for t in threads: t.join()
```

*The GIL is released during `requests.get`, so dozens of threads can run concurrently. No IPC overhead, minimal memory footprint.*

### 2. Data‑Science Pipeline – Multiprocessing

```python
import multiprocessing as mp
import pandas as pd
import numpy as np

def transform(df_chunk):
    df_chunk["norm"] = np.linalg.norm(df_chunk.values, axis=1)
    return df_chunk

if __name__ == "__main__":
    df = pd.read_csv("large_dataset.csv")
    chunks = np.array_split(df, mp.cpu_count())
    with mp.Pool() as pool:
        results = pool.map(transform, chunks)
    df = pd.concat(results)
```

*Each worker runs in its own process, bypassing the GIL. Pickled chunks are sent once; the cost is outweighed by the speedup on CPU‑heavy work.*

### 3. Real‑Time Sensor Fusion – Hybrid

```python
import threading, multiprocessing as mp
import time
import numpy as np

def read_sensor(q):
    while True:
        data = np.random.rand(10)  # mock sensor frame
        q.put(data)
        time.sleep(0.01)

def filter_worker(q_in, q_out):
    while True:
        frame = q_in.get()
        filtered = frame * 0.9  # placeholder filter
        q_out.put(filtered)

if __name__ == "__main__":
    q1, q2 = mp.Queue(), mp.Queue()
    t = threading.Thread(target=read_sensor, args=(q1,))
    p = mp.Process(target=filter_worker, args=(q1, q2))
    t.start(); p.start()
    t.join(); p.join()
```

*The reader thread keeps up with the high‑rate serial port, while the process applies CPU‑intensive filtering without blocking the I/O path.*

---

These snippets illustrate when the GIL’s release pattern, IPC overhead, and memory isolation dictate the optimal concurrency model. Refer to the *State of Python 3.13 Performance* and the *Threading vs Multiprocessing* guide for deeper performance insights.

## Best Practices, Pitfalls, and the Future of Free‑Threading  

| Guideline | Action | Why it matters |
|-----------|--------|----------------|
| **Use thread‑safe primitives** | Prefer `threading.Lock`, `RLock`, `Semaphore`, and `Condition` over manual flag toggling. | Guarantees atomicity and prevents race conditions without manual bookkeeping. |
| **Avoid shared mutable state** | Design functions to be stateless or use immutable objects. | Reduces the need for locks and simplifies reasoning about concurrency. |
| **Pickle‑safe objects** | Ensure any object passed to `multiprocessing` is picklable (no open file handles, threads, or C extensions). | Pickling errors abort processes and waste time; they also expose hidden bugs. |
| **Choose the right start method** | Use `spawn` or `forkserver` on Windows; `fork` only on POSIX if you’re sure of process safety. | `fork` can inherit locks in an inconsistent state, leading to deadlocks. |
| **Explicitly manage process lifecycles** | Call `join()` promptly and handle `KeyboardInterrupt` gracefully. | Prevents orphaned processes and resource leaks. |
| **Profile with realistic workloads** | Use `timeit`, `cProfile`, and external tools like `codspeed` to compare threading vs. multiprocessing. | Benchmarks reveal the true cost of inter‑process communication, as shown in the *State of Python 3.13 Performance* article. |
| **Prepare for free‑threading** | Refactor code to be thread‑safe before 3.13, then drop the `threading` guard in critical sections. | Free‑threading removes the GIL, so any thread‑unsafe code will crash or misbehave. |
| **Adopt ISO/TC 159/SC 3 standards** | Use the walking‑speed measurement protocol as a model for rigorous benchmarking. | Demonstrates how external standards can guide reproducible performance tests. |

### Quick Checklist

- [ ] All shared data protected by a lock or made immutable.  
- [ ] No non‑picklable objects in `multiprocessing` arguments.  
- [ ] Correct start method set for the target OS.  
- [ ] Processes joined or terminated in a `finally` block.  
- [ ] Benchmarks run on production‑like data and measured with `codspeed`.  
- [ ] Code reviewed for thread‑safety before upgrading to 3.13+.  

### Resources

- [Python 3.13 free‑threading announcement](https://codspeed.io/blog/state-of-python-3-13-performance-free-threading)  
- [Official threading documentation](https://docs.python.org/3/library/threading.html)  
- [Official multiprocessing documentation](https://docs.python.org/3/library/multiprocessing.html)  
- [ISO/TC 159/SC 3 walking‑speed measurement standard](https://www.iso.org/standard/xxxxxx.html) (replace with the actual URL)

By following these guidelines, you’ll write robust, future‑proof concurrent code that performs well both today and when free‑threading becomes the default in Python 3.13+.


![Production deployment topology for Python service with threading or multiprocessing](../images/treading_diagram_3.png)

*Cloud deployment layout showing Kubernetes pods running either multi-threaded or multi-process workers*


## Conclusion

Threading in CPython is constrained by the Global Interpreter Lock, which guarantees that only one native thread executes Python bytecode at any moment. This design makes `threading` cheap in terms of memory and context‑switch overhead, but it limits true parallelism to I/O‑bound workloads where the GIL is released during system calls. In contrast, `multiprocessing` sidesteps the GIL by launching separate interpreter processes, each with its own heap and lock, enabling CPU‑bound tasks to run concurrently at the cost of higher startup latency and the need to serialize data between processes.

The upcoming free‑threading experiment in Python 3.13 removes the monolithic GIL, allowing multiple interpreter threads to run independently while still protecting internal structures with fine‑grained locks. Early benchmarks show modest gains for I/O‑heavy code but retain the classic trade‑off: multiprocessing remains slower than a single process due to inter‑process communication, whereas free‑threaded builds promise a more scalable path for CPU‑intensive Python without the heavy process‑level overhead.

## References & Further Reading

- [State of Python 3.13 Performance: Free-Threading](https://codspeed.io/blog/state-of-python-3-13-performance-free-threading)
- [Threading vs Multiprocessing in Python: A Comprehensive Guide](https://medium.com/@arjunprakash027/threading-vs-multiprocessing-in-python-a-comprehensive-guide-cae3ce0ca6c1)
- [Multiprocessing vs Threading Python](https://stackoverflow.com/questions/3044580/multiprocessing-vs-threading-python)
- [(PDF) A biomechanics dataset of healthy human walking at ...](https://www.researchgate.net/publication/365444153_A_biomechanics_dataset_of_healthy_human_walking_at_various_speeds_step_lengths_and_step_widths)
- [ISO/TC 159/SC 3 - Anthropometry and biomechanics](https://standards.iteh.ai/catalog/tc/iso/54d481d8-3cac-4f31-b4d3-64e0d0897e12/iso-tc-159-sc-3)
- [Standards Documents - International Society of Biomechanics](https://isbweb.org/members/29-standards-documents)


### Diagram: Concurrency Model Decision Flow

```mermaid
graph TD
    Start[Start] --> Q1{Is workload CPU‑bound?}
    Q1 -- Yes --> MP[Use multiprocessing]
    MP --> FT{Free‑threading build available?}
    FT -- Yes --> FT_Use[Use free‑threading (no GIL)]
    FT -- No --> MP_Use[Spawn multiple processes]
    Q1 -- No --> Q2{Is workload I/O‑bound?}
    Q2 -- Yes --> TH[Use threading (shared memory)]
    Q2 -- No --> SY[Use simple single‑threaded execution]
    style Start fill:#efe,stroke:#333,stroke-width:2px
    style MP fill:#ddf,stroke:#333,stroke-width:1px
    style TH fill:#ddf,stroke:#333,stroke-width:1px
    style FT_Use fill:#cfc,stroke:#333,stroke-width:1px
    style MP_Use fill:#cfc,stroke:#333,stroke-width:1px
```

