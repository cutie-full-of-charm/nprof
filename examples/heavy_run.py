import hashlib
import math
import time

from nprof import mark, profiling_start, profiling_stop, start, stop


def phase_1_cpu_math_heavy(duration_sec: float = 3.0):
    end_time = time.time() + duration_sec
    acc = 0.0
    while time.time() < end_time:
        for i in range(1, 5000):
            acc += math.sqrt(i) * math.sin(i) / (math.cos(i) + 2.0)


def phase_2_memory_allocation_spike():
    big_data = [f"data_payload_{i}_" * 10 for i in range(1_500_000)]
    # удерживаем память 2.5 секунды
    time.sleep(2.5)
    del big_data


def phase_3_idle_pause(duration_sec: float = 2.0):
    time.sleep(duration_sec)


def phase_4_memory_churn(duration_sec: float = 3.0):
    end_time = time.time() + duration_sec
    while time.time() < end_time:
        temp_chunk = [bytearray(1024) for _ in range(50_000)]
        _ = len(temp_chunk)


def phase_5_cpu_hashing(duration_sec: float = 3.0):
    end_time = time.time() + duration_sec
    payload = b"benchmark_payload_string" * 100
    while time.time() < end_time:
        hashlib.sha256(payload).hexdigest()


if __name__ == "__main__":
    profiling_start(
        output="out/heavy",
        interval=0.005,         # CPU + RSS: 5 мс
        memory_interval=0.05,   # USS + PSS: 50 мс
        gpu_interval=0.1,       # GPU:       100 мс
        gpu="none",             # можно попробовать "auto" для первого доступного GPU
        include_children=True,
        sampler="process",      # также поддерживается thread, но настоятельно рекомендуется именно process
        html=True,              # на выходе получим .html
        json=True,
        md=True,
        description="""## Тяжелый профайл\n
        первая фаза должна сильно нагрузить CPU,
        вторая должна вызвать скачок потребления RAM,
        третья это просто sleep(),
        четвертая грузит GC и создает много аллокаций,
        пятая нагружает cpu через криптографию""", # подобное форматирование сломано, работает только первый перенос строки
    )

    print("heavy run started (it'll take ~13 sec.)")
    start()
    start_total = time.time()

    print("run: phase 1...")
    mark("p1") # Здесь используются mark вместо phase, это может удобнее, но mark не поддерживает вложенность
    phase_1_cpu_math_heavy(3.0)

    print("run: phase 2...")
    mark("p2")
    phase_2_memory_allocation_spike()

    print("run: phase 3...")
    mark("p3")
    phase_3_idle_pause(2.0)

    print("run: phase 4...")
    mark("p4")
    phase_4_memory_churn(3.0)

    print("run: phase 5...")
    mark("p5")
    phase_5_cpu_hashing(3.0)

    stop()

    total_time = round(time.time() - start_total, 2)
    print(f"Execution time={total_time}s")
    summary = profiling_stop()
    print('Report: docs/profiles/a.html')
    print('Duration:', summary['selection']['duration_s'])
    print('Sampling:', summary['sampling'])
