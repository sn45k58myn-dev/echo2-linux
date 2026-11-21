// Exercise a DATA capacity rejection arriving during a later PROGRESS request.
#include "engine_sink.h"
#include "sendspin_engine_fixture.h"
#include <array>

using namespace libreecho::sendspin;
using namespace libreecho::sendspin::test;

int main() {
    constexpr unsigned frames = 512;
    std::array<int16_t, frames * 2> pcm{};
    fill_pattern(pcm.data(), frames);
    const auto *bytes = reinterpret_cast<const uint8_t *>(pcm.data());
    EngineServer server;
    const auto path = unique_socket_path("backpressure");
    CHECK(server.start(path, frames), "start real bounded sink");
    EngineSink sink;
    CHECK(sink.connect(path, 1000) == SinkResult::Ok && sink.open(1, 1000) == SinkResult::Ok,
          "open generation");
    CHECK(sink.on_audio_write(bytes, sizeof(pcm), 1000) == sizeof(pcm), "fill queue");
    server.pause();
    CHECK(sink.on_audio_write(bytes, sizeof(pcm), 5) == 0, "DATA reply missed original deadline");
    server.resume();
    EngineSinkTiming timing{};
    CHECK(sink.progress(&timing, 1000) == SinkResult::Ok, "poll consumes late capacity refusal and its own reply");
    CHECK(sink.accepted_frames() == frames, "refused DATA did not advance cursor");
    server.with_sink([&](le_audio_sink *backend) {
        std::array<int16_t, frames * 2> output{};
        le_audio_sink_progress cursor{};
        CHECK(le_audio_sink_get_progress(backend, &cursor) == LE_AUDIO_SINK_OK, "read physical queue");
        uint64_t first = 0;
        CHECK(le_audio_sink_render(backend, output.data(), frames, &first) == frames && output == pcm,
              "first audio block intact");
        CHECK(le_audio_sink_commit(backend, cursor.epoch, cursor.generation, frames) == LE_AUDIO_SINK_OK,
              "space available");
    });
    CHECK(sink.on_audio_write(bytes, sizeof(pcm), 1000) == sizeof(pcm), "retry succeeds after space is freed");
    CHECK(sink.accepted_frames() == 2 * frames, "two accepted blocks accounted exactly once");
    return g_failures ? 1 : 0;
}
