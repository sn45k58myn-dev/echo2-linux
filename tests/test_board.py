import json
from pathlib import Path
import subprocess
import tempfile
import unittest


PROBE = Path(__file__).resolve().parents[1] / "src" / "echo2-board"
RADAR_TYPE = b"A7WXQPH584YP\0"
BISCUIT_TYPE = b"A3S5BH2HU6VAYF\0"
MT8163 = b"mediatek,mt8163\0"


class BoardTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def put(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def board(self, compatible, device_type, tree="proc/device-tree"):
        self.put(tree + "/compatible", compatible)
        if device_type is not None:
            self.put(tree + "/idme/device_type_id/value", device_type)

    def probe(self):
        result = subprocess.run(["sh", str(PROBE), "--root", str(self.root)],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("private-test", result.stdout)
        return json.loads(result.stdout)

    def test_recognizes_exact_device_model_with_multistring_soc(self):
        self.board(b"arm,board\0" + MT8163, RADAR_TYPE)
        self.put("proc/device-tree/idme/serial/value", b"private-test")
        self.assertEqual(self.probe()["board"], "radar_puffin")

    def test_supports_sysfs_device_tree(self):
        self.board(MT8163, RADAR_TYPE, tree="sys/firmware/devicetree/base")
        self.assertEqual(self.probe()["board"], "radar_puffin")

    def test_shared_chip_and_hardware_properties_do_not_detect_an_echo_2(self):
        for device_type, expected in [(None, "unknown"), (BISCUIT_TYPE, "other"),
                                      (b"prefix" + RADAR_TYPE, "unknown"),
                                      (RADAR_TYPE + BISCUIT_TYPE, "unknown")]:
            with self.subTest(device_type=device_type):
                self.board(MT8163, device_type)
                self.assertEqual(self.probe()["board"], expected)

    def test_foreign_board_and_conflicting_properties(self):
        self.board(b"brcm,bcm2711\0", None)
        self.assertEqual(self.probe()["board"], "other")
        self.board(b"brcm,bcm2711\0", RADAR_TYPE)
        self.assertEqual(self.probe()["board"], "unknown")

    def test_missing_kernel_interfaces_are_unknown(self):
        data = self.probe()
        self.assertEqual(data["board"], "unknown")
        self.assertIsNone(data["alsa"]["cards"])
        self.assertIsNone(data["alsa"]["array_pcm"])
        self.assertIsNone(data["input"]["mute_key"])
        self.assertIsNone(data["input"]["privacy_state_interface"])
        self.assertIsNone(data["ring"]["frame_interface"])
        self.assertIsNone(data["rfkill"]["wlan"])

    def test_empty_alsa_reports_no_endpoints(self):
        self.put("proc/asound/cards", b"--- no soundcards ---\n")
        self.put("proc/asound/pcm", b"")
        self.assertEqual(self.probe()["alsa"], {
            "cards": 0, "playback_devices": 0, "capture_devices": 0,
            "speaker_pcm": False, "array_pcm": False,
        })

    def test_pcm_matching_requires_valid_direction_substreams_and_codec(self):
        self.put("proc/asound/cards", b" 0 [private-test   ]: driver\n   additional info\n")
        self.put("proc/asound/pcm",
                 b"00-23: TLV320AIC3204 Playback tlv320aic32x4-hifi-23 : : playback 1\n"
                 b"00-24: TLV320AIC3101 Capture tlv320aic3101-codec-24 : : capture 2\n"
                 b"00-25: I2S : playback 1 : capture 1\n"
                 b"00-26: private-test Playback : playback 0 : capture invalid\n"
                 b"no pcm: invalid : capture 1\n")
        self.assertEqual(self.probe()["alsa"], {
            "cards": 1, "playback_devices": 2, "capture_devices": 2,
            "speaker_pcm": True, "array_pcm": True,
        })
        self.put("proc/asound/pcm", b"00-23: unrelated : playback 1\n"
                                    b"00-24: unrelated : capture 1\n")
        self.assertFalse(self.probe()["alsa"]["speaker_pcm"])
        self.assertFalse(self.probe()["alsa"]["array_pcm"])

    def test_only_public_key_and_interface_presence_is_returned(self):
        self.put("sys/class/input/input0/modalias", b"input:b0019v0000p0000e0000-e0,1,k71,72,ramlsfw\n")
        self.put("sys/class/input/input1/modalias", b"input:b0019v0000p0000e0000-e0,1,k8A,ramlsfw\n")
        self.put("sys/devices/platform/amz_privacy/privacy_state", b"private-test")
        self.put("sys/bus/i2c/drivers/is31fl32xx/1-003f/frame", b"private-test")
        for index, radio in enumerate([b"bluetooth", b"wlan", b"private-test"]):
            self.put(f"sys/class/rfkill/rfkill{index}/type", radio + b"\n")
        data = self.probe()
        self.assertEqual(data["input"], {
            "action_key": True, "mute_key": True, "privacy_state_interface": True,
        })
        self.assertEqual(data["ring"], {"frame_interface": True})
        self.assertEqual(data["rfkill"], {"wlan": True, "bluetooth": True})

    def test_key_bit_indices_and_driver_binding_are_exact(self):
        self.put("sys/class/input/input0/modalias", b"input:b0019-e0,1,k171,18A,r8A,amlsfw\n")
        self.put("sys/bus/i2c/drivers/is31fl32xx/bind", b"private-test")
        self.assertFalse(self.probe()["input"]["mute_key"])
        self.assertFalse(self.probe()["input"]["action_key"])
        self.assertFalse(self.probe()["ring"]["frame_interface"])


if __name__ == "__main__":
    unittest.main()
