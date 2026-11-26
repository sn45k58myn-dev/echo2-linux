FROM debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ git libc6-dev python3 && rm -rf /var/lib/apt/lists/*
WORKDIR /lab
COPY . .
RUN gcc -O2 -Wall -Wextra test-route.c -o /tmp/test-route && \
    gcc -O2 -Wall -Wextra paired_capture.c -o /tmp/capture-test && \
    g++ -std=c++17 -O2 -Wall -Wextra -pthread router.cpp -o /tmp/router
CMD ["sh", "-ec", "/tmp/test-route && python3 test-router.py /tmp/router"]
