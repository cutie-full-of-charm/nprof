import time

from nprof import phase, profiling_start, profiling_stop, start, stop

profiling_start(
    output="out/quickstart",
    interval=0.005,         # CPU + RSS: 5 мс
    memory_interval=0.05,   # USS + PSS: 50 мс
    gpu_interval=0.1,       # GPU:       100 мс
    gpu="none",             # можно попробовать "auto" для первого доступного GPU
    include_children=True,
    sampler="process",      # также поддерживается thread, но настоятельно рекомендуется именно process
    html=True,
    description="## Быстрый старт\nЗдесь могут быть версии драйверов и ПО, параметры тестируемого кода, дата и время, etc",
)

try:
    start()                         # начало интересующего окна
    try:
        with phase("pipeline"):     # phase работают инклюзивно: включают в себя вложенные интервалы
            with phase("load"):
                time.sleep(0.4)
            with phase("compute"):
                result = sum(i*i for i in range(4_000_000))
                with phase("postprocess"):
                    time.sleep(0.2)
            with phase("save"):
                time.sleep(0.3)
    finally:
        stop()                       # конец окна
finally:
    report = profiling_stop()        # остановить сборщик и записать отчёты

print(report["selection"]["duration_s"])
print(report["phase_summaries"]["pipeline"]["metrics"]["rss_mib"]["max"])
