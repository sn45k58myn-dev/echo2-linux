/* Connect the 0.16.8 render producer and capture-side reference consumer. */
#include "aec_reference.h"
#include "adapter/voice_reference.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    struct le_aec_reference_sender sender;
    struct le_voice_reference consumer;
    int16_t stereo[960 * 2];
    int16_t reference[320];
    char socket_path[sizeof(consumer.socket_path)];

    assert(argc == 2);
    int path_size = snprintf(socket_path, sizeof(socket_path), "%s/%s",
                             argv[1], LE_AEC_REFERENCE_SOCKET);
    assert(path_size > 0 && (size_t)path_size < sizeof(socket_path));
    assert(le_voice_reference_open(&consumer, socket_path) == 0);
    assert(le_aec_reference_init(&sender, argv[1]) == 0);
    for (size_t i = 0; i < 960; ++i) {
        stereo[2 * i] = 4000;
        stereo[2 * i + 1] = -4000;
    }

    for (int sequence = 0; sequence < 3; ++sequence) {
        assert(le_aec_reference_publish(&sender, stereo, 960, 2, 1) == 0);
        assert(le_voice_reference_drain(&consumer) == 1);
        assert(consumer.count == 320);
        le_voice_reference_read(&consumer, reference, 320);
        /* Check after the resampler's initial transient. The playback owner
         * publishes its first channel even when the second is its inverse. */
        if (sequence > 0) {
            for (size_t i = 0; i < 320; ++i)
                assert(abs((int)reference[i] - 4000) <= 8);
        }
    }
    assert(consumer.packets == 3 && consumer.discontinuities == 0);
    assert(consumer.malformed_packets == 0 && consumer.underflows == 0);
    le_voice_reference_close(&consumer);
    assert(le_aec_reference_publish(&sender, stereo, 960, 2, 1) == 1);
    le_aec_reference_close(&sender);
    puts("audio link: 3 render packets, 2880 stereo frames to 960 mono reference samples; receiver disconnect handled");
    return 0;
}
