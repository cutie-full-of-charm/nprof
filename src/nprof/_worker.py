import json
import os
import sys
import threading

from . import _Sampler


def main():
    (
        pid,
        interval,
        memory_interval,
        gpu_interval,
        gpu,
        children,
        start_ns,
        output,
    ) = sys.argv[1:]
    sampler = _Sampler(
        int(pid),
        float(interval),
        float(memory_interval),
        float(gpu_interval),
        gpu,
        children == "1",
        int(start_ns),
        excluded_pid=os.getpid(),
    )
    stopped = threading.Event()
    ready = threading.Event()

    def watch_stdin():
        sys.stdin.readline()
        stopped.set()

    thread = threading.Thread(target=sampler.run, args=(stopped, ready), daemon=True)
    thread.start()
    ready.wait()
    if sampler.error or not sampler.rows:
        print(
            json.dumps({"ready": False, "error": sampler.error or "no sample"}),
            flush=True,
        )
        stopped.set()
        thread.join()
        sampler.close()
        sys.exit(1)
    print(
        json.dumps(
            {
                "ready": True,
                "first_tick": sampler.rows[0]["_tick"],
                "gpu": sampler.gpu.info,
                "gpu_error": sampler.gpu.error,
            }
        ),
        flush=True,
    )
    threading.Thread(target=watch_stdin, daemon=True).start()
    thread.join()
    sampler.close()
    if sampler.error:
        print(json.dumps({"finished": False, "error": sampler.error}), flush=True)
        sys.exit(1)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(sampler.rows, f, ensure_ascii=False)
    print(json.dumps({"finished": True, "samples": len(sampler.rows)}), flush=True)


if __name__ == "__main__":
    main()
